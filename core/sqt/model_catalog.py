"""Official catalogs checked 2026-10-29. Account entitlement requires a real response."""
CHECKED_AT='2026-10-29'
CATALOG={
    'codex':[
        {'id':'gpt-6.1-sol','label':'GPT-6.1 Sol'},
        {'id':'gpt-6.1-terra','label':'GPT-6.1 Terra'},
        {'id':'gpt-6-astra','label':'GPT-6 Astra'},
        {'id':'gpt-6-luna','label':'GPT-6 Luna'},
        {'id':'gpt-6-sol','label':'GPT-6 Sol · 기존 설정 호환'},
    ],
    'claude':[
        {'id':'claude-opus-5-5','label':'Claude Opus 5.5'},
        {'id':'claude-sonnet-5-5','label':'Claude Sonnet 5.5'},
        {'id':'claude-haiku-5-5','label':'Claude Haiku 5.5'},
        {'id':'claude-fable-5-1','label':'Claude Fable 5.1 · 추가 크레딧 가능','credit_optin':True},
        {'id':'sonnet','label':'Claude Sonnet · 자동 별칭(기존 설정 호환)'},
    ],
}
SOURCES={
    'codex':'https://learn.chatgpt.com/docs/models',
    'claude':'https://code.claude.com/docs/en/model-config',
}
