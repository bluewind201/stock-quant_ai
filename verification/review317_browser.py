from pathlib import Path
from playwright.sync_api import sync_playwright
from sqt.paper_models import DayTradeConfig
root=Path(__file__).resolve().parents[1]
cfg=DayTradeConfig(strategy='portfolio',max_positions=8,position_pct=10).model_dump()
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page(viewport={'width':1280,'height':1000})
    for source in (root/'stock-web').glob('*.js'):page.evaluate('(s)=>new Function(s)',source.read_text(encoding='utf-8'))
    page.set_content((root/'stock-web/index.html').read_text(encoding='utf-8'))
    page.add_style_tag(content=(root/'stock-web/styles.css').read_text(encoding='utf-8'))
    helpers=(root/'stock-web/app.js').read_text(encoding='utf-8').split('let overview')[0]
    page.add_script_tag(content=helpers+'\nconst action=()=>{};const api=async()=>({});let authenticated=false;function notify(){};function tab(){};')
    for name in ('dashboard.js','daytrade.js','portfolio.js'):
        page.add_script_tag(content=(root/'stock-web'/name).read_text(encoding='utf-8'))
    actual=page.evaluate('(c)=>{fillDtDefaults(c);return dtConfig();}',cfg)
    assert actual['strategy']=='portfolio' and actual['portfolio_style']=='balanced'
    assert DayTradeConfig.model_validate(actual).entry_window_minutes==365
    assert page.locator('#legacy-entry-fields').evaluate('(e)=>e.hidden')
    page.evaluate("()=>{$('portfolio-style').value='long';$('portfolio-core').value='005930,069500';}")
    actual=page.evaluate('()=>dtConfig()')
    assert actual['portfolio_style']=='long' and actual['portfolio_core']==['005930','069500']
    page.evaluate('''(c)=>{dtState={id:'test',config:c,status:'running',phase:'monitoring',day:'2026-10-21',
        positions:{},trades:[],quotes:{},ai:{history:[],calls_today:1},log:[],tuning:{},days:{},stamps:{},
        equity:10000000,cash:10000000,realized_pnl:0,unrealized:0,total_return_pct:0,equity_curve:[],errors:{},
        scan:{candidates:[]},plan:{summary:'상승 추세를 확인한 뒤 매수합니다.',picks:[]},
        portfolio_checks:[{symbol:'005930',name:'예시 종목',eligible:['medium'],reason:'기간별 매수 검토 가능'},
                          {symbol:'069500',name:'예시 ETF',eligible:[],reason:'장기 이력 부족'}]};
        renderDaytrade();dbData={account:dtState,connections:{toss:true,telegram:false,claude:{installed:true,authenticated:true}},
            mode:'paper',day:dtState.day,has:{paper:true,live:false},live_status:{available:false},market_session:true};
        renderDashboard();$('login').hidden=true;$('workspace').hidden=false;}''',cfg)
    assert '매수 후보로 선택' in page.locator('#portfolio-decisions').text_content()
    assert '장기 이력 부족' in page.locator('#portfolio-decisions').text_content()
    assert '강제 청산 없음' in page.locator('#dt-schedule').text_content()
    assert '최대 30%' in page.locator('#db-portfolio').text_content()
    assert page.locator('nav [data-tab="paper"]').count()==0
    assert '당일 단타' not in page.locator('nav').text_content()
    page.screenshot(path=str(root/'verification/portfolio317-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.evaluate("()=>{$('dashboard').hidden=true;$('daytrade').hidden=false;$('dt-empty').hidden=false;}")
    page.screenshot(path=str(root/'verification/portfolio317-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    browser.close()
print('Portfolio config persistence, period allocation, reasons, legacy navigation removal, desktop/mobile: PASS')
