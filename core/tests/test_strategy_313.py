import json
from datetime import timedelta
from types import SimpleNamespace
import pytest
from sqt import trade_setups, subscription_ai, notify
from sqt.paper_models import DayPlan,DayTradeConfig
from sqt.toss import Toss,TossError
from tests.test_daytrade import svc,make,clock,at


def chart(strategy):
    bars=[dict(timestamp=(at('09:00')+timedelta(minutes=5*i)).isoformat(),open=100,high=101,low=99,close=100,volume=100) for i in range(6)]
    bars[-1].update(open=100,high=103,close=102,volume=200)
    if strategy=='retest':
        bars[-3].update(close=102,high=103)
        bars[-2].update(close=101.1,low=100.9,high=101.8)
    if strategy=='bull_flag':
        bars[-4].update(open=100,close=104,high=105,volume=1000)
        bars[-3].update(open=104,close=103.5,high=104,low=102.5,volume=200)
        bars[-2].update(open=103.5,close=103,high=103.7,low=102,volume=100)
        bars[-1].update(open=103,close=105,high=105.1,volume=400)
    return {'intraday':{'vwap_scope':'session','vwap':95,'opening_range_high':101,'opening_range_low':99,
            'frames':{'5m':{'recent_bars':bars,'volume_ratio_prior_bars':2}}}}


@pytest.mark.parametrize('strategy',['orb','retest','bull_flag'])
def test_setups_require_structure_freshness_and_no_chase(strategy):
    data=chart(strategy); price=data['intraday']['frames']['5m']['recent_bars'][-1]['close']
    assert trade_setups.evaluate(strategy,data,{'ask':price},at('09:30'))['allowed']
    assert not trade_setups.evaluate(strategy,data,{'ask':price*1.03},at('09:30'))['allowed']
    assert not trade_setups.evaluate(strategy,data,{'ask':price},at('09:40'))['allowed']
    data['intraday']['vwap_scope']='available_window'
    assert not trade_setups.evaluate(strategy,data,{'ask':price},at('09:30'))['allowed']


def test_strategy_change_requires_stopped_and_discards_previous_plan(svc):
    service,store,broker,ai=svc;id=make(service)
    state=store.get(service.account_kind,id);state['status']='running';store.put(service.account_kind,id,state)
    with pytest.raises(ValueError):service.update_config(id,{'strategy':'orb'})
    state['status']='stopped';state['plan']={'picks':[]};store.put(service.account_kind,id,state)
    service.update_config(id,{'strategy':'retest'})
    state=store.get(service.account_kind,id)
    assert state['config']['strategy']=='retest' and state['plan'] is None
    with pytest.raises(ValueError):DayTradeConfig(strategy='invented')


def test_claude_large_snapshot_goes_over_stdin(monkeypatch):
    large='x'*200000
    monkeypatch.setattr(subscription_ai,'executable',lambda:'fake-claude')
    def run(args,**kwargs):
        assert large in kwargs['input']
        assert max(len(a.encode()) for a in args)<120000
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'structured_output':{'summary':'test','market_stance':'no_trade','picks':[]}}))
    monkeypatch.setattr(subscription_ai.subprocess,'run',run)
    assert subscription_ai.SubscriptionAI()._run(DayPlan,large)['picks']==[]


def test_index_fallback_preserves_source_and_time(monkeypatch):
    broker=Toss()
    def fail(*a,**k):raise TossError('HTTP 403')
    monkeypatch.setattr(broker,'get',fail)
    monkeypatch.setattr('sqt.toss.utcnow',lambda:at('09:30'))
    def get(url,**kwargs):
        assert url.startswith('https://m.stock.naver.com/api/index/')
        assert 'headers' not in kwargs
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'localTradedAt':at('09:29').isoformat(),
             'closePrice':'2,800','fluctuationsRatio':'1.2','marketStatus':'OPEN'})
    monkeypatch.setattr('sqt.toss.requests.get',get)
    result=broker.market_context()
    assert len(result['indices_and_rates'])==2 and not result['indices_and_rates'][0]['stale']
    assert 'indices_or_rates_unavailable:403' in result['errors']


def test_scheduled_notification_is_not_zero_price_surge():
    message=notify.event_message('midday','000500','test',{}, {'summary':'review','decisions':[]})
    assert '0 → 0' not in message and '정오' in message


def test_planned_stock_never_inherits_other_stocks_profit(svc):
    service,*_=svc
    state={'days':{'2026-09-11':{'picks':[{'symbol':'096770'}],'realized_pnl':123}},
           'trades':[{'at':at('12:00').isoformat(),'symbol':'000500','side':'sell','pnl':123}]}
    result=service._recent_picks(state)[0]
    assert result['realized_by_symbol']=={'000500':123}
    assert result['picks_are_plans_not_fills'] and result['account_realized_pnl']==123


def test_stale_surge_and_previous_day_reference_do_not_trigger(svc,monkeypatch):
    service,store,broker,ai=svc; id=make(service)
    clock(monkeypatch,at('09:30'))
    state=store.get(service.account_kind,id)
    state['events']['surge_ref']={'000500':1}
    state['quotes']={'000500':{'price':10000,'timestamp':at('09:00').isoformat()}}
    monkeypatch.setattr(service,'_fire_event',lambda *a:pytest.fail('false surge'))
    service._check_surge(state,at('09:30'),['000500'])
    assert not state['events']['surged']
    service._refresh_quotes(state,['000500'])
    service._check_surge(state,at('09:30'),['000500'])
    assert state['events']['surge_ref']['000500']==10000


def test_profile_buy_waits_for_pattern_and_retains_stop(svc,monkeypatch):
    from sqt import daytrade as dt
    service,store,broker,ai=svc; id=make(service,strategy='orb')
    clock(monkeypatch,at('09:30')); state=store.get(service.account_kind,id)
    broker.prices['000500']=102
    service._refresh_quotes(state,['000500']); quote=state['quotes']['000500']
    monkeypatch.setattr(dt.scanner,'chart_context',lambda *a:{})
    assert not service._fill_buy(state,'000500','test',10000,quote,10,3,{},at('09:30'))
    monkeypatch.setattr(dt.scanner,'chart_context',lambda *a:chart('orb'))
    assert service._fill_buy(state,'000500','test',10000,quote,10,5,{},at('09:30'))
    assert state['positions']['000500']['strategy_stop']==99
    state['config']['strategy']='ai'
    broker.prices['000500']=98.99
    service._do_monitor(state,at('09:30'))
    assert not state['positions']
    assert state['trades'][-1]['strategy']=='orb'
    assert state['trades'][-1]['reason']=='선택 전략의 가격 구조 무효화'
