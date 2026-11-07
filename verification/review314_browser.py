from pathlib import Path
from playwright.sync_api import sync_playwright
from sqt.paper_models import DayTradeConfig

root=Path(__file__).resolve().parents[1]
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page()
    page.set_content((root/'stock-web/index.html').read_text(encoding='utf-8'))
    helpers=(root/'stock-web/app.js').read_text(encoding='utf-8').split('let overview')[0]
    page.add_script_tag(content=helpers+'\nconst action=()=>{}; const api=async()=>{}; let authenticated=false; function notify(){};')
    page.add_script_tag(content=(root/'stock-web/daytrade.js').read_text(encoding='utf-8'))
    cfg=page.evaluate('''(d)=>{fillDtDefaults(d);$('dt-strategy').value='in_play';
        $('dt-strategy').dispatchEvent(new Event('change')); return dtConfig();}''',DayTradeConfig().model_dump())
    assert cfg['strategy']=='in_play' and cfg['risk_per_trade_pct']==.25
    assert cfg['approval_minutes']==20 and cfg['max_price_reviews_per_day']==16
    assert '조건부' in page.locator('#dt-strategy-note').text_content()
    page.evaluate('(d)=>fillFromAccount(d)',dict(DayTradeConfig().model_dump(),strategy='in_play',risk_per_trade_pct=.1))
    assert page.locator('#dt-risk').input_value()=='0.1'
    browser.close()
print('In-play form initialization, selection, config payload and editing: PASS')
