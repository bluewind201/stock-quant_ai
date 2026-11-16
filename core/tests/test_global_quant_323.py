"""3.23.0: 국내(KR) + 미국(US) 24시간 멀티타임프레임 퀀트 전략, 본절잠금·분할익절 및 글로벌 대시보드 검증."""
from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from sqt import ai_office, daytrade as dt, notify, portfolio, reporting
from sqt.api import create_app
from sqt.calendar import exchange_sessions
from sqt.paper_models import DayTradeConfig
from sqt.store import Store

NOW = datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc)  # KST 10:00
US_OPEN_UTC = datetime(2026, 10, 21, 14, 30, tzinfo=timezone.utc)  # EDT 10:30 (KST 23:30)
US_AFTER_MIDNIGHT_KST = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)  # EDT 12:00 (KST 2026-10-22 01:00)


def kr_bars():
    days = exchange_sessions('KRX', '2025-01-01', '2026-10-20')[-300:]
    return [{'date': d, 'open': 10000 + i * 20, 'close': 10000 + i * 20,
             'low': 9950 + i * 20, 'high': 10050 + i * 20, 'volume': 500_000}
            for i, d in enumerate(days)]


def us_bars():
    days = exchange_sessions('NASDAQ', '2025-01-01', '2026-10-20')[-300:]
    return [{'date': d, 'open': round(100 + i * 0.25, 2), 'close': round(100 + i * 0.25, 2),
             'low': round(99.2 + i * 0.25, 2), 'high': round(100.8 + i * 0.25, 2), 'volume': 2_000_000}
            for i, d in enumerate(days)]


def quote(price: float, at: datetime = NOW):
    return {'bid': price, 'ask': price, 'price': price,
            'timestamp': at.isoformat(), 'book_timestamp': at.isoformat(), 'spread_bps': 0}


def make_service(tmp_path, monkeypatch, markets=('KR', 'US'), style='growth'):
    monkeypatch.setattr(dt, 'utcnow', lambda: NOW)
    monkeypatch.setattr(notify, 'send', lambda *a: True)
    monkeypatch.setattr(portfolio, 'fx_rate', lambda *a, **k: 1400.0)
    broker = SimpleNamespace(
        quotes=lambda instruments: {i.symbol: quote(150.0 if portfolio.is_us_symbol(i.symbol) else 50000.0, dt.utcnow())
                                    for i in instruments},
        history=lambda inst, *a, **k: us_bars() if portfolio.is_us_symbol(inst.symbol) else kr_bars(),
        warnings=lambda *a: [],
        rankings=lambda *a, **k: [],
        stock_meta=lambda syms: {s: {'name': s, 'exchange': 'NASDAQ' if portfolio.is_us_symbol(s) else 'KRX',
                                     'status': 'NORMAL'} for s in syms},
    )
    service = dt.DayTradeService(Store(tmp_path / 'global.db'), broker, ai=SimpleNamespace(lock=threading.Lock()))
    created = service.create({
        'strategy': 'portfolio',
        'markets': list(markets),
        'portfolio_style': style,
        'portfolio_risk_pct': 1.2,
        'initial_cash': 20_000_000,
        'max_positions': 10,
        'position_pct': 10,
        'breakeven_lock': True,
        'partial_tp': True,
    })
    state = service.store.get(service.account_kind, created['id'])
    state['day'] = '2026-10-21'
    state['status'] = 'running'
    monkeypatch.setattr(service, '_tradable', lambda *a: True)
    return service, state


def test_anti_chasing_blocks_spike_top_while_higher_atr_trend_passes():
    rows = kr_bars()
    # 변동성이 기존 4% 일간 ATR 한도(2*ATR=8%)를 넘는 우량 성장주(ATR ~5.6%, 2*ATR=11.2%)도 이제 정상 통과
    volatile = [dict(r, low=r['close'] * 0.972, high=r['close'] * 1.028) for r in rows]
    f_vol = portfolio.features(volatile, NOW)
    assert f_vol['atr_pct'] > 5.0
    assert any(s['eligible'] for s in f_vol['signals'].values())

    # 단기 급등(하루 +22%) 추격매수는 overheated 필터가 차단
    spiked = [dict(r) for r in rows]
    spiked[-1] = dict(spiked[-1], open=spiked[-2]['close'] * 1.18,
                      close=spiked[-2]['close'] * 1.22, high=spiked[-2]['close'] * 1.23)
    f_spike = portfolio.features(spiked, NOW)
    assert f_spike['overheated'] is True
    assert not f_spike['signals']['short']['eligible']
    assert not f_spike['signals']['medium']['eligible']


