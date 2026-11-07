"""Fixed morning cohort: no external AI, news, notifications or real orders."""
import copy
import pytest
from sqt import daytrade as dt
from sqt import daytrade_live as dl
from sqt.paper_models import DayTradeConfig
from tests.test_daytrade import svc, clock, at, make, SCAN
from tests.test_daytrade_live import FakeOrders

SYMBOLS=['000500','006260','096770','005930','000660']

def prepare(svc,monkeypatch,**config):
    service,store,broker,ai=svc
    monkeypatch.setattr(dt.notify,'send',lambda *a,**k:None)
    monkeypatch.setattr(dt.overnight,'brief',lambda *a,**k:{'as_of':'','rows':[],'note':''})
    scan=copy.deepcopy(SCAN)
    scan['candidates']=[dict(symbol=s,name=s,score=50-i,news=[],themes=[],origins=[]) for i,s in enumerate(SYMBOLS)]
    monkeypatch.setattr(dt.scanner,'premarket_scan',lambda *a,**k:copy.deepcopy(scan))
    broker.prices.update({s:10000.0 for s in SYMBOLS})
    ai.plan['ranked_symbols']=list(reversed(SYMBOLS))
    clock(monkeypatch,at('07:30'))
    id=make(service,strategy='top5_morning',daily_review=False,**config)
    service.tick(id)
    return id

def test_five_equal_allocations_and_morning_exit(svc,monkeypatch):
    id=prepare(svc,monkeypatch)
    service,store,broker,ai=svc
    clock(monkeypatch,at('09:04'));service.tick(id)
    assert not store.get(service.account_kind,id)['positions']
    clock(monkeypatch,at('09:05'));service.tick(id)
    state=store.get(service.account_kind,id)
    assert list(state['positions'])==list(reversed(SYMBOLS))
    assert len({p['qty'] for p in state['positions'].values()})==1
    assert state['basket']['budget_equity']==10_000_000
    assert [p['basket_rank'] for p in state['positions'].values()]==[1,2,3,4,5]
    assert all(p['tp_pct']==4 and p['sl_pct']==2 for p in state['positions'].values())
    clock(monkeypatch,at('11:50'));service.tick(id)
    state=store.get(service.account_kind,id)
    assert state['phase']=='closed' and not state['positions']
    assert len(state['trades'])==10
    assert state['days'][state['day']]['basket']['symbols']==list(reversed(SYMBOLS))
    assert len(state['days'][state['day']]['realized_by_symbol'])==5
    assert [kind for kind,_ in ai.calls]==['plan']
    clock(monkeypatch,at('15:01'));service.tick(id)
    assert len(store.get(service.account_kind,id)['trades'])==10

def test_no_trade_opinion_does_not_veto_explicit_basket(svc,monkeypatch):
    svc[3].plan.update(market_stance='no_trade',picks=[],no_trade_reason='약세')
    id=prepare(svc,monkeypatch)
    clock(monkeypatch,at('09:05'));svc[0].tick(id)
    state=svc[1].get(svc[0].account_kind,id)
    assert len(state['positions'])==5
    assert state['plan']['ai_market_stance']=='no_trade'

@pytest.mark.parametrize('symbols',[['000500']*5,['999999']+SYMBOLS[:4],SYMBOLS[:4]])
def test_invalid_nonempty_ranking_rejected(svc,monkeypatch,symbols):
    id=prepare(svc,monkeypatch)
    state=svc[1].get(svc[0].account_kind,id)
    state['plan']=None
    svc[0]._apply_plan(state,dict(svc[3].plan,ranked_symbols=symbols),at('07:30'))
    assert state['plan'] is None and state['phase']=='scanned'

def test_fallback_and_shortage_are_explicit(svc,monkeypatch):
    id=prepare(svc,monkeypatch)
    state=svc[1].get(svc[0].account_kind,id)
    state['scan']['candidates']=state['scan']['candidates'][:3]
    svc[0]._apply_plan(state,dict(svc[3].plan,ranked_symbols=[]),at('07:30'))
    assert state['basket']['rank_source']=='scanner_order'
    assert state['basket']['symbols']==SYMBOLS[:3]
    assert any('후보 부족' in l['message'] for l in state['log'])

