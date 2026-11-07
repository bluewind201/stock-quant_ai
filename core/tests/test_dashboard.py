"""대시보드가 기대는 기록과 API.

지키려는 성질
  1) 단타 계좌는 자산 곡선을 5분 간격으로 남기고, 마감 정리 때 하루 요약을 남긴다
  2) 장전 판단 스냅샷에 국내 시장 뉴스 제목이 들어가고, 무엇을 봤는지 ai_inputs 로 남는다
  3) 이전 버전 계좌(필드 없음)도 view/tick 이 깨지지 않는다
  4) /api/dashboard 는 로그인 없이는 401, 로그인하면 연결 상태·계좌 요약을 준다 (모델 호출 없음)
  5) /api/news/kr 는 네트워크가 죽어도 200 + 빈 목록
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from sqt import daytrade as dt
from sqt import krnews
from sqt.api import create_app
from sqt.store import Store

KST = timezone(timedelta(hours=9))


def at(hhmm: str, day: str = '2026-09-11') -> datetime:
    h, m = map(int, hhmm.split(':'))
    return datetime.fromisoformat(day).replace(hour=h, minute=m, tzinfo=KST).astimezone(timezone.utc)


class Broker:
    price = 10000.0

    def quotes(self, instruments):
        return {i.symbol: {'symbol': i.symbol, 'bid': self.price * .999, 'ask': self.price * 1.001,
                           'price': self.price, 'timestamp': dt.utcnow().isoformat(), 'book_timestamp': dt.utcnow().isoformat()} for i in instruments}


class AI:
    import threading
    lock = threading.Lock()
    last_usage = {}

    def status(self):
        return {'installed': True, 'authenticated': True}

    def plan_day(self, s):
        self.snapshot = s
        return {'summary': '요약', 'market_view': '보통', 'market_stance': 'normal', 'picks': [
            {'symbol': '000500', 'take_profit_pct': 10, 'stop_loss_pct': 3, 'weight_pct': 15,
             'thesis': '수주 기사와 수급 확인', 'counter_case': '되돌림 가능성', 'invalidation': '시가 -2%'}]}

    def judge_eod(self, s):
        return {'summary': '정리', 'decisions': [{'symbol': '000500', 'action': 'close', 'reason': '일회성'}]}

    def judge_event(self, s):
        return {'summary': '유지', 'decisions': []}
    def review_day(self, s): return {'summary': '복기', 'diagnosis': '표본 부족', 'lessons': [], 'adjustments': []}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setattr(dt.scanner, 'premarket_scan', lambda b, cfg=None: {
        'scanned_at': '', 'pool_size': 1, 'enriched': 1, 'rejected': [], 'note': 'n',
        'candidates': [{'symbol': '000500', 'name': '가온전선', 'score': 50, 'origins': ['theme'], 'themes': [],
                        'news': [{'title': '가온전선 대형 수주', 'body': '본문', 'source': '', 'published_at': '', 'url': ''}]}]})
    monkeypatch.setattr(dt.krnews, 'stock_news', lambda *a, **k: [])
    monkeypatch.setattr(dt.krnews, 'market_news', lambda *a, **k: [
        {'title': '코스피 7000 재돌파 시도', 'source': '연합', 'published_at': '2026-09-10T22:00:00+00:00',
         'url': 'https://example.test/a', 'topic': 'market'}])
    ai = AI()
    service = dt.DayTradeService(Store(str(tmp_path / 'd.sqlite3')), Broker(), ai=ai)

    def sync(state, kind, fn, snapshot, on_done, fail_phase=None, counts=True):
        state['ai']['calls_today' if counts else 'anchor_calls_today'] += 1
        on_done(state, fn(snapshot), dt.utcnow())
    monkeypatch.setattr(service, '_ai_call', sync)
    return service, ai


def test_equity_curve_and_day_summary(svc, monkeypatch):
    service, ai = svc
    id = service.create({'initial_cash': 10_000_000})['id']
    service.control(id, 'start')
    monkeypatch.setattr(dt, 'utcnow', lambda: at('07:31')); service.tick(id)
    monkeypatch.setattr(dt, 'utcnow', lambda: at('09:01')); service.tick(id)
    monkeypatch.setattr(dt, 'utcnow', lambda: at('09:03')); service.tick(id)   # 5분 안 → 점 추가 없음
    monkeypatch.setattr(dt, 'utcnow', lambda: at('09:10')); service.tick(id)
    s = service.view(id)
    stamps = [p['at'] for p in s['equity_curve']]
    assert len(stamps) == 3 and len(set(stamps)) == 3, stamps
    assert all({'at', 'equity', 'cash'} <= set(p) for p in s['equity_curve'])

    monkeypatch.setattr(dt, 'utcnow', lambda: at('15:01')); service.tick(id)
    monkeypatch.setattr(dt, 'utcnow', lambda: at('15:21')); service.tick(id)
    s = service.view(id)
    day = s['days']['2026-09-11']
    assert day['buys'] == 1 and day['sells'] == 1 and day['wins'] + day['losses'] == 1
    assert day['realized_pnl'] == round(s['realized_pnl'])
    assert day['ai_calls'] == 2


def test_plan_snapshot_carries_market_news_and_records_inputs(svc, monkeypatch):
    service, ai = svc
    id = service.create({'initial_cash': 10_000_000})['id']
    service.control(id, 'start')
    monkeypatch.setattr(dt, 'utcnow', lambda: at('07:31')); service.tick(id)
    assert ai.snapshot['kr_market_news'][0]['title'] == '코스피 7000 재돌파 시도'
    # 출처 추적용 URL은 유지한다. 본문은 종목별·오피스 자료에서 따로 제한한다.
    assert ai.snapshot['kr_market_news'][0]['url']=='https://example.test/a'
    inputs = service.view(id)['ai_inputs']
    assert inputs['kind'] == '장전 판단' and inputs['candidates'] == 1 and inputs['candidate_news'] == 1
    assert inputs['kr_market_news'][0]['url'] == 'https://example.test/a'


def test_legacy_account_without_new_fields_still_works(svc, monkeypatch):
    service, ai = svc
    id = service.create({'initial_cash': 10_000_000})['id']
    raw = service.store.get(service.account_kind, id)
    for key in ('equity_curve', 'days', 'ai_inputs'):
        raw.pop(key)
    service.store.put(service.account_kind, id, raw)
    service.control(id, 'start')
    monkeypatch.setattr(dt, 'utcnow', lambda: at('07:31')); service.tick(id)
    view = service.view(id)
    assert view['equity_curve'] and view['days'] == {} and view['ai_inputs']['candidates'] == 1


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-stock-password')
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    app = create_app(tmp_path / 'test.sqlite3', background=False)
    with TestClient(app) as c:
        yield c


def test_dashboard_requires_login_and_reports_connections(client, monkeypatch):
    assert client.get('/api/dashboard').status_code == 401
    assert client.post('/api/login', json={'password': 'test-stock-password'}).status_code == 200
    # claude --version 을 실제로 띄우지 않는다
    from sqt import subscription_ai
    monkeypatch.setattr(subscription_ai.SubscriptionAI, 'status',
                        lambda self: {'installed': False, 'authenticated': False, 'message': '테스트'})
    d = client.get('/api/dashboard').json()
    assert d['account'] is None and d['accounts'] == []
    assert set(d['connections']) == {'toss', 'claude', 'telegram'}
    assert d['connections']['telegram'] is False
    assert d['connections']['claude']['message'] == '테스트'
    assert len(d['day']) == 10 and isinstance(d['market_session'], bool)

    created = client.post('/api/daytrade/accounts', json={'initial_cash': 5_000_000}).json()
    d = client.get('/api/dashboard').json()
    assert d['account']['id'] == created['id'] and d['account']['equity'] == 5_000_000
    assert d['account']['equity_curve'] == [] and d['account']['days'] == {}


def test_news_kr_is_empty_not_error_without_network(client, monkeypatch):
    client.post('/api/login', json={'password': 'test-stock-password'})
    monkeypatch.setattr(krnews, 'search_news', lambda *a, **k: [])
    r = client.get('/api/news/kr')
    assert r.status_code == 200 and r.json()['rows'] == [] and 'market' in r.json()['topics']


def test_market_news_merges_topics_and_dedupes(monkeypatch):
    rows = {'코스피 OR 코스닥 증시': [{'title': '같은 제목', 'source': 'a', 'published_at': '2026-09-11T01:00:00+00:00', 'url': ''}],
            '금리 OR 한국은행 OR 정부 정책 경제': [{'title': '같은 제목', 'source': 'b', 'published_at': '2026-09-11T02:00:00+00:00', 'url': ''},
                                       {'title': '다른 제목', 'source': 'c', 'published_at': '2026-09-11T03:00:00+00:00', 'url': ''}],
            '미국 증시 OR 연준 OR 관세 OR 환율': []}
    monkeypatch.setattr(krnews, 'search_news', lambda q, **k: rows[q])
    merged = krnews.market_news()
    assert [r['title'] for r in merged] == ['다른 제목', '같은 제목']
    assert merged[1]['topic'] == 'market' and merged[0]['topic'] == 'policy'


def test_session_skips_history_and_fast_dashboard_does_not_wait_for_ai(client, monkeypatch):
    import threading
    from sqt.ai_router import AIRouter
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    def slow_status(self):
        entered.set()
        try:
            assert release.wait(10)
            return {'installed':True,'authenticated':True,'message':'ready'}
        finally:finished.set()
    monkeypatch.setattr(AIRouter,'status',slow_status)
    assert client.get('/api/session').status_code==401
    client.post('/api/login',json={'password':'test-stock-password'})
    original=Store.all
    def guarded(self,kind,*args,**kwargs):
        assert kind not in ('datasets','sessions')
        return original(self,kind,*args,**kwargs)
    monkeypatch.setattr(Store,'all',guarded)
    assert client.get('/api/session').json()['authenticated']
    try:
        result=client.get('/api/dashboard?fast=true').json()
        assert entered.wait(1)
        assert result['connections']['claude']['checking']
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2)
