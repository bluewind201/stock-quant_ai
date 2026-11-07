from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqt import in_play, trade_setups, scanner, notify
from sqt.paper_models import DayTradeConfig
from tests.test_daytrade import svc, make, clock, at
from tests.test_strategy_313 import chart


def setup(svc,monkeypatch):
    service,store,broker,ai=svc
    clock(monkeypatch,at('09:30'))
    monkeypatch.setattr(notify,'send',lambda *a:None)
    id=make(service,strategy='in_play',risk_per_trade_pct=.05)
    state=store.get(service.account_kind,id)
    state['day']='2026-09-11'
    data=chart('orb');data['daily']={'atr14':8,'price':100}
    data['intraday'].update(opening_5m_high=101,opening_5m_low=99,opening_5m_volume=500)
    state['scan']={'candidates':[{'symbol':'000500','name':'test','technical':data,'in_play':{},'news':[]}]}
    broker.prices['000500']=102
    service._refresh_quotes(state,['000500'])
    service.in_play_feed=SimpleNamespace(poll=lambda *a:None)
    service._in_play_state(state,at('09:30'))['reviews']=16
    decision={'symbol':'000500','action':'buy','reason':'수주 촉매와 가격 조건을 확인할 때만 매수',
              'take_profit_pct':4,'stop_loss_pct':5,'max_entry_price':103}
    return service,store,broker,ai,state,data,decision


def test_buy_decision_is_permission_not_immediate_order(svc,monkeypatch):
    service,_,_,_,state,_,d=setup(svc,monkeypatch)
    service._apply_event(state,{'decisions':[d]},at('09:30'))
    assert not state['positions'] and not state['trades']
    assert state['in_play']['approvals']['000500']['expires_at']==at('09:50').isoformat()


def test_approved_signal_executes_while_ai_pending_and_only_once(svc,monkeypatch):
    service,_,_,_,state,_,d=setup(svc,monkeypatch)
    service._approve_in_play(state,[d],at('09:30'))
    state['ai']['pending']=True
    service._monitor_in_play(state,at('09:30'))
    position=state['positions']['000500']
    assert position['strategy']=='in_play' and position['strategy_stop']>99
    costs=.75/100*position['entry_price']
    assert position['qty']*(position['entry_price']-position['strategy_stop']+costs)<=position['risk_budget_krw']
    service._monitor_in_play(state,at('09:30'))
    assert len(state['trades'])==1 and state['in_play']['approvals']['000500']['used']


def test_old_or_revoked_permission_never_executes(svc,monkeypatch):
    service,_,_,_,state,_,d=setup(svc,monkeypatch)
    service._approve_in_play(state,[d],at('09:30'),issued_at=at('09:00'))
    service._monitor_in_play(state,at('09:30'))
    assert not state['positions']
    service._approve_in_play(state,[d],at('09:30'))
    service._approve_in_play(state,[dict(d,action='hold')],at('09:30'))
    service._monitor_in_play(state,at('09:30'))
    assert not state['positions']


def test_news_budget_cannot_block_conditional_review(svc,monkeypatch):
    service,_,_,ai,state,_,d=setup(svc,monkeypatch)
    state['in_play']['reviews']=0
    state['ai']['calls_today']=state['config']['max_ai_calls_per_day']
    ai.event={'summary':'조건부 승인','decisions':[d]}
    service._monitor_in_play(state,at('09:30'))
    assert state['in_play']['approvals']['000500']
    assert state['ai']['calls_today']==state['config']['max_ai_calls_per_day']
    assert ai.calls[-1][1]['trigger']=='conditional_approval'


def test_opening_rvol_is_distinct_from_local_volume_and_stale_bars(svc,monkeypatch):
    _,_,_,_,_,data,_=setup(svc,monkeypatch)
    quote={'ask':102}
    assert trade_setups.evaluate('in_play',data,quote,at('09:30'))['allowed']
    data['in_play']={'opening_rvol':1.1,'rvol_samples':14}
    assert not trade_setups.evaluate('in_play',data,quote,at('09:30'))['allowed']
    data['in_play']={'opening_rvol':3,'relative_strength_pct':-1}
    assert not trade_setups.evaluate('in_play',data,quote,at('09:30'))['allowed']
    data['in_play']={'opening_rvol':3,'relative_strength_pct':1}
    assert not trade_setups.evaluate('in_play',data,quote,at('09:33'))['allowed']


