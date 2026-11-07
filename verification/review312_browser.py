from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    page=browser.new_page()
    for source in (root/'stock-web').glob('*.js'):
        page.evaluate('(source) => {new Function(source); return true}',source.read_text(encoding='utf-8'))
    page.set_content((root/'stock-web/index.html').read_text(encoding='utf-8'))
    choices=page.locator('#st-ai-provider option').evaluate_all('(items)=>items.map(x=>x.value)')
    assert 'codex:gpt-6-sol' in choices and 'claude:claude-opus-5-5' in choices
    page.select_option('#st-ai-provider','codex:gpt-6-sol',force=True)
    assert page.locator('#st-ai-provider').input_value()=='codex:gpt-6-sol'
    for strategy in ('ai','orb','retest','bull_flag','in_play'):
        page.select_option('#dt-strategy',strategy,force=True)
        assert page.locator('#dt-strategy').input_value()==strategy
    assert 'login --device-auth' in page.content()
    browser.close()
print('JavaScript parse and model selector: PASS')
