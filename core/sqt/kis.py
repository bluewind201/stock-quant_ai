"""KIS read-only daily data adapter. This module has no brokerage order endpoint."""
import os
import threading
import time
from datetime import date, timedelta

import requests

from .calendar import session
from .models import Bar, Instrument, utcnow


class KIS:
    def __init__(self):
        self.environment = os.getenv('KIS_ENV', 'demo')
        if self.environment not in ('demo', 'real'):
            raise ValueError('KIS_ENV는 demo 또는 real이어야 합니다.')
        self.base = ('https://openapivts.koreainvestment.com:29443' if self.environment == 'demo'
                     else 'https://openapi.koreainvestment.com:9443')
        self.key = os.getenv('KIS_APP_KEY', '')
        self.secret = os.getenv('KIS_APP_SECRET', '')
        self.token, self.expires, self.last_call, self.last_auth = '', 0, 0, 0
        self.lock = threading.Lock()
        self.http = requests.Session()

    @property
    def configured(self):
        return bool(self.key and self.secret)

    def get(self, path, tr, params):
        with self.lock:
            if not self.configured:
                raise ValueError('KIS_APP_KEY와 KIS_APP_SECRET을 서버 환경변수에 설정하세요.')
            if time.monotonic() >= self.expires:
                if time.monotonic()-self.last_auth < 60:
                    raise ValueError('접근토큰 재발급 대기 중입니다. 60초 후 재시도하세요.')
                self.last_auth = time.monotonic()
                response = self.http.post(self.base+'/oauth2/tokenP', json={
                    'grant_type':'client_credentials', 'appkey':self.key, 'appsecret':self.secret}, timeout=15)
                if response.status_code != 200:
                    raise ValueError(f'KIS 인증 실패 (HTTP {response.status_code}). 키와 환경을 확인하세요.')
                data = response.json()
                if not data.get('access_token'):
                    raise ValueError('KIS 접근토큰을 발급하지 못했습니다.')
                self.token = data['access_token']
                self.expires = time.monotonic()+max(0, int(data.get('expires_in', 3600))-120)
            delay = 1.1-(time.monotonic()-self.last_call)
            if delay > 0:
                time.sleep(delay)
            self.last_call = time.monotonic()
            response = self.http.get(self.base+path, params=params, headers={
                'authorization':'Bearer '+self.token, 'appkey':self.key,
                'appsecret':self.secret, 'tr_id':tr, 'custtype':'P'}, timeout=20)
            if response.status_code == 401:
                self.expires = 0
            if response.status_code != 200:
                raise ValueError(f'KIS 시세 조회 실패 (HTTP {response.status_code})')
            data = response.json()
            if data.get('rt_cd') != '0':
                raise ValueError('KIS 시세 조회가 거부되었습니다. 코드: '+str(data.get('msg_cd', 'unknown')))
            return data

    def history(self, instrument: Instrument, count=300):
        end, rows = utcnow().date(), {}
        for _ in range(12):
            if instrument.exchange == 'KRX':
                data = self.get('/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice',
                    'FHKST03010100', {'FID_COND_MRKT_DIV_CODE':'J', 'FID_INPUT_ISCD':instrument.symbol,
                    'FID_INPUT_DATE_1':(end-timedelta(days=400)).strftime('%Y%m%d'),
                    'FID_INPUT_DATE_2':end.strftime('%Y%m%d'), 'FID_PERIOD_DIV_CODE':'D', 'FID_ORG_ADJ_PRC':'0'})
                mapping = dict(date='stck_bsop_date', open='stck_oprc', high='stck_hgpr',
                               low='stck_lwpr', close='stck_clpr', volume='acml_vol')
            else:
                data = self.get('/uapi/overseas-price/v1/quotations/dailyprice', 'HHDFS76240000',
                    {'AUTH':'', 'EXCD':{'NASDAQ':'NAS','NYSE':'NYS','AMEX':'AMS'}[instrument.exchange],
                     'SYMB':instrument.symbol, 'GUBN':'0', 'BYMD':end.strftime('%Y%m%d'), 'MODP':'1'})
                mapping = dict(date='xymd', open='open', high='high', low='low', close='clos', volume='tvol')
            batch = data.get('output2') or []
            if not batch:
                break
            earliest = end
            before = len(rows)
            for item in batch:
                value = str(item.get(mapping['date'], ''))
                if len(value) != 8:
                    continue
                day = date(int(value[:4]),int(value[4:6]),int(value[6:8]))
                earliest = min(earliest, day)
                day_string = day.isoformat()
                try:
                    _, close_time = session(instrument.exchange, day_string)
                except ValueError:
                    continue
                if close_time >= utcnow():
                    continue  # never import today's unfinished daily bar
                row = {key: (day_string if key == 'date' else float(item[field])) for key,field in mapping.items()}
                rows[day_string] = Bar.model_validate(row).model_dump()
            if len(rows) >= count or len(rows) == before:
                break
            end = earliest-timedelta(days=1)
        if len(rows) < 65:
            raise ValueError('완료된 일봉이 65개 미만입니다. 시세 권한·종목·시장 코드를 확인하세요.')
        return [rows[key] for key in sorted(rows)][-count:]
