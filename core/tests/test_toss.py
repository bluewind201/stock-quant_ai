import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import sqt.toss as module
from sqt.api import create_app
from sqt.calendar import session
from sqt.demo import build_demo
from sqt.models import Instrument
from sqt.toss import Toss, TossError


class Reply:
    def __init__(self,body=None,status=200,headers=None):
        self.body=body
        self.status_code=status
        self.headers=headers or {}

    def json(self):
        return self.body


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv('TOSS_CLIENT_ID','test-client')
    monkeypatch.setenv('TOSS_CLIENT_SECRET','test-secret-do-not-log')
    clock={'at':100.0,'sleeps':[]}
    def sleep(seconds):
        clock['at']+=seconds
        clock['sleeps'].append(seconds)
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:clock['at'],sleep=sleep))
    broker=Toss()
    replies,calls=[],[]
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs))
        assert replies,'Unexpected additional HTTP request'
        return replies.pop(0)
    monkeypatch.setattr(broker.http,'request',request)
    return broker,replies,calls,clock


def token(value='fake-token'):
    return Reply({'access_token':value,'token_type':'Bearer','expires_in':3600})


def stock():
    return {'symbol':'AAPL','name':'애플','market':'NASDAQ','currency':'USD','status':'ACTIVE'}


def test_token_form_and_reuse(adapter):
    b,q,c,_=adapter
    q.extend([token(),Reply({'result':[stock()]}),Reply({'result':[stock()]})])
    instrument=Instrument(symbol='AAPL',exchange='NASDAQ')
    assert b.stock(instrument)['currency']=='USD'
    b.stock(instrument)
    assert [x[0] for x in c]==['POST','GET','GET']
    assert c[0][2]['data']=={'grant_type':'client_credentials','client_id':'test-client','client_secret':'test-secret-do-not-log'}
    assert 'json' not in c[0][2]
    assert all(not item[2]['allow_redirects'] for item in c)
    assert c[1][2]['headers']=={'Authorization':'Bearer fake-token'}


def test_expiry_renews_token(adapter):
    b,q,c,clock=adapter
    q.extend([token(),Reply({'result':[]}),token('new-token'),Reply({'result':[]})])
    b.get('/api/v1/stocks',{'symbols':'AAPL'})
    clock['at']+=4000
    b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert c[-1][2]['headers']['Authorization']=='Bearer new-token'


def test_401_renews_once(adapter):
    b,q,c,_=adapter
    q.extend([token(),Reply(status=401),token('renewed'),Reply({'result':[]})])
    b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert len(c)==4 and c[-1][2]['headers']['Authorization']=='Bearer renewed'


def test_repeated_401_is_bounded(adapter):
    b,q,c,_=adapter
    q.extend([token(),Reply(status=401),token(),Reply(status=401)])
    with pytest.raises(TossError,match='401'):
        b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert len(c)==4


def test_403_no_retry_or_secret_leak(adapter):
    b,q,c,_=adapter
    q.append(Reply({'error_description':'test-secret-do-not-log'},status=403))
    with pytest.raises(TossError,match='공인 IP') as exc:
        b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert 'test-secret' not in str(exc.value)
    assert len(c)==1


def test_retry_after_honored(adapter):
    b,q,c,clock=adapter
    q.extend([token(),Reply(status=429,headers={'Retry-After':'3'}),Reply({'result':[]})])
    b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert 3 in clock['sleeps'] and len(c)==3


def test_long_retry_after_not_retried_early(adapter):
    b,q,c,_=adapter
    q.extend([token(),Reply(status=429,headers={'Retry-After':'120'})])
    with pytest.raises(TossError,match='30초'):
        b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert len(c)==2


def test_429_retry_limit(adapter):
    b,q,c,_=adapter
    q.extend([token(),*[Reply(status=429,headers={'Retry-After':'0'}) for _ in range(3)]])
    with pytest.raises(TossError,match='429'):
        b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert len(c)==4


@pytest.mark.parametrize('expiry',[0,-1,'NaN','Infinity',None])
def test_bad_token_expiry_rejected(adapter,expiry):
    b,q,_,_=adapter
    q.append(Reply({'access_token':'secret','token_type':'Bearer','expires_in':expiry}))
    with pytest.raises(TossError):
        b.get('/api/v1/stocks',{'symbols':'AAPL'})


