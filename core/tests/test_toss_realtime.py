from datetime import datetime,timedelta,timezone
import pytest
from sqt.toss import Toss
from sqt.models import Instrument
import sqt.toss as module

NOW=datetime(2026,9,8,15,tzinfo=timezone.utc)


@pytest.fixture
def broker(monkeypatch):
    b=Toss();responses=[];calls=[]
    def get(path,params):calls.append((path,params));return responses.pop(0)
    monkeypatch.setattr(b,'get',get);monkeypatch.setattr(module,'utcnow',lambda:NOW)
    return b,responses,calls


def raw_price():return [{'symbol':'AAPL','currency':'USD','lastPrice':'100','timestamp':NOW.isoformat()}]
def book():return {'currency':'USD','timestamp':NOW.isoformat(),'asks':[{'price':'102','volume':'99'},{'price':'101','volume':'3'}],
                  'bids':[{'price':'98','volume':'44'},{'price':'99','volume':'4'}]}


def test_quote_sorting_best_liquidity_and_no_order_routes(broker):
    b,q,c=broker;q.extend([raw_price(),book()]);result=b.quotes([Instrument(symbol='AAPL',exchange='NASDAQ')])['AAPL']
    assert (result['ask'],result['ask_qty'],result['bid'],result['bid_qty'])==(101,3,99,4)
    assert [p for p,params in c]==['/api/v1/prices','/api/v1/orderbook']


@pytest.mark.parametrize('kind',['null_time','wrong_currency','nan','crossed','empty','null_book_time'])
def test_bad_quote_cannot_be_filled(broker,kind):
    b,q,_=broker;price=raw_price();depth=book()
    if kind=='null_time':price[0]['timestamp']=None
    if kind=='wrong_currency':price[0]['currency']='KRW'
    if kind=='nan':price[0]['lastPrice']='NaN'
    if kind=='crossed':depth['bids'][0]['price']='200'
    if kind=='empty':depth['asks']=[]
    if kind=='null_book_time':depth['timestamp']=None
    q.extend([price,depth])
    assert b.quotes([Instrument(symbol='AAPL',exchange='NASDAQ')])=={}


def test_minutes_exclude_incomplete_bar_and_sort(broker):
    b,q,c=broker
    def candle(at):return dict(timestamp=at.isoformat(),currency='USD',openPrice='100',highPrice='102',lowPrice='99',closePrice='101',volume='1000')
    q.append({'candles':[candle(NOW),candle(NOW-timedelta(minutes=1)),candle(NOW-timedelta(minutes=2))]})
    rows=b.minutes(Instrument(symbol='AAPL',exchange='NASDAQ'))
    assert len(rows)==2 and rows[0]['timestamp']<rows[-1]['timestamp']
    assert c[0][1]['interval']=='1m' and c[0][1]['adjusted']=='true'
