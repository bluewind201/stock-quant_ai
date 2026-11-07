import copy
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
import pytest
import requests
from fastapi.testclient import TestClient
from sqt import forward
from sqt.api import create_app
from sqt.live_service import LiveService
from sqt.store import Store
from sqt.toss_orders import TossOrders,OrderUncertain,TossError
from .test_forward import state,plan,quote,NOW,snapshot


class FakeOrders:
    enabled=True;account_seq='1'
    def __init__(self):self.sent=[];self.cancels=[];self.rows=[];self.records={};self.uncertain=False
    def validate_account(self):pass
    def holdings(self):return self.rows
    def open_orders(self):return []
    def buying_power(self,currency):return 10000
    def sellable(self,symbol):return 10000
    def commission(self,currency):return 0
    def create(self,i):
        self.sent.append(copy.deepcopy(i))
        if self.uncertain:raise OrderUncertain('응답 미확인')
        id='broker-'+i['id']
        self.records[id]={'orderId':id,'symbol':i['symbol'],'side':i['side'].upper(),'currency':i['currency'],
            'orderType':'LIMIT','timeInForce':'DAY','price':str(i['limit_price']),'quantity':str(i['qty']),
            'orderedAt':NOW.isoformat(),'status':'PENDING',
            'execution':{'filledQuantity':'0','filledAmount':None,'commission':None,'tax':None}}
        return id
    def detail(self,id):return copy.deepcopy(self.records[id])
    def cancel(self,id):self.cancels.append(id);return {'orderId':'cancel-operation'}
    def fill(self,id,qty,price=100,status='PARTIAL_FILLED',fees='0'):
        self.records[id].update(status=status)
        self.records[id]['execution'].update(filledQuantity=str(qty),filledAmount=str(qty*price),commission=fees,tax='0')


@pytest.fixture
def service(state,tmp_path,monkeypatch):
    monkeypatch.setattr('sqt.live_service.utcnow',lambda:NOW)
    state['mode']='live';state['account_seq']='1';state['reconciled_at']=NOW.isoformat()
    store=Store(tmp_path/'live.sqlite3');store.put('live_accounts',state['id'],state)
    return LiveService(store,SimpleNamespace(configured=True),orders=FakeOrders())


def queue(service,state):
    r=service.apply_decision(state,plan(),snapshot(state),NOW,[],[])
    service.store.put('live_accounts',state['id'],state)
    return r[0]['order_intent_id']


def test_paper_sizing_does_not_fill_real_account(service,state):
    id=queue(service,state)
    assert state['cash']==10000 and not state['positions'] and not state['trades']
    service.execution_pulse()
    intent=service.store.get('live_orders',id)
    assert intent['status']=='OPEN' and len(service.orders.sent)==1
    assert not service.store.get('live_accounts',state['id'])['positions']


def test_partial_fills_and_duplicate_polls_are_atomic(service,state):
    id=queue(service,state);service.execution_pulse();intent=service.store.get('live_orders',id)
    bid=intent['broker_order_id'];service.orders.fill(bid,2)
    service.execution_pulse();service.execution_pulse()
    actual=service.store.get('live_accounts',state['id'])
    assert actual['cash']==9800 and actual['positions']['AAPL:swing']['qty']==2 and len(actual['trades'])==1
    service.orders.fill(bid,10,status='FILLED');service.orders.rows=[{'symbol':'AAPL','currency':'USD','quantity':'10'}]
    service.execution_pulse();actual=service.store.get('live_accounts',state['id'])
    assert actual['cash']==9000 and actual['positions']['AAPL:swing']['qty']==10 and len(actual['trades'])==2
    # Even a stale in-memory intent must use durable cumulative totals.
    service.ingest(intent,service.orders.detail(bid),NOW)
    assert service.store.get('live_accounts',state['id'])['cash']==9000


def test_uncertain_submit_is_never_automatically_retried(service,state):
    service.orders.uncertain=True;id=queue(service,state)
    service.execution_pulse();service.execution_pulse();service.initialize();service.execution_pulse()
    assert len(service.orders.sent)==1 and service.store.get('live_orders',id)['status']=='UNKNOWN'
    assert not service.store.get('live_accounts',state['id'])['positions']


def test_restart_after_committed_send_does_not_resubmit(service,state):
    id=queue(service,state);i=service.store.get('live_orders',id);i['status']='SUBMITTING';i['submitted_at']=NOW.isoformat()
    service.store.put('live_orders',id,i);service.initialize();service.execution_pulse()
    assert service.store.get('live_orders',id)['status']=='UNKNOWN' and not service.orders.sent
    assert service.store.get('live_accounts',state['id'])['status']=='stopped'


