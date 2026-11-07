from functools import lru_cache

import exchange_calendars as xcals
import pandas as pd


@lru_cache(maxsize=8)
def calendar(exchange):
    return xcals.get_calendar('XKRX' if exchange == 'KRX' else 'XNYS', start='1990-01-01', end='2035-12-31')


# exchange_calendars 4.13.2 가 거래일로 잘못 아는 실제 휴장일. 공개 일봉과 대조해 확인했다.
# 달력이 모르는 새 휴장일은 portfolio.market_closures 가 기준 ETF 일봉으로 찾아낸다.
KRX_EXTRA_CLOSED = frozenset({'2026-06-03', '2026-07-17'})  # 지방선거일, 제헌절


def krx_sessions(start, end):
    return [d.strftime('%Y-%m-%d') for d in calendar('KRX').sessions_in_range(start, end)
            if d.strftime('%Y-%m-%d') not in KRX_EXTRA_CLOSED]


def session(exchange, day):
    cal = calendar(exchange)
    if not cal.is_session(day):
        raise ValueError(f'{day}: {exchange} 정규 거래일이 아닙니다.')
    return cal.session_open(day).to_pydatetime(), cal.session_close(day).to_pydatetime()


def status(exchange, now):
    cal = calendar(exchange)
    minute = pd.Timestamp(now).floor('min')
    try:
        return {'exchange': exchange, 'open': bool(cal.is_open_on_minute(minute)),
                'next_open': cal.next_open(minute).isoformat()}
    except (ValueError, IndexError):
        return {'exchange': exchange, 'open': False, 'error': '거래 캘린더 범위 밖입니다.'}