def test_no_rebuy_after_take_profit_or_manual_rescan(svc,monkeypatch):
    id=prepare(svc,monkeypatch)
    clock(monkeypatch,at('09:05'));svc[0].tick(id)
    svc[2].prices[SYMBOLS[0]]=11000
    clock(monkeypatch,at('09:06'));svc[0].tick(id)
    clock(monkeypatch,at('09:07'));svc[0].tick(id)
    state=svc[1].get(svc[0].account_kind,id)
    assert len([t for t in state['trades'] if t['side']=='buy'])==5
    assert SYMBOLS[0] not in state['positions']
    with pytest.raises(ValueError):svc[0].control(id,'scan_now')

def test_missing_quote_cutoff_and_late_plan(svc,monkeypatch):
    id=prepare(svc,monkeypatch)
    svc[2].prices.pop(SYMBOLS[0])
    clock(monkeypatch,at('09:05'));svc[0].tick(id)
    svc[2].prices[SYMBOLS[0]]=10000
    clock(monkeypatch,at('09:15'));svc[0].tick(id)
    state=svc[1].get(svc[0].account_kind,id)
    assert len(state['positions'])==4
    state.pop('basket');state['plan']=None
    svc[0]._apply_plan(state,svc[3].plan,at('09:15'))
    assert state['plan'] is None

@pytest.mark.parametrize('payload',[{'basket_entry_at':'09:99'},{'basket_exit_at':'12:01'},
    {'position_pct':21},{'max_candidates':4},{'basket_entry_at':'11:45','basket_entry_minutes':10}])
def test_config_rejects_invalid_basket(payload):
    with pytest.raises(ValueError):DayTradeConfig(strategy='top5_morning',**payload)

def test_live_window_expiry_cancels_and_partial_fill_exits(svc,monkeypatch):
    monkeypatch.setenv('ENABLE_LIVE_TRADING','true')
    monkeypatch.setenv('TOSS_ACCOUNT_SEQ','12345')
    monkeypatch.setenv('LIVE_CAPITAL_KRW','10000000')
    monkeypatch.setenv('LIVE_MAX_ORDER_KRW','2000000')
    service,store,broker,ai=svc
    orders=FakeOrders();orders.power=10_000_000
    service.live.orders=orders
    id=prepare(svc,monkeypatch,mode='live')
    clock(monkeypatch,at('09:05'));service.tick(id)
    assert len(orders.created)==5
    oid,intent=orders.created[0]
    orders.fill(oid,2,10000,done=False)
    clock(monkeypatch,at('09:15'));service.tick(id)
    assert len(orders.cancelled)==5
    clock(monkeypatch,at('11:50'));service.tick(id)
    state=store.get(service.account_kind,id)
    assert state['phase']!='closed'
    sellid,sell=orders.created[-1]
    assert sell['side']=='sell' and sell['qty']==2
    orders.fill(sellid,2,9990)
    clock(monkeypatch,at('11:51'));service.tick(id)
    state=store.get(service.account_kind,id)
    assert state['phase']=='closed' and not state['positions']
    assert state['trades'][-1]['basket_rank']==intent['meta']['basket_rank']

def test_frozen_budget_and_config_cannot_change_during_cohort(svc,monkeypatch):
    id=prepare(svc,monkeypatch)
    svc[2].prices.pop(SYMBOLS[0])
    clock(monkeypatch,at('09:05'));svc[0].tick(id)
    svc[2].prices[SYMBOLS[0]]=10000
    clock(monkeypatch,at('09:06'));svc[0].tick(id)
    state=svc[1].get(svc[0].account_kind,id)
    assert len({p['qty'] for p in state['positions'].values()})==1
    with pytest.raises(ValueError):svc[0].update_config(id,{'position_pct':16})

def test_switch_requires_empty_account_and_ai_schema_accepts_no_trade_rank(svc,monkeypatch):
    from sqt.paper_models import DayPlan
    plan=DayPlan(summary='Weak market',market_stance='no_trade',no_trade_reason='Weak',ranked_symbols=SYMBOLS,picks=[])
    assert len(plan.ranked_symbols)==5
    clock(monkeypatch,at('09:05'))
    id=make(svc[0],daily_review=False)
    state=svc[1].get(svc[0].account_kind,id)
    state['positions']={'000500':{'symbol':'000500'}}
    state['status']='stopped'
    svc[1].put(svc[0].account_kind,id,state)
    with pytest.raises(ValueError,match='보유가 없는'):svc[0].update_config(id,{'strategy':'top5_morning'})
