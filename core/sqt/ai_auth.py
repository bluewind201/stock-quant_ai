"""구독 AI 인증을 업데이트 뒤에도 유지하고, NAS 터미널 없이 웹 화면에서 로그인한다.

배경
    이미지를 갱신하면 컨테이너가 새로 만들어진다. 인증 폴더(/home/stock/.claude,
    /home/stock/.stock-codex)가 Compose 볼륨으로 연결돼 있지 않으면 로그인 정보가 컨테이너와
    함께 사라져 매번 다시 로그인해야 한다. 거래 기록이 남는 /data(stock-data 볼륨)는 항상 유지되므로,
    인증 폴더가 볼륨이 아니면 /data/ai-auth 아래로 옮기고 원래 경로를 심볼릭 링크로 바꾼다.

안전 규칙
    - 볼륨(마운트 지점)인 폴더는 건드리지 않는다(두 가지 방법으로 확인).
    - 이미지 안의 폴더는 먼저 보관 위치로 복사하고, 복사를 확인한 뒤에만 비운다.
    - 인증 원문(토큰·이메일)은 API 응답과 로그에 넣지 않는다. 로그인 과정에서 화면에 돌려주는 것은
      공식 로그인 주소와 일회용 기기 코드뿐이다.
"""
from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)')
URL = re.compile(r'https://[^\s\x1b\]]+')
DEVICE_CODE = re.compile(r'\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b')
LOGIN_TIMEOUT = 15 * 60


def data_dir() -> Path:
    return Path(os.getenv('SQT_DB_PATH', '/data/stock.sqlite3')).resolve().parent


def auth_root() -> Path:
    return data_dir() / 'ai-auth'


