import copy
import threading
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sqt import forward
from sqt.api import create_app
from sqt.paper_models import PaperConfig,Plan
from sqt.paper_service import PaperService
from sqt.store import Store
from sqt.subscription_ai import AIError,SubscriptionAI,child_env
import sqt.subscription_ai as ai_module

NOW=datetime(2026,9,8,15,0,tzinfo=timezone.utc)


def quote(now=NOW,price=100,**kw):
    return dict(symbol='AAPL',currency='USD',exchange='NASDAQ',price=price,bid=price,ask=price,
                bid_qty=10000,ask_qty=10000,spread_bps=0,timestamp=now.isoformat(),book_timestamp=now.isoformat(),**kw)


@pytest.fixture
def state():
    s=forward.create({'watchlist':[{'symbol':'AAPL','exchange':'NASDAQ'}],
        'require_news':False,'fee_bps':0,'slippage_bps':0,'decision_policy_v2':False},NOW)
    s['status']='running';s['quotes']={'AAPL':quote()}
    s['features']={'AAPL':{'daily':{'price':100},'daily_checked_at':NOW.isoformat(),
                         'minute':{'price':100},'minute_at':NOW.isoformat()}}
    return s


def snapshot(s,at=NOW):
    return {'snapshot_id':'test-snapshot','at':at.isoformat(),'revision':s['revision'],
            'generation':s['generation'],'news':[],'quotes':copy.deepcopy(s['quotes']),'trigger':'test'}


def plan(action='buy',bucket='swing',**kw):
    d=dict(symbol='AAPL',bucket=bucket,action=action,target_pct=10,confidence=.9,
           stop_pct=2,take_profit_pct=10,trailing_pct=3,reason='검증용 근거',news_ids=[])
    d.update(kw)
    return {'snapshot_id':'test-snapshot','summary':'가상 운용 검증','allocation':{'scalp':20,'swing':30,'long':30,'cash':20},
            'decisions':[d],'next_review_seconds':900}


def apply(s,p=None,now=NOW,snap=None,news=None,events=None):
    return forward.apply_plan(s,p or plan(),snap or snapshot(s,now),now,news or [],events or [])


def test_currency_and_duplicate_symbols():
    with pytest.raises(ValueError,match='동일 통화'):
        PaperConfig(watchlist=[{'symbol':'005930','exchange':'KRX'}])
    with pytest.raises(ValueError,match='중복'):
        PaperConfig(watchlist=[{'symbol':'AAPL','exchange':'NASDAQ'}]*2)


def test_buy_targets_are_total_and_duplicate_snapshot_rejected(state):
    snap=snapshot(state);assert apply(state,snap=snap)[0]['filled']==10
    assert state['cash']==9000 and state['positions']['AAPL:swing']['qty']==10
    with pytest.raises(ValueError,match='포지션이 변경'):
        apply(state,snap=snap)
    assert apply(state)[0]['filled']==0
    assert len(state['trades'])==1


def test_allocation_symbol_and_news_validation_precedes_trades(state):
    for value in [plan(symbol='MSFT'),plan(news_ids=['invented'])]:
        with pytest.raises(ValueError,match='관심 종목'):
            apply(state,value)
    p=plan();p['allocation']={'scalp':40,'swing':20,'long':20,'cash':20}
    with pytest.raises(ValueError,match='위험 한도'):apply(state,p)
    assert not state['trades']


@pytest.mark.parametrize('kind',['stale','future','closed','wide'])
def test_untradeable_quotes_never_fill(state,kind):
    now=NOW
    if kind=='stale':state['quotes']['AAPL']=quote(NOW-timedelta(seconds=31))
    if kind=='future':state['quotes']['AAPL']=quote(NOW+timedelta(seconds=20))
    if kind=='closed':now=NOW+timedelta(days=4);state['quotes']['AAPL']=quote(now)
    if kind=='wide':state['quotes']['AAPL']['spread_bps']=100
    assert apply(state,now=now)[0]['filled']==0


