from copy import deepcopy

import pytest

from sqt import daytrade as dt, scanner
from sqt.paper_models import DayTradeConfig, DayPlan
from tests.test_daytrade import svc, make, clock, at, SCAN


def theme(name='power', symbols=None):
    return {'name': name, 'symbols': symbols or ['000500'],
            'catalyst': 'New orders with quantified demand',
            'risk': 'Demand may reverse', 'invalidation': 'Break below support'}


def test_midnight_manual_request_waits_for_0730_kst(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('00:00'))
    service.control(id, 'scan_now'); service.tick(id)
    assert not ai.calls
    clock(monkeypatch, at('07:29')); service.tick(id)
    assert not ai.calls
    # 07:30 KST is 22:30 UTC on the previous date.
    clock(monkeypatch, at('07:30')); service.tick(id)
    assert len(ai.calls) == 1
    s = store.get(service.account_kind, id)
    assert s['stamps']['plan_trigger'] == '예약 07:30'
    assert s['stamps']['plan'] == at('07:30').isoformat()
    service.tick(id)
    assert len(ai.calls) == 1


def test_legacy_midnight_config_is_corrected_on_load(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    s = store.get(service.account_kind, id)
    s['config']['scan_at'] = '00:00'
    store.put(service.account_kind, id, s)
    clock(monkeypatch, at('00:01')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['config']['scan_at'] == '07:30' and not ai.calls
    assert 'schedule_migration' in s['errors']


@pytest.mark.parametrize('when', ['00:00', '07:29', '09:00', '25:00'])
def test_invalid_premarket_schedule_rejected(when):
    with pytest.raises(ValueError):
        DayTradeConfig(scan_at=when)


def test_theme_selection_is_saved_and_supplied_to_ai(svc, monkeypatch):
    service, store, broker, ai = svc
    scan = deepcopy(SCAN)
    scan['candidates'][0]['themes'] = [{'name': 'power'}]
    monkeypatch.setattr(dt.scanner, 'premarket_scan', lambda *a: scan)
    ai.plan['top_themes'] = [theme()]
    id = make(service)
    clock(monkeypatch, at('07:30')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['plan']['top_themes'] == [theme()]
    assert ai.calls[0][1]['theme_universe'][0]['symbols'] == ['000500']
    assert ai.calls[0][1]['theme_target_count'] == 5
    assert s['phase'] == 'planned'


@pytest.mark.parametrize('ranked', [[], [theme('invented')], [theme(symbols=['006260'])]])
def test_missing_or_invented_theme_cannot_authorize_buys(svc, monkeypatch, ranked):
    service, store, broker, ai = svc
    scan = deepcopy(SCAN)
    scan['candidates'][0]['themes'] = [{'name': 'power'}]
    monkeypatch.setattr(dt.scanner, 'premarket_scan', lambda *a: scan)
    ai.plan['top_themes'] = ranked
    id = make(service)
    clock(monkeypatch, at('07:30')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['phase'] == 'scanned' and not s['positions']


def test_theme_schema_rejects_six_and_duplicate_rankings(svc):
    ai = svc[3]
    with pytest.raises(ValueError):
        DayPlan(**dict(ai.plan, top_themes=[theme(str(i)) for i in range(6)]))
    with pytest.raises(ValueError):
        DayPlan(**dict(ai.plan, top_themes=[theme(), theme()]))


def test_shortlist_keeps_five_theme_representatives(monkeypatch):
    pool = {str(i): {'symbol': f'{i:06d}', 'name': str(i), 'origins': ['theme'],
                     'themes': [{'name': 'crowded' if i < 10 else f'theme{i}'}],
                     'trading_amount': 5e9, 'news_count': 0}
            for i in range(14)}
    monkeypatch.setattr(scanner, '_pool', lambda *a: pool)
    monkeypatch.setattr(scanner, '_enrich', lambda *a: None)
    result = scanner.premarket_scan(object(), {'max_candidates': 5})
    assert len(result['candidates']) == 5
    assert len({c['themes'][0]['name'] for c in result['candidates']}) == 5


def test_eod_refreshes_rebound_evidence_and_preserves_overnight(svc, monkeypatch):
    service, store, broker, ai = svc
    ai.eod = {'summary': 'rebound exception', 'decisions': [
        {'symbol': '000500', 'action': 'hold_overnight', 'reason': 'Business intact, market pressure'}], 'buys': []}
    monkeypatch.setattr(dt.krnews, 'stock_news', lambda *a, **k: [
        {'id': 'news-1', 'title': 'Orders maintained', 'published_at': at('14:30').isoformat()}])
    id = make(service)
    clock(monkeypatch, at('07:30')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    clock(monkeypatch, at('15:01')); service.tick(id)
    evidence = next(snapshot for kind, snapshot in ai.calls if kind == 'eod')['rebound_evidence']
    assert evidence['000500']['news'][0]['title'] == 'Orders maintained'
    clock(monkeypatch, at('15:21')); service.tick(id)
    assert store.get(service.account_kind, id)['positions']['000500']['hold_overnight']
