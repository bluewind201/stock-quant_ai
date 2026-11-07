"""3.22.2: 달력 오류 하루가 전 종목을 탈락시켜 회의·매수를 멈춘 문제.

실제 사고(2026-11-04~): exchange_calendars 가 2026-06-03, 2026-07-17 을 거래일로 알아서
일봉 300개 전체 일치 검사가 모든 종목에서 실패했고, 후보 0종이라 AI 회의가 열리지 않았다.
기존 테스트는 일봉을 같은 달력으로 만들어 이 문제를 잡지 못했다. 여기서는 실제처럼 휴장일에 일봉이 없다.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sqt import diagnostics, portfolio
from sqt.calendar import calendar, krx_sessions
from tests.test_portfolio_317 import setup

NOW = datetime(2026, 11, 5, 22, 30, tzinfo=timezone.utc)  # 2026-11-06 07:30 KST


def market_bars(end='2026-11-05', skip=()):
    days = [d for d in krx_sessions('2025-01-01', end) if d not in skip][-300:]
    return [{'date': d, 'open': 1000 + i, 'close': 1000 + i, 'low': 998 + i, 'high': 1002 + i, 'volume': 10000000}
            for i, d in enumerate(days)]


def test_real_closures_the_library_does_not_know():
    library = [d.strftime('%Y-%m-%d') for d in calendar('KRX').sessions_in_range('2026-06-01', '2026-07-31')]
    assert '2026-06-03' in library and '2026-07-17' in library  # 라이브러리 오류가 고쳐지면 이 표를 정리한다
    ours = krx_sessions('2026-06-01', '2026-07-31')
    assert '2026-06-03' not in ours and '2026-07-17' not in ours


def test_bars_without_holiday_rows_pass():
    rows = market_bars()
    assert not {'2026-06-03', '2026-07-17'} & {b['date'] for b in rows}
    assert portfolio.features(rows, NOW)['last_date'] == '2026-11-05'


def test_one_stock_gap_is_still_rejected_with_the_date():
    rows = market_bars()
    gap = rows[200]['date']
    with pytest.raises(ValueError, match=f'일봉 누락 \\({gap}\\)'):
        portfolio.features(rows[:200] + rows[201:], NOW)
    with pytest.raises(ValueError, match='중복'):
        portfolio.features(rows + [dict(rows[-1])], NOW)


def test_stale_feed_stays_rejected_even_if_the_reference_is_stale_too(tmp_path, monkeypatch):
    service, state = setup(tmp_path, monkeypatch)
    stale = market_bars(end='2026-11-04')
    service.broker.history = lambda *a, **k: stale
    closed = portfolio.market_closures(service, state, NOW)
    assert closed == set()
    with pytest.raises(ValueError, match='미갱신'):
        portfolio.features(stale, NOW, closed)


def test_unknown_market_closure_is_learned_from_the_reference(tmp_path, monkeypatch):
    service, state = setup(tmp_path, monkeypatch)
    holiday = '2026-09-07'  # 앞으로 생길 임시공휴일을 흉내 낸다
    rows = market_bars(skip={holiday})
    calls = []
    service.broker.history = lambda *a, **k: calls.append(a) or rows
    with pytest.raises(ValueError, match='누락'):
        portfolio.features(rows, NOW)
    closed = portfolio.market_closures(service, state, NOW)
    assert closed == {holiday} and portfolio.features(rows, NOW, closed)['last_date'] == '2026-11-05'
    assert any(holiday in r['message'] for r in state['log'])
    assert portfolio.market_closures(service, state, NOW) == {holiday} and len(calls) == 1  # 하루 한 번만 조회


def test_reference_problems_do_not_excuse_other_stocks(tmp_path, monkeypatch):
    service, state = setup(tmp_path, monkeypatch)
    rows = market_bars()
    service.broker.history = lambda *a, **k: rows[:100] + rows[110:]  # 10일 빠진 기준 종목 = 자료 문제
    assert portfolio.market_closures(service, state, NOW) == set()

    def down(*a, **k):
        raise OSError('offline')
    service.broker.history = down
    state.pop('market_closures', None)
    assert portfolio.market_closures(service, state, NOW) == set() and 'market_closures' not in state


def test_scan_finds_candidates_on_real_shaped_bars(tmp_path, monkeypatch):
    service, state = setup(tmp_path, monkeypatch)
    state['config']['portfolio_core'] = ['005930']
    monkeypatch.setattr(portfolio.scanner, '_pool', lambda *a: {})
    monkeypatch.setattr(portfolio.scanner.krnews, 'stock_news', lambda *a: [])
    service.broker.history = lambda *a, **k: market_bars()
    service.broker.warnings = lambda *a: []
    result = portfolio.scan(service, state, NOW)
    assert [c['symbol'] for c in result['candidates']] == ['005930']


def test_scanned_stock_qualifies_for_any_period_it_passes(tmp_path, monkeypatch):
    """실제 사례: 셀트리온이 중기·장기 규칙을 통과했지만 단기 탐색 목록에만 있어 후보에서 빠졌다."""
    service, state = setup(tmp_path, monkeypatch)
    state['config']['portfolio_core'] = []
    state['config']['portfolio_watchlists'] = {'short': ['068270'], 'medium': [], 'long': []}
    monkeypatch.setattr(portfolio.scanner, '_pool', lambda *a: {})
    monkeypatch.setattr(portfolio.scanner.krnews, 'stock_news', lambda *a: [])
    rows = market_bars()
    rows[-1].update(open=rows[-2]['close'], close=rows[-2]['close'])  # 반등이 없어 단기 규칙만 탈락
    assert not portfolio.features(rows, NOW)['signals']['short']['eligible']
    service.broker.history = lambda *a, **k: rows
    service.broker.warnings = lambda *a: []
    result = portfolio.scan(service, state, NOW)
    assert state['portfolio_universes']['medium'] == [] and state['portfolio_universes']['long'] == []
    candidate = result['candidates'][0]
    assert candidate['symbol'] == '068270' and candidate['eligible_horizons'] in (['medium'], ['long'])


def test_focused_style_still_blocks_periods_with_no_allocation(tmp_path, monkeypatch):
    service, state = setup(tmp_path, monkeypatch)
    state['config'].update(portfolio_core=['068270'], portfolio_style='short')
    monkeypatch.setattr(portfolio.scanner, '_pool', lambda *a: {})
    monkeypatch.setattr(portfolio.scanner.krnews, 'stock_news', lambda *a: [])
    service.broker.history = lambda *a, **k: market_bars()
    service.broker.warnings = lambda *a: []
    result = portfolio.scan(service, state, NOW)
    assert all(set(c['eligible_horizons']) <= {'short'} for c in result['candidates'])


def checks_for(reviewed):
    account = {'status': 'running', 'mode': 'paper', 'phase': 'idle', 'config': {'strategy': 'portfolio', 'scan_at': '07:30'},
               'ai': {'history': []}, 'errors': {}, 'log': [], 'portfolio_checks': reviewed,
               'last_poll_at': NOW.isoformat()}
    result = diagnostics.checks(account, ai_status={'installed': True, 'authenticated': True}, toss_configured=True,
                                session=True, telegram={'configured': False}, storage={}, now=NOW)
    return {c['id']: c for c in result['checks']}


def test_health_card_flags_every_stock_failing_for_one_reason():
    same = [{'symbol': str(i), 'eligible': [], 'reason': '일봉 누락 (2026-06-03)'} for i in range(10)]
    card = checks_for(same)['candidates']
    assert card['status'] == 'bad' and '10종 중 10종' in card['detail'] and '2026-06-03' in card['detail']
    mixed = [{'symbol': str(i), 'eligible': [], 'reason': f'조건 {i}'} for i in range(10)]
    assert checks_for(mixed)['candidates']['status'] == 'warn'
    found = mixed + [{'symbol': 'A', 'eligible': ['short'], 'reason': '기간별 매수 검토 가능'}]
    card = checks_for(found)['candidates']
    assert card['status'] == 'ok' and '후보 1종' in card['detail']


def test_worker_error_is_cleared_after_recovery(tmp_path, monkeypatch):
    from sqt import daytrade as dt
    service, state = setup(tmp_path, monkeypatch)
    state['errors']['worker'] = '오래된 오류'
    service.store.put(service.account_kind, state['id'], state)
    seen = []

    class Stop:
        calls = 0

        def wait(self, seconds):
            Stop.calls += 1
            return Stop.calls > 1
    service.stop_event = Stop()
    service.tick = lambda id: seen.append(id)
    monkeypatch.setattr(dt.threading, 'Thread', lambda target, **k: SimpleNamespace(start=target))
    service.start_worker()
    assert seen and 'worker' not in service.store.get(service.account_kind, state['id'])['errors']
