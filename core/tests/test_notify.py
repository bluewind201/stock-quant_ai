"""텔레그램 알림. 네트워크 없이 검증한다.

지키려는 성질
  1) 토큰·chat_id 가 없으면 아무것도 보내지 않고 예외도 없다 (알림이 매매를 막으면 안 된다)
  2) 시각은 전부 KST 로 찍힌다
  3) 단타 서비스의 매수·매도·장전 판단·마감 판단이 실제로 메시지를 만든다
  4) 메시지에 참고한 뉴스 제목·수급이 들어간다 ("무슨 뉴스를 보고 샀나")
"""
from datetime import datetime, timezone, timedelta

import pytest

from sqt import notify
from sqt import daytrade as dt
from sqt.store import Store

KST = timezone(timedelta(hours=9))


def test_unconfigured_is_silent_noop(monkeypatch):
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    assert notify.configured() is False
    assert notify.send('무엇이든') is False
    assert notify.send_now('무엇이든') is False


def test_kst_rendering():
    # 2026-09-11 00:30 UTC == 09:30 KST
    assert notify.kst('2026-09-11T00:30:00+00:00') == '09-11 09:30'
    assert notify.kst(datetime(2026, 9, 11, 0, 30, tzinfo=timezone.utc)) == '09-11 09:30'
    assert notify.kst('2026-09-11T00:30:00Z') == '09-11 09:30'
    assert notify.kst('이상한값') == '이상한값'


def test_plan_message_includes_news_and_flow():
    plan = {'summary': '외국인·기관 동시 순매수만 골랐다', 'market_view': '리스크오프 직후',
            'picks': [{'symbol': '001820', 'name': '삼화콘덴서', 'weight_pct': 10, 'take_profit_pct': 8,
                       'stop_loss_pct': 4.5, 'thesis': '전일 외국인 +174,849주', 'counter_case': '하루 스파이크 가능'}]}
    cands = {'001820': {'flow_foreigner_1d': 174849, 'flow_institution_1d': 133622,
                        'themes': [{'name': 'MLCC'}],
                        'news': [{'title': 'MLCC 수요 급증 보도', 'published_at': '2026-09-11T00:30:00+00:00',
                                  'url': 'https://n.news.naver.com/article/1/2'}]}}
    text = notify.plan_message({}, plan, cands)
    assert '장전 판단' in text and '삼화콘덴서' in text
    assert '익절 +8% / 손절 -4.5%' in text
    assert '외국인 +174,849' in text and '기관 +133,622' in text
    assert 'MLCC 수요 급증 보도' in text and '09-11 09:30' in text
    assert 'href="https://n.news.naver.com/article/1/2"' in text
    # HTML 이스케이프: 제목의 < 가 그대로 나가면 텔레그램이 거절한다
    cands['001820']['news'][0]['title'] = 'A < B'
    assert 'A &lt; B' in notify.plan_message({}, plan, cands)


def test_buy_sell_messages():
    pos = {'symbol': '001820', 'name': '삼화콘덴서', 'qty': 12, 'entry_price': 81000.0, 'cost': 973000.0,
           'tp_pct': 8, 'sl_pct': 4.5, 'tp_price': 87480.0, 'sl_price': 77355.0,
           'entry_at': '2026-09-11T00:01:00+00:00', 'thesis': '수급 확인'}
    b = notify.buy_message(pos)
    assert '매수' in b and '12주' in b and '81,000원' in b and '87,480' in b and '09-11 09:01' in b
    trade = {'symbol': '001820', 'qty': 12, 'price': 87000.0, 'pnl': 65000.0, 'pnl_pct': 6.68,
             'at': '2026-09-11T02:00:00+00:00', 'reason': '익절 +8%'}
    s = notify.sell_message(trade, '삼화콘덴서')
    assert '매도' in s and '+65,000원' in s and '+6.68%' in s and '익절' in s and '🔵' in s
    trade['pnl'] = -30000.0
    assert '🔴' in notify.sell_message(trade, '삼화콘덴서')


