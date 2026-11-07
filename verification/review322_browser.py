"""3.22.0 화면 검증. 모든 숫자·기사는 합성 자료이며 실제 계좌·수익 결과가 아니다.

실행: PYTHONPATH=core python verification/review322_browser.py
외부 통신 없이 /api 응답을 가로채 실제 전체 스크립트(index.html + 모든 js)를 띄운다.
"""
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from sqt.model_catalog import CATALOG
from sqt.paper_models import DayTradeConfig
from sqt.portfolio import features
from sqt.reporting import summary
from tests.test_portfolio_317 import bars, NOW as FNOW

root = Path(__file__).resolve().parents[1]
out = root / 'verification'
now = datetime.now(timezone.utc).replace(microsecond=0)
iso = lambda t: t.isoformat()
rng = random.Random(322)

cfg = DayTradeConfig(strategy='portfolio', max_positions=8, position_pct=10).model_dump()
cfg['initial_cash'] = 10_000_000
f = features(bars(), FNOW)
curve, eq = [], 10_000_000.0
for i in range(480):
    eq *= 1 + rng.gauss(0.00012, 0.0016)
    curve.append({'at': iso(now - timedelta(minutes=5 * (480 - i) * 6)), 'equity': round(eq), 'cash': 6_200_000})
day = now.astimezone(timezone(timedelta(hours=9))).date()
days = {}
for k in range(18, 0, -1):
    d = (day - timedelta(days=k)).isoformat()
    days[d] = {'realized_pnl': round(rng.gauss(8000, 30000)), 'buys': rng.randint(0, 3), 'sells': rng.randint(0, 3)}
positions = {
    '005930': dict(symbol='005930', name='검증용 반도체', qty=40, entry_price=71000, sl_price=65300, entry_at=iso(now - timedelta(days=12)),
                   strategy='portfolio', horizon='long', cost=2_840_000, thesis='합성 자료 · 장기 추세 유지'),
    '000660': dict(symbol='000660', name='검증용 메모리', qty=6, entry_price=182000, sl_price=171000, entry_at=iso(now - timedelta(days=5)),
                   strategy='portfolio', horizon='medium', cost=1_092_000, thesis='합성 자료 · 중기 모멘텀'),
    '042700': dict(symbol='042700', name='검증용 장비', qty=20, entry_price=48000, sl_price=45600, entry_at=iso(now - timedelta(days=1)),
                   strategy='portfolio', horizon='short', cost=960_000, thesis='합성 자료 · 단기 눌림'),
}
quotes = {'005930': {'bid': 74300}, '000660': {'bid': 176500}, '042700': {'bid': 49100}}
trades = [
    dict(symbol='035420', name='검증용 플랫폼', side='buy', qty=8, price=210000, at=iso(now - timedelta(days=6)), strategy='portfolio', horizon='medium'),
    dict(symbol='035420', name='검증용 플랫폼', side='sell', qty=8, price=226000, at=iso(now - timedelta(days=2)), pnl=125000, pnl_pct=7.4,
         entry_at=iso(now - timedelta(days=6)), strategy='portfolio', horizon='medium', reason='익절 목표 도달'),
    dict(symbol='051910', name='검증용 화학', side='buy', qty=5, price=330000, at=iso(now - timedelta(days=4)), strategy='portfolio', horizon='short'),
    dict(symbol='051910', name='검증용 화학', side='sell', qty=5, price=318000, at=iso(now - timedelta(hours=20)), pnl=-62000, pnl_pct=-3.8,
         entry_at=iso(now - timedelta(days=4)), strategy='portfolio', horizon='short', reason='보호 가격 이탈 · 추적 손절'),
    dict(symbol='042700', name='검증용 장비', side='buy', qty=20, price=48000, at=iso(now - timedelta(days=1)), strategy='portfolio', horizon='short'),
]
evidence = [
    {'id': 'ev1', 'title': '검증용 반도체, 차세대 메모리 공급 계약 보도', 'source': '합성 언론', 'url': 'https://example.org/1',
     'published_at': iso(now - timedelta(hours=3)), 'symbols': ['005930'], 'kind': 'news'},
    {'id': 'ev2', 'title': 'Federal Reserve Board announces approval of application', 'source': 'fed', 'url': 'https://example.org/2',
     'published_at': iso(now - timedelta(hours=9)), 'symbols': [], 'kind': 'official'},
]
office = {'id': 'synthetic', 'status': 'running', 'updated_at': iso(now), 'application': '검증용 합성 회의', 'stages': {
    'collect': {'status': 'done', 'updated_at': iso(now), 'output': {'summary': '중복 제거 기사 42건', 'evidence': evidence,
                'coverage': {'articles': 42, 'unique_received': 57, 'requests': 60, 'excluded': {'undated': 1, 'future': 0, 'old': 9, 'duplicate': 5},
                             'missing': ['원문 재무제표·실적 컨센서스 미수집'], 'sources': [{'source': 'stock:005930', 'status': 'available', 'received': 12}]}}},
    'technical': {'status': 'done', 'updated_at': iso(now), 'output': {'summary': '장기 보유 종목은 200일 평균 위에서 추세를 유지합니다.',
                  'findings': [{'symbol': '005930', 'positive': '완료 일봉이 20·50일 평균 위', 'negative': '최근 거래량 감소', 'evidence_ids': ['ev1'], 'missing': []}],
                  'usage': {'provider': 'claude', 'model': 'claude-opus-5-5'}}},
    'flow': {'status': 'done', 'updated_at': iso(now), 'output': {'summary': '외국인 순매수가 사흘째 이어지고 있어요.', 'findings': []}},
    'news': {'status': 'done', 'updated_at': iso(now), 'output': {'summary': '연준 발표는 금리 경로에 큰 변화가 없다는 해석이 많습니다.', 'findings': []}},
    'company': {'status': 'done', 'updated_at': iso(now), 'output': {'summary': '공급 계약 보도는 원문 공시 확인이 필요합니다.', 'findings': []}},
    'research': {'status': 'running', 'updated_at': iso(now)}}}