def test_late_model_and_price_drift_rejected(state):
    with pytest.raises(ValueError,match='240초'):apply(state,now=NOW+timedelta(seconds=241),snap=snapshot(state))
    snap=snapshot(state);state['quotes']['AAPL']=quote(price=103)
    assert '2%' in ' '.join(apply(state,snap=snap)[0]['blocked'])


def test_partial_liquidity_cannot_be_reused(state):
    state['quotes']['AAPL']['ask_qty']=3
    assert apply(state)[0]['filled']==3
    assert apply(state)[0]['filled']==0
    later=NOW+timedelta(seconds=1);state['quotes']['AAPL']=quote(later)
    assert apply(state,now=later)[0]['filled']==7


def test_total_symbol_exposure_across_buckets_is_capped(state):
    apply(state,plan(bucket='swing'))
    apply(state,plan(bucket='long'))
    assert sum(p['qty'] for p in state['positions'].values())==15
    assert state['cash']==8500


def test_fees_slippage_and_risk_budget_included(state):
    state['config'].update(fee_bps=50,sell_tax_bps=50,slippage_bps=50,risk_per_trade_pct=.1)
    apply(state,plan(target_pct=30,stop_pct=.8))
    p=state['positions']['AAPL:swing'];qty=p['qty']
    expected=100*1.005*1.005*qty
    assert state['cash']==pytest.approx(10000-expected)
    loss=p['unit_cost']-p['entry_price']*(1-.008)*.995*.99
    assert loss*qty<=10+1e-6
    assert state['cash']>=0


def test_hard_stop_runs_while_gpt_pending_and_with_quota_exhausted(state):
    apply(state);state['ai']['pending']=True;state['ai']['calls_today']=100
    later=NOW+timedelta(seconds=15);state['quotes']['AAPL']=quote(later,price=95)
    forward.mark_and_protect(state,later)
    assert not state['positions'] and state['cash']==9950
    assert state['trades'][-1]['reason']=='자동 손절·추적 손절'


def test_stop_never_fills_stale_quotes_and_gap_uses_observed_bid(state):
    apply(state);state['quotes']['AAPL']=quote(price=90)
    later=NOW+timedelta(seconds=60);forward.mark_and_protect(state,later)
    assert state['positions']
    state['quotes']['AAPL']=quote(later,price=90);forward.mark_and_protect(state,later)
    assert state['trades'][-1]['price']==90


def test_daily_loss_halt_and_partial_liquidation_remain_latched(state):
    state['config']['daily_loss_pct']=.5
    apply(state);later=NOW+timedelta(seconds=15)
    state['quotes']['AAPL']=quote(later,price=90);state['quotes']['AAPL']['bid_qty']=2
    forward.mark_and_protect(state,later)
    assert state['halt_reason'] and not state['entries_enabled']
    assert state['positions']['AAPL:swing']['qty']==8
    forward.mark_and_protect(state,later)
    assert state['positions']['AAPL:swing']['qty']==8


def test_protect_never_loosens_stop(state):
    apply(state);p=state['positions']['AAPL:swing'];original=p['stop']
    apply(state,plan('protect',stop_pct=10,trailing_pct=10))
    assert p['stop']==original and p['trailing_pct']==3
    apply(state,plan('protect',stop_pct=1,trailing_pct=1))
    assert p['stop']==99 and p['trailing_pct']==1


def test_scalp_closes_before_market_close_but_long_remains(state):
    apply(state,plan(bucket='scalp',target_pct=5));apply(state,plan(bucket='long',target_pct=5))
    later=NOW.replace(hour=19,minute=56);state['quotes']['AAPL']=quote(later)
    forward.mark_and_protect(state,later)
    assert 'AAPL:scalp' not in state['positions'] and 'AAPL:long' in state['positions']


