"""국내 주식 뉴스 수집. 종목별 기사(본문 포함)와 키워드 검색.

왜 필요한가
    기존 news.py 는 연준·백악관·BLS·BEA 만 본다. 국내 종목이 왜 움직이는지에 대한
    근거가 0 이었고, AI 지침이 "근거 약하면 현금 유지" 라서 한 번도 사지 않았다.

출처
    종목별  : 네이버 모바일 증권  GET m.stock.naver.com/api/news/stock/{code}
              제목·본문·언론사·시각. 본문(body)이 온다는 점이 중요하다.
    키워드  : 구글 뉴스 RSS 한국어  news.google.com/rss/search?q=...&hl=ko&gl=KR
              테마명·이슈어로 검색. 제목·링크·시각만.

이 데이터의 성격
    - 기사 본문은 신뢰할 수 없는 **데이터**다. AI 지침에서 지시로 읽지 않게 못박는다.
    - 언론사 보도는 이미 가격에 반영된 뒤일 수 있다. 시각을 반드시 같이 넘긴다.
    - 공식 문서가 있는 API 가 아니다. 깨지면 빈 결과를 돌려주고 매매는 계속된다.
"""
from __future__ import annotations

import html
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

log = logging.getLogger('bqt.krnews')

KST = timezone(timedelta(hours=9))
NAVER_NEWS = 'https://m.stock.naver.com/api/news/stock/{code}'
NAVER_INTEGRATION = 'https://m.stock.naver.com/api/stock/{code}/integration'
NAVER_FINANCE_ANNUAL = 'https://m.stock.naver.com/api/stock/{code}/finance/annual'
NAVER_US_BASIC = 'https://api.stock.naver.com/stock/{code}/basic'
GOOGLE_RSS = 'https://news.google.com/rss/search'
MOBILE_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15'
DESKTOP_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_last_request = 0.0
_request_lock = threading.Lock()
_TAG = re.compile(r'<[^>]+>')
_ITEM = re.compile(r'<item>(.*?)</item>', re.S)


def _throttle() -> None:
    global _last_request
    with _request_lock:
        gap = time.monotonic() - _last_request
        if gap < 0.3:
            time.sleep(0.3 - gap)
        _last_request = time.monotonic()


