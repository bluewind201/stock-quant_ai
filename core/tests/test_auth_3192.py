import json
from types import SimpleNamespace
import pytest
from sqt import subscription_ai as claude
from sqt.ai_router import CodexAI


@pytest.mark.parametrize('method,subscription,ready',[
    ('claude.ai','pro',True),('claude.ai','max',True),('oauth_token',None,True),
    ('claude.ai',None,False),('api_key','pro',False),('third_party','max',False),('none',None,False)])
def test_claude_saved_subscription_login(monkeypatch,method,subscription,ready):
    monkeypatch.delenv('CLAUDE_CODE_OAUTH_TOKEN',raising=False)
    monkeypatch.setattr(claude,'executable',lambda:'claude')
    def run(args,**kwargs):
        if '--version' in args:return SimpleNamespace(stdout='2.1.283',returncode=0)
        assert args[1:]==['auth','status','--json']
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(loggedIn=True,authMethod=method,
                   subscriptionType=subscription,email='private@example.com',accessToken='do-not-expose')))
    monkeypatch.setattr(claude.subprocess,'run',run)
    result=claude.SubscriptionAI().status()
    assert result['authenticated'] is ready
    assert 'private@example.com' not in str(result) and 'do-not-expose' not in str(result)


def test_codex_status_uses_same_file_store_as_login(monkeypatch):
    from sqt import ai_router
    monkeypatch.setattr(ai_router,'codex_command',lambda:'codex')
    def run(args,**kwargs):
        assert 'cli_auth_credentials_store="file"' in args
        assert 'forced_login_method="chatgpt"' in args
        return SimpleNamespace(returncode=0,stdout='',stderr='Logged in using ChatGPT')
    monkeypatch.setattr(ai_router.subprocess,'run',run)
    assert CodexAI().status()['authenticated']
