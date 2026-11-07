from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import threading
import pytest
from sqt import portfolio,daytrade as dt,notify
from sqt.calendar import calendar
from sqt.paper_models import DayTradeConfig,PortfolioPlan
from sqt.store import Store
from sqt.daytrade_live import LiveExecution

NOW=datetime(2026,10,21,1,tzinfo=timezone.utc)

def bars():
    days=calendar('KRX').sessions_in_range('2025-01-01','2026-10-20')[-300:]
    return [{'date':d.strftime('%Y-%m-%d'),'open':1000+i,'close':1000+i,'low':998+i,'high':1002+i,'volume':10000000} for i,d in enumerate(days)]

def test_completed_bars_signals_and_no_future_leak():
    rows=bars();f=portfolio.features(rows,NOW)
    assert all(s['eligible'] for s in f['signals'].values())
    assert f['sma200'] and f['momentum252_skip21_pct']>0
    assert portfolio.features(rows+[dict(rows[-1],date='2026-10-21',close=1)],NOW)==f
    with pytest.raises(ValueError,match='누락'):
        portfolio.features(rows[:100]+rows[101:],NOW)
    with pytest.raises(ValueError,match='미갱신'):
        portfolio.features(rows[:-1],NOW)
    assert not portfolio.features(rows[-100:],NOW)['signals']['long']['eligible']

def test_profile_preserves_explicit_legacy_and_portfolio_clock():
    cfg=DayTradeConfig(strategy='portfolio',portfolio_style='long',auto_tune=True)
    assert cfg.entry_at=='09:05' and cfg.entry_window_minutes==365 and not cfg.close_entry and not cfg.auto_tune
    assert DayTradeConfig(strategy='top5_morning').force_close_at=='11:50'
    with pytest.raises(ValueError):DayTradeConfig(portfolio_core=['bad'])
    with pytest.raises(ValueError):PortfolioPlan(summary='test',picks=[],exits=[{'symbol':'A','reason':'short'}])

def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(dt,'utcnow',lambda:NOW)
    monkeypatch.setattr(notify,'send',lambda *a:True)
    broker=SimpleNamespace(quotes=lambda instruments:{i.symbol:quote() for i in instruments})
    service=dt.DayTradeService(Store(tmp_path/'p.db'),broker,ai=SimpleNamespace(lock=threading.Lock()))
    state=service.create({'strategy':'portfolio','initial_cash':10000000,'max_positions':8,'position_pct':10})
    state=service.store.get(service.account_kind,state['id']);state['day']='2026-10-21';state['status']='running'
    monkeypatch.setattr(service,'_tradable',lambda *a:True)
    return service,state

def quote(price=10000,at=NOW):
    return {'bid':price,'ask':price,'price':price,'timestamp':at.isoformat(),'book_timestamp':at.isoformat(),'spread_bps':0}

def buy(service,state,h='long',sl=8):
    state['quotes']['005930']=quote()
    return service._fill_buy(state,'005930','Test',1000000,quote(),24,sl,
        {'source':'plan','horizon':h,'thesis':'test','max_entry_price':11000},NOW)