def test_daytrade_emits_notifications(tmp_path, monkeypatch):
    """서비스가 실제로 각 시점에서 메시지를 만드는지. 전송 대신 수집한다."""
    sent = []
    monkeypatch.setattr(notify, 'send', lambda text: sent.append(text) or True)
    monkeypatch.setattr(notify, '_level_override', 'all')  # 상세 알림을 켠 경우의 전체 흐름
    monkeypatch.setattr(dt.scanner, 'premarket_scan', lambda b, cfg=None: {
        'scanned_at': '', 'pool_size': 1, 'enriched': 1, 'rejected': [], 'note': 'n',
        'candidates': [{'symbol': '000500', 'name': '가온전선', 'score': 50, 'origins': ['theme'], 'themes': [],
                        'news': [{'title': '가온전선 대형 수주', 'body': '', 'source': '', 'published_at': '', 'url': ''}],
                        'flow_foreigner_1d': 1000, 'flow_institution_1d': 2000}]})
    monkeypatch.setattr(dt.krnews, 'stock_news', lambda *a, **k: [])
    monkeypatch.setattr(dt.krnews, 'market_news', lambda *a, **k: [
        {'title': '코스피 7000 재돌파 시도', 'source': '연합', 'published_at': '2026-09-10T22:00:00+00:00', 'url': 'https://x', 'topic': 'market'}])

    class Broker:
        price = 10000.0
        def quotes(self, instruments):
            return {i.symbol: {'symbol': i.symbol, 'bid': self.price * .999, 'ask': self.price * 1.001, 'price': self.price, 'timestamp': dt.utcnow().isoformat(), 'book_timestamp': dt.utcnow().isoformat()}
                    for i in instruments}

    class AI:
        import threading
        lock = threading.Lock(); last_usage = {}
        def status(self): return {'installed': True, 'authenticated': True}
        def plan_day(self, s): return {'summary': '요약', 'market_view': '보통', 'market_stance': 'normal', 'picks': [
            {'symbol': '000500', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
             'thesis': '수주 기사와 수급 확인', 'counter_case': '되돌림 가능성', 'invalidation': '시가 -2%'}]}
        def judge_eod(self, s): return {'summary': '정리', 'decisions': [{'symbol': '000500', 'action': 'close', 'reason': '일회성 이슈'}]}
        def judge_event(self, s): return {'summary': '유지', 'decisions': []}
        def review_day(self, s): return {'summary': '복기', 'diagnosis': '표본 부족', 'lessons': ['수급 없는 급등주는 사지 않는다'], 'adjustments': []}
    def review_day(self, s): return {'summary': '복기', 'diagnosis': '표본 부족', 'lessons': [], 'adjustments': []}

    svc = dt.DayTradeService(Store(str(tmp_path / 'n.sqlite3')), Broker(), ai=AI())
    def sync(state, kind, fn, snapshot, on_done, fail_phase=None, counts=True):
        state['ai']['calls_today' if counts else 'anchor_calls_today'] += 1
        on_done(state, fn(snapshot), dt.utcnow())
    monkeypatch.setattr(svc, '_ai_call', sync)

    def at(h, m):
        return datetime(2026, 9, 11, h, m, tzinfo=KST).astimezone(timezone.utc)
    id = svc.create({'initial_cash': 10_000_000})['id']; svc.control(id, 'start')
    monkeypatch.setattr(dt, 'utcnow', lambda: at(7, 31)); svc.tick(id)
    monkeypatch.setattr(dt, 'utcnow', lambda: at(9, 1)); svc.tick(id)
    monkeypatch.setattr(dt, 'utcnow', lambda: at(15, 1)); svc.tick(id)
    monkeypatch.setattr(dt, 'utcnow', lambda: at(15, 21)); svc.tick(id)

    kinds = [t.split('\n')[0] for t in sent]
    assert any('장전 판단' in k for k in kinds)
    assert any('매수' in k for k in kinds)
    assert any('마감 판단' in k for k in kinds)
    assert any('매도' in k for k in kinds)
    assert any('결산' in k for k in kinds)
    assert any('마감 복기' in k for k in kinds)
    # "무슨 뉴스를 보고 샀나" 가 장전 메시지에 들어 있다
    plan_text = next(t for t in sent if '장전 판단' in t)
    assert '가온전선 대형 수주' in plan_text and '외국인 +1,000' in plan_text
