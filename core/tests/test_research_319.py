import copy
from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqt import portfolio,notify,reporting
from sqt.ai_router import AIRouter,CodexAI,strict_schema
from sqt.paper_models import PortfolioPlan,DayPlan
from sqt.store import Store
from tests.test_portfolio_317 import setup,buy,quote,NOW,bars


def test_entry_day_previous_candle_never_reverses_pullback_buy(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);assert buy(service,s,h='short',sl=3)
    s['portfolio_held_data']={'005930':{'last_date':'2026-10-20','price':9900,'sma20':10000}}
    portfolio.monitor(service,s,NOW)
    assert s['positions'] and not any(t['side']=='sell' for t in s['trades'])
    # A fresh completed candle from the entry session may invalidate on the next day.
    next_day=NOW+timedelta(days=1)
    monkeypatch.setattr('sqt.daytrade.utcnow',lambda:next_day)
    service.broker.quotes=lambda instruments:{i.symbol:quote(10000,next_day) for i in instruments}
    s['portfolio_held_data']['005930']['last_date']='2026-10-21'
    portfolio.monitor(service,s,next_day)
    assert not s['positions'] and '진입 이후' in s['trades'][-1]['reason']


def test_same_day_stop_still_executes(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);assert buy(service,s,h='long')
    service.broker.quotes=lambda instruments:{i.symbol:quote(8000) for i in instruments}
    portfolio.monitor(service,s,NOW)
    assert not s['positions'] and '보호 가격' in s['trades'][-1]['reason']


def test_same_day_ai_exit_requires_new_cited_evidence(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);assert buy(service,s)
    plan={'summary':'risk','picks':[],'exits':[{'symbol':'005930','reason':'새로운 위험을 다시 검토합니다','evidence_ids':[]}]}
    later=NOW+timedelta(minutes=2)
    portfolio.apply_plan(service,s,copy.deepcopy(plan),later)
    assert not s['portfolio_exits'] and s['portfolio_exit_deferred']
    s['scan']={'candidates':[{'symbol':'005930','news':[{'title':'공시 위험','source':'test','published_at':(NOW+timedelta(minutes=1)).isoformat()}]}]}
    evidence=portfolio.exit_evidence(s)
    plan['exits'][0]['evidence_ids']=['invented']
    portfolio.apply_plan(service,s,copy.deepcopy(plan),later)
    assert not s['portfolio_exits']
    plan['exits'][0]['evidence_ids']=[evidence[0]['id']]
    portfolio.apply_plan(service,s,plan,later)
    assert s['portfolio_exits']['005930']['evidence_ids']==[evidence[0]['id']]
    monkeypatch.setattr('sqt.daytrade.utcnow',lambda:later)
    service.broker.quotes=lambda instruments:{i.symbol:quote(10000,later) for i in instruments}
    portfolio.monitor(service,s,later)
    assert not s['positions'] and 'AI 투자 근거' in s['trades'][-1]['reason']


def test_reporting_partial_sales_and_unclassified_history():
    at=NOW.isoformat()
    s={'day':'2026-10-21','positions':{'A':{'symbol':'A','strategy':'portfolio','horizon':'medium','entry_at':at,'qty':2}},
       'trades':[{'symbol':'A','side':'sell','at':at,'entry_at':at,'pnl':100,'strategy':'portfolio','horizon':'medium'},
                 {'symbol':'B','side':'sell','at':at,'entry_at':at,'pnl':-50}],
       'equity_curve':[{'at':at,'equity':100},{'at':at,'equity':80},{'at':at,'equity':0}]}
    r=reporting.summary(s)
    assert r['closed_count']==1 and r['win_rate']==0
    assert r['buckets']['medium']['today_realized']==100 and r['buckets']['legacy']['realized']==-50
    assert r['max_drawdown_pct']==-100 and r['exits'][0]['same_day']


def test_telegram_all_periods_and_realized_pnl(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);assert buy(service,s,h='long')
    p=s['positions']['005930'];p['name']='<sample>'
    message=notify.buy_message(p,state=s)
    assert '장기' in message and '&lt;sample&gt;' in message and len(message)<400
    assert all(h+':' in notify.summary_message(s) for h in ('단기','중기','장기'))
    trade={'symbol':'005930','side':'sell','qty':1,'price':9000,'at':NOW.isoformat(),'pnl':-1000,'pnl_pct':-10,
           'strategy':'portfolio','horizon':'long','reason':'보호 가격 이탈'}
    text=notify.sell_message(trade,'test',state=s)
    assert '장기' in text and '실현손익' in text and '누적' in text and '보호 가격' in text
    s['trades'].append(trade)
    assert '매수 체결 1건 / 매도 체결 1건' in notify.summary_message(s)


def test_model_catalog_preserves_exact_ids_and_credit_optin(tmp_path,monkeypatch):
    store=Store(tmp_path/'models.db');ai=AIRouter(store)
    for child in ai.providers.values():monkeypatch.setattr(child,'status',lambda:{'authenticated':True})
    for model in ('gpt-6.1-sol','gpt-6.1-terra','gpt-6-astra','gpt-6-luna'):
        ai.select('codex',model)
        assert AIRouter(store).providers['codex'].model==model
    for model in ('claude-opus-5-5','claude-sonnet-5-5','claude-haiku-5-5'):
        ai.select('claude',model)
    with pytest.raises(ValueError,match='크레딧'):ai.select('claude','claude-fable-5-1')
    ai.select('claude','claude-fable-5-1',True)
    assert store.get('settings','ai_usage_credits')['fable'] is True
    assert 'evidence_ids' in strict_schema(PortfolioPlan.model_json_schema())['$defs']['PortfolioExit']['required']


def test_charts_not_duplicated_in_ai_snapshot(tmp_path,monkeypatch):
    service,s=setup(tmp_path,monkeypatch);f=portfolio.features(bars(),NOW)
    assert len(f['chart'])==60 and f['chart'][-1]['sma200']
    view=service._candidate_view({'symbol':'A','portfolio':f,'technical':{'daily':f}})
    assert 'chart' not in view['portfolio'] and 'chart' not in view['technical']['daily']
    assert len(f['chart'])==60


def test_catalog_api_is_authenticated_and_contains_current_models(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from sqt.api import create_app
    monkeypatch.setenv('ADMIN_PASSWORD','test-model-catalog-only')
    with TestClient(create_app(tmp_path/'api.db',background=False)) as client:
        assert client.get('/api/ai/models').status_code==401
        client.post('/api/login',json={'password':'test-model-catalog-only'})
        catalog=client.get('/api/ai/models').json()
        assert {'gpt-6.1-sol','gpt-6.1-terra','gpt-6-astra','gpt-6-luna'}<={r['id'] for r in catalog['providers']['codex']}
        assert {'claude-fable-5-1','claude-opus-5-5','claude-sonnet-5-5','claude-haiku-5-5'}<={r['id'] for r in catalog['providers']['claude']}
