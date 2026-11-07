import copy
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sqt.api import create_app
from sqt.calendar import session, status
from sqt.context import evaluate
from sqt.demo import build_demo
from sqt.engine import backtest, create_state, step
from sqt.kis import KIS
from sqt.models import Bar, Evidence, Instrument, Strategy
from sqt.news import fomc_events, ics_events, rss_evidence, source_matches, white_house_evidence
from sqt.technical import analyze


@pytest.fixture(scope='module')
def demo():
    return build_demo()


@pytest.fixture
def config():
    return Strategy(initial_cash=10000,news_required=False,threshold=0,min_technical=0).model_dump()


def test_us_holiday_dst_and_early_close():
    assert not status('NASDAQ',datetime(2026,9,7,15,tzinfo=timezone.utc))['open']
    assert session('NASDAQ','2026-03-06')[0].hour == 14
    assert session('NASDAQ','2026-03-09')[0].hour == 13
    assert session('NASDAQ','2026-11-27')[1].hour == 18


def test_krx_holiday_and_hours():
    with pytest.raises(ValueError):
        session('KRX','2026-03-02')
    op,cl = session('KRX','2026-03-03')
    assert (op.hour,cl.hour,cl.minute) == (0,6,30)


@pytest.mark.parametrize('field,value',[('open',float('nan')),('volume',-1),('high',0),('low',300)])
def test_reject_malformed_bars(field,value):
    data=dict(date='2026-01-05',open=100,high=110,low=90,close=105,volume=1000)
    data[field]=value
    with pytest.raises(ValidationError):
        Bar.model_validate(data)


def test_stock_symbols():
    assert Instrument(symbol='005930',exchange='KRX').currency == 'KRW'
    assert Instrument(symbol='AAPL',exchange='NASDAQ').currency == 'USD'
    with pytest.raises(ValidationError):
        Instrument(symbol='KRW-BTC',exchange='KRX')


def test_flat_price_is_neutral_rsi(demo):
    bars=[dict(b,open=100,high=100,low=100,close=100) for b in demo[0]['bars'][:80]]
    assert analyze(bars)['rsi14'] == 50


def test_future_price_does_not_change_decision(demo,config):
    dataset,news,events = demo
    altered = copy.deepcopy(dataset)
    altered['bars'][62]['close'] *= 8
    a=step(create_state(dataset,config),dataset,news,events)
    b=step(create_state(altered,config),altered,news,events)
    assert a['decisions'] == b['decisions']
    assert a['cash'] == b['cash']


def test_entry_uses_next_open_integer_shares_and_costs(demo,config):
    dataset = copy.deepcopy(demo[0])
    bar=dataset['bars'][61]
    bar.update(high=bar['open']*1.001,low=bar['open']*.999,close=bar['open'])
    s=step(create_state(dataset,config),dataset,[],[])
    assert s['position'] and isinstance(s['position']['qty'],int)
    expected=bar['open']*(1+config['slippage_bps']/10000)
    assert s['position']['entry_price'] == pytest.approx(expected)
    assert s['cash']+s['position']['cost'] == pytest.approx(config['initial_cash'])
    assert s['position']['cost'] <= config['initial_cash']*config['position_pct']/100


def test_gap_stop_not_filled_at_unreachable_price(demo,config):
    d=copy.deepcopy(demo[0]);s=create_state(d,config)
    s['position']=dict(qty=10,entry_date=d['bars'][60]['date'],entry_price=100,cost=1000,stop=95,target=120)
    s['cash']=9000;d['bars'][61].update(open=80,high=90,low=75,close=85)
    s=step(s,d,[],[])
    assert s['trades'][0]['exit_price'] == pytest.approx(80*.999)
    assert s['position'] is None
    assert not s['entries_enabled']


def test_both_stop_and_target_take_stop_first(demo,config):
    d=copy.deepcopy(demo[0]);s=create_state(d,config)
    s['position']=dict(qty=10,entry_date=d['bars'][60]['date'],entry_price=100,cost=1000,stop=90,target=110)
    s['cash']=9000;s['entries_enabled']=False
    d['bars'][61].update(open=100,high=115,low=85,close=103)
    s=step(s,d,[],[])
    assert s['trades'][0]['exit_price'] == pytest.approx(90*.999)


def test_zero_volume_never_fills(demo,config):
    d=copy.deepcopy(demo[0]);d['bars'][61]['volume']=0
    s=create_state(d,config)
    assert step(s,d,[],[])['position'] is None
    s=create_state(d,config);s['position']=dict(qty=1,entry_date='2025-01-02',entry_price=100,cost=100,stop=500,target=600)
    s=step(s,d,[],[])
    assert not s['trades']


