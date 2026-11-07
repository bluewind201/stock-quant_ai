from datetime import datetime, timedelta, timezone

import pytest

from sqt import scanner
from sqt.daytrade import DayTradeService
from tests.test_daytrade import svc, make, clock, at


@pytest.mark.parametrize('gate', ['require_news', 'require_flow'])
def test_optional_gate_runs_after_enrichment(monkeypatch, gate):
    entry = {'symbol': '000500', 'name': 'example', 'origins': ['trading_amount'],
             'themes': [], 'trading_amount': 5e9, 'prev_change_pct': 2}
    monkeypatch.setattr(scanner, '_pool', lambda *a: {'000500': entry})
    visited = []
    def enrich(broker, row, cfg):
        visited.append(row['symbol'])
        row.update(news_count=1, flow_foreigner_1d=100, warnings=[])
    monkeypatch.setattr(scanner, '_enrich', enrich)
    result = scanner.premarket_scan(object(), {gate: True})
    assert visited == ['000500']
    assert len(result['candidates']) == 1
    assert result['candidates'][0]['technical']['daily'] is None


def test_gate_still_rejects_when_enriched_evidence_absent(monkeypatch):
    entry = {'symbol': '000500', 'name': 'example', 'origins': [], 'themes': [], 'trading_amount': 5e9}
    monkeypatch.setattr(scanner, '_pool', lambda *a: {'000500': entry})
    monkeypatch.setattr(scanner, '_enrich', lambda *a: None)
    result = scanner.premarket_scan(object(), {'require_news': True})
    assert not result['candidates']
    assert len(result['rejected']) == 1


@pytest.mark.parametrize('age', [-1, 31, 3600])
def test_stale_and_future_quotes_rejected(age):
    now = datetime.now(timezone.utc)
    q = {'bid': 99, 'ask': 101, 'timestamp': (now-timedelta(seconds=age)).isoformat(),
         'book_timestamp': now.isoformat()}
    assert not DayTradeService._fresh_quote(q, now)


def test_entry_retries_after_stale_quote_without_phantom_fill(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    original = broker.quotes
    def stale(instruments):
        rows = original(instruments)
        for q in rows.values():
            q['book_timestamp'] = at('08:59').isoformat()
        return rows
    monkeypatch.setattr(broker, 'quotes', stale)
    clock(monkeypatch, at('09:01')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert not s['positions'] and not s['plan']['picks'][0]['_done']
    monkeypatch.setattr(broker, 'quotes', original)
    clock(monkeypatch, at('09:02')); service.tick(id)
    assert len(store.get(service.account_kind, id)['positions']) == 1


def test_stale_quote_does_not_liquidate_paper_position(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    s = store.get(service.account_kind, id)
    cash = s['cash']
    service._fill_sell(s, '000500', 1, 'test', at('09:03'))
    assert '000500' in s['positions'] and s['cash'] == cash


def test_delayed_event_cannot_buy_after_entry_cutoff(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    s = store.get(service.account_kind, id)
    clock(monkeypatch, at('15:01'))
    service._apply_event(s, {'decisions': [{'symbol': '000500', 'action': 'buy'}]}, at('15:01'))
    assert not s['positions']


def test_chart_excludes_current_daily_bar():
    now = datetime(2026, 9, 17, 1, tzinfo=timezone.utc)
    class Broker:
        def history(self, *a, **k):
            return [{'date': (now.date()-timedelta(days=i)).isoformat(), 'open': 100,
                     'high': 101, 'low': 99, 'close': 100 if i else 999, 'volume': 1000}
                    for i in range(100, -1, -1)]
        def minutes(self, *a, **k):
            return []
    result = scanner.chart_context(Broker(), '000500', now)
    assert result['daily']['price'] == 100
    assert result['daily']['last_date'] == '2026-09-16'
    assert result['intraday']['bars'] == 0


def test_failed_close_is_retried_when_quotes_recover(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    original = broker.quotes
    monkeypatch.setattr(broker, 'quotes', lambda *a: {})
    clock(monkeypatch, at('15:21')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['positions'] and s['phase'] != 'closed'
    monkeypatch.setattr(broker, 'quotes', original)
    clock(monkeypatch, at('15:22')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert not s['positions'] and s['phase'] == 'closed'


def test_busy_eod_review_is_retried_and_can_keep_overnight(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    original = service._ai_allowed
    monkeypatch.setattr(service, '_ai_allowed', lambda *a, **k: False)
    clock(monkeypatch, at('15:01')); service.tick(id)
    assert store.get(service.account_kind, id)['phase'] != 'eod_reviewed'
    assert not any(kind == 'eod' for kind, _ in ai.calls)
    monkeypatch.setattr(service, '_ai_allowed', original)
    ai.eod = {'summary': 'keep', 'decisions': [
        {'symbol': '000500', 'action': 'hold_overnight', 'reason': 'Thesis still valid'}], 'buys': []}
    clock(monkeypatch, at('15:02')); service.tick(id)
    clock(monkeypatch, at('15:21')); service.tick(id)
    assert store.get(service.account_kind, id)['positions']['000500']['hold_overnight']
