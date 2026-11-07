import copy
import threading
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from sqt import learning,financial_brief,news,notify,scanner
from sqt.store import Store
from sqt.paper_models import DayTradeConfig
from sqt.subscription_ai import SubscriptionAI,compact_snapshot

NOW=datetime(2026,10,7,0,tzinfo=timezone.utc)

def account():
    return {'id':'A','config':DayTradeConfig().model_dump(),'positions':{},'trades':[],'scan':{'candidates':[]},'quotes':{}}

def sells(state,version,count,return_pct,start=NOW,decision=None):
    for i in range(count):
        at=start+timedelta(days=i%3,minutes=i)
        state['trades'].append({'symbol':str(i),'side':'sell','at':at.isoformat(),
            'entry_at':(at-timedelta(minutes=1)).isoformat(),'policy_version':version,
            'decision_id':decision,'pnl':return_pct*100,'cost_basis':10000})

def test_evidence_is_immutable_and_results_separate(tmp_path):
    store=Store(tmp_path/'a.db');state=account();snapshot={'quote':{'ask':123}}
    id=learning.record(store,state,'plan',snapshot,NOW)
    snapshot['quote']['ask']=999
    learning.result(store,id,{'summary':'done'},'',{'model':'test'},2,NOW)
    assert store.get('decision_evidence',id)['snapshot']['quote']['ask']==123
    assert store.get('decision_results',id)['seconds']==2
    assert learning.context(store,state)['evidence'][0]['id']==id

def test_proposal_requires_real_linked_outcomes_and_unknown_ids_rejected(tmp_path):
    store=Store(tmp_path/'a.db');state=account()
    learning.propose(store,state,{'improvements':[{'lesson':'Do not chase unsupported moves','evidence_ids':['fake']}]},NOW)
    assert state['learning']['proposals'][0]['status']=='rejected'
    learning.propose(store,state,{'lessons':['Avoid late chase']},NOW)
    assert state['learning']['proposals'][-1]['status']=='needs_evidence'
    learning.advance(state,NOW+timedelta(days=1))
    assert not state['learning']['active_lessons']

def ready_trial(store,state):
    version=learning.policy(state)
    ids=[learning.record(store,state,'plan',{},NOW+timedelta(days=i)) for i in range(3)]
    sells(state,version,12,1,decision=ids[0])
    learning.propose(store,state,{'improvements':[{'lesson':'Avoid unsupported late chase','evidence_ids':ids}]},NOW+timedelta(days=4))
    assert state['learning']['proposals'][0]['status']=='ready'
    return version

def test_next_day_trial_keeps_limits_and_rolls_back_bad_outcomes(tmp_path):
    store=Store(tmp_path/'a.db');state=account();old=ready_trial(store,state)
    config=copy.deepcopy(state['config'])
    learning.advance(state,NOW+timedelta(days=4))
    assert state['learning']['trial'] is None
    learning.advance(state,NOW+timedelta(days=5))
    trial=state['learning']['trial'];assert trial and trial['version']!=old
    assert state['config']==config
    sells(state,trial['version'],12,-1,start=NOW+timedelta(days=5))
    learning.advance(state,NOW+timedelta(days=9))
    assert not state['learning']['active_lessons']
    assert state['learning']['proposals'][0]['status']=='rolled_back'

def test_trial_does_not_apply_when_disabled(tmp_path):
    state=account();store=Store(tmp_path/'a.db');ready_trial(store,state)
    state['config']['self_improve']=False
    learning.advance(state,NOW+timedelta(days=5))
    assert not state['learning']['trial']

def test_partial_exit_is_not_two_samples_or_complete_while_held():
    state=account();sells(state,'v',1,1)
    t=state['trades'][0];state['trades'].append(dict(t))
    assert learning.metrics(learning.rounds(state))['trades']==1
    state['positions'][t['symbol']]={'entry_at':t['entry_at'],'qty':1}
    assert not learning.rounds(state)

def test_candidate_observation_uses_fresh_quotes_and_is_not_real_profit():
    state=account();state['scan']['candidates']=[{'symbol':'A'}]
    state['quotes']['A']={'bid':100,'ask':101,'timestamp':NOW.isoformat(),'book_timestamp':NOW.isoformat()}
    learning.observe(state,NOW)
    row=state['learning']['observations']['A'];assert row['mark_move_pct']<0
    learning.observe(state,NOW+timedelta(seconds=31))
    assert row['last_at']==NOW.isoformat()

def test_fed_calendar_minutes_are_next_day_kst_and_dst_correct():
    body=b'<div class="panel-body"><div class="row"><div class="col-xs-2">2:00 p.m.</div><div class="col-xs-7">FOMC Minutes</div><div class="col-xs-3">7</div></div></div>'
    row=news.fed_calendar_events(body,NOW,2026,10,'https://www.federalreserve.gov/calendar')[0]
    assert notify.kst(row['scheduled_at'])=='10-08 03:00'
    winter=news.fed_calendar_events(body,datetime(2026,11,7,tzinfo=timezone.utc),2026,11,'https://www.federalreserve.gov/calendar')[0]
    assert notify.kst(winter['scheduled_at'])=='11-08 04:00'


def test_fed_next_month_does_not_skip_short_month_at_month_end(tmp_path,monkeypatch):
    urls=[]
    monkeypatch.setattr(news,'utcnow',lambda:datetime(2026,10,31,tzinfo=timezone.utc))
    def unavailable(url):
        urls.append(url)
        raise OSError('offline test')
    monkeypatch.setattr(news,'fetch',unavailable)
    news.collect(Store(tmp_path/'calendar.db'))
    assert any('2026-november.htm' in url for url in urls)
    assert not any('2026-december.htm' in url for url in urls)

