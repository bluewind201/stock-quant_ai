from datetime import timedelta
from types import SimpleNamespace

import pytest

from sqt import technical, news_quality
from sqt.ai_router import AIRouter
from sqt.store import Store
from sqt.models import Instrument
from sqt.toss import Toss
from sqt.daytrade_live import LiveExecution
from tests.test_daytrade import svc, make, clock, at


def test_contiguous_frames_supply_exit_evidence():
    rows = [dict(timestamp=(at('09:00')+timedelta(minutes=i)).isoformat(),
                 open=100, high=103, low=98, close=100+i, volume=10 if i<25 else 30)
            for i in range(31)]
    chart=technical.intraday_features(rows, 'KRX', at('09:30'))
    assert chart['bars']==30  # forming bar excluded
    assert chart['session_open']==100
    assert chart['frames']['5m']['previous_close']==124
    assert chart['frames']['5m']['last_close']==129
    assert chart['frames']['5m']['volume_ratio_prior_bars']==3
    assert len(chart['frames']['5m']['recent_bars'])==6
    bars=chart['frames']['5m']['recent_bars']
    assert bars[0]['vwap_at_close'] < bars[-1]['vwap_at_close']
    assert bars[-1]['vwap_at_close'] == pytest.approx(chart['vwap'])
    partial=technical.intraday_features(rows[5:], 'KRX', at('09:30'))
    assert partial['session_open'] is None and partial['coverage']=='partial'


def test_pagination_keeps_opening_after_hours(monkeypatch):
    broker=Toss(); calls=[]
    def get(path, params):
        calls.append(params)
        start=at('09:00') if params.get('before') else at('15:00')
        return {'candles':[{'timestamp':start.isoformat(),'currency':'KRW',
             'openPrice':100,'highPrice':101,'lowPrice':99,'closePrice':100,'volume':10}],
             'nextBefore':None if params.get('before') else 'cursor'}
    monkeypatch.setattr(broker, 'get', get)
    monkeypatch.setattr('sqt.toss.utcnow', lambda: at('19:30'))
    rows=broker.session_minutes(Instrument(symbol='000500',exchange='KRX'))
    assert len(rows)==2 and rows[0]['timestamp']==at('09:00').isoformat()
    assert calls[1]['before']=='cursor'


def test_lg_entry_above_plan_ceiling_blocked(svc, monkeypatch):
    service,store,broker,ai=svc
    ai.plan['picks'][0]['max_entry_price']=9999
    id=make(service)
    clock(monkeypatch,at('07:31')); service.tick(id)
    clock(monkeypatch,at('09:01')); service.tick(id)
    assert not store.get(service.account_kind,id)['positions']


def test_live_limit_never_exceeds_ceiling(tmp_path):
    live=LiveExecution(Store(tmp_path/'live.sqlite3'), SimpleNamespace())
    state={'id':'x','cash':1000000,'config':{'live_limit_buffer_bps':30,'fee_bps':10}}
    assert live.queue_buy(state,'066570','LG',700000,{'ask':215000},5,2,
                          {'max_entry_price':215238},at('09:00'),lambda *a:None)
    assert live.pending('x')[0]['limit_price']==215000


def test_news_requires_direct_relevance_and_suppresses_rehash():
    assert not news_quality.relevant({'title':'SNS 공동구매 과세 논란'}, 'LG전자', '066570')
    article={'title':'LG전자 엔비디아 냉각 파트너 선정 급등'}
    assert news_quality.relevant(article,'LG전자','066570')
    assert news_quality.duplicate(article,['LG전자 엔비디아 냉각 파트너 선정 강세'])
    assert not news_quality.duplicate({'title':'LG전자 엔비디아 냉각 파트너 선정 취소'},[article['title']])
    assert not news_quality.duplicate({'title':'LG전자 대규모 유상증자 발표'},[article['title']])


def test_open_review_survives_news_budget(svc, monkeypatch):
    service,store,broker,ai=svc; id=make(service)
    clock(monkeypatch,at('07:31')); service.tick(id)
    clock(monkeypatch,at('09:01')); service.tick(id)
    state=store.get(service.account_kind,id)
    state['ai']['calls_today']=state['config']['max_ai_calls_per_day']
    store.put(service.account_kind,id,state)
    clock(monkeypatch,at('09:16')); service.tick(id)
    events=[s for k,s in ai.calls if k=='event']
    assert events[-1]['trigger']=='opening'
    clock(monkeypatch,at('09:17')); service.tick(id)
    assert len([1 for k,s in ai.calls if k=='event'])==len(events)


@pytest.mark.parametrize('provider,model',[('codex','gpt-6-sol'),('claude','claude-opus-5-5')])
def test_model_selection_persists(tmp_path,monkeypatch,provider,model):
    store=Store(tmp_path/'model.sqlite3'); router=AIRouter(store)
    monkeypatch.setattr(router.providers[provider],'status',lambda:{'authenticated':True})
    assert router.select(provider,model)['model']==model
    assert AIRouter(store).providers[provider].model==model
    with pytest.raises(ValueError):router.select(provider,'made-up-model')


def test_market_context_preserves_time_and_cache(monkeypatch):
    broker=Toss(); calls=[]
    def get(path, params):
        calls.append(path)
        if path.endswith('/prices'):
            return [{'symbol':s,'lastPrice':'2812.45','timestamp':at('09:00').isoformat()} for s in ('KOSPI','KOSDAQ')]
        return {'rate':'1380.5','validFrom':at('09:00').isoformat(),'validUntil':at('09:01').isoformat()}
    monkeypatch.setattr(broker,'get',get)
    monkeypatch.setattr('sqt.toss.utcnow',lambda:at('09:02'))
    data=broker.market_context()
    assert data['fx']['stale'] and data['indices_and_rates'][0]['value']==2812.45
    assert broker.market_context()==data and len(calls)==2


def test_cost_negative_reward_is_rejected(svc,monkeypatch):
    service,store,broker,ai=svc; id=make(service)
    clock(monkeypatch,at('09:00'))
    state=store.get(service.account_kind,id)
    quote=broker.quotes([Instrument(symbol='000500',exchange='KRX')])['000500']
    assert not service._fill_buy(state,'000500','test',100000,quote,1,2,{},at('09:00'))
    assert state['cash']==state['config']['initial_cash'] and not state['positions']


def test_fresh_intraday_plan_can_supersede_old_resistance(svc,monkeypatch):
    service,store,broker,ai=svc; id=make(service)
    clock(monkeypatch,at('09:30'))
    state=store.get(service.account_kind,id)
    state['scan']={'candidates':[{'symbol':'000500','technical':{'daily':{'price':9900,'resistance20':11000}}}]}
    quote=broker.quotes([Instrument(symbol='000500',exchange='KRX')])['000500']
    assert not service._fill_buy(state,'000500','test',100000,quote,10,3,
                                 {'source':'plan','max_entry_price':10500},at('09:30'))
    assert service._fill_buy(state,'000500','test',100000,quote,10,3,
                             {'source':'event','max_entry_price':10500},at('09:30'))