@pytest.mark.parametrize('change',['stop','stale','holdings','disabled'])
def test_dispatch_rechecks_everything_after_planning(service,state,change):
    id=queue(service,state)
    if change=='stop':state['status']='stopped'
    if change=='stale':state['quotes']['AAPL']=quote(NOW-timedelta(minutes=1))
    if change=='holdings':service.orders.rows=[{'symbol':'AAPL','currency':'USD','quantity':'99'}]
    if change=='disabled':service.orders.enabled=False
    service.store.put('live_accounts',state['id'],state);service.execution_pulse()
    assert service.store.get('live_orders',id)['status']=='BLOCKED' and not service.orders.sent


def test_cancel_ack_is_not_a_fill_or_terminal_state(service,state,monkeypatch):
    id=queue(service,state);service.execution_pulse()
    monkeypatch.setattr('sqt.live_service.utcnow',lambda:NOW+timedelta(seconds=31))
    service.execution_pulse();intent=service.store.get('live_orders',id)
    assert intent['status']=='CANCELING' and intent['cancel_operation_id']=='cancel-operation'
    assert intent['broker_order_id']!=intent['cancel_operation_id'] and len(service.orders.cancels)==1
    service.execution_pulse();assert len(service.orders.cancels)==1
    service.orders.fill(intent['broker_order_id'],0,status='CANCELED');service.execution_pulse()
    assert service.store.get('live_orders',id)['status']=='DONE'


def test_invalid_broker_fill_does_not_change_cash(service,state):
    id=queue(service,state);service.execution_pulse();intent=service.store.get('live_orders',id)
    service.orders.fill(intent['broker_order_id'],11,status='FILLED')
    service.execution_pulse()
    assert service.store.get('live_accounts',state['id'])['cash']==10000
    assert service.store.get('live_orders',id)['error'] and service.pending(state['id'])


def test_pending_buy_is_cancelled_when_partial_position_hits_stop(service,state):
    id=queue(service,state);service.execution_pulse();intent=service.store.get('live_orders',id)
    service.orders.fill(intent['broker_order_id'],2);service.execution_pulse()
    state=service.store.get('live_accounts',state['id']);state['quotes']['AAPL']=quote(price=95)
    service.protect_state(state,NOW);service.store.put('live_accounts',state['id'],state)
    assert state['positions']['AAPL:swing']['qty']==2 and state['cash']==9800
    service.execution_pulse();assert service.orders.cancels==[intent['broker_order_id']]


def test_risk_exit_only_changes_holdings_after_actual_sell_fill(service,state):
    id=queue(service,state);service.execution_pulse();intent=service.store.get('live_orders',id)
    service.orders.fill(intent['broker_order_id'],10,status='FILLED');service.execution_pulse()
    state=service.store.get('live_accounts',state['id']);state['quotes']['AAPL']=quote(price=95)
    service.orders.rows=[{'symbol':'AAPL','currency':'USD','quantity':'10'}]
    service.protect_state(state,NOW);service.store.put('live_accounts',state['id'],state)
    assert state['cash']==9000 and state['positions']['AAPL:swing']['qty']==10
    service.execution_pulse();sell=next(o for o in service.pending(state['id']) if o['side']=='sell')
    service.orders.fill(sell['broker_order_id'],10,95,'FILLED');service.orders.rows=[];service.execution_pulse()
    result=service.store.get('live_accounts',state['id'])
    assert result['cash']==9950 and not result['positions'] and result['realized_pnl']==-50