def test_redirect_rejected(adapter):
    b,q,c,_=adapter;q.append(Reply(status=302,headers={'Location':'https://example.com'}))
    with pytest.raises(TossError,match='302'):
        b.get('/api/v1/stocks',{'symbols':'AAPL'})
    assert len(c)==1


def test_order_path_cannot_be_used(adapter):
    b,_,calls,_=adapter
    with pytest.raises(TossError):
        b.get('/api/v1/orders',{})
    with pytest.raises(TossError):
        b._request('POST','/api/v1/orders',json={})
    assert not calls


@pytest.fixture(scope='module')
def candles():
    d,_,_=build_demo()
    result=[]
    for b in d['bars'][:150]:
        opened,_=session('NASDAQ',b['date'])
        result.append({'timestamp':opened.astimezone(ZoneInfo('Asia/Seoul')).isoformat(),
            'openPrice':str(b['open']),'highPrice':str(b['high']),'lowPrice':str(b['low']),
            'closePrice':str(b['close']),'volume':str(b['volume']),'currency':'USD'})
    return result


def test_history_inclusive_cursor_dedup_and_request_contract(adapter,candles):
    b,q,c,_=adapter
    cursor=candles[50]['timestamp']
    q.extend([token(),Reply({'result':[stock()]}),
              Reply({'result':{'candles':list(reversed(candles[50:])),'nextBefore':cursor}}),
              Reply({'result':{'candles':list(reversed(candles[:51])),'nextBefore':None}})])
    rows=b.history(Instrument(symbol='AAPL',exchange='NASDAQ'),150)
    assert len(rows)==150 and rows[0]['date']<rows[-1]['date']
    assert c[-1][2]['params']['before']==cursor
    assert c[-1][2]['params']['adjusted']=='true'
    assert c[-1][2]['params']['interval']=='1d'
    assert c[-1][2]['params']['count']==200
    assert rows[-1]['close']==float(candles[-1]['closePrice'])


def test_market_mismatch_rejected(adapter):
    b,q,_,_=adapter
    q.extend([token(),Reply({'result':[stock()]})])
    with pytest.raises(TossError,match='거래소'):
        b.history(Instrument(symbol='AAPL',exchange='NYSE'),100)


def test_korean_alphanumeric_symbol_supported(adapter):
    b,q,c,_=adapter
    q.extend([token(),Reply({'result':[dict(stock(),symbol='0101N0',market='KOSPI',currency='KRW')]} )])
    result=b.stock(Instrument(symbol='0101N0',exchange='KRX'))
    assert result['symbol']=='0101N0' and result['currency']=='KRW'
    assert c[-1][2]['params']=={'symbols':'0101N0'}


def test_currency_mismatch_rejected(adapter,candles):
    b,q,_,_=adapter
    raw=dict(candles[0],currency='KRW')
    q.extend([token(),Reply({'result':[stock()]}),Reply({'result':{'candles':[raw]}})])
    with pytest.raises(TossError,match='통화'):
        b.history(Instrument(symbol='AAPL',exchange='NASDAQ'),100)


def test_current_local_date_excluded_and_short_history_returned(adapter,candles,monkeypatch):
    b,q,_,_=adapter
    now=datetime(2026,9,8,22,tzinfo=timezone.utc)
    monkeypatch.setattr(module,'utcnow',lambda:now)
    today=dict(candles[-1],timestamp='2026-09-08T09:30:00-04:00')
    q.extend([token(),Reply({'result':[stock()]}),
              Reply({'result':{'candles':[today]+list(reversed(candles[:80])),'nextBefore':None}})])
    rows=b.history(Instrument(symbol='AAPL',exchange='NASDAQ'),100)
    assert len(rows)==80 and all(r['date']<'2026-09-08' for r in rows)