def test_news_and_macro_entry_gates(state):
    state['config']['require_news']=True
    assert apply(state)[0]['blocked']==['최근 72시간 뉴스 없음']
    news=[{'id':'fed1','source':'fed','available_at':(NOW-timedelta(minutes=1)).isoformat(),'symbols':[],'impact':'high'}]
    assert '중요 발표' in ' '.join(apply(state,news=news)[0]['blocked'])
    news[0]['available_at']=(NOW-timedelta(hours=1)).isoformat()
    events=[{'id':'fomc','symbols':[],'impact':'high','known_at':(NOW-timedelta(days=1)).isoformat(),'scheduled_at':(NOW+timedelta(minutes=10)).isoformat()}]
    assert '경제 발표' in ' '.join(apply(state,news=news,events=events)[0]['blocked'])
    assert apply(state,news=news)[0]['filled']==10


def test_old_news_is_not_fresh_just_because_it_was_collected_today(state):
    state['config']['require_news']=True
    old={'id':'old','source':'fed','available_at':NOW.isoformat(),'published_at':(NOW-timedelta(days=30)).isoformat(),'symbols':[],'impact':'high'}
    assert apply(state,news=[old])[0]['filled']==0


def test_daily_halt_can_only_resume_on_later_day(state,tmp_path,monkeypatch):
    state.update(halt_reason='일일 손실 한도',halted_day='2026-09-08',entries_enabled=False)
    store=Store(tmp_path/'halt.sqlite3');store.put('paper_accounts',state['id'],state)
    service=PaperService(store,None);monkeypatch.setattr('sqt.paper_service.utcnow',lambda:NOW)
    with pytest.raises(ValueError,match='다음 거래일'):service.control(state['id'],'resume_entries')
    monkeypatch.setattr('sqt.paper_service.utcnow',lambda:NOW+timedelta(days=1))
    saved=service.control(state['id'],'resume_entries')
    assert saved['entries_enabled'] and not saved['halt_reason']


def test_slow_chart_refresh_does_not_block_protective_poll(state,tmp_path,monkeypatch):
    apply(state);state['ai']['pending']=True;store=Store(tmp_path/'slow.sqlite3');store.put('paper_accounts',state['id'],state)
    started,release=threading.Event(),threading.Event()
    def history(*args):started.set();release.wait(5);raise ValueError('test unavailable')
    broker=SimpleNamespace(history=history,quotes=lambda items:{'AAPL':quote(price=95)})
    service=PaperService(store,broker);monkeypatch.setattr('sqt.paper_service.utcnow',lambda:NOW)
    service.features_lock.acquire()
    from sqt.models import Instrument
    thread=threading.Thread(target=service.refresh_features,args=(state['id'],[Instrument(symbol='AAPL',exchange='NASDAQ')]))
    thread.start()
    try:
        assert started.wait(2)
        service.pulse(state['id'])
        assert not store.get('paper_accounts',state['id'])['positions']
    finally:release.set();thread.join(5)


def test_no_minute_data_blocks_scalp_but_not_long(state):
    state['features']['AAPL']['minute']=None
    assert apply(state,plan(bucket='scalp'))[0]['filled']==0
    assert apply(state,plan(bucket='long'))[0]['filled']==10


def test_ai_child_process_never_inherits_keys(monkeypatch):
    for k in ('OPENAI_API_KEY','CODEX_API_KEY','TOSS_CLIENT_SECRET','ADMIN_PASSWORD','KIS_APP_KEY'):
        monkeypatch.setenv(k,'secret')
    assert not any(k in child_env() for k in ('OPENAI_API_KEY','CODEX_API_KEY','TOSS_CLIENT_SECRET','ADMIN_PASSWORD','KIS_APP_KEY'))


