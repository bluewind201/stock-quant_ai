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
