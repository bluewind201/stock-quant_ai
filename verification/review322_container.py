"""3.22.0 컨테이너 검증. 외부 통신을 끊은(--network none) 컨테이너 안에서만 실행한다.

phase1  : 인증 볼륨 없이 /data 볼륨만 있는 컨테이너(예전 Compose와 같은 상황)
          → 인증 폴더가 /data/ai-auth 로 옮겨지고, 가짜 인증 파일·알림 범위·Claude 토큰을 저장한다.
phase2  : 같은 /data 볼륨으로 새 컨테이너(= 이미지 업데이트 후 재생성)
          → phase1 에 저장한 것이 그대로 남아 있어야 한다.
volume  : 전용 인증 볼륨이 연결된 컨테이너(현재 Compose) → 아무것도 옮기지 않는다.
"""
import json
import os
import sys
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, Request, build_opener

phase = sys.argv[1]
assert os.getuid() == 10001
assert not list(Path('/app').rglob('.env*'))
opener = build_opener(HTTPCookieProcessor(CookieJar()))


def request(path, body=None):
    data = None if body is None else json.dumps(body).encode()
    return json.load(opener.open(Request('http://127.0.0.1:8080/api/' + path, data=data,
                                         headers={'Content-Type': 'application/json'}), timeout=30))


for _ in range(40):
    try:
        health = request('health')
        break
    except OSError:
        time.sleep(1)
else:
    raise RuntimeError('health 응답 없음')
request('login', {'password': 'local-release-smoke-only-322'})
auth = request('ai/auth')
claude_dir, codex_dir = Path('/home/stock/.claude'), Path('/home/stock/.stock-codex')
FAKE = 'sk-ant-oat01-' + 'smoke' * 8

if phase in ('phase1', 'phase2'):
    for name, folder in (('claude', claude_dir), ('codex', codex_dir)):
        info = auth['storage'][name]
        assert info['mode'] == 'data' and info['persistent'], info
        assert folder.is_symlink() and folder.resolve() == Path('/data/ai-auth') / name, folder.resolve()
    assert 'token' not in json.dumps(auth['storage']).lower()

if phase == 'phase1':
    (claude_dir / '.credentials-smoke.json').write_text('{"smoke":true}')
    (codex_dir / 'auth-smoke.json').write_text('{"smoke":true}')
    assert request('notify/level', {'level': 'all'})['level'] == 'all'
    saved = request('ai/claude-token', {'action': 'save', 'token': FAKE})
    assert saved['claude_mode'] == 'token' and saved['status']['authenticated'], saved
    assert FAKE not in json.dumps(saved) and FAKE not in json.dumps(request('ai/auth'))
    stat = os.stat('/data/ai-auth/claude-auth.json')
    assert oct(stat.st_mode & 0o777) == '0o600', oct(stat.st_mode)
    # 실제 Claude CLI 로 웹 로그인 주소를 받는다 (주소 생성은 로컬, 완료는 사용자 브라우저 몫)
    view = request('ai/login/claude/start', {})
    print('claude login start:', view['state'], view['url'][:48])
    assert view['url'].startswith('https://claude.com/cai/oauth/authorize') and view['needs_code'], view
    assert request('ai/login/claude/cancel', {})['state'] == 'cancelled'
    diag = request('diagnostics?fresh=true')
    by = {c['id']: c for c in diag['checks']}
    assert diag['overall'] == 'bad' and by['account']['status'] == 'bad' and by['ai']['status'] == 'ok', diag
    assert FAKE not in json.dumps(diag)
    news = request('news/center?hours=24')
    assert news['rows'] == [] and set(news['categories']) >= {'holding', 'global'}
    html = opener.open('http://127.0.0.1:8080/', timeout=10).read().decode()
    for asset in ('home.js', 'home.css', 'charts.js', 'office.js', 'office.css'):
        assert '/static/' + asset in html
        assert opener.open('http://127.0.0.1:8080/static/' + asset, timeout=10).status == 200
    assert 'id="home"' in html and 'id="office-canvas"' in html and 'name="tg-level"' in html
    print('phase1 PASS: 인증 폴더 → /data/ai-auth, 토큰 600 권한·비노출, 웹 로그인 주소, 뉴스 센터, 화면 파일')

if phase == 'phase2':
    assert (claude_dir / '.credentials-smoke.json').read_text() == '{"smoke":true}'
    assert (codex_dir / 'auth-smoke.json').read_text() == '{"smoke":true}'
    assert request('notify/status')['level'] == 'all'
    assert auth['claude_mode'] == 'token'
    status = request('ai/providers')['providers']['claude']
    assert status['authenticated'], status
    print('phase2 PASS: 컨테이너를 새로 만들어도 인증 파일·Claude 토큰·알림 범위 유지')

if phase == 'volume':
    for name, folder in (('claude', claude_dir), ('codex', codex_dir)):
        info = auth['storage'][name]
        assert info['mode'] == 'volume' and info['persistent'] and not folder.is_symlink(), info
    print('volume PASS: 전용 인증 볼륨은 그대로 사용')
