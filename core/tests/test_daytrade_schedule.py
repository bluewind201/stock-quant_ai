"""장전 판단이 실패해도 다시 시도하고, 안 사는 날을 허용하고, 마감에 복기한다.

실제로 겪은 일 (2026-09-12): 07:30 장전 판단이 실패했는데 재시도가 없어 하루가 통째로 비었다.
그리고 08:00 에 수동 실행한 것이 화면에는 07:30 에 한 것처럼 보였다 - 예정 시각만 찍었기 때문이다.

지키려는 성질
  1) 스캔이 실패하면 retry_minutes 뒤에 다시 시도한다 (max_scan_attempts 까지)
  2) AI 판단이 실패하면 스캔을 다시 돌리지 않고 판단만 다시 시도한다
  3) 실제로 몇 시에 했는지 stamps 에 남는다 (예정 시각이 아니라)
  4) 장전·마감·복기 판단은 하루 이벤트 한도에 치지 않는다
  5) market_stance='no_trade' 면 오전에 아무것도 사지 않는다
  6) 그런 날도 마감 판단은 부르고, 거기서 익일 보유 목적으로 살 수 있다
  7) 마감 복기가 제안한 설정 조정은 허용 항목·하루 변동 한도 안에서만 반영된다
"""
from datetime import datetime, timedelta, timezone

import pytest

from sqt import daytrade as dt
from sqt.store import Store

KST = timezone(timedelta(hours=9))


def at(hhmm: str, day: str = '2026-09-11') -> datetime:
    h, m = map(int, hhmm.split(':'))
    return datetime.fromisoformat(day).replace(hour=h, minute=m, tzinfo=KST).astimezone(timezone.utc)


CANDIDATES = [
    {'symbol': '000500', 'name': '가온전선', 'score': 50, 'origins': ['theme'], 'themes': [], 'news': [],
     'last_price': 10000.0, 'prev_change_pct': 1.0},
    {'symbol': '006260', 'name': 'LS', 'score': 40, 'origins': ['theme'], 'themes': [], 'news': [],
     'last_price': 20000.0, 'prev_change_pct': 2.0},
]


class Broker:
    def __init__(self):
        self.prices = {'000500': 10000.0, '006260': 20000.0}

    def quotes(self, instruments):
        return {i.symbol: {'symbol': i.symbol, 'bid': self.prices[i.symbol] * .999,
                           'ask': self.prices[i.symbol] * 1.001, 'price': self.prices[i.symbol], 'timestamp': dt.utcnow().isoformat(), 'book_timestamp': dt.utcnow().isoformat()}
                for i in instruments if i.symbol in self.prices}


class AI:
    def __init__(self):
        import threading
        self.lock = threading.Lock()
        self.last_usage = {}
        self.calls = []
        self.plan_error = 0
        self.plan = {'summary': '평범한 날', 'market_view': '보통', 'market_stance': 'normal',
                     'picks': [{'symbol': '000500', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
                                'thesis': '수급과 기사 확인했다', 'counter_case': '되돌림 가능성 있다',
                                'invalidation': '시가 -2%'}]}
        self.eod = {'summary': '정리', 'decisions': [], 'buys': []}
        self.event = {'summary': '유지', 'decisions': []}
        self.review = {'summary': '복기', 'diagnosis': '표본 부족', 'lessons': [], 'adjustments': []}

    def status(self):
        return {'installed': True, 'authenticated': True}

    def plan_day(self, snapshot):
        self.calls.append(('plan', snapshot))
        if self.plan_error > 0:
            self.plan_error -= 1
            raise dt.AIError('알 수 없는 오류')
        return self.plan

    def judge_event(self, snapshot):
        self.calls.append(('event', snapshot)); return self.event

    def judge_eod(self, snapshot):
        self.calls.append(('eod', snapshot)); return self.eod

    def review_day(self, snapshot):
        self.calls.append(('review', snapshot)); return self.review


