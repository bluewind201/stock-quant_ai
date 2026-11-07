"""당일 단타 서비스. 가짜 시세·가짜 AI 로, 시각을 주입해 하루를 돌린다.

지키려는 성질
  1) AI 가 후보 밖 종목을 내면 계획 전체를 버린다 (종목 코드 지어내기 차단)
  2) 익절·손절 폭이 설정 범위 밖이면 그 종목만 뺀다
  3) 괄호는 손절을 먼저 본다. 같은 주기에 둘 다 닿으면 손절
  4) 마감 정리 시각이 지나면 hold_overnight 가 아닌 보유분은 전부 청산된다
  5) 스캔은 scan_at 이후에만, 진입은 entry_at 부터 entry_window 안에서만
  6) AI 일일 한도에 닿으면 판단을 건너뛰고 괄호 감시는 계속한다
"""
from datetime import datetime, timezone, timedelta

import pytest

from sqt import daytrade as dt
from sqt.store import Store

KST = timezone(timedelta(hours=9))
DAY = '2026-09-11'          # 금요일, KRX 거래일


def at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(':'))
    return datetime(2026, 9, 11, h, m, tzinfo=KST).astimezone(timezone.utc)


class FakeBroker:
    def __init__(self):
        self.prices = {'000500': 10000.0, '006260': 50000.0, '096770': 120000.0}

    def quotes(self, instruments):
        out = {}
        for i in instruments:
            p = self.prices.get(i.symbol)
            if p is None:
                continue
            out[i.symbol] = {'symbol': i.symbol, 'currency': 'KRW', 'exchange': 'KRX', 'price': p,
                             'bid': p * 0.999, 'ask': p * 1.001, 'bid_qty': 100, 'ask_qty': 100,
                             'spread_bps': 20.0, 'timestamp': dt.utcnow().isoformat(),
                             'book_timestamp': dt.utcnow().isoformat(), 'received_at': dt.utcnow().isoformat()}
        return out


class FakeAI:
    """호출을 기록하고 미리 정한 답을 준다. lock 속성은 서비스가 검사한다."""
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.last_usage = {'cost_usd': 0.01}
        self.calls = []
        self.plan = {'summary': '테스트 계획', 'market_view': '보통', 'market_stance': 'normal',
                     'picks': [{'symbol': '000500', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
                                'thesis': '외국인 순매수와 신규 수주 기사 확인', 'counter_case': '전일 급등이라 되돌림 가능',
                                'invalidation': '시가 대비 -2% 이탈'}]}
        self.eod = {'summary': '전부 정리', 'decisions': [], 'buys': []}
        self.event = {'summary': '유지', 'decisions': []}
        self.review = {'summary': '오늘 복기', 'diagnosis': '표본이 얇다', 'lessons': [], 'adjustments': []}

    def status(self):
        return {'installed': True, 'authenticated': True, 'api_key_used': False}

    def plan_day(self, snapshot):
        self.calls.append(('plan', snapshot)); return self.plan

    def judge_event(self, snapshot):
        self.calls.append(('event', snapshot)); return self.event

    def judge_eod(self, snapshot):
        self.calls.append(('eod', snapshot)); return self.eod

    def review_day(self, snapshot):
        self.calls.append(('review', snapshot)); return self.review


SCAN = {'scanned_at': '', 'pool_size': 3, 'enriched': 3, 'rejected': [], 'note': 'n',
        'candidates': [{'symbol': '000500', 'name': '가온전선', 'score': 50, 'origins': ['theme'], 'themes': [], 'news': []},
                       {'symbol': '006260', 'name': 'LS', 'score': 40, 'origins': ['theme'], 'themes': [], 'news': []}]}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    store = Store(str(tmp_path / 'dt.sqlite3'))
    broker = FakeBroker()
    ai = FakeAI()
    service = dt.DayTradeService(store, broker, ai=ai)
    monkeypatch.setattr(dt.scanner, 'premarket_scan', lambda b, cfg=None: dict(SCAN))
    monkeypatch.setattr(dt.krnews, 'stock_news', lambda *a, **k: [])
    monkeypatch.setattr(dt.krnews, 'market_news', lambda *a, **k: [
        {'title': '코스피 7000 재돌파 시도', 'source': '연합', 'published_at': '2026-09-10T22:00:00+00:00', 'url': 'https://x', 'topic': 'market'}])
    # AI 는 동기로 돌려 테스트가 결정적이게 한다
    def sync_call(state, kind, fn, snapshot, on_done, fail_phase=None, counts=True):
        state['ai']['pending'] = False
        state['ai']['calls_today' if counts else 'anchor_calls_today'] += 1
        try:
            result = fn(snapshot)
        except Exception:
            if fail_phase:
                state['phase'] = fail_phase
            raise
        on_done(state, result, dt.utcnow())
    monkeypatch.setattr(service, '_ai_call', sync_call)
    return service, store, broker, ai


