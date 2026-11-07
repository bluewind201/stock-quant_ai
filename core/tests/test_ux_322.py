"""3.22.0: 인증 보존·웹 로그인, 번역, 뉴스 분류, 텔레그램 알림 범위."""
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sqt import ai_auth, news_center, notify, translate
from sqt.api import create_app
from sqt.store import Store
from sqt.subscription_ai import SubscriptionAI

NOW = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)


# ── 텔레그램: 기본은 체결·결산·경고만 ─────────────────────────────────────────

def test_default_level_sends_only_trade_reports(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, 'send', lambda text: sent.append(text) or True)
    monkeypatch.setattr(notify, '_level_override', None)
    monkeypatch.delenv('TELEGRAM_LEVEL', raising=False)
    assert notify.level() == 'trades'
    assert notify.post('장전 계획') is False and notify.post('장중 판단', 'detail') is False
    assert notify.post('매수 체결', 'trade') and notify.post('주문 결과 불명확', 'alert')
    assert sent == ['매수 체결', '주문 결과 불명확']
    notify.set_level('all')
    try:
        assert notify.post('장전 계획')
    finally:
        notify.set_level(None)
    monkeypatch.setenv('TELEGRAM_LEVEL', 'all')
    assert notify.level() == 'all'
    monkeypatch.setenv('TELEGRAM_LEVEL', '이상한값')
    assert notify.level() == 'trades'


def test_trade_messages_are_short_and_name_the_exit_kind():
    base = {'symbol': '005930', 'qty': 3, 'price': 70000.0, 'at': NOW.isoformat(), 'pnl_pct': -2.0,
            'strategy': 'portfolio', 'horizon': 'medium'}
    stop = notify.sell_message(dict(base, pnl=-4000, reason='보호 가격 이탈 · 추적 손절'), '삼성전자')
    assert '손절 매도' in stop and '중기' in stop and '🔴' in stop and len(stop) < 400
    gain = notify.sell_message(dict(base, pnl=9000, pnl_pct=4.3, reason='보호 가격 이탈 · 추적 손절'), '삼성전자')
    assert '이익 확정' in gain and '🔵' in gain
    target = notify.sell_message(dict(base, strategy='ai', pnl=5000, reason='익절 +5%'), '삼성전자')
    assert '익절 매도' in target
    buy = notify.buy_message({'symbol': '005930', 'name': '삼성전자', 'qty': 3, 'entry_price': 70000.0,
                              'sl_price': 66000.0, 'tp_price': 0, 'strategy': 'portfolio', 'horizon': 'long',
                              'entry_at': NOW.isoformat(), 'thesis': '추세 유지'})
    assert '매수' in buy and '210,000원' in buy and '장기' in buy and '66,000원' in buy


def test_daily_summary_is_sent_every_day_with_no_trade_reason():
    quiet = {'day': '2026-11-02', 'positions': {}, 'trades': [], 'status': 'running', 'phase': 'monitoring',
             'ai': {'history': []}, 'errors': {'scan': '스캔 실패: 토스 403'}, 'config': {}}
    assert notify.summary_kind(quiet) == 'trade'
    text = notify.summary_message(quiet)
    assert '오늘 매매 없음' in text and '토스 403' in text
    busy = dict(quiet, trades=[{'side': 'buy', 'at': '2026-11-02T01:00:00Z'}])
    assert notify.summary_kind(busy) == 'trade' and '오늘 매매 없음' not in notify.summary_message(busy)


def test_public_url_link_is_escaped(monkeypatch):
    monkeypatch.setenv('PUBLIC_URL', 'http://nas.local:8430/?a="b"')
    text = notify.buy_message({'symbol': 'A', 'qty': 1, 'entry_price': 1.0, 'sl_price': 1.0, 'tp_price': 2.0,
                               'entry_at': NOW.isoformat()})
    assert 'href="http://nas.local:8430/?a=&quot;b&quot;"' in text
    monkeypatch.setenv('PUBLIC_URL', 'javascript:alert(1)')
    assert 'javascript' not in notify.buy_message({'symbol': 'A', 'qty': 1, 'entry_price': 1.0, 'sl_price': 1.0,
                                                   'tp_price': 2.0, 'entry_at': NOW.isoformat()})