def test_pending_orders_count_against_position_limit(svc,monkeypatch):
    service,_,_,_,state,data,d=setup(svc,monkeypatch)
    state['mode']='live';state['config']['max_positions']=1
    monkeypatch.setattr(service.live,'pending',lambda *a:[{'symbol':'006260'}])
    assert not service._fill_buy(state,'000500','test',100000,state['quotes']['000500'],4,5,{'source':'in_play','_chart':data},at('09:30'))


def test_feed_saves_observed_history_excluding_today(tmp_path,monkeypatch):
    from sqt.store import Store
    store=Store(tmp_path/'feed.sqlite3')
    store.put('in_play_volume','000500',{'2026-09-08':100,'2026-09-09':100,'2026-09-10':100,'2026-09-11':99999})
    monkeypatch.setattr(in_play,'utcnow',lambda:at('09:30'))
    monkeypatch.setattr(scanner,'chart_context',lambda *a:{'daily':{'price':100},'intraday':{'opening_5m_volume':300}})
    monkeypatch.setattr(in_play.krnews,'stock_news',lambda *a,**k:[])
    broker=SimpleNamespace(rankings=lambda *a,**k:[],quotes=lambda *a:{'000500':{'price':103,'timestamp':at('09:30').isoformat()}},
        market_context=lambda:{'indices_and_rates':[{'symbol':'KOSPI','stale':False,'change_pct':1,'timestamp':at('09:30').isoformat()}]})
    feed=in_play.InPlayFeed(store,broker)
    try:
        result=feed.collect(at('09:30'),DayTradeConfig().model_dump(),[{'symbol':'000500','trading_amount':1e10}])
        metrics=result['candidates'][0]['in_play']
        assert metrics['opening_rvol']==3 and metrics['rvol_samples']==3
        assert metrics['relative_strength_pct']==pytest.approx(2)
        assert store.get('in_play_volume','000500')['2026-09-11']==300
    finally:feed.pool.shutdown()


def test_feed_poll_never_waits_for_unfinished_network(tmp_path):
    from sqt.store import Store
    from concurrent.futures import Future
    feed=in_play.InPlayFeed(Store(tmp_path/'async.sqlite3'),SimpleNamespace())
    feed.jobs['x']=Future()
    try:assert feed.poll('x',at('09:30'),{},[]) is None
    finally:feed.pool.shutdown()


def test_expired_live_permission_blocks_submission_and_risk_uses_tick_price(tmp_path):
    from sqt.store import Store
    from sqt.daytrade_live import LiveExecution
    from tests.test_daytrade_live import FakeOrders
    orders=FakeOrders();live=LiveExecution(Store(tmp_path/'live.sqlite3'),orders)
    cfg=DayTradeConfig().model_dump()
    state={'id':'x','config':cfg,'cash':1000000,'status':'running','errors':{}}
    meta={'strategy_stop':9800,'risk_budget_krw':1000,'max_qty':50,'approval_expires_at':at('09:29').isoformat()}
    assert live.queue_buy(state,'000500','test',500000,{'ask':10001,'spread_bps':20},5,3,meta,at('09:30'),lambda *a:None)
    intent=live.pending('x')[0]
    assert intent['qty']*(intent['limit_price']-9800+intent['limit_price']*.0075)<=1000
    live.pulse(state,at('09:30'),lambda *a:None)
    assert not orders.created and live.journal('x')[0]['status']=='BLOCKED'


def test_separate_eod_overnight_exception_is_preserved(svc,monkeypatch):
    service,_,_,_,state,_,_=setup(svc,monkeypatch)
    clock(monkeypatch,at('15:01'));service._refresh_quotes(state,['000500'])
    assert service._fill_buy(state,'000500','test',10000,state['quotes']['000500'],10,3,
        {'source':'close','hold_overnight':True},at('15:01'))
    assert state['positions']['000500']['hold_overnight']
    assert state['positions']['000500']['source']=='close'
