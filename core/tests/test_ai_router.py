import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from sqt.ai_router import AIRouter, CodexAI, codex_env, strict_schema
from sqt.paper_models import DayPlan
from sqt.store import Store
from sqt.subscription_ai import AIError


def test_switch_persists_and_blocks_during_judgment(tmp_path, monkeypatch):
    store = Store(tmp_path / 'router.sqlite3')
    router = AIRouter(store)
    monkeypatch.setattr(router.providers['codex'], 'status', lambda: {'authenticated': True})
    router.select('codex')
    assert AIRouter(store).provider == 'codex'
    with router.lock:
        with pytest.raises(ValueError):
            router.select('claude')


def test_failed_auth_does_not_switch(tmp_path, monkeypatch):
    router = AIRouter(Store(tmp_path / 'router.sqlite3'))
    monkeypatch.setattr(router.providers['codex'], 'status',
                        lambda: {'authenticated': False, 'message': 'Login required'})
    with pytest.raises(ValueError):
        router.select('codex')
    assert router.provider == 'claude'


def test_no_broker_or_api_secrets_in_codex_env(monkeypatch):
    for key in ['TOSS_CLIENT_SECRET', 'OPENAI_API_KEY', 'CODEX_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN']:
        monkeypatch.setenv(key, 'not-for-child')
    env = codex_env()
    assert all(v != 'not-for-child' for v in env.values())


def test_codex_schema_requires_all_properties():
    schema = strict_schema(DayPlan.model_json_schema())
    assert set(schema['required']) == set(schema['properties'])
    assert 'default' not in schema['properties']['market_view']
    assert schema['additionalProperties'] is False


@pytest.mark.parametrize('model', ['gpt-6-astra', 'gpt-6-sol', 'gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna'])
def test_codex_uses_exact_astra_and_validates_output(monkeypatch, model):
    monkeypatch.setenv('SQT_CODEX_EXECUTABLE', 'fake-codex')
    def run(args, **kwargs):
        assert args[args.index('-m')+1] == model
        assert '--ignore-user-config' in args and '--ephemeral' in args
        assert 'features.shell_tool=false' in args and 'forced_login_method="chatgpt"' in args
        Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({
            'summary': 'no trade', 'market_stance': 'no_trade', 'picks': []}), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed","usage":{"input_tokens":10}}')
    monkeypatch.setattr(subprocess, 'run', run)
    ai = CodexAI()
    ai.model = model
    assert ai._run(DayPlan, 'test')['market_stance'] == 'no_trade'
    assert ai.last_usage['model'] == model


def test_codex_failure_never_falls_back(monkeypatch):
    monkeypatch.setenv('SQT_CODEX_EXECUTABLE', 'fake-codex')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=''))
    with pytest.raises(AIError):
        CodexAI()._run(DayPlan, 'test')


def test_provider_api_requires_login_and_persists(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqt.api import create_app
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-switch-password')
    monkeypatch.setattr(CodexAI, 'status', lambda self: {
        'provider': 'codex', 'authenticated': True, 'installed': True})
    with TestClient(create_app(tmp_path / 'api.sqlite3', background=False)) as client:
        assert client.post('/api/ai/provider', json={'provider': 'codex'}).status_code == 401
        client.post('/api/login', json={'password': 'test-switch-password'})
        assert client.post('/api/ai/provider', json={'provider': 'codex'}).status_code == 200
        assert client.post('/api/ai/provider', json={'provider': 'invented'}).status_code == 400
    assert Store(tmp_path / 'api.sqlite3').get('settings', 'ai_provider')['provider'] == 'codex'
