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
from sqt.model_catalog import CATALOG
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1050})
    errors=[];writes=[];auth={'ok':False,'fail':False}
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(req):
        path=req.request.url.split('http://stock.test')[-1].split('?')[0]
        if req.request.method!='GET':writes.append(path)
        if path=='/api/login':
            auth['ok']=True
            return req.fulfill(json={'ok':True})
        if path=='/api/health':return req.fulfill(json={'password_configured':True})
        if path=='/api/session':return req.fulfill(status=200 if auth['ok'] else 401,json={'authenticated':auth['ok'],'detail':'Login required'})
        if path=='/api/ai/providers':
            if auth['fail']:return req.fulfill(status=503,json={'detail':'offline test'})
            return req.fulfill(json={'selected':'codex','providers':{'codex':{'authenticated':True,'message':'ChatGPT 구독 로그인 확인'},'claude':{'authenticated':True,'message':'Claude 구독 로그인 확인'}}})
        if path=='/api/dashboard':return req.fulfill(json=dict(account=state,connections=dict(toss=True,telegram=False,claude=dict(installed=True,authenticated=True,model='test-model')),mode='paper',day=state['day'],has=dict(paper=True,live=False),live_status=dict(available=False),market_session=True))
        if path=='/api/daytrade/status':return req.fulfill(json={'defaults':cfg,'paper':state,'live':None,'live_status':{'available':False}})
        if path=='/api/financial-brief':return req.fulfill(json={'events':[],'attention':[],'missing_sources':[],'as_of':NOW.isoformat()})
        if path=='/api/ai/models':return req.fulfill(json={'providers':CATALOG})
        if path=='/api/research':return req.fulfill(json={'health':[],'evidence':[],'events':[]})
        if path=='/api/news/kr':return req.fulfill(json={'rows':[],'topics':{}})
        if path.startswith('/api/'):return req.fulfill(json={})
        file=root/'stock-web'/('index.html' if path=='/' else path.removeprefix('/static/'))
        req.fulfill(path=str(file))
    page.route('http://stock.test/**',route)
    page.goto('http://stock.test/')
    page.locator('#password').fill('offline-test-only');page.locator('#login-form button').click()
    page.wait_for_selector('.auth-chip.connected')
    assert page.locator('.auth-chip.connected').count()==2
    page.wait_for_selector('.research-candles')
    assert page.locator('#desk-market').is_visible()
    assert not page.locator('#desk-portfolio').is_visible()
    page.wait_for_timeout(250)
    page.screenshot(path=str(root/'verification/terminal320-desktop.png'),full_page=True)
    page.locator('#desk-tab-portfolio').click()
    assert page.locator('.selection-card').count()==3
    assert page.locator('.selection-card').first.is_visible()
    page.wait_for_timeout(250)
    page.screenshot(path=str(root/'verification/terminal320-portfolio.png'),full_page=True)
    page.locator('#desk-tab-portfolio').press('ArrowRight')
    assert page.locator('#desk-activity').is_visible()
    assert '매수 당일 매도' in page.locator('#research-exits').text_content()
    page.locator('#desk-tab-market').click()
    assert page.locator('#research-evidence script').count()==0
    for target in ('daytrade','news','settings','dashboard'):
        page.locator(f'nav [data-tab="{target}"]').click()
        page.wait_for_function('(id)=>!document.getElementById(id).hidden',arg=target)
        page.wait_for_timeout(150)
        if target=='daytrade':
            page.wait_for_selector('#dt-start');page.screenshot(path=str(root/'verification/terminal320-operation.png'),full_page=True)
    auth['fail']=True
    page.locator('#terminal-auth-refresh').click()
    page.wait_for_function("document.getElementById('terminal-auth').textContent.includes('조회 실패')")
    assert '로그인 필요' not in page.locator('#terminal-auth').text_content()
    auth['fail']=False;page.locator('#terminal-auth-refresh').click();page.wait_for_selector('.auth-chip.connected')
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(250)
    page.screenshot(path=str(root/'verification/terminal320-mobile.png'),full_page=True)
    for target in ('dashboard','daytrade','news','settings'):
        page.locator(f'nav [data-tab="{target}"]').click();page.wait_for_timeout(150)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),target
    page.emulate_media(reduced_motion='reduce')
    assert page.locator('.terminal-zone').first.evaluate('(e)=>getComputedStyle(e).animationName')=='none'
    assert writes==['/api/login'],writes
    assert not errors,errors
    browser.close()
print('Terminal full-page login, auth states, four workspace tabs, operating/news/settings pages, mobile, reduced motion and no trading writes: PASS')
