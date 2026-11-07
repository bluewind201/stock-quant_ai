"""국내 테마 구성종목 수집 (네이버 증권 공개 JSON API).

무엇을 주는가
    테마 이름 · 전일 등락률 · 최근 3일 등락률 · 상승/보합/하락 종목 수 · 주도주,
    테마별 구성종목(거래정지·관리종목 여부 포함), 그리고 테마 설명문.

    토스 API 에는 업종·테마 분류가 아예 없다(`/api/v1/stocks` 응답에 해당 필드 없음).
    그래서 "어떤 종목들이 같은 테마인가" 를 알려면 외부 출처가 필요하다.

출처가 한 번 바뀌었다 (2026-09-10)
    예전에는 finance.naver.com 의 EUC-KR HTML 표를 정규식으로 긁었다. 네이버가
    stock.naver.com 의 SPA 로 옮기면서 그 마크업이 사라져 파싱이 통째로 깨졌다.
    지금은 그 SPA 가 쓰는 JSON API 를 직접 호출한다. HTML 구조 변경에 훨씬 덜 민감하다.

    GET /api/domestic/market/theme/list?startIdx&pageSize&sortType=changeRate
    GET /api/domestic/market/theme/{no}/stocklist?startIdx&pageSize&sortType=quantTop
    GET /api/domestic/market/theme/{no}/info?marketType=ALL

이 데이터의 성격 - 오해하지 말 것
    등락률 상위 테마는 **이미 오른 테마**다. 후행 지표이고, 그대로 매수 신호로 쓰면
    급등추격이 된다. 같은 구조를 두 번 측정했는데 둘 다 우위가 없었다.
        코인 급등추격  승률 27.7% (무작위 48.9%)
        주식 추세 게이트 무작위 분포 45~49 백분위
    여기서 실제로 값어치가 있는 건 등락률이 아니라 **구성종목 묶음**이다.

깨질 수 있는 의존성
    공식 문서가 있는 API 가 아니다. 경로나 필드가 또 바뀔 수 있다. 그래서
    - 실패는 예외를 밖으로 던지지 않고 빈 결과와 error 문자열로 돌려준다.
    - 매매 경로에서 호출하더라도 이 모듈 때문에 주문 판단이 멈추면 안 된다.
    - 캐시를 오래 잡아 요청 수를 줄인다. 테마 구성은 하루 단위로도 거의 안 바뀐다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger('bqt.themes')

BASE = 'https://stock.naver.com'
LIST_PATH = '/api/domestic/market/theme/list'
STOCKS_PATH = '/api/domestic/market/theme/{no}/stocklist'
INFO_PATH = '/api/domestic/market/theme/{no}/info'
PAGE_URL = BASE + '/market/stock/kr/theme'
AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'

_cache: dict[str, tuple[float, Any]] = {}
_lock = threading.Lock()
_last_request = 0.0
_request_lock = threading.Lock()


def _get(path: str, params: dict[str, Any], timeout: float = 15.0):
    """JSON 한 번. 호출 간 최소 간격을 둔다."""
    global _last_request
    with _request_lock:
        gap = time.monotonic() - _last_request
        if gap < 0.4:
            time.sleep(0.4 - gap)
        _last_request = time.monotonic()
    url = BASE + path + '?' + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        'User-Agent': AGENT, 'Accept': 'application/json',
        'Accept-Language': 'ko-KR,ko', 'Referer': PAGE_URL})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


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


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(',', ''))
    except (TypeError, ValueError):
        return default


def _leaders(raw: Any) -> list[dict[str, str]]:
    """`"1,007540,샘표|1,248170,샘표식품"` 형태를 풀어낸다."""
    out: list[dict[str, str]] = []
    for part in str(raw or '').split('|'):
        fields = part.split(',')
        if len(fields) >= 3 and fields[1].strip():
            out.append({'symbol': fields[1].strip(), 'name': fields[2].strip()})
    return out


def parse_list(rows: Any) -> list[dict[str, Any]]:
    """리스트 API 응답을 화면·AI 가 쓰는 형태로 정리한다."""
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or not row.get('no'):
            continue
        up = int(_number(row.get('riseCnt')))
        flat = int(_number(row.get('steadyCnt')))
        down = int(_number(row.get('fallCnt')))
        out.append({
            'no': int(_number(row.get('no'))),
            'name': str(row.get('name') or '').strip(),
            'change_pct': _number(row.get('changeRate')),
            'change_3d_avg_pct': _number(row.get('recent3daysChangeRate')),
            'up': up, 'flat': flat, 'down': down,
            'members': int(_number(row.get('totalCnt'))) or (up + flat + down),
            'leaders': _leaders(row.get('leadingItem')),
        })
    return out


def parse_members(rows: Any) -> list[dict[str, Any]]:
    """구성종목. 거래정지·관리종목은 살려서 내보내되 표시를 남긴다."""
    if not isinstance(rows, list):
        return []
    seen: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get('itemcode') or '').strip()
        if len(code) != 6 or code in seen:
            continue
        seen[code] = {
            'symbol': code,
            'name': str(row.get('itemname') or '').strip(),
            # 거래가 막힌 종목을 관심 종목에 넣으면 주문이 나가지 않는다. 걸러낼 수 있게 표시한다.
            'tradable': str(row.get('tradableStatus') or '') == 'tradable'
                        and str(row.get('tradeStopYn') or 'N') != 'Y',
            'managed': str(row.get('manageStatusGb') or '0') not in ('0', '', 'None'),
        }
    return list(seen.values())


def top_themes(limit: int = 10, ttl: float = 600.0) -> dict[str, Any]:
    """등락률 상위 테마. 실패해도 예외를 던지지 않는다."""
    size = max(20, min(int(limit) * 2, 100))

    def produce() -> dict[str, Any]:
        try:
            raw = _get(LIST_PATH, {'startIdx': 0, 'pageSize': size,
                                   'sortType': 'changeRate'})
        except Exception as exc:  # noqa: BLE001 - 수집 실패가 매매를 멈추면 안 된다
            log.warning('테마 목록 수집 실패: %s', exc)
            return {'themes': [], 'error': f'테마 목록 수집 실패: {type(exc).__name__}'}
        rows = parse_list(raw)
        if not rows:
            return {'themes': [], 'error': '테마 목록을 파싱하지 못했습니다 (출처 구조 변경 가능).'}
        return {'themes': rows, 'error': ''}

    result = _cached(f'list:{size}', ttl, produce)
    return {'themes': result['themes'][:limit], 'error': result['error'],
            'total': len(result['themes']),
            'source': BASE + LIST_PATH,
            'note': '등락률 상위는 이미 오른 테마입니다. 매수 신호가 아니라 구성 참고용입니다.'}


def members(theme_no: int, ttl: float = 3600.0) -> dict[str, Any]:
    """한 테마의 구성종목 전체."""
    def produce() -> dict[str, Any]:
        try:
            raw = _get(STOCKS_PATH.format(no=int(theme_no)),
                       {'startIdx': 0, 'pageSize': 100, 'sortType': 'quantTop'})
        except Exception as exc:  # noqa: BLE001
            return {'members': [], 'error': f'구성종목 수집 실패: {type(exc).__name__}'}
        rows = parse_members(raw)
        return {'members': rows, 'error': '' if rows else '구성종목을 파싱하지 못했습니다.'}

    return _cached(f'members:{int(theme_no)}', ttl, produce)


def description(theme_no: int, ttl: float = 86400.0) -> str:
    """테마 설명문. 무엇이 이 테마를 움직이는지 사람이 쓴 문장이라 판단 맥락으로 쓸 만하다."""
    def produce() -> str:
        try:
            raw = _get(INFO_PATH.format(no=int(theme_no)), {'marketType': 'ALL'})
        except Exception:  # noqa: BLE001
            return ''
        return str(raw.get('categoryInfo') or '').strip() if isinstance(raw, dict) else ''

    return _cached(f'info:{int(theme_no)}', ttl, produce)


def overview(limit: int = 10, with_members: bool = True,
             with_description: bool = True) -> dict[str, Any]:
    """상위 테마 + 각 테마의 구성종목·설명. AI 스냅샷과 화면이 함께 쓴다.

    테마당 요청이 최대 2회 더 붙으므로 limit 을 크게 잡지 말 것. 기본 10개면
    최초 1회 약 10초, 이후에는 캐시에서 나온다.
    """
    top = top_themes(limit)
    if with_members:
        for theme in top['themes']:
            detail = members(theme['no'])
            theme['stocks'] = detail['members']
            if detail['error']:
                theme['members_error'] = detail['error']
            if with_description:
                text = description(theme['no'])
                if text:
                    theme['description'] = text
    return top