def test_stale_future_unassessed_news_never_earn_points(demo,config):
    _,items,_=demo;n=copy.deepcopy(items[70]);now=datetime.fromisoformat(n['available_at'])
    config['news_required']=True
    assert evaluate([n],[],'DEMO',(now-timedelta(seconds=1)).isoformat(),config)['blocked']
    assert evaluate([n],[],'DEMO',(now+timedelta(days=8)).isoformat(),config)['blocked']
    ctx=evaluate([n],[],'DEMO',(now+timedelta(seconds=30)).isoformat(),config)
    assert ctx['news_score'] == 0 and not ctx['assessed_ids']
    n['symbols']=['AAPL']
    assert evaluate([n],[],'DEMO',(now+timedelta(hours=1)).isoformat(),config)['blocked']


def test_important_intraday_event_blocks_daily_entry(demo,config):
    d=demo[0];op,cl=session('NASDAQ',d['bars'][61]['date'])
    event={'id':'fomc-test','title':'금리 결정','source':'fed','url':'https://www.federalreserve.gov/',
           'scheduled_at':(op+timedelta(hours=4)).isoformat(),'known_at':(op-timedelta(days=1)).isoformat(),
           'impact':'high','symbols':[]}
    s=step(create_state(d,config),d,[],[event])
    assert s['position'] is None
    assert s['decisions'][0]['entry_context']['blocked']
    event['known_at']=(op+timedelta(hours=1)).isoformat()
    s=step(create_state(d,config),d,[],[event])
    assert not s['decisions'][0]['entry_context']['blocked']


def test_disabled_entries_still_exit(demo,config):
    d=copy.deepcopy(demo[0]);s=create_state(d,config);s['entries_enabled']=False
    s['position']=dict(qty=1,entry_date=d['bars'][60]['date'],entry_price=100,cost=100,stop=90,target=110)
    d['bars'][61].update(open=80,low=70,high=100,close=90)
    assert step(s,d,[],[])['trades']


def test_technical_gate_cannot_be_offset_by_news(demo,config):
    d,n,e=demo;config['min_technical']=70
    s=step(create_state(d,config),d,n,e)
    assert s['decisions'][0]['technical']['technical_score'] < 70
    assert s['position'] is None


def test_holdout_starts_at_last_thirty_percent(demo,config):
    d,n,e=demo;r=backtest(d,config,n,e,holdout=True)
    assert r['decisions'][0]['date'] == d['bars'][210]['date']
    assert len(r['decisions']) == 90
    assert math.isfinite(r['metrics']['max_drawdown_pct'])


def test_official_source_domain_cannot_be_forged():
    assert source_matches('fed','https://www.federalreserve.gov/newsevents/')
    assert not source_matches('fed','https://www.federalreserve.gov.evil.test/')


def test_syndicated_news_does_not_multiply_score(demo,config):
    n=copy.deepcopy(demo[1][70]);at=datetime.fromisoformat(n['assessed_at'])+timedelta(minutes=1)
    duplicate=copy.deepcopy(n);duplicate.update(id='another-id',url='https://example.com/syndicated-copy')
    one=evaluate([n],[],'DEMO',at.isoformat(),config)
    many=evaluate([n,duplicate],[],'DEMO',at.isoformat(),config)
    assert one['news_score'] == many['news_score']
    assert len(many['evidence_ids'])==1


def test_news_date_and_availability_validation(demo):
    item=copy.deepcopy(demo[1][0]);item['available_at']='2000-01-01T00:00:00Z'
    with pytest.raises(ValidationError):
        Evidence.model_validate(item)