state = dict(id='demo', mode='paper', config=cfg, status='running', phase='monitoring', day=day.isoformat(),
             positions=positions, trades=trades, quotes=quotes, ai={'history': [], 'calls_today': 2}, log=[], tuning={}, days=days, stamps={},
             equity=round(eq), cash=6_200_000, realized_pnl=63000, unrealized=95400, errors={}, equity_curve=curve, office=office,
             scan={'candidates': [{'symbol': '005930', 'name': '검증용 반도체', 'eligible_horizons': ['long'], 'portfolio': f, 'warnings_checked': True,
                                   'news': [{'title': '<script>위험 문구도 텍스트로</script>', 'source': '합성', 'published_at': iso(now)}]}]},
             plan={'summary': '검증용 합성 계획', 'picks': [], 'horizon_reviews': {'long': '추세 유지'}},
             portfolio_checks=[{'symbol': '005930', 'name': '검증용 반도체', 'eligible': ['long'], 'reason': '추세 유지'}])
state['reporting'] = summary(state)
news_rows = [
    {'title': '검증용 반도체, 차세대 메모리 공급 계약 보도', 'url': 'https://example.org/1', 'source': '합성 언론', 'published_at': iso(now - timedelta(hours=3)),
     'categories': ['holding'], 'category': 'holding', 'symbols': ['005930'], 'names': ['검증용 반도체'], 'cited': True, 'official': False, 'important': False, 'body': '합성 본문입니다.'},
    {'title': 'Federal Reserve Board announces approval of application', 'title_ko': '연준 이사회, 신청 승인 발표', 'english': True, 'url': 'https://example.org/2',
     'source': '연준', 'published_at': iso(now - timedelta(hours=9)), 'categories': ['global'], 'category': 'global', 'symbols': [], 'names': [], 'cited': False, 'official': True, 'important': True, 'body': ''},
    {'title': 'Treasury yields climb as investors weigh tariff outlook', 'english': True, 'title_ko': '', 'url': '', 'source': '해외 언론 색인',
     'published_at': iso(now - timedelta(hours=30)), 'categories': ['global'], 'category': 'global', 'symbols': [], 'names': [], 'cited': False, 'official': False, 'important': False, 'body': ''},
    {'title': '한국은행 기준금리 동결 전망', 'url': 'https://example.org/3', 'source': '합성 언론', 'published_at': iso(now - timedelta(hours=5)),
     'categories': ['policy'], 'category': 'policy', 'symbols': [], 'names': [], 'cited': False, 'official': False, 'important': False, 'body': ''},
    {'title': '검증용 장비 수주 기대감', 'url': 'https://example.org/4', 'source': '합성 언론', 'published_at': iso(now - timedelta(hours=1)),
     'categories': ['holding', 'candidate'], 'category': 'holding', 'symbols': ['042700'], 'names': ['검증용 장비'], 'cited': False, 'official': False, 'important': False, 'body': ''},
]
news = {'rows': news_rows, 'hours': 72, 'cited': 1, 'categories': {'holding': '보유 종목', 'candidate': '관심 후보', 'market': '국내 증시', 'policy': '금리·정책', 'global': '미국·해외', 'theme': '테마'},
        'counts': {'holding': 2, 'candidate': 1, 'market': 0, 'policy': 1, 'global': 2, 'theme': 0},
        'translation': {'english': 2, 'waiting': 1, 'calls_today': 0, 'daily_cap': 16, 'paused_until': 0}}

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe', headless=True)
    errors, writes, auth = [], [], {'ok': False}

    def route(req):
        path = req.request.url.split('http://stock.test')[-1].split('?')[0]
        if req.request.method != 'GET':
            writes.append(path)
        if path == '/api/login':
            auth['ok'] = True
            return req.fulfill(json={'ok': True})
        if path == '/api/health':
            return req.fulfill(json={'password_configured': True})
        if path == '/api/session':
            return req.fulfill(status=200 if auth['ok'] else 401, json={'authenticated': auth['ok'], 'detail': '로그인이 필요합니다.'})
        if path == '/api/ai/providers':
            return req.fulfill(json={'selected': 'claude', 'providers': {'codex': {'authenticated': False, 'message': '로그인 필요'}, 'claude': {'authenticated': True, 'message': 'Claude 구독 로그인 확인'}}})
        if path == '/api/dashboard':
            return req.fulfill(json=dict(account=state, connections=dict(toss=True, telegram=True, claude=dict(installed=True, authenticated=True, model='claude-opus-5-5')),
                                         mode='paper', day=state['day'], has=dict(paper=True, live=False), live_status=dict(available=False), market_session=True))
        if path == '/api/news/center':
            return req.fulfill(json=news)
        if path == '/api/ai/auth':
            return req.fulfill(json={'storage': {'claude': {'persistent': True, 'message': '업데이트 후에도 유지 · 거래 기록 볼륨에 저장'},
                                                 'codex': {'persistent': True, 'message': '업데이트 후에도 유지 · 전용 인증 볼륨'}},
                                     'claude_mode': 'env', 'claude_source': '저장된 구독 로그인',
                                     'logins': {k: {'provider': k, 'state': 'idle', 'message': '', 'url': '', 'code': '', 'needs_code': k == 'claude'} for k in ('claude', 'codex')}})
        if path == '/api/ai/login/codex/start':
            return req.fulfill(json={'provider': 'codex', 'state': 'waiting', 'message': '주소를 열어 아래 코드를 입력하고 로그인을 승인하세요.',
                                     'url': 'https://auth.openai.com/codex/device', 'code': 'TEST-CODE1', 'needs_code': False})
        if path.startswith('/api/ai/login/'):
            return req.fulfill(json={'provider': 'codex', 'state': 'waiting', 'message': '대기', 'url': 'https://auth.openai.com/codex/device', 'code': 'TEST-CODE1', 'needs_code': False})
        if path == '/api/notify/status':
            return req.fulfill(json={'configured': True, 'level': 'trades', 'public_url_set': False})
        if path == '/api/diagnostics':
            return req.fulfill(json={'overall': 'bad', 'generated_at': iso(now), 'checks': [
                {'id': 'account', 'label': '운용 상태', 'status': 'ok', 'detail': '모의 운용 중', 'action': ''},
                {'id': 'meeting', 'label': '오늘 장전 회의', 'status': 'bad', 'detail': '07:30 장전 회의 실패 · Claude 구독 로그인이 필요합니다.', 'action': ''},
                {'id': 'ai', 'label': 'AI 로그인', 'status': 'bad', 'detail': 'Claude 로그인 필요', 'action': 'settings'},
                {'id': 'telegram', 'label': '텔레그램', 'status': 'ok', 'detail': '마지막 전송 07:31', 'action': ''}],
                'recent_log': [{'at': iso(now), 'message': '장전 판단 실패: Claude 구독 로그인이 필요합니다.'}],
                'ai_history': [{'at': iso(now), 'kind': '장전 판단', 'ok': False, 'error': 'Claude 구독 로그인이 필요합니다.'}]})
        if path == '/api/daytrade/status':
            return req.fulfill(json={'defaults': cfg, 'paper': state, 'live': None, 'live_status': {'available': False}})
        if path == '/api/financial-brief':
            return req.fulfill(json={'events': [], 'attention': [], 'missing_sources': [], 'as_of': iso(now)})
        if path == '/api/ai/models':
            return req.fulfill(json={'providers': CATALOG})
        if path == '/api/research':
            return req.fulfill(json={'health': [], 'evidence': [], 'events': [{'id': 'e', 'title': 'Consumer Price Index', 'title_ko': '소비자물가지수(CPI)', 'source': 'bls', 'scheduled_at': iso(now + timedelta(days=1)), 'impact': 'high', 'url': 'https://example.org/cpi'}]})
        if path.startswith('/api/'):
            return req.fulfill(json={})
        file = root / 'stock-web' / ('index.html' if path == '/' else path.removeprefix('/static/'))
        req.fulfill(path=str(file))

    def page_for(viewport, reduced=False):
        auth['ok'] = False
        ctx = browser.new_context(viewport=viewport, reduced_motion='reduce' if reduced else 'no-preference', device_scale_factor=1)
        page = ctx.new_page()
        page.on('pageerror', lambda e: errors.append(str(e)))
        # 로그인 전 /api/session 401 은 정상 흐름이다
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' and '401' not in m.text else None)
        page.route('http://stock.test/**', route)
        page.goto('http://stock.test/')
        page.evaluate("()=>localStorage.clear()")
        page.goto('http://stock.test/')
        page.locator('#password').fill('offline-test-only')
        page.locator('#login-form button').click()
        page.wait_for_selector('#home-view:not([hidden])')
        return page

    # ── PC: 홈 ─────────────────────────────────────────────────────────
    page = page_for({'width': 1440, 'height': 1000})
    assert page.locator('#home').is_visible() and not page.locator('#dashboard').is_visible(), '첫 화면은 홈'
    page.wait_for_timeout(1500)
    assert page.locator('#home-kpis .hkpi').count() == 4
    assert page.locator('#home-trades .htrade').count() == 5
    assert '손절' in page.locator('#home-trades').inner_text() and '익절' in page.locator('#home-trades').inner_text()
    assert page.locator('#home-mix-legend li').count() == 4
    assert page.locator('#home-news .hnews').count() == 5
    assert '연준 이사회, 신청 승인 발표' in page.locator('#home-news').inner_text()  # 영어 기사는 한국어 제목
    # 운영 점검: 실패 항목이 맨 앞, 해결 버튼, 회의실에도 같은 이유
    page.wait_for_selector('#home-health .hcheck')
    assert 'bad' in page.locator('#home-health').get_attribute('class')
    assert page.locator('#home-health .hcheck').first.get_attribute('class').endswith('bad')
    assert page.locator('#home-health [data-goto="settings"]').count() == 1
    assert '확인이 필요한 항목 2개' in page.locator('#home-health-summary').inner_text()
    page.locator('#home-health').screenshot(path=str(out / 'health3221-desktop.png'))
    nav = page.locator('nav[aria-label="주 메뉴"]').inner_text()
    assert all(w in nav for w in ('홈', 'AI 회의실', '뉴스', '상세 분석', '투자 운용', '연결 · 설정')) and 'WORKSPACE' not in nav
    page.locator('#home-equity').scroll_into_view_if_needed()
    box = page.locator('#home-equity').bounding_box()
    page.mouse.move(box['x'] + box['width'] * .6, box['y'] + box['height'] * .5)
    page.wait_for_timeout(150)
    assert page.locator('.sq-tip').is_visible() and '평가자산' in page.locator('.sq-tip').inner_text()
    page.mouse.move(5, 5)
    page.screenshot(path=str(out / 'home322-desktop.png'), full_page=True)

    # ── PC: 회의실 ─────────────────────────────────────────────────────
    page.locator('nav [data-tab="office"]').click()
    page.wait_for_timeout(2600)
    assert page.locator('.office-role-chip').count() == 8 and page.locator('.office-role-chip.running').count() == 1
    assert page.locator('.office-role-chip.done').count() == 5
    page.locator('.office-stage').screenshot(path=str(out / 'office322-running.png'))
    # 담당자 클릭 → 보고서
    page.locator('[data-office-role="technical"]').click()
    assert '200일 평균' in page.locator('#office-report').inner_text() and '찬성' in page.locator('#office-report').inner_text()
    # 회의 완료 상태
    page.evaluate("""()=>{const o=dbData.account.office;o.status='done';o.stages.research={status:'done',output:{summary:'찬성 근거가 우세하지만 거래량 감소는 주의할 점입니다.',findings:[]}};
      o.stages.trader={status:'done',output:{summary:'장기 보유 유지, 신규 매수 1건 제안',picks:[{symbol:'005930',name:'검증용 반도체',horizon:'long',thesis:'추세 유지'}]}};
      o.stages.risk={status:'done',output:{summary:'제안 1건 승인',approved_buys:['005930'],approved_exits:[],reasons:[]}};
      o.stages.office={status:'done',output:{summary:'검증용 결론',approved_buys:['005930'],approved_exits:[]}};renderDashboard();}""")
    page.wait_for_timeout(4200)
    page.locator('.office-stage').screenshot(path=str(out / 'office322-meeting.png'))
    # 다시 보기
    page.locator('#office-replay').click()
    page.wait_for_timeout(5000)
    assert page.locator('#office-replay').inner_text() == '다시 보기 멈추기'
    page.locator('.office-stage').screenshot(path=str(out / 'office322-replay.png'))
    page.locator('#office-replay').click()

    # ── PC: 뉴스 센터 ──────────────────────────────────────────────────
    page.locator('nav [data-tab="news"]').click()
    page.wait_for_selector('.nc-item')
    assert page.locator('.nc-item').count() == 5
    text = page.locator('#nc-list').inner_text()
    assert '연준 이사회, 신청 승인 발표' in text and '원문 · Federal Reserve' in text and '번역 대기' in text
    assert '소비자물가지수(CPI)' in page.locator('#event-list').inner_text()
    page.screenshot(path=str(out / 'news322-desktop.png'), full_page=True)
    page.locator('#nc-cats [data-cat="holding"]').click()
    assert page.locator('.nc-item').count() == 2
    page.locator('#nc-search').fill('장비')
    assert page.locator('.nc-item').count() == 1
    page.locator('#nc-search').fill('')
    page.locator('#nc-cats [data-cat=""]').click()
    page.locator('#nc-cited').check()
    assert page.locator('.nc-item').count() == 1

    # ── PC: 연결 설정 ──────────────────────────────────────────────────
    page.locator('nav [data-tab="settings"]').click()
    page.wait_for_selector('#st-auth-storage .hchip')
    assert page.locator('input[name="tg-level"][value="trades"]').is_checked()
    page.locator('#st-codex-start').click()
    page.wait_for_selector('.device-code')
    assert page.locator('.device-code').inner_text() == 'TEST-CODE1'
    page.locator('.ai-login').screenshot(path=str(out / 'settings322-login.png'))
    page.locator('#st-codex-cancel').click()

    # ── 모바일 ─────────────────────────────────────────────────────────
    mobile = page_for({'width': 390, 'height': 844})
    mobile.wait_for_timeout(1200)
    overflow = mobile.evaluate('()=>document.scrollingElement.scrollWidth-innerWidth')
    assert overflow <= 1, f'모바일 가로 넘침 {overflow}px'
    mobile.screenshot(path=str(out / 'home322-mobile-view.png'))
    mobile.screenshot(path=str(out / 'home322-mobile.png'), full_page=True)
    mobile.locator('nav [data-tab="office"]').click()
    mobile.wait_for_timeout(1500)
    assert mobile.evaluate('()=>document.scrollingElement.scrollWidth-innerWidth') <= 1
    mobile.screenshot(path=str(out / 'office322-mobile.png'), full_page=True)

    # ── 동작 줄이기 ─────────────────────────────────────────────────────
    calm = page_for({'width': 1280, 'height': 900}, reduced=True)
    calm.locator('nav [data-tab="office"]').click()
    calm.wait_for_timeout(800)

    allowed = {'/api/login', '/api/ai/login/codex/start', '/api/ai/login/codex/cancel'}
    unexpected = [w for w in writes if w not in allowed]
    assert not unexpected, f'화면 탐색 중 예상 밖 쓰기 요청: {unexpected}'
    assert not errors, errors
    browser.close()
print('review322 browser checks passed')
