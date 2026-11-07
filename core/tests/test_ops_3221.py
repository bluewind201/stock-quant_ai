"""3.22.1: 회의·알림이 조용히 멈추지 않게 하는 운영 점검과 경고."""
import threading
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from sqt import diagnostics, notify
from sqt.api import create_app
from sqt.daytrade import DayTradeService

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 11, 4, 1, 30, tzinfo=timezone.utc)  # 10:30 KST
DAY = '2026-11-04'


def account(**extra):
    base = {'id': 'a1', 'mode': 'paper', 'status': 'running', 'phase': 'monitoring', 'day': DAY,
            'config': {'strategy': 'portfolio', 'scan_at': '07:30', 'ai_office': True},
            'ai': {'history': [], 'pending': False}, 'errors': {}, 'plan': None, 'log': [], 'stamps': {},
            'last_poll_at': (NOW - timedelta(seconds=20)).isoformat(), 'positions': {}, 'trades': []}
    base.update(extra)
    return base


def test_meeting_reason_for_each_silent_failure():
    failed = account(ai={'history': [{'at': (NOW - timedelta(hours=3)).isoformat(), 'kind': '장전 판단', 'ok': False,
                                      'error': 'Claude 구독 로그인이 필요합니다.'}], 'pending': False})
    m = diagnostics.meeting(failed, NOW)
    assert m['status'] == 'bad' and '로그인이 필요' in m['detail'] and '07:30' in m['detail']
    scan = account(errors={'scan': '스캔 실패: 403'})
    assert diagnostics.meeting(scan, NOW) == {'status': 'bad', 'detail': '스캔 실패: 403'}
    none = account(log=[{'at': (NOW - timedelta(hours=2)).isoformat(), 'message': '후보 없음. 오늘은 신규 진입하지 않습니다.'}])
    m = diagnostics.meeting(none, NOW)
    assert m['status'] == 'warn' and '후보 없음' in m['detail']
    stopped = account(status='stopped')
    assert '운용 시작' in diagnostics.meeting(stopped, NOW)['detail']
    early = account(phase='idle')
    assert diagnostics.meeting(early, NOW.replace(hour=21, minute=0) - timedelta(days=1))['status'] == 'ok'
    done = account(plan={'picks': [{'symbol': 'A'}]}, stamps={'plan': (NOW - timedelta(hours=2)).isoformat()})
    m = diagnostics.meeting(done, NOW)
    assert m['status'] == 'ok' and '매수 계획 1건' in m['detail'] and '08:30' in m['detail']
    assert diagnostics.meeting(account(phase='holiday'), NOW)['status'] == 'ok'
    assert diagnostics.meeting(None, NOW)['status'] == 'bad'


def test_checks_flag_stopped_worker_ai_login_and_telegram_failure():
    stale = account(last_poll_at=(NOW - timedelta(minutes=10)).isoformat())
    result = diagnostics.checks(stale, ai_status={'installed': True, 'authenticated': False, 'provider': 'codex',
                                                  'message': 'ChatGPT 구독 로그인이 필요합니다.'},
                                toss_configured=True, session=True,
                                telegram={'configured': True, 'level': 'trades', 'last_ok_at': None,
                                          'last_error': 'HTTP 401', 'last_error_at': NOW.isoformat()},
                                storage={'claude': {'persistent': True}, 'codex': {'persistent': False}}, now=NOW)
    by = {c['id']: c for c in result['checks']}
    assert result['overall'] == 'bad'
    assert by['worker']['status'] == 'bad' and '멈춰' in by['worker']['detail']
    assert by['ai']['status'] == 'bad' and by['ai']['action'] == 'settings' and 'ChatGPT' in by['ai']['detail']
    assert by['telegram']['status'] == 'bad' and 'HTTP 401' in by['telegram']['detail']
    assert by['auth_storage']['status'] == 'warn'
    legacy = diagnostics.checks(account(config={'strategy': 'ai'}), ai_status={'installed': True, 'authenticated': True},
                                toss_configured=False, session=True, telegram={'configured': False}, storage={}, now=NOW)
    by = {c['id']: c for c in legacy['checks']}
    assert by['strategy']['status'] == 'warn' and '단기·중기·장기' in by['strategy']['detail']
    assert by['toss']['status'] == 'bad'


def test_post_once_sends_each_key_once(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, 'send', lambda text: sent.append(text) or True)
    monkeypatch.setattr(notify, '_once', {})
    monkeypatch.setattr(notify, '_level_override', None)
    monkeypatch.delenv('TELEGRAM_LEVEL', raising=False)
    assert notify.post_once('ai-fail:2026-11-04:a1:장전 판단', '실패')
    assert not notify.post_once('ai-fail:2026-11-04:a1:장전 판단', '실패')
    assert notify.post_once('ai-fail:2026-11-05:a1:장전 판단', '실패')
    assert sent == ['실패', '실패']


def test_telegram_failures_are_recorded_without_token(monkeypatch):
    import urllib.error
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '123:secret-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', '1')

    def fail(*a, **k):
        raise urllib.error.HTTPError('https://api.telegram.org', 401, 'Unauthorized', {}, None)
    monkeypatch.setattr(notify.urllib.request, 'urlopen', fail)
    assert notify.send_now('test') is False
    s = notify.stats()
    assert 'HTTP 401' in s['last_error'] and 'secret-token' not in str(s) and s['configured']


def test_translation_lock_does_not_count_as_busy_judgment():
    class AI:
        lock = threading.Lock()
        background_task = False
    service = DayTradeService.__new__(DayTradeService)
    service.ai = AI()
    assert not service._ai_busy()
    AI.lock.acquire()
    try:
        assert service._ai_busy()
        service.ai.background_task = True
        assert not service._ai_busy()  # 번역이 쥔 잠금은 판단이 기다렸다 받는다
    finally:
        AI.lock.release()


def test_diagnostics_api(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'ops-3221')
    app = create_app(tmp_path / 's.sqlite3', background=False)
    monkeypatch.setattr(app.state.paper.ai, 'status', lambda: {'installed': True, 'authenticated': True, 'provider': 'claude'})
    with TestClient(app) as c:
        assert c.get('/api/diagnostics').status_code == 401
        c.post('/api/login', json={'password': 'ops-3221'})
        d = c.get('/api/diagnostics?fresh=true').json()
        ids = {x['id'] for x in d['checks']}
        assert {'account', 'ai', 'toss', 'telegram'} <= ids and d['overall'] == 'bad'  # 계좌 없음
        assert next(x for x in d['checks'] if x['id'] == 'account')['action'] == 'daytrade'
