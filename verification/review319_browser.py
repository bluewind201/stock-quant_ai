"""Offline dashboard preview with explicitly synthetic account data."""
from pathlib import Path
from playwright.sync_api import sync_playwright
from sqt.paper_models import DayTradeConfig
from sqt.portfolio import features
from sqt.reporting import summary
from tests.test_portfolio_317 import bars,NOW

root=Path(__file__).resolve().parents[1]
cfg=DayTradeConfig(strategy='portfolio',max_positions=8,position_pct=10).model_dump()
f=features(bars(),NOW)
state=dict(id='demo',mode='paper',config=cfg,status='running',phase='monitoring',day='2026-10-21',
    positions={'005930':dict(symbol='005930',name='검증용 장기 종목',qty=100,entry_price=1290,sl_price=1180,
                            entry_at='2026-10-20T01:00:00Z',strategy='portfolio',horizon='long',cost=129000,
                            thesis='완료 일봉의 장기 상승 추세 유지',counter_case='추세 반전과 시장 급락 가능',invalidation='보호 가격 또는 진입 이후 완료 일봉의 추세 이탈')},
    trades=[dict(symbol='000660',name='검증용 중기 종목',side='sell',qty=1,price=10000,at=NOW.isoformat(),
                 entry_at=NOW.isoformat(),pnl=-500,strategy='portfolio',horizon='medium',reason='보호 가격 이탈 · 추적 손절')],
    quotes={'005930':{'bid':1299}},ai={'history':[],'calls_today':1},log=[],tuning={},days={},stamps={},
    equity=10050000,cash=9920100,realized_pnl=-500,unrealized=900,total_return_pct=.5,errors={},
    equity_curve=[{'at':f'2026-10-{d}T01:00:00Z','equity':v} for d,v in [(16,10000000),(19,10010000),(20,9990000),(21,10050000)]],
    scan={'candidates':[{'symbol':'005930','name':'검증용 장기 종목','eligible_horizons':['long'],'portfolio':f,
                        'warnings_checked':True,'news':[{'title':'<script>위험 문구도 텍스트로 표시</script>','source':'검증 데이터','published_at':NOW.isoformat()}]}]},
    plan={'summary':'검증용 가상자료 · 실제 운용 결과가 아닙니다.','picks':[],'horizon_reviews':{'long':'추세 유지로 보유'}},
    portfolio_checks=[{'symbol':'005930','name':'검증용 장기 종목','eligible':['long'],'reason':'추세 유지'}])
state['reporting']=summary(state)
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1100})
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    for source in (root/'stock-web').glob('*.js'):page.evaluate('(s)=>new Function(s)',source.read_text(encoding='utf-8'))
    page.set_content((root/'stock-web/index.html').read_text(encoding='utf-8'))
    for name in ('styles.css','research.css'):page.add_style_tag(content=(root/'stock-web'/name).read_text(encoding='utf-8'))
    helpers=(root/'stock-web/app.js').read_text(encoding='utf-8').split('let overview')[0]
    page.add_script_tag(content=helpers+'\nconst action=()=>{};const api=async()=>({});let authenticated=false;function notify(){};function tab(){};')
    for name in ('dashboard.js','daytrade.js','portfolio.js','research.js'):
        page.add_script_tag(content=(root/'stock-web'/name).read_text(encoding='utf-8'))
    page.evaluate('''s=>{dbData={account:s,connections:{toss:true,telegram:false,claude:{installed:true,authenticated:true}},
        mode:'paper',day:s.day,has:{paper:true,live:false},live_status:{available:false},market_session:true};
        $('login').hidden=true;$('workspace').hidden=false;renderDashboard();}''',state)
    assert page.locator('.research-candles').count()==1
    assert '매수 당일 매도' in page.locator('#research-exits').text_content()
    assert '보호 가격' in page.locator('#research-exits').text_content()
    assert '장기' in page.locator('#research-periods').text_content()
    assert '<script>' in page.locator('#research-evidence').text_content()
    assert page.locator('#research-evidence script').count()==0
    page.screenshot(path=str(root/'verification/research319-desktop.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(root/'verification/research319-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.evaluate("()=>{dbData.account.config.strategy='ai';renderDashboard();}")
    assert '기존 당일 전략' in page.locator('#research-mode').text_content()
    page.evaluate("()=>{dbData.account.scan=null;dbData.account.reporting={};renderDashboard();}")
    assert '후보가 수집되면' in page.locator('#research-evidence').text_content()
    assert not errors,errors
    browser.close()
print('Research dashboard: candle/MA chart, drawdown, horizon holdings, exit audit, escaping, legacy warning, empty states and mobile: PASS')