def clock(monkeypatch, when: datetime):
    monkeypatch.setattr(dt, 'utcnow', lambda: when)


def make(service, **overrides):
    cfg = {'initial_cash': 10_000_000, 'max_positions': 5, 'position_pct': 15}
    cfg.update(overrides)
    state = service.create(cfg)
    service.control(state['id'], 'start')
    return state['id']


def test_schedule_scan_then_plan_then_entry(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)

    clock(monkeypatch, at('07:00'))       # 스캔 시각 전
    service.tick(id)
    assert store.get(service.account_kind, id)['phase'] == 'idle'
    assert ai.calls == []

    clock(monkeypatch, at('07:31'))       # 스캔 → AI 계획
    service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['phase'] == 'planned'
    assert [c[0] for c in ai.calls] == ['plan']
    assert [p['symbol'] for p in s['plan']['picks']] == ['000500']
    assert s['positions'] == {}            # 아직 진입 전

    clock(monkeypatch, at('08:30'))       # 진입 창 전
    service.tick(id)
    assert store.get(service.account_kind, id)['positions'] == {}

    clock(monkeypatch, at('09:01'))       # 진입
    service.tick(id)
    s = store.get(service.account_kind, id)
    assert '000500' in s['positions']
    pos = s['positions']['000500']
    assert pos['tp_pct'] == 10 and pos['sl_pct'] == 3
    assert pos['tp_price'] == pytest.approx(pos['entry_price'] * 1.10)
    assert pos['sl_price'] == pytest.approx(pos['entry_price'] * 0.97)
    # 15% 비중 안에서 정수 주로 샀다
    assert pos['cost'] <= 10_000_000 * 0.15 + 1


def test_plan_with_outside_symbol_is_discarded(svc, monkeypatch):
    service, store, broker, ai = svc
    ai.plan['picks'].append({'symbol': '005930', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
                             'thesis': '삼성전자 살래요 진짜로요', 'counter_case': '후보에 없음 그래도요',
                             'invalidation': '없음'})
    id = make(service)
    clock(monkeypatch, at('07:31'))
    service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['plan'] is None or s['plan'].get('picks') in (None, [])
    assert s['phase'] == 'monitoring'
    assert any('후보 밖' in row['message'] for row in s['log'])


def test_out_of_range_bracket_drops_only_that_pick(svc, monkeypatch):
    service, store, broker, ai = svc
    ai.plan['picks'] = [
        {'symbol': '000500', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
         'thesis': '정상 범위 종목입니다', 'counter_case': '반대 근거 있습니다', 'invalidation': '조건'},
        {'symbol': '006260', 'take_profit_pct': 40, 'stop_loss_pct': 3, 'weight_pct': 15,   # 익절 40% 는 범위 밖
         'thesis': '범위 밖 종목입니다요', 'counter_case': '반대 근거 있습니다', 'invalidation': '조건'},
    ]
    id = make(service)
    clock(monkeypatch, at('07:31'))
    service.tick(id)
    s = store.get(service.account_kind, id)
    assert [p['symbol'] for p in s['plan']['picks']] == ['000500']
    assert any('범위 밖' in row['message'] for row in s['log'])