def test_claude_runner_contract_and_no_api_key_fallback(monkeypatch):
    """실행 계약을 고정한다. 특히 구독 대신 API 과금으로 새지 않는지."""
    monkeypatch.setattr(ai_module, 'executable', lambda: 'claude')
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'sk-ant-oat01-test')
    # 이 값이 자식으로 새면 -p 모드에서 구독 대신 API 과금이 된다.
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-should-not-leak')
    monkeypatch.setenv('TOSS_CLIENT_SECRET', 'broker-secret-should-not-leak')

    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:] == ['--version']:
            return SimpleNamespace(returncode=0, stdout='2.1.267 (Claude Code)', stderr='')
        import json
        result = plan()
        result['decisions'] = []
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(
            {'is_error': False, 'structured_output': result, 'total_cost_usd': 0.01}))

    monkeypatch.setattr(ai_module.subprocess, 'run', run)
    assert SubscriptionAI().decide({'snapshot_id': 'test-snapshot'})['decisions'] == []

    args, kwargs = calls[-1]
    # 스키마 강제와 도구 차단, 단일 턴이 유지되어야 한다.
    assert '--json-schema' in args and args[args.index('--output-format') + 1] == 'json'
    # 도구 차단을 '*' 로 하면 --json-schema 의 결과 전달까지 막혀 error_max_turns 로 끝난다.
    # 실측으로 확인한 함정이라 이름 지정 방식을 고정한다.
    denied = args[args.index('--disallowedTools') + 1]
    assert denied != '*', "'*' 는 구조화 출력까지 막는다"
    for tool in ('Bash', 'Read', 'Write', 'WebFetch', 'mcp__*'):
        assert tool in denied, f'{tool} 이 차단 목록에 없다'
    # 구조화 출력은 생각 1턴 + 결과 전달 1턴을 쓴다. 1이면 항상 실패한다.
    assert int(args[args.index('--max-turns') + 1]) >= 2
    assert '--permission-prompts' in args and args[args.index('--permission-mode') + 1] == 'dontAsk'
    # --bare 는 OAuth 자격증명을 읽지 않으므로 구독 인증이 깨진다. 절대 붙이면 안 된다.
    assert '--bare' not in args
    assert not kwargs.get('shell', False)

    env = kwargs['env']
    assert env['CLAUDE_CODE_OAUTH_TOKEN'] == 'sk-ant-oat01-test'
    assert 'ANTHROPIC_API_KEY' not in env
    assert 'TOSS_CLIENT_SECRET' not in env

    # 토큰이 없으면 조용히 API 로 넘어가지 않고 명확히 거부한다.
    monkeypatch.delenv('CLAUDE_CODE_OAUTH_TOKEN')
    with pytest.raises(AIError, match='setup-token'):
        SubscriptionAI().decide({})


def test_claude_error_and_missing_schema_are_discarded(monkeypatch):
    monkeypatch.setattr(ai_module, 'executable', lambda: 'claude')
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'sk-ant-oat01-test')
    import json

    def responder(payload):
        def run(args, **kwargs):
            if args[1:] == ['--version']:
                return SimpleNamespace(returncode=0, stdout='2.1.267', stderr='')
            return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps(payload))
        return run

    # CLI 가 오류를 냈는데 exit 0 으로 끝나는 경우가 있다. is_error 를 봐야 한다.
    monkeypatch.setattr(ai_module.subprocess, 'run',
                        responder({'is_error': True, 'result': 'Not logged in'}))
    with pytest.raises(AIError, match='Not logged in'):
        SubscriptionAI().decide({})

    # 스키마에 맞는 출력이 없으면 본문 텍스트를 대신 쓰지 않는다.
    monkeypatch.setattr(ai_module.subprocess, 'run',
                        responder({'is_error': False, 'result': '사도 될 것 같습니다'}))
    with pytest.raises(AIError):
        SubscriptionAI().decide({})

    # result 가 비는 오류도 있다. '알 수 없는 오류' 로 뭉개지 말고 subtype 을 남긴다.
    monkeypatch.setattr(ai_module.subprocess, 'run',
                        responder({'is_error': True, 'subtype': 'error_max_turns'}))
    with pytest.raises(AIError, match='error_max_turns'):
        SubscriptionAI().decide({})