def test_brief_current_revision_future_known_excluded_and_missing_health(tmp_path):
    store=Store(tmp_path/'a.db')
    base={'id':'fed-A','title':'FOMC Minutes','source':'fed','url':'https://www.federalreserve.gov','known_at':NOW.isoformat(),'scheduled_at':(NOW+timedelta(hours=18)).isoformat(),'impact':'high'}
    store.put('events','fed-A',base)
    revised=dict(base,id='fed-A-revised',revision_of='fed-A',known_at=(NOW+timedelta(seconds=1)).isoformat(),scheduled_at=(NOW+timedelta(hours=19)).isoformat())
    store.put('events',revised['id'],revised)
    assert financial_brief.build(store,NOW)['events'][0]['id']=='fed-A'
    brief=financial_brief.build(store,NOW+timedelta(seconds=2))
    assert len(brief['events'])==1 and brief['events'][0]['id']=='fed-A-revised'
    assert brief['missing_sources']

def test_brief_sends_once_per_slot_and_no_direct_external_messages(tmp_path,monkeypatch):
    store=Store(tmp_path/'a.db');sent=[]
    monkeypatch.setattr(notify,'configured',lambda:True)
    monkeypatch.setattr(notify,'send',lambda text:sent.append(text) or True)
    monkeypatch.setattr(notify,'_level_override','all')  # 상세 알림을 켠 경우에만 금융 일정을 보낸다
    financial_brief.send_due(store,NOW-timedelta(hours=2)) # KST07:00
    assert not sent
    financial_brief.send_due(store,NOW);financial_brief.send_due(store,NOW)
    assert len(sent)==1
    financial_brief.send_due(store,NOW+timedelta(hours=12))
    assert len(sent)==2

def test_short_notifications_distinguish_decisions_orders_and_fills():
    message=notify.event_message('news','A','Test',{}, {'summary':'long '*1000,'decisions':[{'symbol':'A','action':'buy','reason':'long '*1000}]})
    assert len(message)<300 and '체결 아님' in message
    order=notify.order_message({'side':'buy','symbol':'A','name':'<test>','qty':2,'limit_price':100})
    assert '주문 접수' in order and '&lt;test&gt;' in order


def test_end_summary_uses_full_kst_date_not_previous_year():
    state={'day':'2026-10-07','positions':{},'trades':[
        {'side':'sell','at':'2025-10-07T01:00:00Z','pnl':99999},
        {'side':'sell','at':'2026-10-06T23:00:00Z','pnl':1234}]}
    text=notify.summary_message(state)
    assert '1,234원' in text and '99,999' not in text and '1건' in text

def test_compact_keeps_all_trade_costs_latest_continuous_bars_and_source_unchanged():
    value={'trades':[{'fee':i} for i in range(60)],'recent_bars':list(range(100)),'news':[{'body':'x'*1000}]}
    result=compact_snapshot(value)
    assert len(result['trades'])==60 and result['recent_bars']==list(range(76,100))
    assert len(result['news'][0]['body'])==400 and len(value['news'][0]['body'])==1000

def test_auth_success_is_cached_but_failed_status_is_not(monkeypatch):
    ai=SubscriptionAI();calls=[]
    monkeypatch.setattr(ai,'status',lambda:calls.append(1) or {'installed':True,'authenticated':True})
    ai._guard();ai._guard();assert len(calls)==1

def test_chart_cache_is_short_and_parallel_batch_bounded(monkeypatch):
    broker=SimpleNamespace();calls=[]
    def read(b,s,now):calls.append(s);return {'as_of':now.isoformat(),'errors':[]}
    monkeypatch.setattr(scanner,'_read_chart',read)
    scanner.chart_context(broker,'A',NOW);scanner.chart_context(broker,'A',NOW+timedelta(seconds=14))
    assert calls==['A']
    scanner.chart_context(broker,'A',NOW+timedelta(seconds=15));assert len(calls)==2
    barrier=threading.Barrier(3,timeout=3)
    def concurrent(b,s,now):barrier.wait();return {'as_of':now.isoformat(),'errors':[]}
    monkeypatch.setattr(scanner,'_read_chart',concurrent)
    assert len(scanner.chart_batch(broker,['B','C','D'],NOW))==3


def test_completed_daily_cache_expires_hourly_and_at_kst_day_change(monkeypatch):
    calls=[]
    broker=SimpleNamespace(history=lambda *a,**k:calls.append(1) or [{'date':'2026-10-06'}],
                           minutes=lambda *a,**k:[])
    monkeypatch.setattr(scanner,'analyze',lambda bars:{'price':100})
    monkeypatch.setattr(scanner,'intraday_features',lambda *a:{'ok':True})
    first=scanner.chart_context(broker,'005930',NOW)
    first['daily']['price']=0
    assert scanner.chart_context(broker,'005930',NOW+timedelta(minutes=5))['daily']['price']==100
    assert len(calls)==1
    scanner.chart_context(broker,'005930',NOW+timedelta(hours=1))
    assert len(calls)==2
    scanner.chart_context(broker,'005930',NOW+timedelta(days=1))
    assert len(calls)==3


def test_financial_brief_stays_on_web_by_default(tmp_path,monkeypatch):
    store=Store(tmp_path/'b.db');sent=[]
    monkeypatch.setattr(notify,'configured',lambda:True)
    monkeypatch.setattr(notify,'send',lambda text:sent.append(text) or True)
    monkeypatch.setattr(notify,'_level_override',None)
    monkeypatch.delenv('TELEGRAM_LEVEL',raising=False)
    financial_brief.send_due(store,NOW)
    assert not sent