@pytest.fixture
def svc(tmp_path, monkeypatch):
    fail = {'n': 0}
    def scan(broker, cfg=None):
        if fail['n'] > 0:
            fail['n'] -= 1
            raise RuntimeError('토스 랭킹 조회 실패')
        return {'scanned_at': '', 'pool_size': 3, 'enriched': 3, 'rejected': [], 'note': 'n',
                'candidates': [dict(c) for c in CANDIDATES]}
    monkeypatch.setattr(dt.scanner, 'premarket_scan', scan)
    monkeypatch.setattr(dt.krnews, 'stock_news', lambda *a, **k: [])
    monkeypatch.setattr(dt.krnews, 'market_news', lambda *a, **k: [])
    ai = AI()
    broker = Broker()
    store = Store(str(tmp_path / 's.sqlite3'))
    service = dt.DayTradeService(store, broker, ai=ai)

    def sync(state, kind, fn, snapshot, on_done, fail_phase=None, counts=True):
        state['ai']['calls_today' if counts else 'anchor_calls_today'] += 1
        try:
            result = fn(snapshot)
        except dt.AIError as exc:
            state['ai']['error'] = str(exc)
            if fail_phase:
                state['phase'] = fail_phase
            return
        on_done(state, result, dt.utcnow())
    monkeypatch.setattr(service, '_ai_call', sync)
    return service, store, broker, ai, fail


def make(service, **overrides):
    cfg = {'initial_cash': 10_000_000}
    cfg.update(overrides)
    id = service.create(cfg)['id']
    service.control(id, 'start')
    return id


def tick(service, monkeypatch, id, hhmm, day='2026-09-11'):
    monkeypatch.setattr(dt, 'utcnow', lambda: at(hhmm, day)); service.tick(id)