def test_multiday_entry_without_intraday_or_resistance_gate_and_risk_cap(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    s['scan']={'candidates':[{'symbol':'005930','technical':{'daily':{'price':10000,'resistance20':10001}}}]}
    assert buy(service,s)
    p=s['positions']['005930']
    assert p['horizon']=='long' and p['hold_overnight']
    assert p['cost']<650000  # .5% risk / (8% stop + costs)
    assert not buy(service,s)

def test_budget_counts_pending_orders_and_disabled_horizon(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(service,'is_live',lambda *a:True)
    monkeypatch.setattr(service.live,'pending',lambda *a:[{'symbol':'000660','qty':200,'filled_qty':0,'limit_price':10000,'meta':{'horizon':'short'}}])
    assert portfolio.budget(service,s,'short',1000000,quote(),3,NOW)==0
    s['config']['portfolio_style']='long'
    assert portfolio.budget(service,s,'medium',1000000,quote(),5,NOW)==0

def test_no_close_at_day_end_and_carry_next_day(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);assert buy(service,s)
    monkeypatch.setattr(service,'_needs_plan',lambda *a:False)
    monkeypatch.setattr(service,'_maybe_review',lambda *a:None)
    portfolio.tick(service,s,NOW.replace(hour=6,minute=30)) # KST 15:30
    assert s['positions'] and s['phase']=='closed' and not any(t['side']=='sell' for t in s['trades'])
    service.store.put(service.account_kind,s['id'],s)
    monkeypatch.setattr(dt,'utcnow',lambda:NOW+timedelta(days=1,hours=-3)) # next KST 07:00
    service.tick(s['id']);after=service.store.get(service.account_kind,s['id'])
    assert after['positions']['005930']['hold_overnight']

def test_stale_quotes_do_not_ratchet_stop_or_sell(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);assert buy(service,s)
    old=s['positions']['005930']['sl_price']
    monkeypatch.setattr(service,'_refresh_quotes',lambda *a:None)
    s['quotes']['005930']=quote(1,NOW-timedelta(seconds=31))
    portfolio.monitor(service,s,NOW)
    assert s['positions']['005930']['sl_price']==old

def test_rising_price_trails_and_gap_exit_uses_actual_bid(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);assert buy(service,s)
    monkeypatch.setattr(service,'_refresh_quotes',lambda *a:None)
    s['quotes']['005930']=quote(14000)
    portfolio.monitor(service,s,NOW)
    assert s['positions'] # No fixed +24% take-profit
    assert s['positions']['005930']['sl_price']==12880
    s['quotes']['005930']=quote(12000)
    portfolio.monitor(service,s,NOW)
    assert not s['positions'] and s['trades'][-1]['price']<=12000
    assert not buy(service,s) # no same-day churn

def test_plan_rejects_wrong_horizon_and_keeps_diagnostics(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    f=portfolio.features(bars(),NOW)
    s['scan']={'candidates':[{'symbol':'005930','name':'Test','eligible_horizons':['medium'],'portfolio':f}]}
    p={'symbol':'005930','horizon':'long','weight_pct':10,'max_entry_price':1000,'thesis':'reason'}
    portfolio.apply_plan(service,s,{'summary':'test','picks':[p]},NOW)
    assert not s['plan']['picks']
    p['horizon']='medium';portfolio.apply_plan(service,s,{'summary':'test','picks':[p]},NOW)
    assert s['plan']['picks'][0]['stop_loss_pct']==f['signals']['medium']['stop_pct']

def test_live_partial_fill_keeps_horizon_and_never_lowers_raised_stop(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    intent={'symbol':'005930','name':'Test','side':'buy','id':'x','broker_order_id':'x','reason':'plan','filled_qty':1,'qty':2,
        'meta':{'tp_pct':24,'sl_pct':8,'strategy':'portfolio','horizon':'long','hold_overnight':True}}
    service.live._apply_fill(s,intent,1,10000,10,NOW,lambda *a:None)
    p=s['positions']['005930'];p['sl_price']=12000
    service.live._apply_fill(s,intent,1,11000,11,NOW,lambda *a:None)
    assert p['horizon']=='long' and p['sl_price']==12000

def test_model_input_uses_portfolio_contract_without_daytrading_guidance(monkeypatch):
    from sqt.subscription_ai import SubscriptionAI
    ai=SubscriptionAI();captured={}
    monkeypatch.setattr(ai,'_guard',lambda:None)
    def run(contract,prompt,**kwargs):captured.update(contract=contract,prompt=prompt);return {}
    monkeypatch.setattr(ai,'_run',run)
    ai.plan_day({'portfolio_policy':{'style':'balanced'}})
    assert captured['contract']==PortfolioPlan and '6개월 이상' in captured['prompt']
    assert '시초 범위 돌파, 돌파 후 눌림' not in captured['prompt']


def test_scan_unknown_warnings_excluded_but_missing_news_not_blanket_veto(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);s['config']['portfolio_core']=['005930','000660']
    monkeypatch.setattr(portfolio.scanner,'_pool',lambda *a:{})
    monkeypatch.setattr(portfolio.scanner.krnews,'stock_news',lambda *a:[])
    service.broker.history=lambda *a,**k:bars()
    def warnings(symbol):
        if symbol=='000660':raise OSError('offline')
        return []
    service.broker.warnings=warnings
    result=portfolio.scan(service,s,NOW)
    assert [c['symbol'] for c in result['candidates']]==['005930']
    assert result['candidates'][0]['eligible_horizons'] and not result['candidates'][0]['news']
    assert '조회 실패' in s['portfolio_checks'][1]['reason']


def test_portfolio_daily_plan_to_entry_to_next_day_end_to_end(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);s['config']['portfolio_core']=['005930']
    s['day']='';s['config']['daily_review']=False
    service.broker.history=lambda *a,**k:bars()
    service.broker.warnings=lambda *a:[]
    service.broker.quotes=lambda instruments:{i.symbol:quote(1299,dt.utcnow()) for i in instruments}
    monkeypatch.setattr(portfolio.scanner,'_pool',lambda *a:{})
    monkeypatch.setattr(portfolio.scanner.krnews,'stock_news',lambda *a:[])
    monkeypatch.setattr(dt.krnews,'market_news',lambda *a,**k:[])
    monkeypatch.setattr(dt.overnight,'brief',lambda *a:{})
    seen=[]
    service.ai.plan_day=lambda snapshot:seen.append(snapshot) or {'summary':'장기 추세 후보', 'picks':[
        {'symbol':'005930','horizon':'long','max_entry_price':1400,'weight_pct':10,
         'thesis':'긴 추세가 유지되고 있음','counter_case':'장기 하락 추세 전환 가능','invalidation':'200일 추세가 깨지는 경우'}]}
    def sync(state,kind,fn,snapshot,on_done,**kw):on_done(state,fn(snapshot),dt.utcnow())
    monkeypatch.setattr(service,'_ai_call',sync)
    service.store.put(service.account_kind,s['id'],s)
    monkeypatch.setattr(dt,'utcnow',lambda:NOW.replace(hour=22,minute=30)-timedelta(days=1)) # KST07:30
    service.tick(s['id'])
    assert seen[0]['portfolio_policy']['no_day_end_liquidation']
    monkeypatch.setattr(dt,'utcnow',lambda:NOW)
    service.tick(s['id']);s=service.store.get(service.account_kind,s['id'])
    assert s['positions']['005930']['horizon']=='long'
    monkeypatch.setattr(dt,'utcnow',lambda:NOW.replace(hour=6,minute=30))
    service.tick(s['id']);s=service.store.get(service.account_kind,s['id'])
    assert s['positions'] and s['phase']=='closed'