def test_dual_market_scan_and_usd_fx_accounting(tmp_path, monkeypatch):
    service, state = make_service(tmp_path, monkeypatch)
    state['config']['portfolio_core'] = ['005930']
    state['config']['us_core'] = ['NVDA']
    monkeypatch.setattr(portfolio.scanner, '_pool', lambda *a: {})
    monkeypatch.setattr(portfolio.scanner.krnews, 'stock_news', lambda *a: [])
    monkeypatch.setattr(portfolio.scanner.krnews, 'search_news', lambda *a, **k: [])

    res = portfolio.scan(service, state, US_OPEN_UTC)
    syms = {c['symbol'] for c in res['candidates']}
    assert {'005930', 'NVDA'} <= syms

    # 미국 주식(NVDA) 매수 시 USD 가격으로 수량 산정 + KRW 현금 차감 + 평가액 환율 반영 검증
    q_us = quote(175.0, US_OPEN_UTC)
    state['quotes']['NVDA'] = q_us
    cash_before = state['cash']
    ok = service._fill_buy(state, 'NVDA', '엔비디아', 2_000_000, q_us, 18.0, 5.0,
                           {'source': 'plan', 'horizon': 'medium', 'thesis': 'AI 가속기 추세', 'max_entry_price': 180.0},
                           US_OPEN_UTC)
    assert ok is True
    pos = state['positions']['NVDA']
    assert pos['currency'] == 'USD' and pos['exchange'] == 'NASDAQ' and pos['qty'] >= 1
    assert state['cash'] < cash_before - 200_000  # 달러 단가($175)가 아닌 원화 환산 금액 차감

    # 주가가 $175 -> $200 상승 시 원화 평가손익 반영
    state['quotes']['NVDA'] = quote(200.0, US_OPEN_UTC)
    eq = service.equity(state)
    assert eq > 20_000_000 + (200.0 - pos['entry_price']) * pos['qty'] * 1350

    rep = reporting.summary(state)
    assert rep['markets']['US']['holdings'] == 1
    assert rep['markets']['US']['invested_krw'] > 0


def test_breakeven_lock_and_partial_profit_taking(tmp_path, monkeypatch):
    service, state = make_service(tmp_path, monkeypatch)
    monkeypatch.setattr(service, '_refresh_quotes', lambda *a: None)
    q0 = quote(10000.0, NOW)
    state['quotes']['005930'] = q0
    assert service._fill_buy(state, '005930', '삼성전자', 2_000_000, q0, 12.0, 4.0,
                             {'source': 'plan', 'horizon': 'short', 'thesis': '반등', 'max_entry_price': 10500}, NOW)
    pos = state['positions']['005930']
    initial_qty = pos['qty']
    assert initial_qty >= 4

    # +3% 상승 -> 단기 본절+수수료 잠금(+2.5% 기준 통과) 작동
    state['quotes']['005930'] = quote(10320.0, NOW)
    portfolio.monitor(service, state, NOW)
    assert pos['breakeven_locked'] is True
    assert pos['sl_price'] > pos['entry_price']

    # +5.5% 상승 -> 단기 1차 분할익절(+4.5% 기준 통과) 50% 매도 후 잔량 보유 유지
    state['quotes']['005930'] = quote(10600.0, NOW)
    portfolio.monitor(service, state, NOW)
    assert '005930' in state['positions']
    assert pos['partial_tp_done'] is True
    assert pos['qty'] == initial_qty - int(round(initial_qty * 0.5))
    sell_trades = [t for t in state['trades'] if t['side'] == 'sell']
    assert len(sell_trades) == 1 and sell_trades[0]['partial'] is True and sell_trades[0]['pnl'] > 0


def test_us_session_after_midnight_kst_does_not_prematurely_roll_day(tmp_path, monkeypatch):
    service, state = make_service(tmp_path, monkeypatch)
    state['day'] = '2026-10-21'
    state['phase'] = 'monitoring'
    state['plan'] = {'summary': '미국장 계획', 'picks': []}
    service.store.put(service.account_kind, state['id'], state)

    monkeypatch.setattr(dt, 'utcnow', lambda: US_AFTER_MIDNIGHT_KST)
    monkeypatch.setattr(portfolio, 'tick', lambda *a: None)
    service.tick(state['id'])
    after = service.store.get(service.account_kind, state['id'])
    # KST 01:00 이지만 미국 정규장 진행 중이므로 전일 계획이 초기화되지 않아야 한다
    assert after['day'] == '2026-10-21' and after['plan'] is not None