def test_overnight_can_read_just_closed_us_date(adapter,candles,monkeypatch):
    b,q,_,_=adapter
    monkeypatch.setattr(module,'utcnow',lambda:datetime(2026,9,8,22,30,tzinfo=timezone.utc))
    today=dict(candles[-1],timestamp='2026-09-08T09:30:00-04:00')
    q.extend([token(),Reply({'result':[stock()]}),
              Reply({'result':{'candles':[today]+list(reversed(candles[:80])),'nextBefore':None}})])
    rows=b.history(Instrument(symbol='AAPL',exchange='NASDAQ'),100,include_closed_session=True)
    assert rows[-1]['date']=='2026-09-08'


def test_conflicting_boundary_candle_rejected(adapter,candles):
    b,q,_,_=adapter
    cursor=candles[50]['timestamp'];changed=dict(candles[50],volume='987')
    q.extend([token(),Reply({'result':[stock()]}),
              Reply({'result':{'candles':candles[50:],'nextBefore':cursor}}),
              Reply({'result':{'candles':[changed],'nextBefore':None}})])
    with pytest.raises(TossError,match='동일 거래일'):
        b.history(Instrument(symbol='AAPL',exchange='NASDAQ'),150)


def test_repeating_cursor_rejected(adapter,candles):
    b,q,_,_=adapter
    page=Reply({'result':{'candles':candles[:80],'nextBefore':candles[0]['timestamp']}})
    q.extend([token(),Reply({'result':[stock()]}),page,page])
    with pytest.raises(TossError,match='반복'):
        b.history(Instrument(symbol='AAPL',exchange='NASDAQ'),150)


def test_korean_market_timezone(adapter,monkeypatch):
    b,q,_,_=adapter
    from sqt.calendar import calendar
    days=calendar('KRX').sessions_in_range('2025-01-02','2025-06-30')[:80]
    raw=[{'timestamp':session('KRX',day.strftime('%Y-%m-%d'))[0].isoformat(),
          'openPrice':'100','highPrice':'102','lowPrice':'99','closePrice':'101','volume':'1000','currency':'KRW'} for day in days]
    q.extend([token(),Reply({'result':[dict(stock(),symbol='005930',market='KOSPI',currency='KRW')]}),
              Reply({'result':{'candles':raw,'nextBefore':None}})])
    rows=b.history(Instrument(symbol='005930',exchange='KRX'),80)
    assert rows[0]['date']==days[0].strftime('%Y-%m-%d') and len(rows)==80


def test_api_default_check_import_and_no_secrets(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','test-password')
    monkeypatch.setenv('TOSS_CLIENT_ID','test-client')
    monkeypatch.setenv('TOSS_CLIENT_SECRET','secret-not-returned')
    monkeypatch.setattr(Toss,'stock',lambda self,instrument:stock())
    dataset,_,_=build_demo()
    monkeypatch.setattr(Toss,'history',lambda self,instrument,count:dataset['bars'][:80])
    app=create_app(tmp_path/'toss.sqlite3',background=False)
    with TestClient(app) as client:
        assert client.post('/api/toss/check',json={'symbol':'AAPL','exchange':'NASDAQ'}).status_code==401
        client.post('/api/login',json={'password':'test-password'})
        overview=client.get('/api/overview').json()
        assert overview['default_broker']=='toss' and overview['toss_configured']
        assert 'secret-not-returned' not in json.dumps(overview)
        assert client.post('/api/toss/check',json={'symbol':'AAPL','exchange':'NASDAQ'}).json()['stock']['name']=='애플'
        job=client.post('/api/toss/import',json={'instrument':{'symbol':'AAPL','exchange':'NASDAQ'},'count':80}).json()
        import time
        for _ in range(100):
            result=client.get('/api/jobs/'+job['id']).json()
            if result['status']!='running': break
            time.sleep(.01)
        assert result['status']=='done'
        data=app.state.store.get('datasets',result['dataset_id'])
        assert '토스증권' in data['source'] and data['instrument']['currency']=='USD'
        assert client.post('/api/orders',json={}).status_code==404


def test_missing_credentials_actionable(monkeypatch):
    monkeypatch.delenv('TOSS_CLIENT_ID',raising=False)
    monkeypatch.delenv('TOSS_CLIENT_SECRET',raising=False)
    with pytest.raises(TossError,match='TOSS_CLIENT_ID'):
        Toss().get('/api/v1/stocks',{'symbols':'005930'})
