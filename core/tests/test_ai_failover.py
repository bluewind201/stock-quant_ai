"""Subscription quota failover: fake providers only, no orders or real AI calls."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from sqt.ai_router import AIRouter, CodexAI
from sqt.paper_models import DayPlan
from sqt.store import Store
from sqt.subscription_ai import SubscriptionAI, AIError, AIQuotaError, quota_error


@pytest.mark.parametrize('message', ["You've hit your limit", 'usage_limit_reached',
    'rate_limit_error', 'Too many requests', 'You exceeded your current quota', '사용 한도 초과'])
def test_quota_detection(message):
    assert quota_error(message)


@pytest.mark.parametrize('message', ['invalid API key', 'error_max_turns', 'Invalid JSON',
                                    '모델 이용 권한·구독 한도를 확인하세요.'])
def test_other_errors_are_not_quota(message):
    assert not quota_error(message)


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_cli_quota_classification(provider, monkeypatch):
    monkeypatch.setattr('sqt.subscription_ai.executable', lambda: 'fake-claude')
    monkeypatch.setattr('sqt.ai_router.codex_command', lambda: 'fake-codex')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw:
        SimpleNamespace(returncode=1, stdout='', stderr='usage_limit_reached SECRET_NOT_EXPOSED'))
    with pytest.raises(AIQuotaError) as exc:
        (CodexAI() if provider == 'codex' else SubscriptionAI())._run(DayPlan, 'snapshot')
    assert 'SECRET_NOT_EXPOSED' not in str(exc.value)


def test_claude_json_quota_and_successful_content(monkeypatch):
    monkeypatch.setattr('sqt.subscription_ai.executable', lambda: 'fake')
    payload = {'is_error': True, 'result': "You've hit your limit"}
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw:
        SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr=''))
    ai = SubscriptionAI()
    with pytest.raises(AIQuotaError):
        ai._run(DayPlan, 'snapshot')
    payload.clear()
    payload['structured_output'] = {'summary': 'usage_limit_reached is news data',
                                    'market_stance': 'no_trade', 'picks': []}
    assert ai._run(DayPlan, 'snapshot')['picks'] == []


@pytest.mark.parametrize('preferred', ['claude', 'codex'])
def test_failover_cooldown_restart_and_recovery(tmp_path, monkeypatch, preferred):
    store = Store(tmp_path / 'failover.sqlite3')
    store.put('settings', 'ai_provider', {'provider': preferred})
    clock = [10000.0]
    monkeypatch.setattr('sqt.ai_router.time.time', lambda: clock[0])
    calls = []
    limited = {preferred}
    answer = {'summary': 'valid decision', 'market_stance': 'no_trade', 'picks': []}
    other = 'codex' if preferred == 'claude' else 'claude'

    def prepare(router):
        for key, child in router.providers.items():
            monkeypatch.setattr(child, 'status', lambda key=key: {
                'provider': key, 'installed': True, 'authenticated': True})
            def run(contract, prompt, timeout, label, key=key):
                calls.append((key, prompt))
                assert router.lock.locked()
                if key in limited:
                    raise AIQuotaError('quota')
                return answer
            monkeypatch.setattr(child, '_run', run)

    router = AIRouter(store)
    prepare(router)
    assert router.plan_day({'candidates': []}) == answer
    assert [c[0] for c in calls] == [preferred, other]
    assert calls[0][1] == calls[1][1]
    assert router.last_usage['provider'] == other and router.last_usage['fallback']
    assert router.provider == preferred
    assert router.status()['provider'] == other
    router = AIRouter(store)
    prepare(router)
    calls.clear()
    router.plan_day({'candidates': []})
    assert [c[0] for c in calls] == [other]
    clock[0] += 901
    limited.clear()
    calls.clear()
    router.plan_day({'candidates': []})
    assert [c[0] for c in calls] == [preferred]
    assert not router.last_usage['fallback']


@pytest.mark.parametrize('failure', ['both_quota', 'no_auth', 'bad_schema'])
def test_fail_closed_no_loop(tmp_path, monkeypatch, failure):
    router = AIRouter(Store(tmp_path / 'fail.sqlite3'))
    router.provider = 'claude'
    calls = []
    for key, child in router.providers.items():
        monkeypatch.setattr(child, 'status', lambda key=key: {
            'installed': True, 'authenticated': not (key == 'codex' and failure == 'no_auth')})
        def run(*args, key=key):
            calls.append(key)
            if failure == 'bad_schema':
                raise AIError('bad schema')
            raise AIQuotaError('quota')
        monkeypatch.setattr(child, '_run', run)
    with pytest.raises(AIError):
        router.plan_day({'candidates': []})
    assert calls == (['claude', 'codex'] if failure == 'both_quota' else ['claude'])
    assert not router.lock.locked()
    if failure == 'both_quota':
        with pytest.raises(AIError):
            router.plan_day({'candidates': []})
        assert calls == ['claude', 'codex']