# ── Claude 인증 방식 ─────────────────────────────────────────────────────────

def test_claude_token_modes(tmp_path, monkeypatch):
    monkeypatch.setenv('SQT_DB_PATH', str(tmp_path / 'stock.sqlite3'))
    monkeypatch.setenv('CLAUDE_CODE_OAUTH_TOKEN', 'sk-ant-oat01-from-env-aaaaaaaaaaaaaaaaaaaa')
    assert ai_auth.claude_mode() == 'env' and ai_auth.claude_token().endswith('from-env-aaaaaaaaaaaaaaaaaaaa')
    with pytest.raises(ValueError):
        ai_auth.save_claude_token('sk-ant-api03-not-a-subscription-token')
    ai_auth.save_claude_token('  sk-ant-oat01-web-saved-bbbbbbbbbbbbbbbbbbbbbb  ')
    assert ai_auth.claude_mode() == 'token' and ai_auth.claude_token() == 'sk-ant-oat01-web-saved-bbbbbbbbbbbbbbbbbbbbbb'
    assert '웹에서 저장한' in ai_auth.claude_source()
    ai_auth.use_claude_login()
    assert ai_auth.claude_token() == ''  # 만료됐을 수 있는 .env 토큰이 저장 로그인을 가로채지 않는다
    ai_auth.reset_claude()
    assert ai_auth.claude_mode() == 'env'


def test_child_env_uses_web_saved_token(tmp_path, monkeypatch):
    from sqt.subscription_ai import child_env
    monkeypatch.setenv('SQT_DB_PATH', str(tmp_path / 'stock.sqlite3'))
    monkeypatch.delenv('CLAUDE_CODE_OAUTH_TOKEN', raising=False)
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'must-not-pass')
    ai_auth.save_claude_token('sk-ant-oat01-web-saved-cccccccccccccccccccccc')
    env = child_env()
    assert env['CLAUDE_CODE_OAUTH_TOKEN'] == 'sk-ant-oat01-web-saved-cccccccccccccccccccccc'
    assert 'ANTHROPIC_API_KEY' not in env


