"""Toss Securities read-only adapter, official OpenAPI 1.2.15 (2026-09-08).

Only token issuance and stock/candle reads are exposed. One instance per app owns
the token: Toss invalidates the old token whenever the same client issues a new one.
"""
import math
import os
import random
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests

from .calendar import session
from .models import Bar, Instrument, stamp, utcnow

BASE_URL = 'https://openapi.tossinvest.com'
READ_PATHS = frozenset(('/api/v1/stocks', '/api/v1/candles','/api/v1/prices','/api/v1/orderbook',
                        '/api/v1/rankings', '/api/v1/market-calendar/KR', '/api/v1/price-limits',
                        '/api/v1/market-indicators/prices', '/api/v1/exchange-rate'))
# 종목별 경로. `/api/v1/stocks/{symbol}/investor-trading` 처럼 심볼이 끼어 있어 접미사로 본다.
READ_SUFFIXES = ('/investor-trading', '/warnings', '/program-trades')


class TossError(ValueError):
    """A user-readable failure which never includes credentials or raw response bodies."""


def retry_delay(value, attempt):
    if value is not None:
        try:
            delay = float(value)
        except (ValueError, TypeError):
            try:
                delay = (parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                delay = None
        if delay is not None and math.isfinite(delay):
            if delay > 30:
                raise TossError('토스 호출 제한: 서버가 30초 이상 대기를 요청했습니다. 잠시 후 다시 시도하세요.')
            return max(0, delay)
    return 2 ** attempt + random.uniform(0, 0.2)


class Toss:
    def __init__(self):
        self.client_id = os.getenv('TOSS_CLIENT_ID', '').strip()
        self.client_secret = os.getenv('TOSS_CLIENT_SECRET', '').strip()
        self.http = requests.Session()
        self.lock = threading.Lock()
        self.token = ''
        self.expires = 0.0
        self.next_request = 0.0

    @property
    def configured(self):
        return bool(self.client_id and self.client_secret)

    def _request(self, method, path, **kwargs):
        # Refuse accidentally extending this adapter into brokerage mutations.
        if not ((method == 'GET' and self._readable(path)) or (method == 'POST' and path == '/oauth2/token')):
            raise TossError('허용되지 않은 토스 API 경로입니다.')
        for attempt in range(3):
            delay = self.next_request-time.monotonic()
            if delay > 0:
                time.sleep(delay)
            try:
                response = self.http.request(method, BASE_URL+path, timeout=(5,20),
                                             allow_redirects=False, **kwargs)
            except requests.RequestException:
                raise TossError('토스 연결 실패: 인터넷 연결과 방화벽을 확인하세요.') from None
            self.next_request = time.monotonic()+0.25
            if response.status_code not in (429, 502, 503, 504) or attempt == 2:
                return response
            time.sleep(retry_delay(response.headers.get('Retry-After'),attempt))
        raise TossError('토스 요청 재시도 한도 초과')

    @staticmethod
    def _json(response, auth=False):
        code = response.status_code
        if code == 403:
            raise TossError('토스 HTTP 403: 웹 설정 → Open API → 허용 IP 관리에서 실행 PC/NAS의 공인 IP를 확인하세요. API 이용 권한도 확인하세요.')
        if code == 401:
            message = ('TOSS_CLIENT_ID·TOSS_CLIENT_SECRET과 API 활성 상태를 확인하세요.' if auth
                       else '토큰 재발급 후에도 인증에 실패했습니다. 같은 클라이언트 키를 쓰는 다른 프로그램을 확인하세요.')
            raise TossError('토스 HTTP 401: '+message)
        if code == 429:
            raise TossError('토스 HTTP 429: 호출 한도 초과입니다. 잠시 후 다시 시도하세요.')
        if code == 404:
            raise TossError('토스 HTTP 404: 해당 종목 또는 API 경로를 찾을 수 없습니다.')
        if code != 200:
            raise TossError(f'토스 HTTP {code}: 요청이 완료되지 않았습니다. 설정과 토스 서비스 상태를 확인하세요.')
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise TossError('토스 응답이 올바른 JSON이 아닙니다.') from None
        if not isinstance(data, dict) or 'error' in data:
            raise TossError('토스 응답 형식 오류 또는 API 오류입니다.')
        return data

    def _authenticate(self):
        if not self.configured:
            raise TossError('TOSS_CLIENT_ID·TOSS_CLIENT_SECRET을 설정하고 서버를 재시작하세요.')
        response = self._request('POST','/oauth2/token', data={
            'grant_type':'client_credentials', 'client_id':self.client_id,
            'client_secret':self.client_secret})
        data = self._json(response,auth=True)
        token = data.get('access_token')
        try:
            lifetime = float(data['expires_in'])
        except (KeyError, TypeError, ValueError):
            raise TossError('토스 토큰 만료시간 형식이 올바르지 않습니다.') from None
        if (not isinstance(token,str) or not token or str(data.get('token_type','')).lower() != 'bearer'
                or not math.isfinite(lifetime) or lifetime <= 0):
            raise TossError('토스 인증 응답 형식이 올바르지 않습니다.')
        self.token = token
        self.expires = time.monotonic()+lifetime-min(60,lifetime*.1)

    def _readable(self, path):
        return path in READ_PATHS or (path.startswith('/api/v1/stocks/') and path.endswith(READ_SUFFIXES))

    def get(self, path, params):
        # 인증 토큰을 받기 전에 거른다. 주문 경로에 조회 어댑터가 접근하는 일 자체가 없어야 한다.
        if not self._readable(path):
            raise TossError('조회 전용 토스 어댑터에서 허용하지 않는 경로입니다.')
        with self.lock:
            if not self.token or time.monotonic() >= self.expires:
                self._authenticate()
            for attempt in range(2):
                response = self._request('GET',path,params=params,
                                        headers={'Authorization':'Bearer '+self.token})
                if response.status_code == 401 and attempt == 0:
                    self.token, self.expires = '', 0
                    self._authenticate()
                    continue
                data = self._json(response)
                if 'result' not in data:
                    raise TossError('토스 응답에 result 필드가 없습니다.')
                return data['result']
        raise TossError('토스 인증 재시도 한도 초과')

    def stock(self, instrument: Instrument):
        result = self.get('/api/v1/stocks', {'symbols':instrument.symbol})
        if not isinstance(result,list):
            raise TossError('토스 종목정보 응답 형식이 올바르지 않습니다.')
        matches = [row for row in result if isinstance(row,dict) and row.get('symbol') == instrument.symbol]
        if len(matches) != 1:
            raise TossError('토스에서 해당 종목을 확인하지 못했습니다. 종목 코드를 확인하세요.')
        stock = matches[0]
        markets = ('KOSPI','KOSDAQ','KR_ETC') if instrument.exchange == 'KRX' else (instrument.exchange,)
        if stock.get('currency') != instrument.currency or stock.get('market') not in markets:
            raise TossError('선택한 거래소·통화와 토스 종목정보가 다릅니다. 거래소와 종목 코드를 확인하세요.')
        # Return only the reference fields needed by this training application.
        return {k:stock.get(k) for k in ('symbol','name','market','currency','status')}

    def history(self, instrument: Instrument, count=300, include_closed_session=False):
        if not 65 <= count <= 1000:
            raise TossError('일봉 개수는 65~1000 사이여야 합니다.')
        self.stock(instrument)
        rows, before, cursors = {}, None, set()
        market_tz = ZoneInfo('Asia/Seoul' if instrument.exchange == 'KRX' else 'America/New_York')
        for _ in range(16):
            params = {'symbol':instrument.symbol,'interval':'1d','count':200,'adjusted':'true'}
            if before is not None:
                params['before'] = before  # preserve the official inclusive cursor and its offset
            result = self.get('/api/v1/candles',params)
            if not isinstance(result,dict) or not isinstance(result.get('candles'),list):
                raise TossError('토스 일봉 응답 형식이 올바르지 않습니다.')
            for raw in result['candles']:
                try:
                    if raw['currency'] != instrument.currency:
                        raise TossError('토스 일봉 통화가 선택한 종목의 통화와 다릅니다.')
                    at = stamp(raw['timestamp'])
                    day = at.astimezone(market_tz).date().isoformat()
                    _, close_time = session(instrument.exchange,day)
                    # Exclude the entire current local trading date: US after-hours and KRX/NXT
                    # aggregates may still update after the regular-session closing bell.
                    if (not include_closed_session and day >= utcnow().astimezone(market_tz).date().isoformat()) or close_time >= utcnow():
                        continue
                    bar = Bar(date=day,open=raw['openPrice'],high=raw['highPrice'],low=raw['lowPrice'],
                              close=raw['closePrice'],volume=raw['volume']).model_dump()
                except TossError:
                    raise
                except (KeyError, ValueError, TypeError):
                    raise TossError('토스 일봉의 시각·가격·거래일 형식이 올바르지 않습니다.') from None
                if day in rows and rows[day] != bar:
                    raise TossError('동일 거래일의 토스 일봉 값이 다릅니다. 데이터 갱신 완료 후 다시 시도하세요.')
                rows[day] = bar
            if len(rows) >= count:
                return [rows[key] for key in sorted(rows)][-count:]
            next_before = result.get('nextBefore')
            if next_before is None or not result['candles']:
                break
            try:
                normalized = stamp(next_before)
            except (ValueError,TypeError):
                raise TossError('토스 다음 페이지 시각 형식이 올바르지 않습니다.') from None
            if normalized in cursors:
                raise TossError('토스 페이지 이동이 반복되었습니다. 잠시 후 다시 시도하세요.')
            if before is not None and normalized >= stamp(before):
                raise TossError('토스 페이지가 과거 방향으로 진행되지 않습니다.')
            cursors.add(normalized)
            before = next_before
        if len(rows) < 65:
            raise TossError('토스의 완료 일봉이 65개 미만입니다. 상장 기간·시세 권한을 확인하세요.')
        if before is not None and result.get('nextBefore') is not None and result['candles']:
            raise TossError('토스 일봉 조회 페이지 한도를 초과했습니다. 조회 개수를 줄여 주세요.')
        return [rows[key] for key in sorted(rows)][-count:]

    def quotes(self,instruments):
        """Best prices are explicitly sorted: upstream examples don't always match ordering prose."""
        expected={i.symbol:i for i in instruments}
        result=self.get('/api/v1/prices',{'symbols':','.join(expected)})
        if not isinstance(result,list): raise TossError('토스 현재가 응답 형식 오류')
        quotes={}
        for raw in result:
            try:
                symbol=raw['symbol']
                if symbol not in expected or symbol in quotes: raise ValueError()
                item=expected[symbol]
                if raw['currency']!=item.currency: raise ValueError()
                at=stamp(raw['timestamp']); price=float(raw['lastPrice'])
                if not math.isfinite(price) or price<=0: raise ValueError()
                book=self.get('/api/v1/orderbook',{'symbol':symbol})
                if book['currency']!=item.currency: raise ValueError()
                book_at=stamp(book['timestamp'])
                sides={}
                for side in ('asks','bids'):
                    levels=[(float(x['price']),float(x['volume'])) for x in book[side]]
                    if any(not math.isfinite(p) or not math.isfinite(v) or p<=0 or v<0 for p,v in levels):
                        raise ValueError()
                    levels=[x for x in levels if x[1]>=1]
                    if not levels: raise ValueError()
                    best=(min if side=='asks' else max)(p for p,v in levels)
                    sides[side]=(best,sum(v for p,v in levels if p==best))
                ask,ask_qty=sides['asks']; bid,bid_qty=sides['bids']
                if bid>ask: raise ValueError()
                quotes[symbol]={'symbol':symbol,'currency':item.currency,'exchange':item.exchange,
                    'price':price,'timestamp':at.isoformat(),'book_timestamp':book_at.isoformat(),
                    'bid':bid,'ask':ask,'bid_qty':int(bid_qty),'ask_qty':int(ask_qty),
                    'spread_bps':(ask-bid)/((ask+bid)/2)*10000,'received_at':utcnow().isoformat()}
            except (KeyError,TypeError,ValueError):
                # One illiquid/stale instrument must not prevent stops on the others.
                if isinstance(raw,dict) and raw.get('symbol') in expected:
                    continue
                raise TossError('토스 현재가·호가 응답 검증 실패') from None
        return quotes

    # ── 단타 스캔용 조회 ──────────────────────────────────────────────────
    def rankings(self, kind='TOP_GAINERS', duration=None, count=100, exclude_caution=True, market_country='KR'):
        """주식 랭킹. kind: TOP_GAINERS | TOP_LOSERS | MARKET_TRADING_AMOUNT | MARKET_TRADING_VOLUME.

        장전 후보 풀을 만드는 데 쓴다. 투자유의 종목은 기본으로 뺀다.
        상승률·하락률 랭킹은 `realtime` 을 지원하지 않는다(400 unsupported-ranking-duration).
        거래대금·거래량은 `realtime` 이 된다. duration 을 비우면 종류에 맞게 고른다.
        market_country: 'KR' (국내) | 'US' (미국).
        """
        if duration is None:
            duration = '1d' if kind in ('TOP_GAINERS', 'TOP_LOSERS') else 'realtime'
        country = 'US' if str(market_country).upper() == 'US' else 'KR'
        result = self.get('/api/v1/rankings', {
            'type': kind, 'marketCountry': country, 'duration': duration,
            'excludeInvestmentCaution': 'true' if exclude_caution else 'false',
            'count': max(1, min(int(count), 100))})
        rows = result.get('rankings') if isinstance(result, dict) else result
        out = []
        for row in rows or []:
            if not isinstance(row, dict) or not row.get('symbol'):
                continue
            price = row.get('price') or {}
            out.append({
                'rank': int(row.get('rank') or 0), 'symbol': str(row['symbol']),
                'name': str(row.get('name') or ''),
                'market_country': country,
                'market': str(row.get('market') or ''),
                'currency': 'USD' if country == 'US' else 'KRW',
                'last_price': float(price.get('lastPrice') or 0),
                'base_price': float(price.get('basePrice') or 0),
                'change_pct': float(price.get('changeRate') or 0) * 100.0,
                'trading_volume': float(row.get('tradingVolume') or 0),
                'trading_amount': float(row.get('tradingAmount') or 0),
            })
        return out

    def investor_trading(self, symbol, count=5):
        """투자자별 매매동향(외국인·기관·개인 순매수). 최신순, 최대 100일.

        국내 종목만. 거래량은 KRX·NXT 통합 기준의 주 단위 정수다.
        """
        result = self.get(f'/api/v1/stocks/{symbol}/investor-trading',
                          {'count': max(1, min(int(count), 100))})
        records = result.get('records') if isinstance(result, dict) else result
        out = []
        for row in records or []:
            if not isinstance(row, dict) or not row.get('date'):
                continue
            def net(group):
                g = row.get(group) or {}
                try:
                    return int(float(g.get('netBuyVolume') or 0))
                except (TypeError, ValueError):
                    return 0
            out.append({'date': row['date'], 'foreigner': net('foreigner'),
                        'institution': net('institution'), 'individual': net('individual')})
        return out

    def market_calendar_kr(self, date=None):
        """국내 장 운영 정보. NXT 프리마켓·정규장·애프터마켓 세션 시각을 준다."""
        params = {'date': date} if date else {}
        result = self.get('/api/v1/market-calendar/KR', params)
        return result if isinstance(result, dict) else {}

    def warnings(self, symbol):
        """투자경고·위험·거래정지 등 종목 경고. 후보에서 걸러내는 데 쓴다."""
        result = self.get(f'/api/v1/stocks/{symbol}/warnings', {})
        return result if isinstance(result, (list, dict)) else []

    def names(self, symbols):
        """여러 국내·해외 종목의 이름을 한 번에. 랭킹에서만 온 종목은 이름이 없어서 필요하다."""
        return {k: v['name'] for k, v in self.stock_meta(symbols).items() if v.get('name')}

    def stock_meta(self, symbols):
        """여러 종목의 이름·거래소·통화·상태를 한 번에 조회한다."""
        out = {}
        symbols = [s for s in dict.fromkeys(symbols) if s]
        for i in range(0, len(symbols), 50):
            chunk = symbols[i:i + 50]
            try:
                rows = self.get('/api/v1/stocks', {'symbols': ','.join(chunk)})
            except Exception:  # noqa: BLE001 - 이름은 없어도 매매에 지장 없다
                continue
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict) and row.get('symbol'):
                    sym = str(row['symbol'])
                    mkt = str(row.get('market') or '')
                    ex = 'KRX' if mkt in ('KOSPI', 'KOSDAQ', 'KR_ETC') else (mkt if mkt in ('NASDAQ', 'NYSE', 'AMEX') else '')
                    out[sym] = {
                        'symbol': sym,
                        'name': str(row.get('name') or ''),
                        'market': mkt,
                        'exchange': ex,
                        'currency': str(row.get('currency') or ('KRW' if ex == 'KRX' else 'USD')),
                        'status': str(row.get('status') or ''),
                    }
        return out

    def market_context(self):
        cached = getattr(self, '_market_context', None)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        result = {'fetched_at':utcnow().isoformat(), 'indices_and_rates':[], 'fx':None, 'errors':[]}
        try:
            rows=self.get('/api/v1/market-indicators/prices', {'symbols':'KOSPI,KOSDAQ,KR_BOND_2Y,KR_BOND_10Y'})
            for row in rows:
                value=float(row['lastPrice'])
                at=stamp(row['timestamp'])
                if not math.isfinite(value) or at>utcnow():
                    raise ValueError()
                result['indices_and_rates'].append({'symbol':row['symbol'],'value':value,'timestamp':at.isoformat(),
                    'source':'Toss market indicators','stale':(utcnow()-at).total_seconds()>300})
        except (TossError, KeyError, TypeError, ValueError) as exc:
            import re
            code=re.search(r'HTTP (\d{3})',str(exc))
            result['errors'].append('indices_or_rates_unavailable:'+ (code[1] if code else type(exc).__name__))
        present={r['symbol'] for r in result['indices_and_rates']}
        # Public index-only fallback. No broker credentials go to this service.
        for symbol in ('KOSPI','KOSDAQ'):
            if symbol in present:continue
            try:
                response=requests.get(f'https://m.stock.naver.com/api/index/{symbol}/basic',timeout=5)
                response.raise_for_status(); row=response.json()
                at=stamp(row['localTradedAt']); value=float(row['closePrice'].replace(',',''))
                change=float(row['fluctuationsRatio'])
                if at>utcnow() or not all(math.isfinite(v) for v in (value,change)) or value<=0:raise ValueError()
                result['indices_and_rates'].append({'symbol':symbol,'value':value,'change_pct':change,
                    'timestamp':at.isoformat(),'source':'Naver public index (unofficial)',
                    'stale':(utcnow()-at).total_seconds()>300,'market_status':row.get('marketStatus')})
            except (requests.RequestException,ValueError,KeyError,TypeError,AttributeError):
                result['errors'].append(symbol+':fallback_unavailable')
        try:
            fx=self.get('/api/v1/exchange-rate', {'baseCurrency':'USD','quoteCurrency':'KRW'})
            rate=float(fx['rate']); valid=stamp(fx['validUntil'])
            if not math.isfinite(rate) or rate<=0:
                raise ValueError()
            result['fx']={'usd_krw':rate,'valid_from':fx['validFrom'],'valid_until':fx['validUntil'],
                          'stale':valid<utcnow(), 'basis':'broker indicative exchange rate'}
        except (TossError, KeyError, TypeError, ValueError):
            result['errors'].append('fx_unavailable')
        present={r['symbol'] for r in result['indices_and_rates']}
        result['missing_symbols']=[s for s in ('KOSPI','KOSDAQ','KR_BOND_2Y','KR_BOND_10Y') if s not in present]
        self._market_context=(time.monotonic(),result)
        return result

    def session_minutes(self, instrument, count=1000):
        """Bounded pagination preserves the opening bars even after regular close."""
        rows, before, cursors = {}, None, set()
        zone=ZoneInfo('Asia/Seoul' if instrument.exchange=='KRX' else 'America/New_York')
        try:start,_=session(instrument.exchange,utcnow().astimezone(zone).date().isoformat())
        except (ValueError,KeyError):start=None
        for _ in range(min(8, (count + 199)//200)):
            params={'symbol':instrument.symbol,'interval':'1m','count':200,'adjusted':'true'}
            if before is not None:
                params['before']=before
            result=self.get('/api/v1/candles', params)
            for bar in self._minute_rows(result, instrument):
                rows[bar['timestamp']]=bar
            if start and rows and min(stamp(k) for k in rows)<=start:break
            cursor=result.get('nextBefore')
            if not cursor or cursor in cursors:
                break
            cursors.add(cursor); before=cursor
        return [rows[k] for k in sorted(rows)][-count:]

    def minutes(self,instrument,count=100):
        result=self.get('/api/v1/candles',{'symbol':instrument.symbol,'interval':'1m','count':count,'adjusted':'true'})
        return self._minute_rows(result, instrument)

    @staticmethod
    def _minute_rows(result, instrument):
        if not isinstance(result,dict) or not isinstance(result.get('candles'),list):
            raise TossError('토스 분봉 응답 형식 오류')
        rows={}
        from datetime import timedelta
        for raw in result['candles']:
            try:
                at=stamp(raw['timestamp'])
                if raw['currency']!=instrument.currency: raise ValueError()
                if at+timedelta(minutes=1)>utcnow(): continue
                bar=Bar(date=at.date().isoformat(),open=raw['openPrice'],high=raw['highPrice'],
                        low=raw['lowPrice'],close=raw['closePrice'],volume=raw['volume']).model_dump()
                bar['timestamp']=at.isoformat()
                if at in rows and rows[at]!=bar: raise ValueError()
                rows[at]=bar
            except (KeyError,TypeError,ValueError):
                raise TossError('토스 분봉의 시각·통화·가격 검증 실패') from None
        return [rows[k] for k in sorted(rows)]