def test_live_mode_cannot_be_started_by_paper_routes_or_missing_ack(tmp_path,monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD','test-password');monkeypatch.delenv('ENABLE_LIVE_TRADING',raising=False)
    with TestClient(create_app(tmp_path/'api.sqlite3',background=False)) as client:
        assert client.get('/api/live/status').status_code==401
        client.post('/api/login',json={'password':'test-password'})
        assert not client.get('/api/live/status').json()['enabled']
        response=client.post('/api/live/accounts',json={'paper_account_id':'missing','watchlist':[{'symbol':'AAPL','exchange':'NASDAQ'}]})
        assert response.status_code==400 and 'ENABLE_LIVE_TRADING' in response.json()['detail']
        assert client.post('/api/live/accounts/missing/control',json={'action':'start'}).status_code==400


def test_unproven_paper_account_never_opens_broker_connection(service,state):
    state['mode']='forward_paper';service.store.put('paper_accounts',state['id'],state)
    cfg=copy.deepcopy(state['config']);cfg['paper_account_id']=state['id']
    with pytest.raises(ValueError,match='모의 검증 조건'):service.create(cfg)
    assert not service.orders.sent


def transport(monkeypatch,response=None,error=None):
    monkeypatch.setenv('TOSS_ACCOUNT_SEQ','1')
    calls=[]
    def request(*a,**k):
        calls.append((a,k))
        if error:raise error
        return response
    from sqt.toss import Toss
    broker=Toss();broker.token='fake-token';broker.expires=time.monotonic()+100;broker.http.request=request
    return TossOrders(broker),calls


def test_transport_is_explicitly_disabled_and_post_timeout_is_single_attempt(monkeypatch):
    monkeypatch.delenv('ENABLE_LIVE_TRADING',raising=False)
    orders,calls=transport(monkeypatch,error=requests.Timeout())
    intent={'id':'unique','qty':1,'limit_price':100,'side':'buy','symbol':'AAPL'}
    with pytest.raises(TossError,match='비활성화'):orders.create(intent)
    assert not calls
    monkeypatch.setenv('ENABLE_LIVE_TRADING','true')
    with pytest.raises(OrderUncertain):orders.create(intent)
    assert len(calls)==1 and calls[0][1]['json']['clientOrderId']=='unique'
    assert calls[0][1]['headers']['X-Tossinvest-Account']=='1'


def test_unknown_order_binding_validates_identity_and_time(service,state):
    id=queue(service,state);service.orders.uncertain=True;service.execution_pulse()
    intent=service.store.get('live_orders',id);service.orders.uncertain=False
    broker_id=service.orders.create(intent)
    service.orders.records[broker_id]['symbol']='MSFT'
    with pytest.raises(ValueError,match='식별'):service.bind_unknown(id,broker_id)
    assert service.store.get('live_orders',id)['status']=='UNKNOWN'
    service.orders.records[broker_id]['symbol']='AAPL';service.bind_unknown(id,broker_id)
    assert service.store.get('live_orders',id)['broker_order_id']==broker_id


def test_confirmed_fees_replace_estimate_without_repeating_fill(service,state,monkeypatch):
    state['config']['fee_bps']=10;service.store.put('live_accounts',state['id'],state)
    id=queue(service,state);service.execution_pulse();intent=service.store.get('live_orders',id)
    qty=intent['qty'];bid=intent['broker_order_id']
    service.orders.fill(bid,qty,status='FILLED',fees=None);service.execution_pulse()
    first=service.store.get('live_accounts',state['id'])
    assert 'costs' in first['errors'] and len(first['trades'])==1
    service.orders.fill(bid,qty,status='FILLED',fees='2')
    monkeypatch.setattr('sqt.live_service.utcnow',lambda:NOW+timedelta(seconds=61))
    service.orders.rows=[{'symbol':'AAPL','currency':'USD','quantity':str(qty)}]
    service.execution_pulse();updated=service.store.get('live_accounts',state['id'])
    assert updated['cash']==10000-qty*100-2 and 'costs' not in updated['errors']
    assert len(updated['trades'])==1 and len(updated['fee_adjustments'])==1


def test_prepared_live_account_remains_stopped_and_has_separate_ledger(tmp_path,monkeypatch):
    from sqt.live_service import LiveConfig
    store=Store(tmp_path/'prepare.sqlite3')
    paper=forward.create({'watchlist':[{'symbol':'AAPL','exchange':'NASDAQ'}]},NOW)
    paper.update(cash=10100,closed_lots=30,winning_lots=20,observed_days=[str(i) for i in range(20)])
    store.put('paper_accounts',paper['id'],paper)
    live=LiveService(store,SimpleNamespace(configured=True),orders=FakeOrders())
    payload=copy.deepcopy(paper['config']);payload.update(paper_account_id=paper['id'],initial_cash=1000)
    created=live.create(payload)
    assert created['mode']=='live' and created['status']=='stopped' and created['cash']==1000
    assert 'account_seq' not in created and not live.orders.sent
    assert store.get('paper_accounts',paper['id'])['cash']==10100


def test_account_lookup_does_not_require_account_header(monkeypatch):
    response=SimpleNamespace(status_code=200,json=lambda:{'result':[{'accountNo':'123456789','accountSeq':1,'accountType':'BROKERAGE'}]})
    orders,calls=transport(monkeypatch,response=response);orders.account_seq=''
    result=orders.accounts()
    assert result[0]['masked_number']=='***6789' and result[0]['account_seq']==1
    assert 'X-Tossinvest-Account' not in calls[0][1]['headers']


def test_second_execution_owner_cannot_start_on_same_database(service,state):
    service.initialize()
    second=LiveService(service.store,service.broker,orders=FakeOrders())
    with pytest.raises(ValueError,match='다른 주문 관리'):second.initialize()