def test_bracket_stop_loss_checked_before_take_profit(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    entry = store.get(service.account_kind, id)['positions']['000500']['entry_price']

    # 익절선 위로 올라가면 익절
    broker.prices['000500'] = entry * 1.12
    clock(monkeypatch, at('09:30')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert '000500' not in s['positions']
    assert s['trades'][-1]['side'] == 'sell' and '익절' in s['trades'][-1]['reason']
    assert s['trades'][-1]['pnl'] > 0


def test_stop_loss_fires_and_realizes_loss(svc, monkeypatch):
    service, store, broker, ai = svc
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    entry = store.get(service.account_kind, id)['positions']['000500']['entry_price']

    broker.prices['000500'] = entry * 0.95
    clock(monkeypatch, at('10:00')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert '000500' not in s['positions']
    assert '손절' in s['trades'][-1]['reason'] and s['trades'][-1]['pnl'] < 0
    assert s['realized_pnl'] < 0


def test_force_close_at_eod_unless_held_overnight(svc, monkeypatch):
    service, store, broker, ai = svc
    ai.plan['picks'] = [
        {'symbol': '000500', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
         'thesis': '첫 번째 종목입니다요', 'counter_case': '반대 근거 있습니다', 'invalidation': '조건'},
        {'symbol': '006260', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
         'thesis': '두 번째 종목입니다요', 'counter_case': '반대 근거 있습니다', 'invalidation': '조건'},
    ]
    ai.eod = {'summary': '하나만 유지', 'decisions': [
        {'symbol': '006260', 'action': 'hold_overnight', 'reason': '마감 강세에 내일 실적 발표'},
        {'symbol': '000500', 'action': 'close', 'reason': '일회성 이슈'}]}
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    assert set(store.get(service.account_kind, id)['positions']) == {'000500', '006260'}

    clock(monkeypatch, at('15:01')); service.tick(id)     # 마감 판단
    s = store.get(service.account_kind, id)
    assert s['positions']['006260']['hold_overnight'] is True
    assert s['positions']['000500']['hold_overnight'] is False

    clock(monkeypatch, at('15:21')); service.tick(id)     # 강제 청산
    s = store.get(service.account_kind, id)
    assert set(s['positions']) == {'006260'}
    assert s['phase'] == 'closed'
    assert [c[0] for c in ai.calls] == ['plan', 'eod', 'review']


def test_ai_budget_caps_events_but_never_the_scheduled_judgments(svc, monkeypatch):
    """한도는 장중 이벤트를 막으라고 둔 것이지, 하루의 뼈대(장전·마감·복기)를 거르라고 둔 게 아니다."""
    service, store, broker, ai = svc
    id = make(service, max_ai_calls_per_day=2, news_trigger=False)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['ai']['calls_today'] == 0 and s['ai']['anchor_calls_today'] == 1   # 장전은 한도 밖
    s['ai']['calls_today'] = 2                              # 이벤트 한도 소진 상태로
    store.put(service.account_kind, id, s)

    # 급등 이벤트는 막힌다
    broker.prices['006260'] = broker.prices['006260'] * 1.10
    clock(monkeypatch, at('10:00')); service.tick(id)
    assert [c[0] for c in ai.calls] == ['plan']

    # 그래도 손절은 걸리고, 마감 판단과 복기는 그대로 돈다
    entry = store.get(service.account_kind, id)['positions']['000500']['entry_price']
    broker.prices['000500'] = entry * 0.95
    clock(monkeypatch, at('15:01')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert '000500' not in s['positions'] and '손절' in s['trades'][-1]['reason']
    clock(monkeypatch, at('15:21')); service.tick(id)
    assert [c[0] for c in ai.calls] == ['plan', 'eod', 'review']


def test_new_day_resets_plan_but_keeps_overnight_position(svc, monkeypatch):
    service, store, broker, ai = svc
    ai.eod = {'summary': '유지', 'decisions': [{'symbol': '000500', 'action': 'hold_overnight', 'reason': '내일 실적 발표 예정'}]}
    id = make(service)
    clock(monkeypatch, at('07:31')); service.tick(id)
    clock(monkeypatch, at('09:01')); service.tick(id)
    clock(monkeypatch, at('15:01')); service.tick(id)
    clock(monkeypatch, at('15:21')); service.tick(id)
    assert '000500' in store.get(service.account_kind, id)['positions']

    # 다음 거래일(월요일) 아침
    monday = datetime(2026, 9, 14, 7, 0, tzinfo=KST).astimezone(timezone.utc)
    clock(monkeypatch, monday); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['day'] == '2026-09-14' and s['phase'] == 'idle' and s['plan'] is None
    assert '000500' in s['positions']
    assert s['positions']['000500']['hold_overnight'] is False   # 오늘 다시 판단받아야 한다