def test_ai_office_collects_us_candidate_news(monkeypatch):
    monkeypatch.setattr(ai_office.krnews, 'stock_news', lambda *a, **k: (_ for _ in ()).throw(AssertionError('6자리 전용')))
    monkeypatch.setattr(ai_office.krnews, 'search_news', lambda q, **k: [
        {'title': f'{q} 데이터센터 실적 호조', 'published_at': NOW.isoformat(), 'source': '연합', 'url': 'https://example.org/nvda'}
    ])
    snap = {
        'at': NOW.isoformat(),
        'day': '2026-10-21',
        'held': [],
        'portfolio_policy': {'caps': {'short': 30, 'medium': 35, 'long': 25}},
        'candidates': [{'symbol': 'NVDA', 'name': '엔비디아', 'eligible_horizons': ['medium'], 'news': []}],
    }
    collected = ai_office.collect(snap)
    assert any('NVDA' in e.get('symbols', []) for e in collected['office_evidence'])


def test_dashboard_global_market_and_web_static_integrity(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'global-323')
    monkeypatch.setattr(portfolio, 'fx_rate', lambda *a, **k: 1392.5)
    app = create_app(tmp_path / 'web.sqlite3', background=False)
    monkeypatch.setattr(app.state.paper.ai, 'status', lambda: {'installed': True, 'authenticated': True, 'provider': 'claude'})
    with TestClient(app) as c:
        c.post('/api/login', json={'password': 'global-323'})
        acc = c.post('/api/daytrade/accounts', json={
            'strategy': 'portfolio',
            'markets': ['KR', 'US'],
            'portfolio_style': 'growth',
            'portfolio_risk_pct': 1.2,
            'initial_cash': 15_000_000,
            'max_positions': 10,
            'position_pct': 10,
        })
        assert acc.status_code == 200, acc.text
        dash = c.get('/api/dashboard?fast=true').json()
        assert 'global_market' in dash
        assert dash['global_market']['usd_krw'] == 1392.5
        assert dash['account']['config']['markets'] == ['KR', 'US']
        assert dash['account']['config']['portfolio_style'] == 'growth'

    # portfolio.js 및 terminal.js 까지 포함해 모든 ID 참조 및 인라인 onclick 부재 검증
    web = Path(__file__).resolve().parents[2] / 'stock-web'
    html = (web / 'index.html').read_text(encoding='utf-8')
    assert 'onclick=' not in html
    js_files = {p.name: p.read_text(encoding='utf-8') for p in web.glob('*.js')}
    present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
    for src in js_files.values():
        assert 'onclick=' not in src
        present |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', src))
    missing = {f'{name}: {used}' for name, src in js_files.items()
               for used in re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", src) if used not in present}
    assert not missing, missing


def test_office_normalization_fallback_and_scan_giveup_clearing(tmp_path, monkeypatch):
    # 1) ai_office normalizes Korean stock names, 'KOSPI' -> 'MARKET', and bare symbol evidence_ids
    monkeypatch.setattr(ai_office.krnews, 'stock_news', lambda *a, **k: [])
    monkeypatch.setattr(ai_office.krnews, 'search_news', lambda *a, **k: [])

    class NormAI:
        def __init__(self):
            self.lock = threading.Lock()
            self.last_usage = {}
            self.provider = 'claude'
            self.calls = 0

        def _guard(self):
            pass

        def _run(self, contract, prompt, timeout=180, label=''):
            self.calls += 1
            self.last_usage = {'provider': 'claude', 'model': 'claude-sonnet-5'}
            if contract == ai_office.RiskReview:
                return {'summary': '승인', 'approved_buys': ['005930'], 'approved_exits': [],
                        'reasons': [{'symbol': '삼성전자', 'positive': '추세 양호', 'negative': '변동성',
                                     'evidence_ids': ['005930'], 'missing': []}]}
            return {'summary': '검토 완료',
                    'findings': [
                        {'symbol': '삼성전자', 'positive': '수급 유입', 'negative': '단기 부담',
                         'evidence_ids': ['005930'], 'missing': []},
                        {'symbol': 'KOSPI', 'positive': '상승', 'negative': '경계',
                         'evidence_ids': [], 'missing': []},
                    ]}

        def _judge(self, contract, prompt, snapshot, timeout=180, label='', _lock_held=False):
            self.calls += 1
            self.last_usage = {'provider': 'claude', 'model': 'claude-sonnet-5'}
            return {'summary': '매수 계획', 'picks': [{
                'symbol': '005930', 'horizon': 'long', 'max_entry_price': 65000,
                'take_profit_pct': 12, 'stop_loss_pct': 4, 'weight_pct': 10,
                'thesis': '확인된 일봉의 장기 추세가 안정적으로 유지됨',
                'counter_case': '거시 충격으로 인한 단기 하락 변동성 확대 가능성',
                'invalidation': '200일 이동평균선 또는 보호 가격 이탈 시 정리',
            }]}

    snap = {
        'at': NOW.isoformat(), 'day': '2026-10-21', 'held': [],
        'portfolio_policy': {'caps': {'short': 20, 'medium': 30, 'long': 30}},
        'candidates': [{'symbol': '005930', 'name': '삼성전자', 'eligible_horizons': ['long'], 'news': []}],
    }
    office_res = ai_office.run(NormAI(), snap, lambda *a: None)
    assert [p['symbol'] for p in office_res['picks']] == ['005930']

    # 2) _ai_call falls back from failed plan_office to single-call fn(snapshot)
    from sqt.subscription_ai import AIError
    service, state = make_service(tmp_path, monkeypatch, markets=('KR',))
    state['scan'] = {'candidates': [{
        'symbol': '005930', 'name': '삼성전자', 'eligible_horizons': ['long'], 'market': 'KR',
        'currency': 'KRW', 'exchange': 'KRX',
        'portfolio': {'price': 60000, 'signals': {'long': {'eligible': True, 'stop_pct': 5.0}}},
    }]}
    state['errors']['scan_giveup'] = '장전 스캔·판단을 5번 시도했지만 실패했습니다'
    service.store.put(service.account_kind, state['id'], state)

    def fail_office(*a, **k):
        raise AIError('AI 오피스 지연 실패')

    service.ai.last_usage = {'provider': 'claude', 'model': 'claude-sonnet-5'}
    service.ai.plan_office = fail_office
    done = threading.Event()

    def single_fn(s):
        return {'summary': '단일 판단 전환', 'picks': [{
            'symbol': '005930', 'horizon': 'long', 'max_entry_price': 62000,
            'take_profit_pct': 12, 'stop_loss_pct': 5, 'weight_pct': 10,
            'thesis': '단일 판단 자동 전환으로 장기 추세 편입',
            'counter_case': '업황 둔화 및 지수 하락 동반 위험 존재',
            'invalidation': '손절선 및 추세선 이탈 시 전량 정리',
        }]}

    def on_done(s, plan, now):
        portfolio.apply_plan(service, s, plan, now)
        done.set()

    with service.lock:
        service._ai_call(state, '장전 판단', single_fn, dict(snap, portfolio_policy={'style': 'growth', 'caps': {'short': 30, 'medium': 35, 'long': 25}}), on_done, fail_phase='scanned', counts=False)
        service.store.put(service.account_kind, state['id'], state)
    assert done.wait(5)
    with service.lock:
        view = service.view(state['id'])
    assert view['plan'] and view['plan']['picks'][0]['symbol'] == '005930'
    assert 'scan_giveup' not in view['errors']
    assert view['office']['status'] == 'done'
    assert '단일 장전 판단 자동 전환 완료' in view['office']['application']

    # 3) scan_now and day rollover clear scan_giveup
    with service.lock:
        s2 = service.store.get(service.account_kind, state['id'])
        s2['errors']['scan_giveup'] = 'old error'
        service.store.put(service.account_kind, state['id'], s2)
    res_ctrl = service.control(state['id'], 'scan_now')
    assert 'scan_giveup' not in res_ctrl['errors']

    with service.lock:
        s3 = service.store.get(service.account_kind, state['id'])
        s3['errors']['scan_giveup'] = 'prev day giveup'
        s3['day'] = '2026-10-20'
        service.store.put(service.account_kind, state['id'], s3)
    monkeypatch.setattr(portfolio, 'tick', lambda *a: None)
    service.tick(state['id'])
    after_roll = service.store.get(service.account_kind, state['id'])
    assert after_roll['day'] == '2026-10-21' and 'scan_giveup' not in after_roll['errors']