def test_model_result_rejected_after_stop_and_no_secret_in_api(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','test-password');app=create_app(tmp_path/'api.sqlite3',background=False)
    service=app.state.paper
    service.ai.status=lambda:{'authenticated':True,'installed':True,'api_key_used':False,'message':'OK'}
    service.broker.client_id='test';service.broker.client_secret='not-exposed'
    with TestClient(app) as client:
        assert client.post('/api/paper/accounts',json={}).status_code==401
        client.post('/api/login',json={'password':'test-password'})
        result=client.post('/api/paper/accounts',json={'watchlist':[{'symbol':'AAPL','exchange':'NASDAQ'}]}).json()
        id=result['id'];assert result['mode']=='forward_paper'
        assert 'not-exposed' not in client.get('/api/paper/status').text
        assert client.post(f'/api/paper/accounts/{id}/control',json={'action':'start'}).status_code==200
        assert client.post(f'/api/paper/accounts/{id}/control',json={'action':'stop'}).status_code==200
        assert client.post('/api/orders',json={}).status_code==404
        assert not client.get(f'/api/paper/accounts/{id}/export').json()['metrics']['live_ready']


def test_service_discard_plan_after_risk_fill(state,tmp_path,monkeypatch):
    store=Store(tmp_path/'state.sqlite3');store.put('paper_accounts',state['id'],state)
    service=PaperService(store,None);snap=snapshot(state);snap['trigger']='test'
    service.ai.decide=lambda _:plan()
    monkeypatch.setattr('sqt.paper_service.utcnow',lambda:NOW)
    state['revision']+=1;store.put('paper_accounts',state['id'],state)
    service.complete_review(state['id'],snap)
    result=store.get('paper_accounts',state['id'])
    assert not result['trades'] and not result['ai']['pending']
    assert result['decisions'][-1]['status']=='rejected'


def test_restart_stops_accounts_and_preserves_cash_positions(state,tmp_path):
    apply(state);state['ai']['pending']=True
    store=Store(tmp_path/'restart.sqlite3');store.put('paper_accounts',state['id'],state)
    service=PaperService(store,None);service.initialize()
    saved=store.get('paper_accounts',state['id'])
    assert saved['status']=='stopped' and not saved['ai']['pending']
    assert saved['cash']==state['cash'] and saved['positions']==state['positions']


def test_explicit_nas_resume_restores_only_previously_running_account(state,tmp_path):
    apply(state);state['ai']['pending']=True
    store=Store(tmp_path/'resume.sqlite3');store.put('paper_accounts',state['id'],state)
    idle=copy.deepcopy(state);idle['id']='idle-account';idle['status']='stopped';store.put('paper_accounts',idle['id'],idle)
    service=PaperService(store,None);service.initialize(resume=True)
    restored=store.get('paper_accounts',state['id'])
    assert restored['status']=='running' and not restored['ai']['pending'] and restored['last_poll_at'] is None
    assert restored['positions']==state['positions'] and restored['cash']==state['cash']
    assert store.get('paper_accounts','idle-account')['status']=='stopped'


def test_subscription_quota_blocks_model_but_keeps_running(state,tmp_path,monkeypatch):
    state['ai'].update(calls_day='2026-09-08',calls_today=32)
    store=Store(tmp_path/'quota.sqlite3');store.put('paper_accounts',state['id'],state)
    service=PaperService(store,None);monkeypatch.setattr('sqt.paper_service.utcnow',lambda:NOW)
    service.maybe_review(state['id'])
    saved=store.get('paper_accounts',state['id'])
    assert saved['status']=='running' and not saved['ai']['pending'] and '한도' in saved['ai']['error']