def test_rss_and_calendar_parsers():
    now=datetime(2026,9,7,tzinfo=timezone.utc)
    body=b'<rss><channel><item><title>Policy statement</title><link>https://www.federalreserve.gov/test</link><pubDate>Fri, 04 Sep 2026 14:00:00 GMT</pubDate></item></channel></rss>'
    items=rss_evidence(body,'fed',now)
    assert items[0]['sentiment'] == 0 and items[0]['confidence'] == 0
    assert datetime.fromisoformat(items[0]['available_at']) == now
    ics=b'BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:cpi\r\nSUMMARY:Consumer Price Index\r\nDTSTART;TZID=America/New_York:20260911T083000\r\nEND:VEVENT\r\nEND:VCALENDAR'
    events=ics_events(ics,'bls','https://www.bls.gov/',now)
    assert events[0]['scheduled_at'] == '2026-09-11T12:30:00Z'
    html='<div class="panel"><div class="panel-heading">2026 FOMC Meetings</div><div class="fomc-meeting"><div class="fomc-meeting__month">September</div><div class="fomc-meeting__date">15-16*</div></div></div>'
    assert fomc_events(html,now)[0]['scheduled_at'] == '2026-09-16T18:00:00Z'
    wh='<h2><a href="https://www.whitehouse.gov/remarks/2026/09/trade/">Trade policy</a></h2>'
    assert white_house_evidence(wh,now)[0]['impact']=='high'


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','test-stock-password')
    app=create_app(tmp_path/'test.sqlite3',background=False)
    with TestClient(app) as c:
        assert c.post('/api/login',json={'password':'test-stock-password'}).status_code == 200
        yield c


def test_api_auth_and_csrf(client):
    assert client.get('/api/health').json()['live_orders'] is False
    assert client.get('/').status_code == 200
    assert client.post('/api/sessions',json={},headers={'Origin':'https://evil.test'}).status_code == 403
    client.post('/api/logout',json={})
    assert client.get('/api/overview').status_code == 401


def test_session_roundtrip_concurrent_idempotency_and_snapshot(client,config):
    id=client.post('/api/sessions',json={'dataset_id':'demo','config':config}).json()['id']
    before=client.get('/api/sessions/'+id).json()
    def advance(_):
        return client.post('/api/sessions/'+id+'/step',json={'request_id':'same-request','steps':2})
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert all(r.status_code==200 for r in pool.map(advance,range(3)))
    after=client.get('/api/sessions/'+id).json()
    assert after['cursor'] == before['cursor']+2
    assert after['dataset_id'] == 'demo'
    assert len(after['chart']) == after['cursor']
    assert client.patch('/api/sessions/'+id,json={'entries_enabled':False}).json()['entries_enabled'] is False


def test_kis_missing_keys_and_bad_strategy(client):
    assert client.post('/api/kis/import',json={'instrument':{'symbol':'AAPL','exchange':'NASDAQ'}}).status_code == 400
    assert client.post('/api/sessions',json={'dataset_id':'demo','config':{'initial_cash':-1}}).status_code == 422


def test_backtest_uses_immutable_session_snapshot(client,config):
    id=client.post('/api/sessions',json={'dataset_id':'demo','config':config}).json()['id']
    result=client.post('/api/sessions/'+id+'/backtest',json={'dataset_id':'demo','config':config,'holdout':True})
    assert result.status_code == 200
    assert result.json()['snapshot']['dataset']['id']=='demo'
    assert result.json()['snapshot']['evidence']
    wrong=dict(config,threshold=20)
    assert client.post('/api/sessions/'+id+'/backtest',json={'dataset_id':'demo','config':wrong}).status_code == 400


def test_reject_non_session_duplicate_and_missing_daily_bars(client,demo):
    payload={'instrument':{'symbol':'TEST','exchange':'NASDAQ'},'bars':copy.deepcopy(demo[0]['bars'][:80])}
    valid=client.post('/api/datasets',json=payload)
    assert valid.status_code==200
    payload['bars'].pop(40)
    assert client.post('/api/datasets',json=payload).status_code==400
    payload['bars'].insert(40,payload['bars'][39])
    assert client.post('/api/datasets',json=payload).status_code==422


def test_no_live_order_endpoint(client):
    assert client.post('/api/orders',json={'symbol':'AAPL','qty':1}).status_code==404


def test_kis_mapping_pagination_and_adjusted_flags(monkeypatch,demo):
    broker=KIS();seen=[];d=demo[0]
    rows=d['bars'][:150]
    def get(path,tr,params):
        seen.append(params)
        end=params['BYMD']
        batch=[r for r in rows if r['date'].replace('-','')<=end][-100:]
        return {'output2':[{'xymd':r['date'].replace('-',''),'open':str(r['open']),'high':str(r['high']),
                           'low':str(r['low']),'clos':str(r['close']),'tvol':str(r['volume'])} for r in reversed(batch)]}
    monkeypatch.setattr(broker,'get',get)
    result=broker.history(Instrument(symbol='AAPL',exchange='NASDAQ'),150)
    assert len(result) == 150 and len(seen) == 2
    assert seen[0]['MODP'] == '1' and seen[0]['EXCD'] == 'NAS'
    assert result[0]['date'] < result[-1]['date']
