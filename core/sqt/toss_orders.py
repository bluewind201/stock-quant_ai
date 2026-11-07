"""Explicitly enabled order transport; shares the market adapter's single OAuth token.

Official OpenAPI 1.2.15. Mutations are NEVER automatically retried: the remote
clientOrderId guarantee lasts only 10 minutes, so durable local reconciliation is required.
"""
import os
import re
import time
from decimal import Decimal, InvalidOperation
import requests
from .toss import BASE_URL, TossError


class OrderUncertain(TossError):
    pass


def number(value,positive=False):
    try:d=Decimal(str(value))
    except (InvalidOperation,ValueError):raise TossError('주문·잔고 숫자 형식 오류') from None
    if not d.is_finite() or d<0 or (positive and d<=0):raise TossError('주문·잔고 숫자 범위 오류')
    return d


class TossOrders:
    def __init__(self,market):
        self.market=market
        self.account_seq=os.getenv('TOSS_ACCOUNT_SEQ','').strip()

    @property
    def enabled(self):
        return os.getenv('ENABLE_LIVE_TRADING','false').lower()=='true'

    def _request(self,method,path,params=None,body=None):
        reads={'/api/v1/accounts','/api/v1/holdings','/api/v1/orders','/api/v1/buying-power',
               '/api/v1/sellable-quantity','/api/v1/commissions'}
        detail=bool(re.fullmatch(r'/api/v1/orders/[A-Za-z0-9_-]{1,200}',path))
        cancel=bool(re.fullmatch(r'/api/v1/orders/[A-Za-z0-9_-]{1,200}/cancel',path))
        if not (method=='GET' and (path in reads or detail) or method=='POST' and (path=='/api/v1/orders' or cancel)):
            raise TossError('허용되지 않은 실거래 경로')
        if method=='POST' and not self.enabled:raise TossError('실거래가 비활성화되어 있습니다.')
        if path!='/api/v1/accounts' and (not self.account_seq.isdigit() or int(self.account_seq)<=0):
            raise TossError('NAS 환경의 TOSS_ACCOUNT_SEQ를 설정하세요.')
        broker=self.market
        with broker.lock:
            if not broker.token or time.monotonic()>=broker.expires:broker._authenticate()
            for attempt in range(2 if method=='GET' else 1):
                pause=broker.next_request-time.monotonic()
                if pause>0:time.sleep(pause)
                try:
                    headers={'Authorization':'Bearer '+broker.token}
                    if path!='/api/v1/accounts':headers['X-Tossinvest-Account']=self.account_seq
                    response=broker.http.request(method,BASE_URL+path,params=params,json=body,
                        headers=headers,
                        timeout=(5,10),allow_redirects=False)
                except requests.RequestException:
                    if method=='POST':raise OrderUncertain('주문 응답 미확인. 자동 재전송 없이 주문 확인이 필요합니다.') from None
                    raise TossError('실계좌 조회 연결 실패') from None
                finally:broker.next_request=time.monotonic()+.25
                if method=='GET' and response.status_code==401 and attempt==0:
                    broker._authenticate();continue
                if method=='POST' and (response.status_code>=500 or response.status_code in (408,409,429)):
                    raise OrderUncertain('주문 처리 결과 불명확. 주문 내역 확인 전 재전송하지 않습니다.')
                try:
                    data=broker._json(response)
                    if 'result' not in data:raise TossError('실계좌 응답 result 없음')
                    return data['result']
                except TossError:
                    if method=='POST' and response.status_code==200:
                        raise OrderUncertain('주문 접수 응답 검증 실패. 실제 주문 존재 여부를 확인하세요.') from None
                    raise

    def holdings(self):
        data=self._request('GET','/api/v1/holdings')
        if not isinstance(data,dict) or not isinstance(data.get('items'),list):raise TossError('보유 주식 응답 오류')
        for item in data['items']:
            if item.get('currency') not in ('KRW','USD') or not item.get('symbol'):raise TossError('보유 종목 통화 오류')
            number(item['quantity'])
        return data['items']

    def buying_power(self,currency):
        data=self._request('GET','/api/v1/buying-power',params={'currency':currency})
        if data.get('currency')!=currency:raise TossError('매수 가능 금액 통화 불일치')
        return float(number(data['cashBuyingPower']))

    def sellable(self,symbol):
        return float(number(self._request('GET','/api/v1/sellable-quantity',params={'symbol':symbol})['sellableQuantity']))

    def open_orders(self):
        data=self._request('GET','/api/v1/orders',params={'status':'OPEN'})
        if not isinstance(data.get('orders'),list) or data.get('hasNext'):raise TossError('미체결 주문 전체 조회 실패')
        return data['orders']

    def commission(self,currency):
        from .models import utcnow
        from zoneinfo import ZoneInfo
        country='KR' if currency=='KRW' else 'US'; day=utcnow().astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
        rows=self._request('GET','/api/v1/commissions')
        rows=[r for r in rows if r.get('marketCountry')==country and
              (not r.get('startDate') or r['startDate']<=day) and (not r.get('endDate') or day<=r['endDate'])]
        if len(rows)!=1:raise TossError('현재 적용 수수료 확인 실패')
        return float(number(rows[0]['commissionRate']))*10000

    def validate_account(self):
        rows=self._request('GET','/api/v1/accounts')
        if not any(str(r.get('accountSeq'))==self.account_seq and r.get('accountType')=='BROKERAGE' for r in rows):
            raise TossError('지정한 종합매매 계좌를 확인하지 못했습니다.')

    def accounts(self):
        rows=self._request('GET','/api/v1/accounts')
        return [{'account_seq':r['accountSeq'],'masked_number':'***'+str(r.get('accountNo',''))[-4:],
                 'account_type':r.get('accountType')} for r in rows]

    def create(self,intent):
        qty=number(intent['qty'],True);price=number(intent['limit_price'],True)
        if qty!=qty.to_integral_value() or intent['side'] not in ('buy','sell'):raise TossError('정수 현금 주식 주문만 지원합니다.')
        data=self._request('POST','/api/v1/orders',body={
            'clientOrderId':intent['id'],'symbol':intent['symbol'],'side':intent['side'].upper(),
            'orderType':'LIMIT','timeInForce':'DAY','quantity':str(int(qty)),'price':format(price,'f'),
            'confirmHighValueOrder':False})
        order_id=data.get('orderId') if isinstance(data,dict) else None
        if not isinstance(order_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',order_id):
            raise OrderUncertain('실제 주문 ID 응답 미확인')
        if data.get('clientOrderId') not in (None,intent['id']):raise OrderUncertain('주문 식별자 응답 불일치')
        return order_id

    def detail(self,order_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',order_id):raise TossError('주문 ID 형식 오류')
        return self._request('GET','/api/v1/orders/'+order_id)

    def cancel(self,order_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',order_id):raise TossError('주문 ID 형식 오류')
        return self._request('POST','/api/v1/orders/'+order_id+'/cancel',body={})