def claude_dir() -> Path:
    return Path(os.getenv('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')


def codex_dir() -> Path:
    return Path(os.getenv('SQT_CODEX_HOME') or os.getenv('CODEX_HOME') or Path.home() / '.stock-codex')


def _mount_points() -> set[str]:
    points = set()
    try:
        with open('/proc/self/mountinfo', encoding='utf-8') as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 4:
                    points.add(fields[4].replace('\\040', ' '))
    except OSError:
        pass
    return points


def is_mount(path: Path) -> bool:
    """볼륨으로 연결된 폴더인지. 두 방법 중 하나라도 마운트라고 하면 마운트로 본다."""
    try:
        if os.path.ismount(path):
            return True
    except OSError:
        pass
    return str(path) in _mount_points()


def _within_mount(path: Path) -> bool:
    """path 자신 또는 상위 폴더 중 / 가 아닌 마운트가 있으면 영구 저장으로 본다."""
    current = path
    while True:
        if str(current) != '/' and is_mount(current):
            return True
        if current.parent == current:
            return False
        current = current.parent


def ensure_persistent(path: Path, name: str, root: Path | None = None) -> dict:
    """인증 폴더 하나를 영구 저장 위치에 두고 상태를 돌려준다."""
    root = root or auth_root()
    info = {'name': name, 'path': str(path)}
    if os.name != 'posix' or os.getenv('SQT_AUTH_PERSIST', 'true').lower() == 'false':
        info.update(mode='local', persistent=True, message='로컬 실행 · 사용자 폴더에 저장')
        return info
    try:
        if path.is_symlink():
            target = path.resolve()
            persistent = _within_mount(target)
            info.update(mode='data' if persistent else 'ephemeral', persistent=persistent, target=str(target),
                        message='업데이트 후에도 유지 · 거래 기록 볼륨에 저장' if persistent
                        else '컨테이너 안에만 저장 · 업데이트하면 다시 로그인해야 합니다')
            return info
        if is_mount(path):
            info.update(mode='volume', persistent=True, message='업데이트 후에도 유지 · 전용 인증 볼륨')
            return info
        if not _within_mount(root.parent):
            info.update(mode='ephemeral', persistent=False,
                        message='저장 볼륨이 없어 컨테이너를 다시 만들면 로그인이 사라집니다')
            return info
        target = root / name
        target.mkdir(parents=True, exist_ok=True)
        os.chmod(target, 0o700)
        if path.is_dir():
            # Docker overlay 는 이미지에 있던 폴더의 이름 바꾸기를 허용하지 않는다(EXDEV).
            # 그래서 먼저 보관 위치로 복사하고, 복사본을 확인한 뒤에만 원본을 비우고 폴더를 지운다.
            # 여기까지 왔다면 두 가지 방법 모두 이 폴더가 볼륨(마운트)이 아니라고 판정한 경우다.
            items = list(path.iterdir())
            for item in items:
                destination = target / item.name
                if item.name == '.keep' or destination.exists():
                    continue
                if item.is_dir() and not item.is_symlink():
                    shutil.copytree(item, destination, symlinks=True)
                else:
                    shutil.copy2(item, destination, follow_symlinks=False)
            for item in items:
                if item.name != '.keep' and not (target / item.name).exists():
                    raise OSError(errno.EIO, '복사 확인 실패')
            for item in items:
                if item.is_dir() and not item.is_symlink():
                    shutil.rmtree(item)
                else:
                    item.unlink()
            path.rmdir()  # 마운트 지점이면 여기서 실패한다(EBUSY)
        elif path.exists():
            raise OSError('인증 경로가 폴더가 아닙니다')
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
        info.update(mode='data', persistent=True, target=str(target), moved=True,
                    message='업데이트 후에도 유지 · 거래 기록 볼륨으로 옮겼습니다')
    except OSError as exc:
        info.update(mode='unknown', persistent=False,
                    message=f'인증 저장 위치를 확인하지 못했습니다 ({type(exc).__name__} errno={exc.errno})')
    return info


_storage: dict = {}


def prepare() -> dict:
    """앱 시작 시 한 번. 자식 프로세스를 띄우기 전에 호출한다."""
    _storage['claude'] = ensure_persistent(claude_dir(), 'claude')
    _storage['codex'] = ensure_persistent(codex_dir(), 'codex')
    return storage()


def storage() -> dict:
    return {k: dict(v) for k, v in _storage.items()}


# ── Claude 인증 방식 ───────────────────────────────────────────────────────
# 'env'   : .env 의 CLAUDE_CODE_OAUTH_TOKEN, 없으면 저장된 로그인 (기존 동작)
# 'token' : 웹에서 붙여넣은 1년 토큰 (/data/ai-auth/claude-auth.json, 권한 600)
# 'login' : 웹·터미널의 `claude auth login` 결과. 만료된 .env 토큰이 끼어들지 않게 무시한다.

def _claude_file() -> Path:
    return auth_root() / 'claude-auth.json'


def claude_settings() -> dict:
    try:
        data = json.loads(_claude_file().read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_claude(data: dict) -> None:
    path = _claude_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(data, handle)
    if os.name == 'posix':
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def claude_mode() -> str:
    mode = claude_settings().get('mode')
    return mode if mode in ('token', 'login') else 'env'


def claude_token() -> str:
    """자식 프로세스에 넘길 토큰. 비면 CLI 에 저장된 구독 로그인을 쓴다."""
    mode = claude_mode()
    if mode == 'token':
        return str(claude_settings().get('token', '')).strip()
    if mode == 'login':
        return ''
    return os.getenv('CLAUDE_CODE_OAUTH_TOKEN', '').strip()


def save_claude_token(value: str) -> None:
    value = (value or '').strip()
    if not re.fullmatch(r'sk-ant-oat[0-9A-Za-z_\-]{20,400}', value):
        raise ValueError('claude setup-token 이 출력한 sk-ant-oat 로 시작하는 값을 붙여넣으세요.')
    _write_claude({'mode': 'token', 'token': value, 'saved_at': time.time()})


def use_claude_login() -> None:
    _write_claude({'mode': 'login', 'saved_at': time.time()})


def reset_claude() -> None:
    try:
        _claude_file().unlink()
    except FileNotFoundError:
        pass


def claude_source() -> str:
    mode = claude_mode()
    if mode == 'token':
        return '웹에서 저장한 1년 토큰'
    if mode == 'login':
        return '웹·터미널 구독 로그인'
    return '.env 토큰' if os.getenv('CLAUDE_CODE_OAUTH_TOKEN', '').strip() else '저장된 구독 로그인'


# ── 웹 로그인 ───────────────────────────────────────────────────────────────

class LoginFlow:
    """프로세스 하나를 띄워 공식 로그인 주소와 코드를 읽어 화면에 전달한다."""

    def __init__(self, provider: str, args: list[str], env: dict, needs_code: bool, on_done=None):
        self.provider = provider
        self.on_done = on_done
        self.after_submit = ''
        self.needs_code = needs_code
        self.started = time.time()
        self.url = ''
        self.code = ''
        self.state = 'starting'
        self.message = '로그인 주소를 받는 중입니다.'
        self.lock = threading.Lock()
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=env, cwd=str(Path.home()),
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        threading.Thread(target=self._read, daemon=True, name=f'login-{provider}').start()

    def _read(self):
        buffer = ''
        stream = self.process.stdout
        while True:
            chunk = stream.read1(4096) if hasattr(stream, 'read1') else stream.read(4096)
            if not chunk:
                break
            piece = chunk.decode('utf-8', 'replace')
            buffer = (buffer + piece)[-20000:]
            text = ANSI.sub('', buffer)
            with self.lock:
                if self.state == 'verifying':
                    self.after_submit = (self.after_submit + ANSI.sub('', piece))[-4000:]
                    lowered = self.after_submit.lower()
                    if 'paste code' in lowered or 'invalid' in lowered or 'error' in lowered:
                        self.state = 'waiting'
                        self.after_submit = ''
                        self.message = '코드가 맞지 않거나 만료됐습니다. 다시 붙여넣거나 처음부터 시작하세요.'
                if not self.url:
                    found = [u for u in URL.findall(text) if 'claude.com' in u or 'openai.com' in u or 'anthropic.com' in u]
                    if found:
                        self.url = found[0].rstrip('.,)')
                if self.provider == 'codex' and not self.code:
                    match = DEVICE_CODE.search(text)
                    if match:
                        self.code = match.group(0)
                if self.url and self.state == 'starting':
                    self.state = 'waiting'
                    self.message = ('브라우저에서 주소를 열어 로그인한 뒤 화면에 나온 코드를 붙여넣으세요.'
                                    if self.needs_code else '주소를 열어 아래 코드를 입력하고 로그인을 승인하세요.')
        code = self.process.wait()
        with self.lock:
            if self.state in ('cancelled',):
                return
            if code == 0:
                self.state = 'done'
                self.message = '로그인 완료. 인증 정보는 업데이트 후에도 유지되는 위치에 저장됩니다.'
                if self.on_done:
                    try:
                        self.on_done()
                    except Exception:  # noqa: BLE001 - 상태 표시만 실패
                        self.message += ' (인증 방식 저장 실패 · 연결 확인을 눌러 주세요)'
            else:
                self.state = 'failed'
                self.message = '로그인이 끝나지 않았습니다. 코드가 틀렸거나 시간이 지났을 수 있습니다. 다시 시작하세요.'

    def submit(self, code: str) -> None:
        code = (code or '').strip()
        if not self.needs_code:
            raise ValueError('이 로그인은 코드를 붙여넣지 않습니다. 브라우저 승인만 하면 됩니다.')
        if not code or len(code) > 600 or any(c.isspace() for c in code):
            raise ValueError('브라우저에 표시된 코드를 그대로 붙여넣으세요.')
        with self.lock:
            if self.state != 'waiting':
                raise ValueError('로그인 대기 상태가 아닙니다. 다시 시작하세요.')
            self.state = 'verifying'
            self.after_submit = ''
            self.message = '코드를 확인하고 있습니다.'
        try:
            self.process.stdin.write((code + '\n').encode('utf-8'))
            self.process.stdin.flush()
        except OSError:
            with self.lock:
                self.state = 'failed'
                self.message = '로그인 프로세스가 이미 끝났습니다. 다시 시작하세요.'

    def cancel(self) -> None:
        with self.lock:
            if self.state in ('done', 'failed'):
                return
            self.state = 'cancelled'
            self.message = '로그인을 취소했습니다.'
        try:
            self.process.kill()
        except OSError:
            pass

    def view(self) -> dict:
        with self.lock:
            if self.state in ('starting', 'waiting', 'verifying') and time.time() - self.started > LOGIN_TIMEOUT:
                self.state = 'failed'
                self.message = '15분이 지나 로그인 코드가 만료됐습니다. 다시 시작하세요.'
                try:
                    self.process.kill()
                except OSError:
                    pass
            return {'provider': self.provider, 'state': self.state, 'message': self.message,
                    'url': self.url, 'code': self.code, 'needs_code': self.needs_code,
                    'started_at': self.started}


class LoginManager:
    def __init__(self):
        self.flows: dict[str, LoginFlow] = {}
        self.lock = threading.Lock()

    def start(self, provider: str, command: str, env: dict, on_done=None) -> dict:
        if provider == 'claude':
            args = [command, 'auth', 'login', '--claudeai']
            needs_code = True
        elif provider == 'codex':
            args = [command, '-c', 'cli_auth_credentials_store="file"', '-c', 'forced_login_method="chatgpt"',
                    'login', '--device-auth']
            needs_code = False
        else:
            raise ValueError('claude 또는 codex 만 로그인할 수 있습니다.')
        with self.lock:
            old = self.flows.get(provider)
            if old:
                old.cancel()
            try:
                flow = LoginFlow(provider, args, env, needs_code, on_done)
            except OSError:
                raise ValueError('로그인 프로그램을 실행하지 못했습니다. 이미지에 CLI가 포함됐는지 확인하세요.') from None
            self.flows[provider] = flow
        deadline = time.time() + 20
        while time.time() < deadline:
            view = flow.view()
            if view['state'] != 'starting':
                return view
            time.sleep(0.2)
        return flow.view()

    def get(self, provider: str) -> dict:
        flow = self.flows.get(provider)
        return flow.view() if flow else {'provider': provider, 'state': 'idle', 'message': '', 'url': '',
                                         'code': '', 'needs_code': provider == 'claude'}

    def submit(self, provider: str, code: str) -> dict:
        flow = self.flows.get(provider)
        if not flow:
            raise ValueError('먼저 로그인을 시작하세요.')
        flow.submit(code)
        deadline = time.time() + 30
        while time.time() < deadline and flow.view()['state'] == 'verifying':
            time.sleep(0.3)
        return flow.view()

    def cancel(self, provider: str) -> dict:
        flow = self.flows.get(provider)
        if flow:
            flow.cancel()
        return self.get(provider)
