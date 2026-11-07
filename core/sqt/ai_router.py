"""Subscription-only provider switching; one shared judgment lock."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .subscription_ai import SubscriptionAI, AIError, AIQuotaError, quota_error
from .model_catalog import CATALOG


def codex_env():
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'HOME', 'USERPROFILE', 'LOCALAPPDATA',
               'APPDATA', 'TEMP', 'TMP', 'LANG', 'LC_ALL', 'SSL_CERT_FILE', 'SSL_CERT_DIR'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env['CODEX_HOME'] = os.getenv('SQT_CODEX_HOME', str(Path.home() / '.stock-codex'))
    return env


def codex_command():
    command = os.getenv('SQT_CODEX_EXECUTABLE') or shutil.which('codex')
    if not command:
        raise AIError('Codex CLI가 없습니다. 두 AI를 포함한 새 Docker 이미지를 사용하세요.')
    return command


def strict_schema(node):
    if isinstance(node, dict):
        node = {k: strict_schema(v) for k, v in node.items() if k != 'default'}
        if node.get('type') == 'object':
            node['additionalProperties'] = False
            node['required'] = list(node.get('properties', {}))
    elif isinstance(node, list):
        node = [strict_schema(v) for v in node]
    return node


class CodexAI(SubscriptionAI):
    def __init__(self):
        super().__init__()
        self.model = 'gpt-6-astra'

    def status(self):
        base = {'provider': 'codex', 'model': self.model, 'api_key_used': False,
                'installed': False, 'authenticated': False}
        try:
            command = codex_command()
            result = subprocess.run([command, '-c', 'cli_auth_credentials_store="file"', '-c', 'forced_login_method="chatgpt"',
                                     'login', 'status'], capture_output=True, timeout=20,
                                    env=codex_env(), text=True, encoding='utf-8', errors='replace',
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            message = ((result.stdout or '') + (result.stderr or '')).lower()
            ready = result.returncode == 0 and 'chatgpt' in message and 'api key' not in message
            base.update(installed=True, authenticated=ready,
                        message='ChatGPT 구독 로그인 확인. 선택한 모델 이용 권한은 실제 호출 시 확인됩니다.' if ready
                        else 'NAS에서 codex login --device-auth로 ChatGPT 구독 로그인이 필요합니다.')
        except (AIError, OSError, subprocess.SubprocessError):
            base['message'] = 'Codex 실행/인증 확인 실패. 설치와 구독 로그인을 확인하세요.'
        return base

    def _run(self, contract_model, prompt, timeout=240, label='판단'):
        with tempfile.TemporaryDirectory(prefix='sqt-codex-') as folder:
            root = Path(folder)
            schema, output = root / 'schema.json', root / 'answer.json'
            schema.write_text(json.dumps(strict_schema(contract_model.model_json_schema())), encoding='utf-8')
            args = [codex_command(), 'exec', '--ignore-user-config', '--ignore-rules',
                    '--skip-git-repo-check', '--ephemeral', '--sandbox', 'read-only',
                    '-m', self.model, '-c', 'cli_auth_credentials_store="file"', '-c', 'forced_login_method="chatgpt"',
                    '-c', 'model_reasoning_effort="medium"', '-c', 'approval_policy="never"',
                    '-c', 'web_search="disabled"', '-c', 'features.shell_tool=false',
                    '-c', 'features.unified_exec=false', '-c', 'features.apps=false',
                    '-c', 'features.multi_agent=false', '--output-schema', str(schema),
                    '--output-last-message', str(output), '--json', '-']
            try:
                result = subprocess.run(args, input=prompt, capture_output=True, cwd=folder,
                                        timeout=timeout, env=codex_env(), text=True,
                                        encoding='utf-8', errors='replace',
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except (OSError, subprocess.SubprocessError):
                raise AIError(f'{self.model} {label} 실행 실패/시간 초과. 이번 판단은 적용하지 않습니다.') from None
            if result.returncode or not output.is_file():
                if quota_error((result.stdout or '') + '\n' + (getattr(result, 'stderr', '') or '')):
                    raise AIQuotaError('ChatGPT 구독 사용 한도에 도달했습니다.')
                raise AIError(f'{self.model} 응답 실패. ChatGPT 로그인·모델 이용 권한·구독 한도를 확인하세요.')
            try:
                answer = contract_model.model_validate_json(output.read_text(encoding='utf-8')).model_dump()
            except (OSError, ValueError):
                raise AIError(f'{self.model} 응답 형식 검증 실패. 이번 판단은 적용하지 않습니다.') from None
            self.last_usage = {'provider': 'codex', 'model': self.model}
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                    if event.get('type') == 'turn.completed':
                        self.last_usage['usage'] = event.get('usage')
                except ValueError:
                    pass
            return answer


class AIRouter(SubscriptionAI):
    MODELS = {provider:tuple(row['id'] for row in rows) for provider,rows in CATALOG.items()}

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.providers = {'claude': SubscriptionAI(), 'codex': CodexAI()}
        try:
            self.provider = store.get('settings', 'ai_provider')['provider']
        except KeyError:
            self.provider = os.getenv('SQT_AI_PROVIDER', 'claude')
        if self.provider not in self.providers:
            raise ValueError('AI provider must be claude or codex')
        try:
            saved = store.get('settings', 'ai_models')
        except KeyError:
            saved = {}
        for key, child in self.providers.items():
            if saved.get(key) in self.MODELS[key]:
                child.model = saved[key]
        try:
            self.cooldowns = store.get('settings', 'ai_quota_cooldowns')
        except KeyError:
            self.cooldowns = {}
        self.last_failover = None

    def _available_order(self):
        return [key for key in (self.provider, *[k for k in self.providers if k != self.provider])
                if self.cooldowns.get(key, 0) <= time.time()]

    def status(self):
        order = self._available_order()
        active = order[0] if order else self.provider
        child = self.providers[active]
        state = dict(child.status(), model=child.model, preferred_provider=self.provider,
                     preferred_model=self.providers[self.provider].model,
                     automatic_failover=True, quota_cooldowns=dict(self.cooldowns),
                     last_failover=self.last_failover)
        if not order:
            state.update(authenticated=False, message='두 AI 모두 사용 한도 대기 중입니다. 이후 자동 재확인합니다.')
        elif active != self.provider:
            state['message'] = '주 AI 한도 초과로 대체 AI 사용 중. ' + state.get('message', '')
        return state

    def select(self, provider, model=None, allow_usage_credits=False):
        if provider not in self.providers:
            raise ValueError('claude 또는 codex를 선택하세요.')
        if model is not None and model not in self.MODELS[provider]:
            raise ValueError('지원되는 모델을 선택하세요.')
        target=model or self.providers[provider].model
        if target=='claude-fable-5-1' and allow_usage_credits is not True:
            raise ValueError('Fable은 비대화형 호출에서 추가 크레딧을 사용할 수 있습니다. 비용 안내 확인이 필요합니다.')
        if not self.lock.acquire(blocking=False):
            raise ValueError('AI 판단이 진행 중입니다. 완료 후 전환하세요.')
        try:
            state = self.providers[provider].status()
            if not state.get('authenticated'):
                raise ValueError(state['message'])
            self.store.put('settings', 'ai_provider', {'provider': provider})
            if model is not None:
                self.providers[provider].model = model
                self.store.put('settings', 'ai_models', {k:v.model for k,v in self.providers.items()})
            self.provider = provider
            if target=='claude-fable-5-1':
                self.store.put('settings','ai_usage_credits',{'fable':True})
            self._auth_checked=None
            return dict(state, model=self.providers[provider].model,
                        preferred_provider=provider, preferred_model=self.providers[provider].model,
                        automatic_failover=True)
        finally:
            self.lock.release()

    def _run(self, contract_model, prompt, timeout=180, label='판단'):
        # Public judgment methods hold the shared lock. Retry inference only: execution
        # sees exactly one validated answer and retains its quote/risk/time gates.
        attempts = []
        self.last_usage = {'provider': self.provider, 'attempts': attempts}
        for key in self._available_order():
            child = self.providers[key]
            if child.model=='claude-fable-5-1':
                try:allowed=self.store.get('settings','ai_usage_credits').get('fable') is True
                except KeyError:allowed=False
                if not allowed:
                    raise AIError('Fable 추가 크레딧 사용 확인이 없어 호출하지 않았습니다.')
            if key != self.provider:
                state = child.status()
                if not state.get('authenticated'):
                    attempts.append({'provider': key, 'outcome': 'unavailable'})
                    continue
            try:
                answer = child._run(contract_model, prompt, timeout, label)
            except AIQuotaError:
                self.cooldowns[key] = time.time() + 15 * 60
                self.store.put('settings', 'ai_quota_cooldowns', self.cooldowns)
                self._auth_checked = None
                attempts.append({'provider': key, 'outcome': 'quota_exceeded'})
                continue
            except AIError:
                attempts.append({'provider': key, 'outcome': 'failed'})
                raise
            attempts.append({'provider': key, 'outcome': 'success'})
            self.last_usage = dict(child.last_usage, provider=key, model=child.model,
                                   attempts=attempts, fallback=key != self.provider)
            if key != self.provider:
                self.last_failover = {'from': self.provider, 'to': key, 'at': time.time(),
                                      'reason': 'quota_exceeded'}
            return answer
        raise AIError('AI 사용 한도 초과: 대체 AI도 한도 대기 중이거나 연결되지 않았습니다. 이번 판단은 적용하지 않습니다.')
