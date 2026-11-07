"""간밤 미국 시장을 숫자로. 한국장이 닫혀 있는 동안 세상이 어떻게 움직였나.

왜 필요한가
    한국 장전 판단에서 제일 중요한 입력은 "간밤에 무슨 일이 있었나" 다. 그런데 지금까지는
    그걸 뉴스 제목으로만 넘겼다. 제목은 "뉴욕증시 하락 마감" 처럼 두루뭉술하고, AI 가
    그것만으로 오늘 살지 말지를 정하기엔 근거가 약하다.

    미국에 상장된 ETF 는 한국장이 닫힌 동안에도 거래된다. 그 등락률이 곧 간밤의 답이다.
    특히 EWY(MSCI 한국 ETF)는 **외국인이 간밤에 한국을 어떻게 값매겼는지** 를 그대로 보여준다.
    2026-09-14 밤: EWY -6.62%, 반도체 -5.63%. 이런 날 아침에 사는 건 대부분 손해다.

한계
    - ETF 는 지수 자체가 아니다. 환율·괴리율이 섞인다. 방향과 크기를 보는 용도다.
    - 미국이 쉬는 날(현지 공휴일)에는 전일 종가가 그대로라 등락률이 0 이 된다. as_of 로 확인한다.
    - 조회가 실패해도 절대 예외를 올리지 않는다. 간밤 지표가 없다고 매매가 멈추면 안 된다.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger('bqt.overnight')

# (종목, 거래소, 라벨, 무엇을 읽는 것인가)
PROXIES: tuple[tuple[str, str, str, str], ...] = (
    ('EWY', 'AMEX', 'MSCI 한국 ETF', '간밤 외국인이 한국을 값매긴 결과. 오늘 갭의 가장 직접적인 신호'),
    ('SOXX', 'NASDAQ', '미 반도체', '삼성전자·SK하이닉스·반도체 장비주에 직결'),
    ('QQQ', 'NASDAQ', '나스닥 100', '성장주 위험선호'),
    ('SPY', 'AMEX', 'S&P 500', '미국 시장 전체'),
    ('XLE', 'AMEX', '미 에너지', '유가·정유·조선 관련'),
    ('TLT', 'NASDAQ', '미 장기국채', '오르면 금리 하락(위험회피), 내리면 금리 상승'),
)

_cache: dict[str, Any] = {'at': 0.0, 'value': None}
_lock = threading.Lock()


def _row(broker, symbol: str, exchange: str, label: str, why: str) -> dict[str, Any] | None:
    bars = broker.history_for(symbol, exchange) if hasattr(broker, 'history_for') else None
    if bars is None:
        from .models import Instrument
        from .toss import Toss
        options = {'include_closed_session': True} if isinstance(broker, Toss) else {}
        bars = broker.history(Instrument(symbol=symbol, exchange=exchange), count=65, **options)
    if len(bars) < 4:
        return None
    last, prev = bars[-1], bars[-2]
    three = bars[-4]
    return {
        'symbol': symbol, 'label': label, 'why': why, 'date': last['date'],
        'price_basis': '정규장 종료 후 조회한 공급자 일봉. 시간외 반영으로 수정될 수 있으며 확정 정규장 종가를 보장하지 않음',
        'close': round(last['close'], 2),
        'change_pct': round((last['close'] / prev['close'] - 1) * 100, 2),
        'change_3d_pct': round((last['close'] / three['close'] - 1) * 100, 2),
    }


def brief(broker, ttl: float = 1800.0) -> dict[str, Any]:
    """간밤 미국 시장 요약. 실패해도 빈 목록으로 돌려준다."""
    with _lock:
        if _cache['value'] is not None and time.time() - _cache['at'] < ttl:
            return _cache['value']
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for symbol, exchange, label, why in PROXIES:
        try:
            row = _row(broker, symbol, exchange, label, why)
            if row:
                rows.append(row)
        except Exception as exc:  # noqa: BLE001 - 간밤 지표가 없다고 매매가 멈추면 안 된다
            errors.append(f'{symbol}: {type(exc).__name__}')
            log.debug('간밤 지표 조회 실패 %s: %s', symbol, exc)
    result = {
        'as_of': rows[0]['date'] if rows else '',
        'rows': rows,
        'headline': summary(rows),
        'error': ' · '.join(errors[:3]),
        'note': ('미국 상장 ETF 의 전일 등락률입니다. 지수 자체가 아니라 환율·괴리가 섞여 있으니 '
                 '방향과 크기를 보는 용도입니다.'),
    }
    with _lock:
        _cache['at'] = time.time()
        _cache['value'] = result
    return result


def summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ''
    parts = [f'{r["label"]} {r["change_pct"]:+.2f}%' for r in rows[:4]]
    return '간밤 ' + ' · '.join(parts)


def korea_gap_signal(rows: list[dict[str, Any]]) -> str:
    """사람이 한 줄로 읽을 신호. 판단은 AI 가 한다 - 이건 화면 표시용이다."""
    by = {r['symbol']: r['change_pct'] for r in rows}
    ewy, sox = by.get('EWY'), by.get('SOXX')
    if ewy is None:
        return ''
    if ewy <= -2 or (sox is not None and sox <= -3):
        return '간밤 한국·반도체가 크게 밀렸습니다. 갭하락 출발 가능성이 높습니다.'
    if ewy >= 2:
        return '간밤 한국 관련 자산이 크게 올랐습니다. 갭상승 출발 가능성이 높습니다.'
    return '간밤 큰 변동은 없었습니다.'
