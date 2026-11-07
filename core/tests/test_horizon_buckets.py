import copy
import pytest
from sqt import portfolio
from sqt.paper_models import DayTradeConfig, PortfolioPlan
from sqt.ai_router import strict_schema
from tests.test_portfolio_317 import setup, bars, NOW, quote


def test_universes_reserve_each_period_and_focus():
    cfg=DayTradeConfig(strategy='portfolio',portfolio_watchlists={
        'short':['000001'],'medium':['000002'],'long':['000003']}).model_dump()
    pool={f'{n:06}':{'symbol':f'{n:06}','trading_amount':n*1e8,'origins':[],'themes':[]} for n in range(10,90)}
    state={'config':cfg,'positions':{}}
    symbols,groups=portfolio.universes(state,pool)
    assert len(symbols)<=48 and {'000001','000002','000003','069500','229200'}<=set(symbols)
    assert all(len(set(v)&set(symbols))>=10 for v in groups.values())
    cfg['portfolio_style']='short'
    _,groups=portfolio.universes(state,pool)
    assert not groups['long'] and not groups['medium']


def test_ranked_candidates_separate_and_preserve_held_horizon():
    f=portfolio.features(bars(),NOW)
    rows=[{'symbol':f'{i:06}','eligible_horizons':list(portfolio.LABELS),'portfolio':copy.deepcopy(f)} for i in range(20)]
    portfolio.partition(rows,{'positions':{}})
    assert all(len(r['eligible_horizons'])<=1 for r in rows)
    assert {h:sum(h in r['eligible_horizons'] for r in rows) for h in portfolio.LABELS}==dict(short=5,medium=5,long=5)
    rows[0]['eligible_horizons']=['long']
    portfolio.partition(rows,{'positions':{'000000':{'horizon':'short'}}})
    assert rows[0]['eligible_horizons']==[] # no relabeling an existing position


def test_medium_uses_3_month_history_and_short_breakout():
    rows=bars()
    f=portfolio.features(rows[-80:],NOW)
    assert f['signals']['medium']['eligible'] and not f['signals']['long']['eligible']
    rows[-1].update(open=1360,close=1360,high=1361,low=1359)
    f=portfolio.features(rows,NOW)
    assert f['price']/f['sma20']>1.04 and f['signals']['short']['eligible']
    assert '돌파' in f['signals']['short']['reason']
    rows[-1].update(open=1700,close=1700,high=1701,low=1699)
    assert not portfolio.features(rows,NOW)['signals']['short']['eligible']


def test_slots_count_pending_and_partial_fill_once(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    s['positions']={'000001':{'symbol':'000001','horizon':'short'},'000002':{'symbol':'000002','horizon':'short'}}
    monkeypatch.setattr(service,'is_live',lambda *a:True)
    monkeypatch.setattr(service.live,'pending',lambda *a:[{'symbol':'000001','meta':{'horizon':'short'}}])
    buckets=portfolio.bucket_status(service,s)
    assert buckets['short']['slot_limit']==2 and buckets['short']['open_slots']==0
    assert buckets['medium']['open_slots']==3 and buckets['long']['open_slots']==3


def test_plan_cannot_consume_another_period_slots(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    f=portfolio.features(bars(),NOW)
    s['scan']={'candidates':[{'symbol':f'{i:06}','name':'Test','eligible_horizons':['short'],'portfolio':f} for i in range(4)]}
    plan={'summary':'test','picks':[{'symbol':f'{i:06}','horizon':'short','max_entry_price':1300,'weight_pct':10} for i in range(4)]}
    portfolio.apply_plan(service,s,plan,NOW)
    assert len(s['plan']['picks'])==2
    assert set(s['plan']['horizon_reviews'])==set(portfolio.LABELS)


def test_watchlist_validation_and_fixed_ai_contract():
    with pytest.raises(ValueError):DayTradeConfig(portfolio_watchlists={'bad':[]})
    with pytest.raises(ValueError):DayTradeConfig(portfolio_watchlists={'short':['005930','005930']})
    with pytest.raises(ValueError):DayTradeConfig(portfolio_watchlists={'medium':['bad']})
    schema=strict_schema(PortfolioPlan.model_json_schema())
    assert set(schema['$defs']['HorizonReviews']['properties'])==set(portfolio.LABELS)
    assert set(schema['$defs']['HorizonReviews']['required'])==set(portfolio.LABELS)


def test_theme_failure_keeps_ranking_candidates(monkeypatch):
    from types import SimpleNamespace
    def fail(**kw):raise OSError('offline')
    monkeypatch.setattr(portfolio.scanner.theme_source,'overview',fail)
    broker=SimpleNamespace(rankings=lambda *a,**kw:[{
        'symbol':'005930','last_price':10000,'change_pct':1,'trading_amount':1e10,'rank':1}])
    pool=portfolio.scanner._pool(broker,portfolio.scanner.DEFAULTS)
    assert '005930' in pool and 'trading_amount' in pool['005930']['origins']


def test_news_failure_does_not_abort_all_periods(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(portfolio.scanner,'_pool',lambda *a:{})
    def fail(*a):raise OSError('offline')
    monkeypatch.setattr(portfolio.scanner.krnews,'stock_news',fail)
    service.broker.history=lambda *a,**kw:bars()
    service.broker.warnings=lambda *a:[]
    result=portfolio.scan(service,s,NOW)
    assert len(result['candidates'])==2
    assert all(r['data_notes'] and r['news']==[] for r in result['candidates'])


def test_three_periods_execute_and_record_separately(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch)
    f=portfolio.features(bars(),NOW);f['price']=10000
    candidates=[];picks=[]
    for i,h in enumerate(portfolio.LABELS,1):
        symbol=f'{i:06}'
        candidates.append({'symbol':symbol,'name':h,'eligible_horizons':[h],'portfolio':f})
        picks.append({'symbol':symbol,'horizon':h,'weight_pct':10,'max_entry_price':10500,
                      'thesis':'trend','counter_case':'reversal','invalidation':'stop'})
    s['scan']={'candidates':candidates}
    portfolio.apply_plan(service,s,{'summary':'three independent periods','picks':picks},NOW)
    service._do_entry(s,NOW)
    assert {p['horizon'] for p in s['positions'].values()}==set(portfolio.LABELS)
    assert {t['horizon'] for t in s['trades']}==set(portfolio.LABELS)
    s['quotes']['000002']=quote(9000)
    service._fill_sell(s,'000002',9000,'test stop',NOW)
    assert s['trades'][-1]['horizon']=='medium'
    assert {p['horizon'] for p in s['positions'].values()}=={'short','long'}
