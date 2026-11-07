import copy
from datetime import datetime,timedelta,timezone
from sqt.session_policy import context
from sqt.technical import intraday_features
from sqt.decision_quality import buy_reasons,net_payoff,performance
from sqt.paper_models import Proposal
from sqt import forward
from sqt.paper_service import PaperService
from sqt.store import Store
from .test_forward import state,plan,quote,NOW,apply,snapshot


def utc(s):return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def quality_state(state):
    state['config']['decision_policy_v2']=True
    state['features']['AAPL'].update(daily={'price':100,'sma20':99,'sma60':95,'sma200':90,'atr14':2},
        minute={'sma20':99,'volume_ratio':1.4,'macd_hist':.1,'atr14':.3},
        intraday={'bars':45,'vwap':99,'opening_range_high':99,'gap_pct':1})
    return state


def proposal(**kw):
    d=plan()['decisions'][0]
    d.update(setup='trend_follow',thesis='완료 일봉 60일선 위이며 거래량 증가 확인',
        counter_case='분봉 추세가 둔화되면 진입 근거가 약화될 수 있음',invalidation='현재 가격에서 2퍼센트 하락하면 상승 가설 무효')
    d.update(kw);return Proposal.model_validate(d)


def test_kr_us_windows_and_dst():
    assert context('KRX',utc('2026-09-08T23:30'))['phase']=='preopen'
    assert context('KRX',utc('2026-09-09T00:10'))['phase']=='opening'
    assert context('KRX',utc('2026-09-09T03:00'))['phase']=='midday'
    assert context('KRX',utc('2026-09-09T06:15'))['phase']=='closing'
    assert context('KRX',utc('2026-09-09T07:00'))['phase']=='postclose'
    assert context('NASDAQ',utc('2026-03-06T14:40'))['phase']=='opening'
    assert context('NASDAQ',utc('2026-03-09T13:40'))['phase']=='opening'
    assert context('NASDAQ',utc('2026-11-27T17:40'))['phase']=='closing'
    assert context('NASDAQ',utc('2026-09-12T15:00'))['phase']=='closed'


def test_only_completed_contiguous_opening_range_and_frames():
    start=utc('2026-09-08T13:30')
    rows=[{'timestamp':(start+timedelta(minutes=i)).isoformat(),'open':100,'high':102,'low':99,'close':101,'volume':10} for i in range(31)]
    f=intraday_features(rows,'NASDAQ',start+timedelta(minutes=30),100)
    assert f['bars']==30 and f['opening_range_high']==102 and f['vwap_scope']=='session'
    assert f['frames']['5m']['completed_bars']==6 and f['frames']['15m']['completed_bars']==2
    assert f['frames']['5m']['ema20'] is None
    partial=intraday_features(rows[1:],'NASDAQ',start+timedelta(minutes=30),100)
    assert partial['opening_range_high'] is None and partial['vwap_scope']=='available_window' and partial['gap_pct'] is None


def test_concrete_counter_case_and_cost_payoff_required(state):
    quality_state(state);assert not buy_reasons(state,proposal(),NOW)
    assert buy_reasons(state,proposal(counter_case=''),NOW)
    d=proposal(stop_pct=3,take_profit_pct=1)
    assert net_payoff(state['config'],d,quote())['reward_risk']<1
    assert any('손익비' in r for r in buy_reasons(state,d,NOW))
    raw=plan();raw['decisions']=[d.model_dump()]
    assert apply(state,raw)[0]['filled']==0 and not state['trades']


def test_midday_volume_is_policy_not_exchange_break(state):
    quality_state(state);now=utc('2026-09-08T16:00');state['quotes']['AAPL']=quote(now)
    d=proposal(bucket='scalp',setup='momentum_breakout',stop_pct=.8,take_profit_pct=2)
    assert context('NASDAQ',now)['regular_open']
    assert any('거래량' in r for r in buy_reasons(state,d,now))
    state['features']['AAPL']['minute']['volume_ratio']=1.6
    assert not buy_reasons(state,d,now)


def test_loss_reentry_wait_and_long_trend_evidence(state):
    quality_state(state)
    state['trades']=[{'symbol':'AAPL','side':'sell','at':NOW.isoformat(),'pnl':-10,'bucket':'swing'}]
    assert any('재진입' in r for r in buy_reasons(state,proposal(),NOW+timedelta(minutes=1)))
    state['trades']=[];state['features']['AAPL']['daily']['sma200']=None
    assert any('200일' in r for r in buy_reasons(state,proposal(bucket='long'),NOW))


def test_planning_only_never_executes_even_if_model_ignores_instruction(state,tmp_path):
    service=PaperService(Store(tmp_path/'plan.sqlite3'),None)
    snap=snapshot(state);snap['planning_only']=True
    import pytest
    with pytest.raises(ValueError,match='주문 판단'):service.apply_decision(state,plan(),snap,NOW,[],[])
    p=plan();p['decisions']=[]
    assert service.apply_decision(state,p,snap,NOW,[],[])==[] and not state['trades']


def test_two_currencies_resume_independently(state,tmp_path):
    store=Store(tmp_path/'dual.sqlite3');store.put('paper_accounts',state['id'],state)
    kr=forward.create({'currency':'KRW','initial_cash':1000000,'watchlist':[{'symbol':'005930','exchange':'KRX'}]},NOW)
    kr['status']='running';store.put('paper_accounts',kr['id'],kr)
    duplicate=copy.deepcopy(kr);duplicate['id']='zzz';store.put('paper_accounts','zzz',duplicate)
    service=PaperService(store,None);service.initialize(resume=True)
    running=[s for s in store.all('paper_accounts') if s['status']=='running']
    assert len(running)==2 and {s['config']['currency'] for s in running}=={'KRW','USD'}


def test_performance_is_net_and_labels_entry_phase(state):
    apply(state);later=NOW+timedelta(seconds=15);state['quotes']['AAPL']=quote(later,price=95)
    forward.mark_and_protect(state,later)
    group=performance(state)['by_entry_phase_bucket']['morning/swing']
    assert group['net_pnl']==-50 and group['wins']==0 and group['sell_fills']==1


def test_preopen_scheduler_runs_without_quote_and_only_once(state,tmp_path,monkeypatch):
    import sqt.paper_service as module
    now=utc('2026-09-08T13:00');monkeypatch.setattr(module,'utcnow',lambda:now)
    state['quotes']={};store=Store(tmp_path/'schedule.sqlite3');store.put('paper_accounts',state['id'],state)
    service=PaperService(store,None);started=[]
    class Thread:
        def __init__(self,**kwargs):started.append(kwargs)
        def start(self):pass
    monkeypatch.setattr(module.threading,'Thread',Thread)
    service.maybe_review(state['id'])
    assert len(started)==1
    record=store.all('paper_decisions')[0];assert record['snapshot']['planning_only'] and not record['snapshot']['quotes']
    result=plan();result.update(snapshot_id=record['id'],decisions=[])
    service.ai.decide=lambda _:result
    service.complete_review(state['id'],record['snapshot'])
    now+=timedelta(minutes=20);service.maybe_review(state['id'])
    assert len(started)==1 and not store.get('paper_accounts',state['id'])['trades']