def _fetch(url: str, agent: str, timeout: float = 12.0) -> bytes:
    _throttle()
    request = urllib.request.Request(url, headers={'User-Agent': agent, 'Accept-Language': 'ko-KR,ko'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _cached(key: str, ttl: float, produce):
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = produce()
    with _lock:
        _cache[key] = (now, value)
    return value


def _clean(text: Any, limit: int = 1200) -> str:
    text = html.unescape(_TAG.sub(' ', str(text or '')))
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:limit]


def _naver_time(raw: Any) -> str:
    """`202609112247` -> ISO UTC. 네이버 모바일은 KST 분 단위 문자열을 준다."""
    digits = re.sub(r'\D', '', str(raw or ''))
    if len(digits) < 12:
        return ''
    try:
        local = datetime.strptime(digits[:12], '%Y%m%d%H%M').replace(tzinfo=KST)
    except ValueError:
        return ''
    return local.astimezone(timezone.utc).isoformat(timespec='seconds')


def parse_stock_news(payload: Any, symbol: str) -> list[dict[str, Any]]:
    """네이버 모바일 응답을 정리한다. 형태가 바뀌면 빈 목록."""
    groups = payload if isinstance(payload, list) else [payload]
    out: list[dict[str, Any]] = []
    for group in groups:
        items = group.get('items') if isinstance(group, dict) else None
        for item in items or []:
            if not isinstance(item, dict) or not item.get('title'):
                continue
            office = str(item.get('officeId') or '')
            article = str(item.get('articleId') or '')
            out.append({
                'id': f'naver:{office}:{article}' if office and article else f'naver:{hash(item["title"])}',
                'symbol': symbol,
                'title': _clean(item.get('title'), 200),
                'body': _clean(item.get('body'), 1200),
                'source': _clean(item.get('officeName'), 40),
                'published_at': _naver_time(item.get('datetime')),
                'url': (f'https://n.news.naver.com/article/{office}/{article}' if office and article else ''),
            })
    return out


def stock_news(symbol: str, count: int = 10, ttl: float = 300.0) -> list[dict[str, Any]]:
    """한 종목의 최근 기사. 본문 포함. 실패하면 빈 목록."""
    def produce() -> list[dict[str, Any]]:
        try:
            url = NAVER_NEWS.format(code=symbol) + '?' + urllib.parse.urlencode(
                {'pageSize': max(1, min(int(count), 30)), 'page': 1})
            payload = json.loads(_fetch(url, MOBILE_AGENT).decode('utf-8'))
        except Exception as exc:  # noqa: BLE001 - 뉴스가 없다고 매매가 멈추면 안 된다
            log.debug('종목 뉴스 실패 %s: %s', symbol, exc)
            return []
        return parse_stock_news(payload, symbol)

    return _cached(f'stock:{symbol}:{count}', ttl, produce)


def parse_rss(xml_text: str, query: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for block in _ITEM.findall(xml_text):
        title = re.search(r'<title>(.*?)</title>', block, re.S)
        link = re.search(r'<link>(.*?)</link>', block, re.S)
        date = re.search(r'<pubDate>(.*?)</pubDate>', block, re.S)
        if not title:
            continue
        published = ''
        if date:
            try:
                published = parsedate_to_datetime(date.group(1).strip()).astimezone(
                    timezone.utc).isoformat(timespec='seconds')
            except (TypeError, ValueError):
                published = ''
        clean = _clean(title.group(1), 200)
        out.append({
            'id': f'grss:{hash((query, clean))}',
            'query': query, 'title': clean, 'body': '',
            'source': clean.rsplit(' - ', 1)[-1] if ' - ' in clean else '',
            'published_at': published,
            'url': _clean(link.group(1), 400) if link else '',
        })
    return out


def search_news(query: str, hours: int = 24, count: int = 20, ttl: float = 600.0) -> list[dict[str, Any]]:
    """키워드로 최근 기사 제목. 테마명·이슈어에 쓴다."""
    def produce() -> list[dict[str, Any]]:
        try:
            url = GOOGLE_RSS + '?' + urllib.parse.urlencode(
                {'q': f'{query} when:{max(1, int(hours))}h', 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'})
            xml_text = _fetch(url, DESKTOP_AGENT).decode('utf-8', 'replace')
        except Exception as exc:  # noqa: BLE001
            log.debug('키워드 뉴스 실패 %s: %s', query, exc)
            return []
        rows = parse_rss(xml_text, query)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(hours)))
        fresh = [r for r in rows if not r['published_at']
                 or datetime.fromisoformat(r['published_at']) >= cutoff]
        return fresh[:max(1, int(count))]

    return _cached(f'search:{query}:{hours}', ttl, produce)


def bundle(symbols: list[str], per_symbol: int = 5, ttl: float = 300.0) -> dict[str, list[dict[str, Any]]]:
    """여러 종목의 기사를 한 번에. 종목당 1회 호출이라 목록 크기를 제한할 것."""
    return {s: stock_news(s, per_symbol, ttl) for s in symbols}


# 시장 전체 뉴스. 종목 뉴스와 달리 "오늘 시장이 어떤 날인가" 를 보는 용도다.
MARKET_TOPICS: dict[str, str] = {
    'market': '코스피 OR 코스닥 증시',
    'policy': '금리 OR 한국은행 OR 정부 정책 경제',
    'global': '미국 증시 OR 연준 OR 관세 OR 환율',
}


def market_news(hours: int = 24, per_topic: int = 8, ttl: float = 600.0) -> list[dict[str, Any]]:
    """국내 시장·정책·해외 요인 기사 제목을 한 목록으로. 주제(topic)를 붙이고 제목이 겹치면 한 번만."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for topic, query in MARKET_TOPICS.items():
        for row in search_news(query, hours=hours, count=per_topic, ttl=ttl):
            key = row['title'].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(row, topic=topic))
    merged.sort(key=lambda r: r.get('published_at') or '', reverse=True)
    return merged


_FUNDAMENTAL_CODE_MAP: dict[str, str] = {
    'marketValue': 'market_cap',
    'foreignRate': 'foreign_rate',
    'per': 'per',
    'eps': 'eps',
    'cnsPer': 'forward_per',
    'cnsEps': 'forward_eps',
    'pbr': 'pbr',
    'bps': 'bps',
    'dividendYieldRatio': 'dividend_yield',
    'highPriceOf52Weeks': 'high_52w',
    'lowPriceOf52Weeks': 'low_52w',
    'industryGroupKor': 'sector',
}

_FINANCE_ROW_MAP: dict[str, str] = {
    '매출액': 'revenue',
    '영업이익': 'operating_profit',
    '당기순이익': 'net_income',
    '영업이익률': 'operating_margin',
    'ROE(지배주주)': 'roe',
    '부채비율': 'debt_ratio',
}


def parse_fundamentals(integration: Any, annual: Any = None, market: str = 'KR') -> dict[str, Any]:
    """네이버 증권 종목 통합/연간 재무 응답에서 핵심 재무·밸류에이션 지표를 추출한다."""
    if not isinstance(integration, dict):
        return {}
    out: dict[str, Any] = {}
    info_key = 'stockItemTotalInfos' if str(market).upper() == 'US' else 'totalInfos'
    for item in integration.get(info_key) or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get('code') or '')
        dest = _FUNDAMENTAL_CODE_MAP.get(code)
        val = str(item.get('value') or '').strip()
        if dest and val and val not in ('N/A', '-', 'nan'):
            out[dest] = val

    consensus = integration.get('consensusInfo')
    if isinstance(consensus, dict):
        rating = str(consensus.get('recommMean') or '').strip()
        target = str(consensus.get('priceTargetMean') or '').strip()
        if rating and rating not in ('0', '0.00', 'N/A'):
            out['consensus_rating'] = rating
        if target and target not in ('0', 'N/A'):
            out['target_price'] = target

    if isinstance(annual, dict):
        finfo = annual.get('financeInfo') or {}
        tr_list = finfo.get('trTitleList') or []
        period_titles = [
            str(t.get('title') or '').strip()
            for t in tr_list if isinstance(t, dict) and t.get('title')
        ]
        for row in finfo.get('rowList') or []:
            if not isinstance(row, dict):
                continue
            title = str(row.get('title') or '').strip()
            dest = _FINANCE_ROW_MAP.get(title)
            if not dest:
                continue
            cols = row.get('columns') or {}
            vals = [
                str(v.get('value')).strip()
                for v in cols.values()
                if isinstance(v, dict) and str(v.get('value') or '').strip() not in ('', '-', 'N/A', 'None')
            ]
            if vals:
                out[dest] = vals[-1] if len(vals) == 1 else f'{vals[-2]} -> {vals[-1]}'
        if period_titles:
            out['annual_periods'] = ', '.join(period_titles[-3:])
        corp = _clean(annual.get('corporationSummary'), 240)
        if corp:
            out['business_summary'] = corp

    if out:
        out['source'] = 'naver_finance'
    return out


def _us_reuters_code(symbol: str, exchange: str = 'NASDAQ') -> str:
    sym = str(symbol or '').strip().upper()
    if '.' in sym:
        return sym
    ex = str(exchange or 'NASDAQ').upper()
    suffix = '.N' if ex == 'NYSE' else '.A' if ex == 'AMEX' else '.O'
    return f'{sym}{suffix}'


def stock_fundamentals(symbol: str, market: str = 'KR', exchange: str = 'KRX',
                       ttl: float = 3600.0, force: bool = False) -> dict[str, Any]:
    """국내·미국 종목의 재무·밸류에이션 요약(PER·PBR·ROE·EPS·시총·매출·영업이익·컨센서스)을 조회한다."""
    if not force and (stock_news is not _ORIG_STOCK_NEWS or search_news is not _ORIG_SEARCH_NEWS):
        return {}
    mkt = 'US' if str(market).upper() == 'US' else 'KR'

    def produce() -> dict[str, Any]:
        if mkt == 'US':
            candidates = [_us_reuters_code(symbol, exchange)]
            for sfx in ('.O', '.N', '.A'):
                alt = f'{str(symbol).strip().upper()}{sfx}'
                if alt not in candidates:
                    candidates.append(alt)
            for code in candidates:
                try:
                    raw = _fetch(NAVER_US_BASIC.format(code=urllib.parse.quote(code)), DESKTOP_AGENT, timeout=3.5)
                    data = json.loads(raw.decode('utf-8', 'replace'))
                    parsed = parse_fundamentals(data, None, market='US')
                    if parsed:
                        return parsed
                except Exception:  # noqa: BLE001
                    continue
            return {}
        try:
            integ_raw = _fetch(NAVER_INTEGRATION.format(code=symbol), MOBILE_AGENT, timeout=3.5)
            integ = json.loads(integ_raw.decode('utf-8', 'replace'))
        except Exception as exc:  # noqa: BLE001
            log.debug('종목 통합정보 실패 %s: %s', symbol, exc)
            return {}
        annual = None
        try:
            ann_raw = _fetch(NAVER_FINANCE_ANNUAL.format(code=symbol), MOBILE_AGENT, timeout=3.5)
            annual = json.loads(ann_raw.decode('utf-8', 'replace'))
        except Exception:  # noqa: BLE001
            annual = None
        return parse_fundamentals(integ, annual, market='KR')

    return _cached(f'fund:{mkt}:{symbol}:{exchange}', ttl, produce)


_ORIG_STOCK_NEWS = stock_news
_ORIG_SEARCH_NEWS = search_news