@pytest.mark.skipif(os.name != 'posix', reason='심볼릭 링크·마운트 판정은 Linux 컨테이너에서 검증')
def test_auth_dir_moves_to_data_volume_when_not_mounted(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    home = tmp_path / 'home' / '.claude'
    home.mkdir(parents=True)
    (home / '.credentials.json').write_text('{"secret":1}')
    data.mkdir()
    monkeypatch.setattr(ai_auth, 'is_mount', lambda p: Path(p) == data)
    info = ai_auth.ensure_persistent(home, 'claude', data / 'ai-auth')
    assert info['mode'] == 'data' and info['persistent'] and home.is_symlink()
    assert (data / 'ai-auth' / 'claude' / '.credentials.json').read_text() == '{"secret":1}'
    assert (home / '.credentials.json').read_text() == '{"secret":1}'  # 링크를 통해 같은 파일을 본다
    again = ai_auth.ensure_persistent(home, 'claude', data / 'ai-auth')
    assert again['mode'] == 'data' and again['persistent']
    assert 'secret' not in str(info) and 'secret' not in str(again)


@pytest.mark.skipif(os.name != 'posix', reason='Linux 컨테이너 전용')
def test_mounted_auth_volume_is_left_alone(tmp_path, monkeypatch):
    volume = tmp_path / '.stock-codex'
    volume.mkdir()
    (volume / 'auth.json').write_text('{}')
    monkeypatch.setattr(ai_auth, 'is_mount', lambda p: Path(p) == volume)
    info = ai_auth.ensure_persistent(volume, 'codex', tmp_path / 'data' / 'ai-auth')
    assert info['mode'] == 'volume' and not volume.is_symlink() and (volume / 'auth.json').exists()


@pytest.mark.skipif(os.name != 'posix', reason='Linux 컨테이너 전용')
def test_no_persistent_storage_is_reported_not_hidden(tmp_path, monkeypatch):
    home = tmp_path / '.claude'
    home.mkdir()
    monkeypatch.setattr(ai_auth, 'is_mount', lambda p: False)
    info = ai_auth.ensure_persistent(home, 'claude', tmp_path / 'data' / 'ai-auth')
    assert info['mode'] == 'ephemeral' and info['persistent'] is False and not home.is_symlink()


# ── 웹 로그인 흐름 (가짜 CLI) ─────────────────────────────────────────────────

FAKE_CODEX = r'''
import sys, time
print("Welcome to Codex\n1. Open this link\n   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n2. Enter this one-time code\n   \x1b[94mTSTS-EXR8P\x1b[0m", flush=True)
time.sleep(0.6)
'''
FAKE_CLAUDE = r'''
import sys
print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=x", flush=True)
while True:
    print("Paste code here if prompted >", flush=True)
    line = sys.stdin.readline().strip()
    if line == "good-code":
        print("Login successful.", flush=True)
        sys.exit(0)
    print("Invalid code", flush=True)
'''


def fake(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding='utf-8')
    return path


def wait(manager, provider, states, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        view = manager.get(provider)
        if view['state'] in states:
            return view
        time.sleep(0.1)
    return manager.get(provider)


def test_codex_device_login_shows_url_and_code(tmp_path, monkeypatch):
    manager = ai_auth.LoginManager()
    script = fake(tmp_path, 'codex.py', FAKE_CODEX)
    monkeypatch.setattr(ai_auth.LoginFlow, '__init__', _wrap_init(sys.executable, script))
    done = []
    view = manager.start('codex', 'codex', dict(os.environ), lambda: done.append(1))
    assert view['url'] == 'https://auth.openai.com/codex/device' and view['code'] == 'TSTS-EXR8P'
    assert wait(manager, 'codex', ('done',))['state'] == 'done' and done == [1]


def test_claude_login_retries_bad_code_then_completes(tmp_path, monkeypatch):
    manager = ai_auth.LoginManager()
    script = fake(tmp_path, 'claude.py', FAKE_CLAUDE)
    monkeypatch.setattr(ai_auth.LoginFlow, '__init__', _wrap_init(sys.executable, script))
    view = manager.start('claude', 'claude', dict(os.environ))
    assert view['url'].startswith('https://claude.com/cai/oauth/authorize') and view['needs_code']
    bad = manager.submit('claude', 'wrong')
    assert bad['state'] == 'waiting' and '맞지 않' in bad['message']
    with pytest.raises(ValueError):
        manager.submit('claude', 'has space')
    good = manager.submit('claude', 'good-code')
    assert wait(manager, 'claude', ('done',))['state'] == 'done', good
    assert set(manager.get('claude')) == {'provider', 'state', 'message', 'url', 'code', 'needs_code', 'started_at'}


def test_cancel_stops_login(tmp_path, monkeypatch):
    manager = ai_auth.LoginManager()
    script = fake(tmp_path, 'claude.py', FAKE_CLAUDE)
    monkeypatch.setattr(ai_auth.LoginFlow, '__init__', _wrap_init(sys.executable, script))
    manager.start('claude', 'claude', dict(os.environ))
    assert manager.cancel('claude')['state'] == 'cancelled'


def _wrap_init(python, script):
    original = ai_auth.LoginFlow.__init__

    def init(self, provider, args, env, needs_code, on_done=None):
        original(self, provider, [python, str(script)], env, needs_code, on_done)
    return init


# ── 판단 잠금: 번역이 쥐고 있으면 판단이 기다린다 ───────────────────────────────

def test_judgment_waits_for_translation_but_not_for_another_judgment():
    ai = SubscriptionAI()
    ai.lock.acquire()
    ai.background_task = True

    def release():
        time.sleep(0.4)
        ai.background_task = False
        ai.lock.release()
    threading.Thread(target=release).start()
    assert ai._acquire() is True
    ai.lock.release()
    ai.lock.acquire()  # 다른 판단이 진행 중이면 바로 실패
    started = time.monotonic()
    assert ai._acquire() is False and time.monotonic() - started < 0.5
    ai.lock.release()


# ── 번역 ─────────────────────────────────────────────────────────────────────

class FakeChild:
    def __init__(self, provider):
        self.provider = provider
        self.model = 'big-model'
        self.calls = []

    def status(self):
        return {'authenticated': True}

    def _run(self, contract, prompt, timeout=0, label=''):
        import json as _json
        rows = _json.loads(prompt.split('입력:\n', 1)[1])
        self.calls.append((self.model, len(rows)))
        return {'items': [{'id': r['id'], 'ko': '번역: ' + r['text'][:20]} for r in rows] +
                [{'id': 'not-requested', 'ko': '끼워 넣기'}, {'id': rows[0]['id'] + 'x', 'ko': 'english only'}]}


class FakeRouter:
    def __init__(self):
        self.lock = threading.Lock()
        self.background_task = False
        self.child = FakeChild('claude')
        self.providers = {'claude': self.child}

    def _available_order(self):
        return ['claude']


def test_translation_detection_glossary_and_batch(tmp_path):
    assert translate.needs('Federal Reserve Board announces final individual capital requirements')
    assert not translate.needs('삼성전자, 3분기 HBM 매출 증가 (Samsung)')
    assert not translate.needs('CPI')
    assert translate.korean('Consumer Price Index', {}) == '소비자물가지수(CPI)'
    store = Store(tmp_path / 't.db')
    router = FakeRouter()
    titles = ['Federal Reserve Board announces final individual capital requirements',
              'Treasury yields climb as investors weigh tariff outlook',
              'Treasury yields climb as investors weigh tariff outlook',
              '코스피 상승 마감', 'Consumer Price Index']
    assert translate.run_pending(store, router, titles) == 2
    assert router.child.calls == [('claude-haiku-5-5', 2)]  # 가벼운 모델 · 중복·한국어·용어표 대상 제외
    assert router.child.model == 'big-model' and not router.lock.locked() and router.background_task is False
    rows = translate.apply([{'title': titles[1]}], translate.cache(store))
    assert rows[0]['title_ko'].startswith('번역:') and rows[0]['english']
    assert translate.run_pending(store, router, titles) == 0 and len(router.child.calls) == 1  # 캐시


def test_translation_skips_while_judging_and_respects_daily_cap(tmp_path):
    store = Store(tmp_path / 't.db')
    router = FakeRouter()
    router.lock.acquire()
    assert translate.run_pending(store, router, ['Stocks fall as investors digest jobs data report']) == 0
    router.lock.release()
    store.put('settings', 'translation_state', {'day': datetime.now(translate.KST).date().isoformat(),
                                                'calls': translate.DAILY_CALLS, 'backoff_until': 0})
    assert translate.run_pending(store, router, ['Stocks fall as investors digest jobs data report']) == 0
    assert router.child.calls == []


def test_translation_failure_backs_off_without_raising(tmp_path):
    store = Store(tmp_path / 't.db')
    router = FakeRouter()

    def boom(*a, **k):
        raise RuntimeError('cli failed')
    router.child._run = boom
    assert translate.run_pending(store, router, ['Stocks fall as investors digest jobs data report']) == 0
    assert store.get('settings', 'translation_state')['backoff_until'] > time.time()
    assert not router.lock.locked()


# ── 뉴스 분류 ─────────────────────────────────────────────────────────────────

def test_news_center_groups_dedupes_and_marks_ai_citations(tmp_path):
    store = Store(tmp_path / 'n.db')
    store.put('evidence', 'fed-1', {'id': 'fed-1', 'source': 'fed', 'title': 'Federal Reserve issues FOMC statement on rates',
                                    'url': 'https://www.federalreserve.gov/x', 'published_at': (NOW - timedelta(hours=3)).isoformat(),
                                    'available_at': (NOW - timedelta(hours=3)).isoformat(), 'impact': 'high'})
    store.put('translations', translate.key('Federal Reserve issues FOMC statement on rates'),
              {'id': translate.key('Federal Reserve issues FOMC statement on rates'), 'ko': '연준, 금리 관련 FOMC 성명 발표'})
    account = {'positions': {'005930': {'name': '삼성전자'}},
               'scan': {'candidates': [{'symbol': '000660', 'name': 'SK하이닉스', 'news': [
                   {'title': 'SK하이닉스 HBM 증설', 'url': 'https://n/1', 'published_at': (NOW - timedelta(hours=1)).isoformat()}]}]},
               'office': {'stages': {'collect': {'output': {'evidence': [
                   {'id': 'e1', 'title': '삼성전자 파운드리 수주', 'url': 'https://n/2', 'source': '연합',
                    'published_at': (NOW - timedelta(hours=2)).isoformat(), 'symbols': ['005930'], 'origin': 'stock:005930'},
                   {'id': 'e2', 'title': '한국은행 기준금리 동결', 'url': 'javascript:bad', 'source': '연합',
                    'published_at': (NOW - timedelta(hours=5)).isoformat(), 'symbols': [], 'origin': '한국은행 금리 환율'},
                   {'id': 'old', 'title': '오래된 기사', 'published_at': (NOW - timedelta(days=9)).isoformat(), 'symbols': []}]}},
                   'technical': {'output': {'findings': [{'symbol': '005930', 'evidence_ids': ['e1']}]}}}}}
    kr = [{'title': 'SK하이닉스 HBM 증설', 'url': 'https://n/1', 'source': '연합', 'topic': 'market',
           'published_at': (NOW - timedelta(hours=1)).isoformat()}]
    result = news_center.build(store, account, kr, 72, NOW)
    rows = {r['title']: r for r in result['rows']}
    assert '오래된 기사' not in rows
    assert rows['삼성전자 파운드리 수주']['category'] == 'holding' and rows['삼성전자 파운드리 수주']['cited']
    assert rows['삼성전자 파운드리 수주']['names'] == ['삼성전자']
    hynix = rows['SK하이닉스 HBM 증설']
    assert hynix['categories'] == ['candidate', 'market'] and len(result['rows']) == 4  # 중복 제거
    assert rows['한국은행 기준금리 동결']['category'] == 'policy' and rows['한국은행 기준금리 동결']['url'] == ''
    fed = rows['Federal Reserve issues FOMC statement on rates']
    assert fed['official'] and fed['important'] and fed['title_ko'] == '연준, 금리 관련 FOMC 성명 발표'
    assert result['counts']['holding'] == 1 and result['cited'] == 1


# ── API ───────────────────────────────────────────────────────────────────────

def test_api_news_center_notify_level_and_auth_view(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-322')
    monkeypatch.setenv('SQT_DB_PATH', str(tmp_path / 'stock.sqlite3'))
    monkeypatch.setattr(notify, '_level_override', None)
    from sqt import krnews
    monkeypatch.setattr(krnews, 'market_news', lambda **k: [])
    app = create_app(tmp_path / 'stock.sqlite3', background=False)
    with TestClient(app) as c:
        assert c.get('/api/news/center').status_code == 401
        assert c.post('/api/login', json={'password': 'test-322'}).status_code == 200
        news = c.get('/api/news/center?hours=24').json()
        assert news['rows'] == [] and set(news['categories']) >= {'holding', 'global'}
        assert c.post('/api/notify/level', json={'level': 'loud'}).status_code == 400
        assert c.post('/api/notify/level', json={'level': 'all'}).json()['level'] == 'all'
        assert c.get('/api/notify/status').json()['level'] == 'all'
        auth = c.get('/api/ai/auth').json()
        assert set(auth['logins']) == {'claude', 'codex'} and auth['logins']['claude']['state'] == 'idle'
        assert c.post('/api/ai/login/other/start', json={}).status_code == 404
        bad = c.post('/api/ai/claude-token', json={'token': 'sk-ant-api03-wrong'})
        assert bad.status_code == 400
    # 저장한 알림 범위는 재시작 후에도 유지된다
    notify.set_level(None)
    create_app(tmp_path / 'stock.sqlite3', background=False)
    assert notify.level() == 'all'
    notify.set_level(None)
