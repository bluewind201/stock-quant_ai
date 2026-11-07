"""당일 단타 실거래 모드. 네트워크 없이 가짜 토스 주문 어댑터로 검증한다.

지키려는 성질 (실제 돈이 걸린 것들)
  1) ENABLE_LIVE_TRADING / TOSS_ACCOUNT_SEQ 없이는 실거래 계좌를 만들 수 없다
  2) 투입 자금은 LIVE_CAPITAL_KRW 를, 주문 1건은 LIVE_MAX_ORDER_KRW 를 넘지 못한다
  3) 매수는 지정가(호가 단위 올림) 주문으로 나가고, 포지션은 토스가 확인한 체결에서만 생긴다
  4) 손절 조건이 오면 매도 주문이 나가고, 체결 후에야 현금·손익이 바뀐다
  5) 제한 시간 안에 안 채워진 주문은 취소된다. 취소된 매도는 다시 시도한다
  6) 주문 결과가 불명확하면 UNKNOWN 으로 남고, 새 매수를 막고, 사람이 연결하거나 폐기한다
  7) 토스 보유량이 장부와 다르면 시작하지 않는다
  8) 실거래 시작은 '실거래 시작' 문구 확인이 필요하다 (API)
  9) 같은 조회를 두 번 해도 체결이 두 번 반영되지 않는다
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from sqt import daytrade as dt
from sqt import daytrade_live as dl
from sqt.api import create_app
from sqt.store import Store
from sqt.toss_orders import OrderUncertain, TossError

KST = timezone(timedelta(hours=9))


def at(hhmm: str, day: str = '2026-09-11', sec: int = 0) -> datetime:
    h, m = map(int, hhmm.split(':'))
    return datetime.fromisoformat(day).replace(hour=h, minute=m, second=sec, tzinfo=KST).astimezone(timezone.utc)


class FakeOrders:
    """토스 주문 API 흉내. 체결은 테스트가 직접 채운다."""

    def __init__(self):
        self.enabled = True
        self.account_seq = '12345'
        self.holdings_rows = []
        self.power = 5_000_000.0
        self.open = []
        self.created = []
        self.details = {}
        self.cancelled = []
        self.fail_create = None
        self.seq = 0

    def validate_account(self): pass
    def holdings(self): return list(self.holdings_rows)
    def open_orders(self): return list(self.open)
    def buying_power(self, currency): return self.power
    def sellable(self, symbol): return sum(int(r['quantity']) for r in self.holdings_rows if r['symbol'] == symbol)

    def create(self, intent):
        if self.fail_create:
            exc, self.fail_create = self.fail_create, None
            raise exc
        self.seq += 1
        oid = f'T{self.seq}'
        self.created.append((oid, dict(intent)))
        self.details[oid] = {'orderId': oid, 'symbol': intent['symbol'], 'side': intent['side'].upper(),
                             'status': 'PENDING', 'execution': {'filledQuantity': 0, 'filledAmount': 0}}
        return oid

    def detail(self, oid): return dict(self.details[oid])

    def cancel(self, oid):
        self.cancelled.append(oid)
        d = self.details[oid]
        d['status'] = 'CANCELED'
        return {'orderId': oid + 'c'}

    # 테스트 도우미: 체결 채우기 (누적)
    def fill(self, oid, qty, price, done=True, commission=None, tax=None):
        d = self.details[oid]
        d['execution'] = {'filledQuantity': qty, 'filledAmount': qty * price}
        if commission is not None: d['execution']['commission'] = commission
        if tax is not None: d['execution']['tax'] = tax
        d['status'] = 'FILLED' if done else 'PARTIAL_FILLED'
        sym = d['symbol']
        if d['side'] == 'BUY':
            row = next((r for r in self.holdings_rows if r['symbol'] == sym), None)
            if row: row['quantity'] = str(int(row['quantity']) + qty)  # 누적이 아니라 증분이라 테스트에서는 done 일 때만 부른다
            else: self.holdings_rows.append({'symbol': sym, 'currency': 'KRW', 'quantity': str(qty)})
        else:
            for r in self.holdings_rows:
                if r['symbol'] == sym: r['quantity'] = str(int(r['quantity']) - qty)
            self.holdings_rows = [r for r in self.holdings_rows if int(r['quantity']) > 0]


class Broker:
    def __init__(self): self.price = {'000500': 10000.0}
    def quotes(self, instruments):
        return {i.symbol: {'symbol': i.symbol, 'bid': self.price[i.symbol] * .999, 'ask': self.price[i.symbol] * 1.001,
                           'price': self.price[i.symbol], 'timestamp': dt.utcnow().isoformat(), 'book_timestamp': dt.utcnow().isoformat()} for i in instruments if i.symbol in self.price}


class AI:
    import threading
    lock = threading.Lock(); last_usage = {}
    def status(self): return {'installed': True, 'authenticated': True}
    def plan_day(self, s): return {'summary': '요약', 'market_view': '보통', 'market_stance': 'normal', 'picks': [
        {'symbol': '000500', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
         'thesis': '수주 기사와 수급 확인', 'counter_case': '되돌림', 'invalidation': '시가 -2%'}]}
    eod = None
    def judge_eod(self, s): return self.eod or {'summary': '정리', 'decisions': [{'symbol': '000500', 'action': 'close', 'reason': '일회성'}], 'buys': []}
    def judge_event(self, s): return {'summary': '유지', 'decisions': []}
    def review_day(self, s): return {'summary': '복기', 'diagnosis': '표본 부족', 'lessons': [], 'adjustments': []}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv('LIVE_CAPITAL_KRW', '2000000')
    monkeypatch.setenv('LIVE_MAX_ORDER_KRW', '1000000')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)


@pytest.fixture
def svc(tmp_path, monkeypatch, env):
    monkeypatch.setattr(dt.scanner, 'premarket_scan', lambda b, cfg=None: {
        'scanned_at': '', 'pool_size': 1, 'enriched': 1, 'rejected': [], 'note': 'n',
        'candidates': [{'symbol': '000500', 'name': '가온전선', 'score': 50, 'origins': ['theme'], 'themes': [], 'news': []}]})
    monkeypatch.setattr(dt.krnews, 'stock_news', lambda *a, **k: [])
    monkeypatch.setattr(dt.krnews, 'market_news', lambda *a, **k: [])
    store = Store(str(tmp_path / 'live.sqlite3'))
    orders = FakeOrders()
    broker = Broker()
    service = dt.DayTradeService(store, broker, ai=AI(), live=dl.LiveExecution(store, orders))
    def sync(state, kind, fn, snapshot, on_done, fail_phase=None, counts=True):
        state['ai']['calls_today' if counts else 'anchor_calls_today'] += 1
        on_done(state, fn(snapshot), dt.utcnow())
    monkeypatch.setattr(service, '_ai_call', sync)
    return service, store, orders, broker


def make_live(service, cash=1_500_000):
    return service.create({'mode': 'live', 'initial_cash': cash, 'max_positions': 5, 'position_pct': 15})['id']


def tick(service, monkeypatch, id, hhmm, sec=0):
    monkeypatch.setattr(dt, 'utcnow', lambda: at(hhmm, sec=sec)); service.tick(id)


# ── 1·2) 켜기 전엔 못 만들고, 상한을 넘지 못한다 ──────────────────────────────
def test_live_requires_flags_and_caps(svc):
    service, store, orders, broker = svc
    orders.enabled = False
    with pytest.raises(ValueError, match='ENABLE_LIVE_TRADING'):
        make_live(service)
    orders.enabled = True; orders.account_seq = ''
    with pytest.raises(ValueError, match='TOSS_ACCOUNT_SEQ'):
        make_live(service)
    orders.account_seq = '12345'
    with pytest.raises(ValueError, match='LIVE_CAPITAL_KRW'):
        make_live(service, cash=3_000_000)
    orders.holdings_rows = [{'symbol': '005930', 'currency': 'KRW', 'quantity': '3'}]
    with pytest.raises(ValueError, match='보유 주식'):
        make_live(service)
    orders.holdings_rows = []; orders.power = 100.0
    with pytest.raises(ValueError, match='매수 가능 금액'):
        make_live(service)
    orders.power = 5_000_000.0
    id = make_live(service)
    assert store.get(service.account_kind, id)['mode'] == 'live'
    assert 'account_seq' not in service.view(id)         # 화면에는 계좌 식별자를 내보내지 않는다
    # 모의 계좌는 여전히 아무 조건 없이 만들 수 있고 서로 섞이지 않는다
    pid = service.create({'initial_cash': 10_000_000})['id']
    assert service.current('paper')['id'] == pid and service.current('live')['id'] == id


def test_krx_tick_rounding():
    assert dl.to_tick(10033.0, up=True) == 10040 and dl.to_tick(10033.0, up=False) == 10030 and dl.to_tick(10030.0, up=True) == 10030
    assert dl.to_tick(81234.0, up=True) == 81300 and dl.to_tick(81234.0, up=False) == 81200
    assert dl.to_tick(1999.4, up=True) == 2000 and dl.to_tick(499_999.0, up=True) == 500_000
    assert dl.to_tick(0.5, up=False) == 1


# ── 3) 매수는 주문으로 나가고 체결에서만 포지션이 생긴다 ──────────────────────
def test_buy_is_an_order_and_position_comes_from_fill(svc, monkeypatch):
    service, store, orders, broker = svc
    id = make_live(service)
    service.control(id, 'start')
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '09:00', 5)
    s = store.get(service.account_kind, id)
    assert s['positions'] == {} and s['cash'] == 1_500_000          # 아직 체결 없음
    assert len(orders.created) == 1
    oid, intent = orders.created[0]
    assert intent['side'] == 'buy' and intent['symbol'] == '000500'
    # 매도호가 10010 × (1+0.3%) = 10040.03 → 호가 올림 10050 · 예산 225,000 / (10050 × 1.001) → 22주
    assert intent['limit_price'] == 10050 and intent['qty'] == 22
    assert intent['id'] == service.live.pending(id)[0]['id']         # clientOrderId == intent id
    assert s['phase'] == 'entering'

    orders.fill(oid, 22, 10020.0, commission=220.0)
    tick(service, monkeypatch, id, '09:00', 12)
    s = store.get(service.account_kind, id)
    pos = s['positions']['000500']
    assert pos['qty'] == 22 and pos['entry_price'] == 10020.0 and pos['cost'] == 22 * 10020.0 + 220.0
    assert pos['tp_price'] == pytest.approx(10020.0 * 1.10) and pos['sl_price'] == pytest.approx(10020.0 * 0.97)
    assert s['cash'] == 1_500_000 - 22 * 10020.0 - 220.0
    assert s['trades'][-1]['side'] == 'buy' and s['trades'][-1]['order_id'] == oid
    assert s['phase'] == 'monitoring' and not service.live.pending(id)
    # 9) 같은 조회를 다시 해도 두 번 반영되지 않는다
    tick(service, monkeypatch, id, '09:01')
    s = store.get(service.account_kind, id)
    assert s['positions']['000500']['qty'] == 22 and len([t for t in s['trades'] if t['side'] == 'buy']) == 1


# ── 4) 손절 → 매도 주문 → 체결 후 손익 ────────────────────────────────────────
def test_stop_loss_sends_sell_order_and_books_only_on_fill(svc, monkeypatch):
    service, store, orders, broker = svc
    id = make_live(service); service.control(id, 'start')
    tick(service, monkeypatch, id, '07:31'); tick(service, monkeypatch, id, '09:00', 5)
    orders.fill(orders.created[0][0], 22, 10020.0, commission=0.0)
    tick(service, monkeypatch, id, '09:00', 12)

    broker.price['000500'] = 9600.0            # -4% → 손절선(-3%) 아래
    tick(service, monkeypatch, id, '09:30')
    s = store.get(service.account_kind, id)
    assert s['positions']['000500']['closing']            # 매도 주문이 나갔다는 표시
    sells = [c for c in orders.created if c[1]['side'] == 'sell']
    assert len(sells) == 1 and sells[0][1]['qty'] == 22
    assert sells[0][1]['limit_price'] == dl.to_tick(9600 * .999 * (1 - .003), up=False)
    assert s['cash'] == 1_500_000 - 22 * 10020.0           # 아직 매도 대금 없음
    tick(service, monkeypatch, id, '09:30', 10)            # 같은 조건이어도 두 번째 매도 주문은 안 낸다
    assert len([c for c in orders.created if c[1]['side'] == 'sell']) == 1

    orders.fill(sells[0][0], 22, 9580.0, commission=210.0, tax=316.0)
    tick(service, monkeypatch, id, '09:31')
    s = store.get(service.account_kind, id)
    assert '000500' not in s['positions']
    pnl = 22 * 9580.0 - 210.0 - 316.0 - 22 * 10020.0
    assert s['realized_pnl'] == pytest.approx(pnl)
    assert s['cash'] == pytest.approx(1_500_000 - 22 * 10020.0 + 22 * 9580.0 - 526.0)
    assert '손절' in s['trades'][-1]['reason'] and s['trades'][-1]['pnl'] == pytest.approx(pnl)


# ── 5) 제한 시간 지나면 취소. 매도는 다시 시도 ────────────────────────────────
def test_unfilled_orders_are_cancelled_and_sells_retry(svc, monkeypatch):
    service, store, orders, broker = svc
    id = make_live(service); service.control(id, 'start')
    tick(service, monkeypatch, id, '07:31'); tick(service, monkeypatch, id, '09:00', 5)
    oid = orders.created[0][0]
    tick(service, monkeypatch, id, '09:04')                # 180초 초과 → 취소
    assert orders.cancelled == [oid]
    tick(service, monkeypatch, id, '09:04', 10)
    s = store.get(service.account_kind, id)
    assert s['positions'] == {} and not service.live.pending(id)
    assert service.live.journal(id)[0]['status'] == 'DONE' and service.live.journal(id)[0]['broker_status'] == 'CANCELED'
    assert s['phase'] == 'monitoring'

    # 매도 쪽: 채워져 있는 포지션을 강제 정리할 때 미체결이면 취소 → 다음 주기에 다시 주문
    orders.created.clear(); orders.cancelled.clear()
    s['positions']['000500'] = {'symbol': '000500', 'name': '가온전선', 'qty': 10, 'entry_price': 10000.0, 'cost': 100000.0,
                                'tp_pct': 10, 'sl_pct': 3, 'tp_price': 11000.0, 'sl_price': 9700.0,
                                'entry_at': at('09:05').isoformat(), 'hold_overnight': False}
    store.put(service.account_kind, id, s)
    orders.holdings_rows = [{'symbol': '000500', 'currency': 'KRW', 'quantity': '10'}]
    tick(service, monkeypatch, id, '15:21')
    assert len(orders.created) == 1 and orders.created[0][1]['side'] == 'sell'
    assert store.get(service.account_kind, id)['phase'] != 'closed'      # 체결 전엔 정리 완료가 아니다
    tick(service, monkeypatch, id, '15:25')                # 취소
    tick(service, monkeypatch, id, '15:25', 10)            # DONE 반영 → closing 해제
    tick(service, monkeypatch, id, '15:25', 20)            # 재주문
    assert len(orders.created) == 2 and orders.cancelled == [orders.created[0][0]]
    orders.fill(orders.created[1][0], 10, 9500.0)
    tick(service, monkeypatch, id, '15:26')
    s = store.get(service.account_kind, id)
    assert s['positions'] == {} and s['phase'] == 'closed' and '2026-09-11' in s['days']


# ── 6) 불명확한 주문은 사람이 처리한다 ─────────────────────────────────────────
def test_uncertain_order_blocks_new_buys_until_resolved(svc, monkeypatch):
    service, store, orders, broker = svc
    id = make_live(service); service.control(id, 'start')
    orders.fail_create = OrderUncertain('주문 응답 미확인')
    tick(service, monkeypatch, id, '07:31'); tick(service, monkeypatch, id, '09:00', 5)
    pend = service.live.pending(id)
    assert len(pend) == 1 and pend[0]['status'] == 'UNKNOWN' and pend[0]['broker_order_id'] is None
    s = store.get(service.account_kind, id)
    assert 'orders' in s['errors']
    # 그 상태로는 정지 후 재시작도 막힌다
    service.control(id, 'stop')
    with pytest.raises(ValueError, match='결과를 모르는 주문'):
        service.control(id, 'start')
    # 토스 앱에서 실제 주문 번호를 찾아 연결하면 체결이 반영된다
    orders.details['REAL1'] = {'orderId': 'REAL1', 'symbol': '000500', 'side': 'BUY', 'status': 'FILLED',
                               'execution': {'filledQuantity': 22, 'filledAmount': 22 * 10000.0}}
    service.bind_order(id, pend[0]['id'], 'REAL1')
    s = store.get(service.account_kind, id)
    assert s['positions']['000500']['qty'] == 22 and 'orders' not in s['errors']
    assert not service.live.pending(id)


def test_uncertain_order_can_be_discarded_after_user_checks(svc, monkeypatch):
    service, store, orders, broker = svc
    id = make_live(service); service.control(id, 'start')
    orders.fail_create = OrderUncertain('주문 응답 미확인')
    tick(service, monkeypatch, id, '07:31'); tick(service, monkeypatch, id, '09:00', 5)
    intent = service.live.pending(id)[0]
    service.discard_order(id, intent['id'])
    assert not service.live.pending(id) and store.get(service.account_kind, id)['positions'] == {}
    with pytest.raises(ValueError):
        service.discard_order(id, intent['id'])        # 두 번 폐기 안 됨


# ── 7) 보유량 불일치면 시작하지 않는다 · 정지하면 미체결 매수 취소 ───────────────
def test_start_requires_reconciled_holdings_and_stop_cancels_buys(svc, monkeypatch):
    service, store, orders, broker = svc
    id = make_live(service)
    orders.holdings_rows = [{'symbol': '005930', 'currency': 'KRW', 'quantity': '1'}]
    with pytest.raises(ValueError, match='보유량'):
        service.control(id, 'start')
    orders.holdings_rows = []
    orders.enabled = False
    with pytest.raises(ValueError, match='실거래가 꺼져'):
        service.control(id, 'start')
    orders.enabled = True
    service.control(id, 'start')
    tick(service, monkeypatch, id, '07:31'); tick(service, monkeypatch, id, '09:00', 5)
    assert service.live.pending(id, 'buy')
    service.control(id, 'stop')
    service.live_pulse(id)                              # 정지된 계좌도 남은 주문은 처리한다
    assert orders.cancelled == [orders.created[0][0]]


def test_live_and_watchlist_live_are_mutually_exclusive(svc):
    service, store, orders, broker = svc
    id = make_live(service)
    store.put('live_accounts', 'w1', {'id': 'w1', 'status': 'running', 'config': {'currency': 'KRW'}, 'positions': {}})
    with pytest.raises(ValueError, match='관심종목 실거래'):
        service.control(id, 'start')


def test_new_account_archives_old_one_of_same_mode(svc):
    service, store, orders, broker = svc
    a = service.create({'initial_cash': 10_000_000})['id']
    service.control(a, 'start')
    with pytest.raises(ValueError, match='정지'):
        service.create({'initial_cash': 10_000_000})
    service.control(a, 'stop')
    b = service.create({'initial_cash': 5_000_000})['id']
    assert store.get(service.account_kind, a)['archived'] is True
    assert service.current('paper')['id'] == b
    with pytest.raises(ValueError, match='보관'):
        service.control(a, 'start')


# ── 8) API: 문구 확인 없이는 실거래 계좌를 만들거나 시작할 수 없다 ────────────────
@pytest.fixture
def client(tmp_path, monkeypatch, env):
    monkeypatch.setenv('ADMIN_PASSWORD', 'pw')
    monkeypatch.setenv('ENABLE_LIVE_TRADING', 'true')
    monkeypatch.setenv('TOSS_ACCOUNT_SEQ', '12345')
    app = create_app(tmp_path / 't.sqlite3', background=False)
    fake = FakeOrders()
    app.state.daytrade.live.orders = fake
    with TestClient(app) as c:
        c.post('/api/login', json={'password': 'pw'})
        yield c, fake


def test_api_live_requires_typed_acknowledgement(client):
    c, fake = client
    r = c.post('/api/daytrade/accounts', json={'mode': 'live', 'initial_cash': 500000})
    assert r.status_code == 400 and '실거래' in r.json()['detail']
    r = c.post('/api/daytrade/accounts', json={'mode': 'live', 'initial_cash': 500000, 'acknowledge_live': '실거래'})
    assert r.status_code == 200, r.text
    id = r.json()['id']
    r = c.post(f'/api/daytrade/accounts/{id}/control', json={'action': 'start'})
    assert r.status_code == 400 and '실거래 시작' in r.json()['detail']
    r = c.post(f'/api/daytrade/accounts/{id}/control', json={'action': 'start', 'acknowledge_live': '실거래 시작'})
    assert r.status_code == 200 and r.json()['status'] == 'running'
    st = c.get('/api/daytrade/status').json()
    assert st['live']['id'] == id and st['paper'] is None and st['live_status']['available'] is True
    d = c.get('/api/dashboard').json()
    assert d['mode'] == 'live' and d['live_running'] is True and d['account']['id'] == id
    assert c.get('/api/dashboard?mode=paper').json()['account'] is None
    # 모의 계좌는 문구 없이 바로
    r = c.post('/api/daytrade/accounts', json={'initial_cash': 1000000})
    assert r.status_code == 200 and r.json()['mode'] == 'paper'
    assert c.get('/api/daytrade/status').json()['paper']['id'] == r.json()['id']


# ── 익일 보유: "보유 0" 은 계좌를 만들 때만 본다 ─────────────────────────────
def test_overnight_position_survives_and_reconciles_the_next_morning(svc, monkeypatch):
    """마감에 사서 다음날로 넘긴 실거래 포지션이 정지·재시작·새 거래일을 지나도 살아 있다."""
    service, store, orders, broker = svc
    service.ai.eod = {'summary': '내일 반등 기대', 'decisions': [], 'buys': [
        {'symbol': '000500', 'weight_pct': 15, 'take_profit_pct': 8, 'stop_loss_pct': 4,
         'reason': '악재 없이 시장 약세로 눌렸다. 수급은 유지'}]}
    id = make_live(service)
    service.control(id, 'start')
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '15:01')                  # 마감 판단 → 마감 매수 주문
    buy = next(c for c in orders.created if c[1]['side'] == 'buy')
    orders.fill(buy[0], buy[1]['qty'], 10000.0, commission=0.0)
    tick(service, monkeypatch, id, '15:02')                  # 체결 반영
    s = store.get(service.account_kind, id)
    assert s['positions']['000500']['hold_overnight'] is True

    tick(service, monkeypatch, id, '15:21')                  # 강제 정리에서 제외된다
    s = store.get(service.account_kind, id)
    assert '000500' in s['positions'] and not [c for c in orders.created if c[1]['side'] == 'sell']

    # 정지했다 다음날 아침 다시 시작해도 대사를 통과한다 (토스 보유 == 장부)
    service.control(id, 'stop')
    service.control(id, 'start')
    monkeypatch.setattr(dt, 'utcnow', lambda: at('07:31', day='2026-09-14'))
    service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['day'] == '2026-09-14' and '000500' in s['positions']
    assert s['positions']['000500']['hold_overnight'] is False   # 오늘 다시 판단받는다

    # 보유가 있으니 새 계좌는 만들 수 없다 - 그래서 "보유 0" 은 생성에만 적용된다
    with pytest.raises(ValueError, match='보유 주식'):
        service.create({'mode': 'live', 'initial_cash': 500_000})