# ── 1) 스캔 실패 → 재시도 ─────────────────────────────────────────────────
def test_scan_failure_is_retried_not_abandoned_for_the_day(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    id = make(service, retry_minutes=3)
    fail['n'] = 2                                   # 07:30, 07:33 실패

    tick(service, monkeypatch, id, '07:30')
    s = store.get(service.account_kind, id)
    assert s['scan'] is None and s['retry']['attempts'] == 1
    assert '스캔 실패' in s['errors'].get('scan', '')

    tick(service, monkeypatch, id, '07:31')          # 쿨다운 안 - 시도하지 않는다
    assert store.get(service.account_kind, id)['retry']['attempts'] == 1

    tick(service, monkeypatch, id, '07:33')          # 두 번째 실패
    assert store.get(service.account_kind, id)['retry']['attempts'] == 2
    tick(service, monkeypatch, id, '07:36')          # 세 번째에 성공
    s = store.get(service.account_kind, id)
    assert s['phase'] == 'planned' and len(s['plan']['picks']) == 1
    assert 'scan' not in s['errors']
    assert [c[0] for c in ai.calls] == ['plan']


def test_scan_gives_up_after_max_attempts_and_says_so(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    id = make(service, retry_minutes=1, max_scan_attempts=2)
    fail['n'] = 9
    for minute in ('07:30', '07:31', '07:32', '07:33'):
        tick(service, monkeypatch, id, minute)
    s = store.get(service.account_kind, id)
    assert s['retry']['attempts'] == 2
    assert '2번 시도' in s['errors']['scan_giveup']


# ── 2) AI 판단 실패 → 스캔은 그대로, 판단만 재시도 ─────────────────────────
def test_failed_ai_judgment_retries_without_rescanning(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    id = make(service, retry_minutes=3)
    ai.plan_error = 1

    tick(service, monkeypatch, id, '07:30')
    s = store.get(service.account_kind, id)
    assert s['phase'] == 'scanned' and s['plan'] is None      # 예전엔 'planning' 에 갇혔다
    assert s['scan']['candidates']

    tick(service, monkeypatch, id, '07:33')
    s = store.get(service.account_kind, id)
    assert s['phase'] == 'planned' and s['plan']['picks']
    assert [c[0] for c in ai.calls] == ['plan', 'plan']       # 판단만 두 번, 스캔은 한 번


def test_late_manual_scan_records_the_real_time_not_the_scheduled_one(svc, monkeypatch):
    """08:00 에 수동으로 돌렸으면 08:00 이라고 남아야 한다."""
    service, store, broker, ai, fail = svc
    id = make(service)
    fail['n'] = 99                                   # 07:30 정시 실행은 실패시킨다
    tick(service, monkeypatch, id, '07:30')
    fail['n'] = 0
    service.control(id, 'scan_now')
    tick(service, monkeypatch, id, '08:00')
    s = store.get(service.account_kind, id)
    assert s['stamps']['scan'] == at('08:00').isoformat()
    assert s['stamps']['plan'] == at('08:00').isoformat()
    assert s['phase'] == 'planned'

    tick(service, monkeypatch, id, '09:01')
    s = store.get(service.account_kind, id)
    assert s['stamps']['entry'] == at('09:01').isoformat()


def test_manual_scan_after_entry_window_still_runs(svc, monkeypatch):
    """수동 요청은 진입 창이 지나도 돈다. 자동은 그때 돌지 않는다."""
    service, store, broker, ai, fail = svc
    id = make(service)
    fail['n'] = 99
    tick(service, monkeypatch, id, '07:30')
    fail['n'] = 0
    tick(service, monkeypatch, id, '11:00')          # 자동 재시도: 스캔만 하고 판단은 생략
    s = store.get(service.account_kind, id)
    assert s['phase'] == 'monitoring' and s['plan'] is None
    service.control(id, 'scan_now')
    tick(service, monkeypatch, id, '11:05')
    assert store.get(service.account_kind, id)['plan'] is not None


# ── 3) 안 사는 날 ─────────────────────────────────────────────────────────
def test_no_trade_stance_buys_nothing_in_the_morning(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    ai.plan = {'summary': '오늘은 쉰다', 'market_view': '미국 급락', 'market_stance': 'no_trade',
               'no_trade_reason': '간밤 나스닥 -3%, 외국인 순매도 지속', 'picks': []}
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    s = store.get(service.account_kind, id)
    assert s['plan']['market_stance'] == 'no_trade' and s['plan']['picks'] == []
    assert s['phase'] == 'monitoring'

    tick(service, monkeypatch, id, '09:01')
    assert store.get(service.account_kind, id)['positions'] == {}


def test_close_entry_buys_for_the_bounce_even_after_a_no_trade_morning(svc, monkeypatch):
    """오전엔 안 샀지만, 오늘 크게 눌린 종목을 마감 무렵 사서 내일로 들고 간다."""
    service, store, broker, ai, fail = svc
    ai.plan = {'summary': '오늘은 쉰다', 'market_view': '약세', 'market_stance': 'no_trade',
               'no_trade_reason': '지수 급락 예상', 'picks': []}
    ai.eod = {'summary': '과매도 반등 노림', 'decisions': [],
              'buys': [{'symbol': '000500', 'weight_pct': 15, 'take_profit_pct': 8, 'stop_loss_pct': 4,
                        'reason': '악재 없이 시장 약세로 -7%. 외국인 순매수 유지'}]}
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '09:01')
    broker.prices['000500'] = 9300.0                 # 오늘 -7%
    tick(service, monkeypatch, id, '15:01')          # 마감 판단
    s = store.get(service.account_kind, id)
    pos = s['positions']['000500']
    assert pos['hold_overnight'] is True and pos['source'] == 'close'
    assert s['stamps']['close_entry']
    # 마감 판단 스냅샷에 "오늘 얼마나 눌렸나" 가 들어간다
    eod_snapshot = next(c[1] for c in ai.calls if c[0] == 'eod')
    assert eod_snapshot['open_slots'] >= 1
    assert eod_snapshot['candidates'][0]['today_change_pct'] < -6
    assert eod_snapshot['market_stance_this_morning'] == 'no_trade'

    tick(service, monkeypatch, id, '15:21')          # 강제 정리에도 살아남는다
    s = store.get(service.account_kind, id)
    assert '000500' in s['positions'] and s['phase'] == 'closed'


def test_close_entry_rejects_symbols_outside_the_candidate_list_and_ranges(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    ai.plan = {'summary': '쉰다', 'market_view': '약세', 'market_stance': 'no_trade',
               'no_trade_reason': '약세', 'picks': []}
    ai.eod = {'summary': '매수', 'decisions': [], 'buys': [
        {'symbol': '999999', 'weight_pct': 15, 'take_profit_pct': 8, 'stop_loss_pct': 4, 'reason': '후보 밖 종목이다'},
        {'symbol': '006260', 'weight_pct': 15, 'take_profit_pct': 40, 'stop_loss_pct': 4, 'reason': '익절이 범위 밖이다'}]}
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '15:01')
    assert store.get(service.account_kind, id)['positions'] == {}


# ── 4) 마감 복기와 자동 조정 ───────────────────────────────────────────────
def test_review_runs_after_close_and_tunes_within_limits(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    ai.review = {'summary': '손절이 너무 촘촘했다', 'diagnosis': '5건 중 4건이 손절 직후 반등',
                 'lessons': ['손절 직후 반등이 반복되면 손절 폭을 넓힌다', '수급 없는 급등주는 사지 않는다'],
                 'adjustments': [
                     {'key': 'stop_loss_min_pct', 'value': 9, 'reason': '손절이 너무 촘촘하다'},   # 한도 초과 → 1.0 만
                     {'key': 'require_flow', 'value': 1, 'reason': '수급 없는 종목이 문제였다'},
                     {'key': 'initial_cash', 'value': 1, 'reason': '허용되지 않은 항목'}]}
    id = make(service, auto_tune=True)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '09:01')
    tick(service, monkeypatch, id, '15:01')
    tick(service, monkeypatch, id, '15:21')
    s = store.get(service.account_kind, id)
    assert s['config']['stop_loss_min_pct'] == 3.0            # 2.0 + 하루 한도 1.0
    assert s['config']['require_flow'] is True
    assert s['config']['initial_cash'] == 10_000_000          # 손대지 못한다
    applied = {a['key'] for a in s['tuning']['last_review']['applied']}
    assert applied == {'stop_loss_min_pct', 'require_flow'}
    assert any('조정할 수 없는' in x['reason'] for x in s['tuning']['last_review']['skipped'])
    assert s['tuning']['lessons'][0].startswith('손절 직후')
    assert s['stamps']['review']

    # Unverified one-day lessons stay proposals; numeric tuning remains a separate opt-in.
    tick(service, monkeypatch, id, '07:31', day='2026-09-14')
    plan_snapshot = [c[1] for c in ai.calls if c[0] == 'plan'][-1]
    assert plan_snapshot['lessons'] == []
    assert all(p['status']=='needs_evidence' for p in s['learning']['proposals'])


def test_review_tuning_is_all_or_nothing_when_validation_fails(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    ai.review = {'summary': '비중을 키우자', 'diagnosis': '표본 부족',
                 'lessons': [], 'adjustments': [
                     {'key': 'max_positions', 'value': 12, 'reason': '더 담자'},
                     {'key': 'position_pct', 'value': 30, 'reason': '더 크게'}]}
    id = make(service, max_positions=5, position_pct=15, auto_tune=True)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '15:01')
    tick(service, monkeypatch, id, '15:21')
    s = store.get(service.account_kind, id)
    # 6종 x 18% = 108% > 100% 라 전체 취소
    assert s['config']['max_positions'] == 5 and s['config']['position_pct'] == 15
    assert s['tuning']['last_review']['applied'] == []
    assert any('취소' in x['reason'] for x in s['tuning']['last_review']['skipped'])


def test_auto_tune_off_records_the_proposal_without_applying_it(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    ai.review = {'summary': '조정 제안', 'diagnosis': '근거 있음', 'lessons': ['교훈 하나'],
                 'adjustments': [{'key': 'surge_trigger_pct', 'value': 6, 'reason': '급등 기준을 올리자'}]}
    id = make(service, auto_tune=False, surge_trigger_pct=4)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '15:01')
    tick(service, monkeypatch, id, '15:21')
    s = store.get(service.account_kind, id)
    assert s['config']['surge_trigger_pct'] == 4
    assert s['tuning']['last_review']['proposed'][0]['key'] == 'surge_trigger_pct'
    assert s['tuning']['lessons'] == ['교훈 하나']


# ── 8) 설정은 운용 중에도 바꿀 수 있다 (자금·모드는 못 바꾼다) ─────────────────
def test_config_can_be_edited_while_running_except_money_and_mode(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    id = make(service, require_flow=False, require_news=False, max_ai_calls_per_day=8)
    tick(service, monkeypatch, id, '07:31')
    view = service.update_config(id, {'require_flow': True, 'require_news': True,
                                      'max_ai_calls_per_day': 4, 'close_entry': False,
                                      'auto_tune': False, 'surge_trigger_pct': 6,
                                      'initial_cash': 999, 'mode': 'live', 'currency': 'KRW'})
    cfg = view['config']
    assert cfg['require_flow'] is True and cfg['require_news'] is True
    assert cfg['max_ai_calls_per_day'] == 4 and cfg['close_entry'] is False and cfg['auto_tune'] is False
    assert cfg['surge_trigger_pct'] == 6
    assert cfg['initial_cash'] == 10_000_000 and cfg['mode'] == 'paper'   # 손대지 못한다
    assert '설정 변경' in store.get(service.account_kind, id)['log'][-1]['message']
    # 보내지 않은 항목은 그대로 둔다
    assert cfg['scan_at'] == '07:30' and cfg['position_pct'] == 15
    # 이미 잡힌 보유 종목의 괄호는 움직이지 않는다
    tick(service, monkeypatch, id, '09:01')
    pos = store.get(service.account_kind, id)['positions']['000500']
    service.update_config(id, {'take_profit_min_pct': 12, 'take_profit_max_pct': 20})
    assert store.get(service.account_kind, id)['positions']['000500']['tp_price'] == pos['tp_price']


def test_invalid_config_change_is_rejected_whole(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    id = make(service, max_positions=5, position_pct=15)
    with pytest.raises(ValueError):
        service.update_config(id, {'max_positions': 12, 'position_pct': 30})   # 360% > 100%
    cfg = store.get(service.account_kind, id)['config']
    assert cfg['max_positions'] == 5 and cfg['position_pct'] == 15


# ── 9) 사람이 원할 때 복기를 다시 돌린다 ────────────────────────────────────
def test_manual_review_runs_even_on_a_holiday_and_while_stopped(svc, monkeypatch):
    """휴장일이든 운용 정지든, 버튼을 누르면 마지막 거래일 자료로 복기한다."""
    service, store, broker, ai, fail = svc
    ai.review = {'summary': '다시 본 결과', 'diagnosis': '같은 결론', 'lessons': ['교훈 갱신'], 'adjustments': []}
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '09:01')
    tick(service, monkeypatch, id, '15:01')
    tick(service, monkeypatch, id, '15:21')            # 자동 복기 1회
    assert [c[0] for c in ai.calls].count('review') == 1
    snapshot = store.get(service.account_kind, id)['tuning']['pending_snapshot']
    assert snapshot['day'] == '2026-09-11' and snapshot['trades']

    # 주말로 넘어가 계획·스캔이 지워지고 운용도 멈춘 상태
    monkeypatch.setattr(dt, 'utcnow', lambda: at('10:00', '2026-09-12')); service.tick(id)
    service.control(id, 'stop')
    s = store.get(service.account_kind, id)
    assert s['plan'] is None and s['phase'] == 'holiday'

    service.control(id, 'review_now')
    assert [c[0] for c in ai.calls].count('review') == 2
    used = [c[1] for c in ai.calls if c[0] == 'review'][-1]
    assert used['day'] == '2026-09-11' and used['trades']      # 마지막 거래일 자료를 그대로 쓴다
    s = store.get(service.account_kind, id)
    assert s['tuning']['lessons'] == ['교훈 갱신']
    assert s['tuning']['review_day'] == '2026-09-11'
    assert any('마감 복기 요청 (수동)' in l['message'] for l in s['log'])


def test_manual_review_is_refused_while_another_judgment_is_running(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    s = store.get(service.account_kind, id)
    s['ai']['pending'] = True
    s['ai']['started_at'] = at('07:31').isoformat()
    store.put(service.account_kind, id, s)
    monkeypatch.setattr(dt, 'utcnow', lambda: at('07:32'))
    with pytest.raises(ValueError, match='진행 중'):
        service.control(id, 'review_now')


def test_manual_review_without_any_trading_day_says_so(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    id = make(service)
    with pytest.raises(ValueError, match='거래일 기록이 없습니다'):
        service.control(id, 'review_now')


# ── 10) 장 시간 밖에서는 아무것도 사고팔지 않는다 ───────────────────────────
CALENDAR = {'today': {'date': '2026-09-11', 'integrated': {
    'preMarket': {'startTime': '2026-09-11T08:00:00.000+09:00', 'endTime': '2026-09-11T09:00:00.000+09:00'},
    'regularMarket': {'startTime': '2026-09-11T09:00:00.000+09:00', 'endTime': '2026-09-11T15:30:00.000+09:00'},
    'afterMarket': {'startTime': '2026-09-11T15:30:00.000+09:00', 'endTime': '2026-09-11T20:00:00.000+09:00'}}}}


def test_session_hours_come_from_the_broker_calendar(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    broker.market_calendar_kr = lambda: CALENDAR
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    s = store.get(service.account_kind, id)['sessions']
    assert s == {'day': '2026-09-11', 'source': 'toss', 'open': '08:00',
                 'regular_open': '09:00', 'regular_close': '15:30', 'close': '20:00'}


def test_session_hours_fall_back_to_config_when_the_calendar_fails(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    def boom(): raise RuntimeError('calendar down')
    broker.market_calendar_kr = boom
    id = make(service, session_open='08:00', session_close='20:00')
    tick(service, monkeypatch, id, '07:31')
    s = store.get(service.account_kind, id)['sessions']
    assert s['source'] == 'default' and s['open'] == '08:00' and s['close'] == '20:00'


def test_manual_scan_at_night_never_cascades_into_a_close_buy(svc, monkeypatch):
    """실제로 겪은 일: 22:58 에 수동 스캔을 눌렀더니 '마감 매수' 가 종가로 나갔다."""
    service, store, broker, ai, fail = svc
    broker.market_calendar_kr = lambda: CALENDAR
    ai.eod = {'summary': '사자', 'decisions': [], 'buys': [
        {'symbol': '000500', 'weight_pct': 15, 'take_profit_pct': 8, 'stop_loss_pct': 4,
         'reason': '오늘 크게 눌렸으니 내일 반등'}]}
    id = make(service)
    monkeypatch.setattr(dt, 'utcnow', lambda: at('22:58'))
    service.control(id, 'scan_now')
    tick(service, monkeypatch, id, '22:58')
    s = store.get(service.account_kind, id)
    assert s['scan'] is None                           # 야간 재계획으로 오전 기록을 덮어쓰지 않는다
    assert s['positions'] == {}                         # 그러나 아무것도 사지 않는다
    assert 'eod' not in [c[0] for c in ai.calls]
    assert s['phase'] == 'closed'                       # 장이 끝난 날이므로 그대로 마감 처리


def test_close_entry_is_cancelled_if_the_market_shuts_while_ai_thinks(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    broker.market_calendar_kr = lambda: CALENDAR
    ai.eod = {'summary': '사자', 'decisions': [], 'buys': [
        {'symbol': '000500', 'weight_pct': 15, 'take_profit_pct': 8, 'stop_loss_pct': 4,
         'reason': '오늘 크게 눌렸으니 내일 반등'}]}
    id = make(service, eod_review_at='19:40', force_close_at='19:50', session_close='20:00')
    tick(service, monkeypatch, id, '07:31')
    # 마감 판단은 장중에 띄웠지만, 결과가 돌아온 순간에는 이미 장이 닫혔다
    state = store.get(service.account_kind, id)
    state['quotes'] = {'000500': {'bid': 9990.0, 'ask': 10010.0, 'price': 10000.0}}
    monkeypatch.setattr(dt, 'utcnow', lambda: at('20:05'))
    service._apply_eod(state, ai.eod, at('20:05'))
    assert state['positions'] == {}
    assert any('장이 닫혔' in l['message'] for l in state['log'])


def test_positions_are_not_sold_after_the_market_closed(svc, monkeypatch):
    """NAS 가 꺼져 있다 늦게 켜진 경우. 종가로 파는 시늉을 하지 않는다."""
    service, store, broker, ai, fail = svc
    broker.market_calendar_kr = lambda: CALENDAR
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '09:01')
    assert store.get(service.account_kind, id)['positions']
    tick(service, monkeypatch, id, '21:00')             # 장 종료 후 첫 tick
    s = store.get(service.account_kind, id)
    assert '000500' in s['positions']                   # 팔지 않았다
    assert s['phase'] == 'closed'
    assert any('정리하지 못한 보유' in l['message'] for l in s['log'])


def test_schedule_must_fit_inside_the_session(svc):
    service, store, broker, ai, fail = svc
    with pytest.raises(ValueError, match='장 종료'):
        service.create({'initial_cash': 1_000_000, 'eod_review_at': '20:30', 'force_close_at': '20:50'})
    with pytest.raises(ValueError, match='순서'):
        service.create({'initial_cash': 1_000_000, 'entry_at': '15:00', 'eod_review_at': '14:00'})
    # 애프터마켓까지 쓰는 시간표는 통과한다
    id = service.create({'initial_cash': 1_000_000, 'entry_at': '08:00', 'entry_session': 'premarket',
                         'eod_review_at': '19:30', 'force_close_at': '19:50'})['id']
    assert store.get(service.account_kind, id)['config']['force_close_at'] == '19:50'


# ── 11) 간밤 미국 · 어제 선택 · 실행 사유 ───────────────────────────────────
def test_plan_snapshot_carries_overnight_us_numbers(svc, monkeypatch):
    """간밤 미국이 제목이 아니라 숫자로 들어간다. EWY 는 '오늘 한국 갭' 의 직접 신호다."""
    service, store, broker, ai, fail = svc
    rows = [{'symbol': 'EWY', 'label': 'MSCI 한국 ETF', 'why': '', 'date': '2026-09-10',
             'close': 70.1, 'change_pct': -6.62, 'change_3d_pct': -7.63},
            {'symbol': 'SOXX', 'label': '미 반도체', 'why': '', 'date': '2026-09-10',
             'close': 497.4, 'change_pct': -5.63, 'change_3d_pct': -6.5}]
    monkeypatch.setattr(dt.overnight, 'brief', lambda broker, **k: {
        'as_of': '2026-09-10', 'rows': rows, 'headline': '간밤 …', 'error': '', 'note': 'n'})
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    snapshot = [c[1] for c in ai.calls if c[0] == 'plan'][-1]
    assert snapshot['overnight_us']['rows'][0]['symbol'] == 'EWY'
    assert snapshot['overnight_us']['rows'][0]['change_pct'] == -6.62
    # 화면에도 남는다
    assert store.get(service.account_kind, id)['ai_inputs']['overnight_us']['rows'][1]['symbol'] == 'SOXX'


def test_overnight_failure_never_blocks_the_plan(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    monkeypatch.setattr(dt.overnight, 'brief', lambda broker, **k: (_ for _ in ()).throw(RuntimeError('down')))
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    s = store.get(service.account_kind, id)
    assert s['phase'] == 'planned' and s['plan']['picks']          # 판단은 그대로 진행된다
    snapshot = [c[1] for c in ai.calls if c[0] == 'plan'][-1]
    assert snapshot['overnight_us']['rows'] == []


def test_recent_picks_are_handed_back_so_themes_do_not_repeat_blindly(svc, monkeypatch):
    service, store, broker, ai, fail = svc
    ai.plan['top_themes'] = [{'name': '전선·전력설비', 'symbols': ['000500'],
                             'catalyst': '수주와 거래량 증가를 확인한 테스트',
                             'risk': '수급 반전의 가능성 있음', 'invalidation': '전일 저점 이탈 시 무효'}]
    monkeypatch.setattr(dt.scanner, 'premarket_scan', lambda b, cfg=None: {
        'scanned_at': '', 'pool_size': 3, 'enriched': 3, 'rejected': [], 'note': 'n',
        'candidates': [dict(CANDIDATES[0], themes=[{'name': '전선·전력설비'}]), dict(CANDIDATES[1])]})
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    tick(service, monkeypatch, id, '09:01')
    tick(service, monkeypatch, id, '15:01')
    tick(service, monkeypatch, id, '15:21')
    day = store.get(service.account_kind, id)['days']['2026-09-11']
    assert day['themes'] == ['전선·전력설비'] and day['picks'][0]['symbol'] == '000500'
    assert day['stance'] == 'normal'

    tick(service, monkeypatch, id, '07:31', day='2026-09-14')
    snapshot = [c[1] for c in ai.calls if c[0] == 'plan'][-1]
    assert snapshot['recent_picks'][-1]['day'] == '2026-09-11'
    assert snapshot['recent_picks'][-1]['themes'] == ['전선·전력설비']
    assert 'account_realized_pnl' in snapshot['recent_picks'][-1]
    assert snapshot['recent_picks'][-1]['picks_are_plans_not_fills']


def test_scan_records_why_it_ran_and_expires_stale_manual_requests(svc, monkeypatch):
    """새벽에 갑자기 도는 일이 없어야 하고, 돌았다면 이유가 남아야 한다."""
    service, store, broker, ai, fail = svc
    id = make(service)
    tick(service, monkeypatch, id, '07:31')
    s = store.get(service.account_kind, id)
    assert s['stamps']['plan_trigger'] == '예약 07:30'
    assert any('장전 스캔·판단 시작 (예약 07:30)' in l['message'] for l in s['log'])

    # 정지된 계좌에 남겨 둔 수동 요청이 몇 시간 뒤에 실행되면 안 된다
    service.control(id, 'scan_now')
    monkeypatch.setattr(dt, 'utcnow', lambda: at('03:00', '2026-09-14')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['scan'] is None and '_force_scan' not in s
    assert any('예약 시각에 실행' in l['message'] for l in s['log'])

    # 방금 누른 요청도 새벽에는 실행하지 않고 07:30을 기다린다
    service.control(id, 'scan_now')
    monkeypatch.setattr(dt, 'utcnow', lambda: at('03:05', '2026-09-14')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['scan'] is None
    monkeypatch.setattr(dt, 'utcnow', lambda: at('07:30', '2026-09-14')); service.tick(id)
    s = store.get(service.account_kind, id)
    assert s['stamps']['plan_trigger'] == '예약 07:30' and s['scan']['candidates']
