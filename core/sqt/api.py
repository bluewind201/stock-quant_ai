from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field

from . import engine, news, themes, krnews, ai_auth, translate, news_center
from . import notify as notify_module
from .daytrade import KST, DayTradeService
from datetime import timedelta as _td, timezone as _tz
KST=_tz(_td(hours=9))
from .paper_models import DayTradeConfig
from .subscription_ai import AIError
from .calendar import calendar, session, status
from .demo import build_demo
from .kis import KIS
from .toss import Toss, TossError
from .models import DatasetInput, Evidence, Event, Instrument, Strategy, StrictModel, utcnow
from .store import Store
from .storage import prepare_storage
from .technical import analyze, chart_series
from .paper_models import PaperConfig
from .paper_service import PaperService
from .live_service import LiveService, LiveConfig

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / 'stock-web'
log = logging.getLogger('stock-quant')


class Login(StrictModel):
    password: str = Field(min_length=1, max_length=500)


class Start(StrictModel):
    dataset_id: str
    config: Strategy
    holdout: bool = False


class Advance(StrictModel):
    request_id: str = Field(min_length=8, max_length=100)
    steps: int = Field(default=1, ge=1, le=20)


class Control(StrictModel):
    auto: bool | None = None
    entries_enabled: bool | None = None


class KISImport(StrictModel):
    instrument: Instrument
    count: int = Field(default=300, ge=65, le=1000)


class ResearchImport(StrictModel):
    evidence: list[Evidence] = Field(default_factory=list, max_length=5000)
    events: list[Event] = Field(default_factory=list, max_length=2000)


class PaperControl(StrictModel):
    action: str = Field(min_length=1,max_length=30)
    acknowledge_live: str = Field(default='',max_length=50)


class DayTradeCreate(DayTradeConfig):
    acknowledge_live: str = Field(default='',max_length=50)


class BindOrder(StrictModel):
    broker_order_id: str = Field(min_length=1,max_length=200,pattern=r'^[A-Za-z0-9_-]+$')


def _setup_logging():
    # 컨테이너 TZ=Asia/Seoul 이므로 asctime 은 KST 다. 라벨을 붙여 UTC 로 오해하지 않게 한다.
    if logging.getLogger().handlers:
        return
    logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),
                        format='%(asctime)s KST %(levelname)s %(name)s %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')


def create_app(path=None, background=True):
    _setup_logging()
    storage_path, storage_notice = (str(path),'') if path else prepare_storage(ROOT)
    store = Store(storage_path)
    broker = KIS()
    toss = Toss()
    # 인증 폴더가 볼륨이 아니면 거래 기록 볼륨으로 옮긴다. AI 프로세스를 띄우기 전에 한다.
    if background:
        ai_auth.prepare()
    try:
        notify_module.set_level(store.get('settings','telegram').get('level'))
    except KeyError:
        pass
    login_manager = ai_auth.LoginManager()
    from .ai_router import AIRouter
    paper = PaperService(store,toss,ai=AIRouter(store))
    live = LiveService(store,toss,ai=paper.ai)
    daytrade = DayTradeService(store,toss,ai=paper.ai)
    default_broker = os.getenv('STOCK_BROKER','toss').lower()
    if default_broker not in ('toss','kis'):
        raise ValueError('STOCK_BROKER는 toss 또는 kis여야 합니다.')
    secret = (os.getenv('SESSION_SECRET') or secrets.token_hex(32)).encode()
    stop_event, refresh_lock, auth_lock = threading.Event(), threading.Lock(), threading.Lock()
    failures = {}

    def snapshot(dataset):
        if dataset['demo']:
            _, evidence, events = build_demo()
            return evidence, events
        return ([n for n in store.all('evidence') if n['source'] != 'demo'],
                [e for e in store.all('events') if e['source'] != 'demo'])

    def save_dataset(data):
        payload = DatasetInput.model_validate(data)
        for bar in payload.bars:
            _, closed = session(payload.instrument.exchange, bar.date)
            if closed >= utcnow():
                raise ValueError('미래 또는 아직 완료되지 않은 일봉은 가져올 수 없습니다.')
        expected = [d.strftime('%Y-%m-%d') for d in calendar(payload.instrument.exchange).sessions_in_range(
                    payload.bars[0].date, payload.bars[-1].date)]
        if expected != [b.date for b in payload.bars]:
            raise ValueError('거래일 데이터에 누락이 있습니다. 휴장일을 제외한 연속 일봉이 필요합니다.')
        result = payload.model_dump(mode='json')
        result['instrument']['currency'] = payload.instrument.currency
        result['id'] = uuid.uuid4().hex
        result['imported_at'] = utcnow().isoformat()
        store.put('datasets',result['id'],result)
        return result

    def advance(id, steps=1, request_id=None, require_auto=False):
        with store.connect(write=True) as con:
            s = store.get('sessions',id,con)
            if require_auto and not s['auto']:
                return s
            if request_id and request_id in s.get('requests',[]):
                return s
            for _ in range(steps):
                if s['done']:
                    break
                engine.step(s,s['dataset'],s['evidence'],s['events'])
            if request_id:
                s.setdefault('requests',[]).append(request_id)
            store.put('sessions',id,s,con)
            return s

    def refresh():
        if not refresh_lock.acquire(blocking=False):
            return
        try:
            news.collect(store)
        finally:
            refresh_lock.release()

    def worker():
        while not stop_event.wait(2):
            for item in store.all('sessions'):
                if item['auto'] and not item['done']:
                    try:
                        advance(item['id'],require_auto=True)
                    except Exception:
                        log.exception('훈련 재생 오류')
                        with store.connect(write=True) as con:
                            s = store.get('sessions',item['id'],con)
                            s['auto'] = False
                            s['error'] = '훈련 재생 오류: 입력 데이터와 서버 로그를 확인하세요.'
                            store.put('sessions',s['id'],s,con)

    def news_worker():
        from .financial_brief import send_due
        while not stop_event.is_set():
            refresh()
            try:
                # Reports about earnings/central banks/conferences are attention
                # items, never treated as a verified scheduled timestamp.
                items=krnews.search_news('증시 주요 일정 실적발표 금통위 투자자 행사',hours=48,count=6)[:2]
                items+=krnews.market_news(hours=24)[:4]
                store.put('market_attention','kr',{'id':'kr','at':utcnow().isoformat(),'items':[
                    {'title':n['title'],'url':n.get('url',''),'source':'국내 시장 보도'} for n in items]})
            except Exception:
                log.warning('국내 관심 이슈 요약 수집 실패')
            send_due(store)
            try:
                account=daytrade.current('live') or daytrade.current('paper')
                translate.run_pending(store,paper.ai,news_center.pending_titles(store,account))
            except Exception:  # noqa: BLE001 - 번역 실패가 수집을 멈추면 안 된다
                log.warning('뉴스 번역 단계 실패')
            stop_event.wait(900)

    @asynccontextmanager
    async def lifespan(app):
        try:
            store.get('datasets','demo')
        except KeyError:
            dataset,_,_ = build_demo()
            store.put('datasets','demo',dataset)
        # Replays do not silently resume after process restarts.
        with store.connect(write=True) as con:
            for state in store.all('sessions',con):
                state['auto'] = False
                store.put('sessions',state['id'],state,con)
        if background:
            paper.initialize(resume=os.getenv('PAPER_RESUME_ON_RESTART','false').lower()=='true')
            paper.start_worker()
            live.initialize()
            live.start_worker()
            daytrade.start_worker()
            # Historical replay has no page or automatic worker in the portfolio product.
            if os.getenv('NEWS_AUTO_REFRESH','true').lower() == 'true':
                threading.Thread(target=news_worker,daemon=True).start()
        yield
        stop_event.set()
        paper.stop_event.set()
        live.stop_event.set()

    app = FastAPI(title='Stock Quant · 주식 자동매매 트레이닝',lifespan=lifespan,
                  docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store = store
    app.state.paper = paper
    app.state.live = live
    app.state.daytrade = daytrade

    @app.exception_handler(ValueError)
    async def bad_data(request, exc):
        return JSONResponse({'detail':str(exc)},status_code=400)

    @app.exception_handler(KeyError)
    async def missing(request,exc):
        return JSONResponse({'detail':'요청한 자료가 없습니다.'},status_code=404)

    @app.middleware('http')
    async def authenticate(request: Request, call_next):
        if request.url.path.startswith('/api/') and request.url.path not in ('/api/login','/api/health'):
            token = request.cookies.get('sqt_session','')
            try:
                expiry, signature = token.split('.')
                expected = hmac.new(secret,expiry.encode(),hashlib.sha256).hexdigest()
                valid = int(expiry) > time.time() and hmac.compare_digest(signature,expected)
            except (ValueError,TypeError):
                valid = False
            if not valid:
                return JSONResponse({'detail':'로그인이 필요합니다.'},status_code=401)
        if request.method not in ('GET','HEAD','OPTIONS'):
            origin = request.headers.get('origin')
            if origin and urlparse(origin).netloc != request.url.netloc:
                return JSONResponse({'detail':'다른 출처의 요청은 허용하지 않습니다.'},status_code=403)
            if 'application/json' not in request.headers.get('content-type',''):
                return JSONResponse({'detail':'JSON 요청이 필요합니다.'},status_code=415)
            size = request.headers.get('content-length','0')
            if not size.isdigit() or int(size) > 15_000_000:
                return JSONResponse({'detail':'요청 크기 초과'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'"
        return response

    @app.get('/api/health')
    def health():
        return {'ok':True,'mode':'historical_paper','live_orders':live.orders.enabled,
                'password_configured':bool(os.getenv('ADMIN_PASSWORD'))}

    @app.post('/api/login')
    def login(payload:Login,response:Response,request:Request):
        password = os.getenv('ADMIN_PASSWORD','')
        if not password:
            raise HTTPException(503,'서버 ADMIN_PASSWORD를 설정하고 다시 실행하세요.')
        address = request.client.host if request.client else 'local'
        with auth_lock:
            now = time.time()
            failures[address] = [t for t in failures.get(address,[]) if now-t < 300]
            if len(failures[address]) >= 10:
                raise HTTPException(429,'로그인 시도가 많습니다. 5분 후 재시도하세요.')
            if not hmac.compare_digest(payload.password.encode(),password.encode()):
                failures[address].append(now)
                raise HTTPException(401,'비밀번호가 올바르지 않습니다.')
            failures.pop(address,None)
        expiry = str(int(time.time())+12*3600)
        token = expiry+'.'+hmac.new(secret,expiry.encode(),hashlib.sha256).hexdigest()
        response.set_cookie('sqt_session',token,httponly=True,samesite='strict',max_age=12*3600,
                            secure=os.getenv('COOKIE_SECURE','false').lower() == 'true')
        return {'ok':True}

    @app.post('/api/logout')
    def logout(response:Response):
        response.delete_cookie('sqt_session')
        return {'ok':True}

    @app.get('/api/session')
    def session_info():
        return {'authenticated':True,'storage_notice':storage_notice}

    @app.get('/api/overview')
    def overview():
        return {'mode':'historical_paper','live_orders':False,'kis_configured':broker.configured,
            'storage_notice':storage_notice,
            'default_broker':default_broker,'toss_configured':toss.configured,
            'kis_environment':broker.environment,'defaults':Strategy().model_dump(),
            'markets':[status(e,utcnow()) for e in ('KRX','NASDAQ')],
            'datasets':[{k:v for k,v in d.items() if k!='bars'}|{'count':len(d['bars']),
                        'first':d['bars'][0]['date'],'last':d['bars'][-1]['date']} for d in store.all('datasets')],
            'sessions':[{'id':s['id'],'symbol':s['dataset']['instrument']['symbol'],'cursor':s['cursor'],
                         'done':s['done'],'auto':s['auto'],'created_at':s['created_at']} for s in store.all('sessions')],
            'health':store.all('health'), 'refresh_running':refresh_lock.locked()}

    @app.post('/api/datasets')
    def import_dataset(data:DatasetInput):
        if data.demo:
            raise ValueError('사용자 업로드는 실제 입력 자료로 구분합니다. demo=false를 사용하세요.')
        result = save_dataset(data.model_dump(mode='json'))
        return {'id':result['id'],'count':len(result['bars'])}

    @app.post('/api/kis/import')
    def kis_import(payload:KISImport):
        if not broker.configured:
            raise ValueError('KIS_APP_KEY·KIS_APP_SECRET 설정 후 서버를 재시작하세요.')
        id = uuid.uuid4().hex
        store.put('jobs',id,{'id':id,'status':'running'})
        def run():
            try:
                rows = broker.history(payload.instrument,payload.count)
                data = save_dataset({'instrument':payload.instrument.model_dump(),'bars':rows,
                    'source':'한국투자증권 KIS 수정 일봉','adjusted':True,'demo':False})
                store.put('jobs',id,{'id':id,'status':'done','dataset_id':data['id'],'count':len(rows)})
            except Exception as exc:
                message = str(exc) if isinstance(exc,ValueError) else 'KIS 연결 오류: 네트워크·API 설정을 확인하세요.'
                store.put('jobs',id,{'id':id,'status':'failed','error':message})
        threading.Thread(target=run,daemon=True).start()
        return {'id':id,'status':'running'}

    @app.post('/api/toss/check')
    def toss_check(instrument:Instrument):
        return {'ok':True,'provider':'toss','checked_at':utcnow().isoformat(),
                'stock':toss.stock(instrument),'live_orders':False}

    @app.post('/api/toss/import')
    def toss_import(payload:KISImport):
        if not toss.configured:
            raise TossError('TOSS_CLIENT_ID·TOSS_CLIENT_SECRET을 설정하고 서버를 재시작하세요.')
        id = uuid.uuid4().hex
        store.put('jobs',id,{'id':id,'status':'running','provider':'toss'})
        def run():
            try:
                rows = toss.history(payload.instrument,payload.count)
                data = save_dataset({'instrument':payload.instrument.model_dump(),'bars':rows,
                    'source':'토스증권 Open API 수정 일봉','adjusted':True,'demo':False})
                store.put('jobs',id,{'id':id,'status':'done','provider':'toss',
                                     'dataset_id':data['id'],'count':len(rows)})
            except Exception as exc:
                message = str(exc) if isinstance(exc,ValueError) else '토스 연결 오류: 네트워크·API 설정을 확인하세요.'
                store.put('jobs',id,{'id':id,'status':'failed','provider':'toss','error':message})
        threading.Thread(target=run,daemon=True).start()
        return {'id':id,'status':'running','provider':'toss'}

    @app.get('/api/jobs/{id}')
    def job(id:str):
        return store.get('jobs',id)

    @app.post('/api/sessions')
    def create_session(payload:Start):
        dataset = store.get('datasets',payload.dataset_id)
        evidence,events = snapshot(dataset)
        start = max(61,int(len(dataset['bars'])*0.7)) if payload.holdout else 61
        state = engine.create_state(dataset,payload.config.model_dump(),start)
        state.update(id=uuid.uuid4().hex,dataset_id=dataset['id'],dataset=dataset,evidence=evidence,events=events,
                     created_at=utcnow().isoformat(),requests=[])
        store.put('sessions',state['id'],state)
        return {'id':state['id']}

    def view(state):
        visible = state['dataset']['bars'][:state['cursor']]
        return {k:v for k,v in state.items() if k not in ('dataset','evidence','events','requests','decisions')} | {
            'instrument':state['dataset']['instrument'],'demo':state['dataset']['demo'],
            'source':state['dataset']['source'],'adjusted':state['dataset']['adjusted'],
            'total_bars':len(state['dataset']['bars']),
            'chart':chart_series(visible[-260:]),'technical':analyze(visible),
            'decisions':state['decisions'][-100:],'metrics':engine.metrics(state,state['dataset']),
            'evidence_count':len(state['evidence']),'event_count':len(state['events'])}

    @app.get('/api/sessions/{id}')
    def get_session(id:str):
        return view(store.get('sessions',id))

    @app.post('/api/sessions/{id}/step')
    def next_step(id:str,payload:Advance):
        return view(advance(id,payload.steps,payload.request_id))

    @app.patch('/api/sessions/{id}')
    def control(id:str,payload:Control):
        with store.connect(write=True) as con:
            state = store.get('sessions',id,con)
            for key,value in payload.model_dump(exclude_none=True).items():
                state[key] = value
            if state['done']:
                state['auto'] = False
            store.put('sessions',id,state,con)
        return view(state)

    @app.post('/api/backtest')
    def run_backtest(payload:Start):
        dataset = store.get('datasets',payload.dataset_id)
        evidence,events = snapshot(dataset)
        result = engine.backtest(dataset,payload.config.model_dump(),evidence,events,payload.holdout)
        result.update(dataset_id=dataset['id'],instrument=dataset['instrument'],
                      source=dataset['source'],generated_at=utcnow().isoformat())
        return result

    @app.post('/api/sessions/{id}/backtest')
    def session_backtest(id:str,payload:Start):
        state = store.get('sessions',id)
        dataset = state['dataset']
        if payload.dataset_id != dataset['id'] or payload.config.model_dump() != state['config']:
            raise ValueError('세션 생성 당시의 데이터와 전략으로 검증해야 합니다.')
        result = engine.backtest(dataset,state['config'],state['evidence'],state['events'],payload.holdout)
        result.update(dataset_id=dataset['id'],instrument=dataset['instrument'],source=dataset['source'],
                      generated_at=utcnow().isoformat(),
                      snapshot={'dataset':dataset,'evidence':state['evidence'],'events':state['events']})
        return result

    @app.get('/api/research')
    def research():
        table=translate.cache(store)
        return {'evidence':translate.apply(sorted(store.all('evidence'),key=lambda n:n['available_at'],reverse=True)[:300],table),
                'events':translate.apply(sorted(store.all('events'),key=lambda e:e['scheduled_at']),table), 'health':store.all('health')}

    @app.get('/api/themes')
    def theme_overview(limit:int=10,members:bool=True):
        """국내 테마별 등락과 구성종목.

        등락률 상위는 **이미 오른** 테마다. 매수 신호가 아니라 "무엇이 함께 움직이는가"
        를 보는 구조 정보로 쓴다. 응답의 note 를 화면에서 지우지 말 것.
        """
        return themes.overview(limit=max(1,min(limit,20)),with_members=members)

    @app.post('/api/themes/recommend')
    def theme_recommend(limit:int=12,picks:int=12):
        """테마 데이터와 미국 거시 자료를 AI 에 넘겨 관심 종목을 고르게 한다.

        주문은 일어나지 않는다. AI 는 넘겨준 후보 목록 안에서만 고를 수 있고,
        목록 밖 종목이 하나라도 섞이면 추천 전체를 폐기한다. 종목 코드를 지어낼 수
        없게 만드는 것이 이 검증의 목적이다.
        """
        data = themes.overview(limit=max(3,min(limit,20)))
        if data['error']:
            raise HTTPException(502,data['error'])
        candidates = {}
        skipped = 0
        for theme in data['themes']:
            for stock in theme.get('stocks',[]):
                # 거래정지·관리종목은 관심 종목에 넣어도 주문이 나가지 않는다. 후보에서 뺀다.
                if not stock.get('tradable',True):
                    skipped += 1
                    continue
                entry = candidates.setdefault(stock['symbol'],
                                              {'symbol':stock['symbol'],'name':stock['name'],
                                               'managed':bool(stock.get('managed')),'themes':[]})
                entry['themes'].append(theme['name'])
        if not candidates:
            raise HTTPException(502,'테마 구성 종목을 가져오지 못했습니다.')

        evidence = sorted(store.all('evidence'),key=lambda n:n['available_at'],reverse=True)[:60]
        events = sorted(store.all('events'),key=lambda e:e['scheduled_at'])[-30:]
        snapshot = {
            'at':utcnow().isoformat(),'market':'KRX','currency':'KRW','max_picks':max(1,min(picks,12)),
            'themes':[{k:v for k,v in th.items() if k!='stocks'} |
                      {'symbols':[s['symbol'] for s in th.get('stocks',[]) if s.get('tradable',True)]}
                      for th in data['themes']],
            'candidates':list(candidates.values()),
            'us_evidence':[{'id':n['id'],'source':n['source'],'title':n['title'],'impact':n['impact'],
                            'published_at':n['published_at'],'assessment':n.get('assessment','')}
                           for n in evidence],
            'us_events':[{'title':e['title'],'source':e['source'],'impact':e['impact'],
                          'scheduled_at':e['scheduled_at']} for e in events],
            'measured_notes':{
                'intra_theme_correlation':0.387,'cross_theme_correlation':0.274,
                'single_theme_volatility_multiple':1.83,
                'note':'같은 테마 종목은 함께 움직입니다. 한 테마에 몰면 분산이 되지 않습니다.'},
        }
        try:
            plan = paper.ai.recommend(snapshot)
        except AIError as exc:
            raise HTTPException(503,str(exc))

        unknown = [x['symbol'] for x in plan['picks'] if x['symbol'] not in candidates]
        if unknown:
            raise HTTPException(422,'AI 가 후보 목록에 없는 종목을 제시해 추천을 폐기했습니다: '
                                    + ', '.join(unknown[:5]))
        for pick in plan['picks']:
            pick['name'] = candidates[pick['symbol']]['name']
            pick['themes'] = candidates[pick['symbol']]['themes']
        used = {p['theme'] for p in plan['picks']}
        plan['candidate_count'] = len(candidates)
        plan['skipped_untradable'] = skipped
        plan['theme_spread'] = len(used)
        plan['watchlist'] = [{'exchange':'KRX','symbol':p['symbol']} for p in plan['picks']]
        plan['note'] = ('AI 가 고른 감시 후보입니다. 주문은 계좌를 만들고 운용을 시작해야 일어납니다. '
                        f'{len(used)}개 테마에 분산되었습니다.')
        return plan

    @app.post('/api/research/refresh')
    def refresh_now():
        threading.Thread(target=refresh,daemon=True).start()
        return {'status':'started'}

    @app.post('/api/research/import')
    def research_import(payload:ResearchImport):
        with store.connect(write=True) as con:
            for kind,items in (('evidence',payload.evidence),('events',payload.events)):
                for value in items:
                    item = value.model_dump(mode='json')
                    if item['source'] == 'demo' or not news.source_matches(item['source'],item['url']):
                        raise ValueError('자료의 출처 유형과 원문 도메인이 일치해야 합니다.')
                    if kind == 'evidence' and (value.available_at > utcnow() or (value.assessed_at and value.assessed_at > utcnow())):
                        raise ValueError('뉴스 수집·평가 시각은 미래일 수 없습니다.')
                    if kind == 'events' and value.known_at > utcnow():
                        raise ValueError('일정을 알게 된 시각은 미래일 수 없습니다.')
                    # Store revisions independently; existing sessions already hold immutable snapshots.
                    item['imported_at'] = utcnow().isoformat()
                    store.put(kind,item['id'],item,con)
        return {'evidence':len(payload.evidence),'events':len(payload.events)}

    @app.get('/api/paper/status')
    def paper_status():
        return {'broker_configured':toss.configured,'mode':'forward_paper','live_orders':False,
                'accounts':[{'id':s['id'],'created_at':s['created_at'],'currency':s['config']['currency'],
                             'status':s['status']} for s in store.all('paper_accounts')]}

    @app.post('/api/paper/ai-check')
    def paper_ai_check():
        state=paper.ai.status()
        ai_status_cache.update(at=time.time(),value=state)
        return state

    @app.get('/api/ai/models')
    def ai_models():
        from .model_catalog import CATALOG,CHECKED_AT,SOURCES
        return {'providers':CATALOG,'checked_at':CHECKED_AT,'sources':SOURCES}

    @app.get('/api/ai/providers')
    def ai_providers():
        states={k:v.status() for k,v in paper.ai.providers.items()}
        ai_status_cache.update(at=0.0,value=None)
        return {'selected':paper.ai.provider,'providers':states}

    @app.post('/api/ai/provider')
    def ai_select(payload: dict):
        if any(s.get('ai', {}).get('pending') for kind in ('daytrade_accounts', 'paper_accounts', 'live_accounts')
               for s in store.all(kind)):
            raise HTTPException(409, 'AI 판단이 진행 중입니다. 완료 후 전환하세요.')
        try:
            result = paper.ai.select(payload.get('provider'), payload.get('model'),payload.get('allow_usage_credits',False))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        ai_status_cache['at'] = 0.0
        ai_status_cache['value'] = None
        return result

    # ── AI 인증 보관 · 웹 로그인 ─────────────────────────────────────────────
    def reset_ai_status():
        ai_status_cache.update(at=0.0,value=None)
        paper.ai._auth_checked=None
        for child in paper.ai.providers.values():
            child._auth_checked=None

    def login_provider(provider):
        if provider not in ('claude','codex'):
            raise HTTPException(404,'claude 또는 codex 만 지원합니다.')
        return provider

    @app.get('/api/ai/auth')
    def ai_auth_view():
        return {'storage':ai_auth.storage(),'claude_mode':ai_auth.claude_mode(),
                'claude_source':ai_auth.claude_source(),
                'logins':{k:login_manager.get(k) for k in ('claude','codex')}}

    @app.post('/api/ai/login/{provider}/start')
    def ai_login_start(provider:str):
        from .subscription_ai import executable, child_env
        from .ai_router import codex_command, codex_env
        login_provider(provider)
        try:
            if provider=='claude':
                env=child_env()
                env.pop('CLAUDE_CODE_OAUTH_TOKEN',None)
                def done():
                    ai_auth.use_claude_login()
                    reset_ai_status()
                return login_manager.start('claude',executable(),env,done)
            return login_manager.start('codex',codex_command(),codex_env(),reset_ai_status)
        except AIError as exc:
            raise HTTPException(400,str(exc)) from None

    @app.get('/api/ai/login/{provider}')
    def ai_login_view(provider:str):
        return login_manager.get(login_provider(provider))

    @app.post('/api/ai/login/{provider}/code')
    def ai_login_code(provider:str,payload:dict):
        return login_manager.submit(login_provider(provider),str(payload.get('code','')))

    @app.post('/api/ai/login/{provider}/cancel')
    def ai_login_cancel(provider:str):
        return login_manager.cancel(login_provider(provider))

    @app.post('/api/ai/claude-token')
    def ai_claude_token(payload:dict):
        action=payload.get('action','save')
        if action=='save':
            ai_auth.save_claude_token(str(payload.get('token','')))
        elif action=='reset':
            ai_auth.reset_claude()
        else:
            raise ValueError('save 또는 reset 만 가능합니다.')
        reset_ai_status()
        return {'claude_mode':ai_auth.claude_mode(),'claude_source':ai_auth.claude_source(),
                'status':paper.ai.providers['claude'].status()}

    @app.post('/api/paper/accounts')
    def paper_create(payload:PaperConfig):
        return paper.create(payload.model_dump(mode='json'))

    @app.get('/api/paper/accounts/{id}')
    def paper_account(id:str):
        return paper.view(id)

    @app.post('/api/paper/accounts/{id}/control')
    def paper_control(id:str,payload:PaperControl):
        return paper.control(id,payload.action)

    @app.get('/api/paper/accounts/{id}/export')
    def paper_export(id:str):
        return paper.export(id)

    # ── 당일 단타 ───────────────────────────────────────────────────────────
    @app.get('/api/daytrade/accounts')
    def daytrade_list():
        rows=[r for r in store.all(daytrade.account_kind) if not r.get('archived')]
        return {'accounts':[{'id':s['id'],'mode':s.get('mode','paper'),'status':s['status'],'phase':s['phase'],'day':s['day'],
                             'positions':len(s['positions']),'created_at':s['created_at']} for s in rows],
                'defaults':DayTradeConfig(strategy='portfolio',max_positions=8,position_pct=10).model_dump(mode='json')}

    @app.get('/api/financial-brief')
    def financial_brief_view():
        from .financial_brief import build
        return build(store)

    @app.get('/api/daytrade/status')
    def daytrade_status():
        """모드별 현재 계좌 하나씩. 화면은 '모의 / 실거래' 둘 중 하나만 고른다."""
        return {'paper':daytrade.current('paper'),'live':daytrade.current('live'),
                'live_status':daytrade.live.status(),
                'watchlist_live_running':any(r['status']=='running' for r in store.all('live_accounts')),
                'defaults':DayTradeConfig(strategy='portfolio',max_positions=8,position_pct=10).model_dump(mode='json')}

    @app.post('/api/daytrade/accounts')
    def daytrade_create(payload:DayTradeCreate):
        if payload.mode=='live' and payload.acknowledge_live!='실거래':
            raise HTTPException(400,'실제 돈으로 주문하는 계좌입니다. 화면에서 "실거래"를 입력해 확인하세요.')
        try:
            return daytrade.create(payload.model_dump(mode='json',exclude={'acknowledge_live'}))
        except (ValueError,TossError) as exc:
            raise HTTPException(400,str(exc))

    @app.get('/api/daytrade/accounts/{id}')
    def daytrade_account(id:str):
        return daytrade.view(id)

    @app.post('/api/daytrade/accounts/{id}/config')
    def daytrade_update_config(id:str,payload:DayTradeConfig):
        """운용 중에도 조건을 바꾼다. 보낸 항목만 반영하고 자금·모드는 무시한다."""
        try:
            return daytrade.update_config(id,payload.model_dump(mode='json',exclude_unset=True))
        except (ValueError,TossError) as exc:
            raise HTTPException(400,str(exc))

    @app.post('/api/daytrade/accounts/{id}/control')
    def daytrade_control(id:str,payload:PaperControl):
        state=store.get(daytrade.account_kind,id)
        if payload.action=='start' and state.get('mode')=='live' and payload.acknowledge_live!='실거래 시작':
            raise HTTPException(400,'실제 자금 주문입니다. 화면에서 "실거래 시작"을 입력해 확인하세요.')
        try:
            return daytrade.control(id,payload.action)
        except (ValueError,TossError) as exc:
            raise HTTPException(400,str(exc))

    @app.post('/api/daytrade/accounts/{id}/orders/{intent_id}/bind')
    def daytrade_bind(id:str,intent_id:str,payload:BindOrder):
        try:
            return daytrade.bind_order(id,intent_id,payload.broker_order_id)
        except (ValueError,TossError) as exc:
            raise HTTPException(400,str(exc))

    @app.post('/api/daytrade/accounts/{id}/orders/{intent_id}/discard')
    def daytrade_discard(id:str,intent_id:str):
        try:
            return daytrade.discard_order(id,intent_id)
        except (ValueError,TossError) as exc:
            raise HTTPException(400,str(exc))

    @app.get('/api/daytrade/accounts/{id}/export')
    def daytrade_export(id:str):
        return daytrade.export(id)

    @app.get('/api/daytrade/scan')
    def daytrade_scan_preview(limit:int=15):
        """지금 스캔하면 무엇이 나오는지 미리 본다. AI 호출 없음. 계좌 상태를 바꾸지 않는다."""
        from . import scanner
        result=scanner.premarket_scan(toss,{'max_candidates':max(3,min(limit,30))})
        result['candidates']=[DayTradeService._candidate_view(c) for c in result['candidates']]
        return result

    @app.get('/api/notify/status')
    def notify_status():
        from . import notify
        return {'configured':notify.configured(),
                'chat_id_set':bool(os.getenv('TELEGRAM_CHAT_ID','').strip()),
                'token_set':bool(os.getenv('TELEGRAM_BOT_TOKEN','').strip()),
                'level':notify.level(),'public_url_set':bool(os.getenv('PUBLIC_URL','').strip())}

    @app.post('/api/notify/level')
    def notify_level(payload:dict):
        level=str(payload.get('level',''))
        if level not in notify_module.LEVELS:
            raise ValueError('trades 또는 all 을 선택하세요.')
        store.put('settings','telegram',{'level':level,'at':utcnow().isoformat()})
        notify_module.set_level(level)
        return {'level':notify_module.level()}

    @app.post('/api/notify/test')
    def notify_test():
        """설정 확인용. 동기로 보내 결과를 바로 돌려준다."""
        from . import notify
        if not notify.configured():
            raise HTTPException(400,'TELEGRAM_BOT_TOKEN 과 TELEGRAM_CHAT_ID 를 .env 에 넣고 컨테이너를 다시 만드세요.')
        ok=notify.send_now('✅ Stock Quant 알림 연결 확인 · '+notify.kst()+chr(10)+'이 메시지가 보이면 설정이 맞습니다.')
        if not ok:
            raise HTTPException(502,'텔레그램이 전송을 거절했습니다. 토큰·chat_id 를 확인하세요. (봇에게 먼저 말을 걸어야 chat 이 열립니다)')
        return {'ok':True}

    # ── 대시보드 · 뉴스 ─────────────────────────────────────────────────────
    ai_status_cache={'at':0.0,'value':None}
    ai_probe_lock=threading.Lock()

    def dashboard_ai_status(fast):
        if not fast:
            return cached_ai_status()
        if (ai_status_cache['value'] is None or time.time()-ai_status_cache['at']>600) and ai_probe_lock.acquire(blocking=False):
            def probe():
                try:cached_ai_status()
                finally:ai_probe_lock.release()
            threading.Thread(target=probe,daemon=True).start()
        return ai_status_cache['value'] or {'checking':True,'message':'AI 연결 확인 중'}

    def cached_ai_status():
        """claude --version 을 15초마다 띄우지 않도록 10분 캐시. 모델 호출은 없다."""
        import time as _time
        now=_time.time()
        if ai_status_cache['value'] is None or now-ai_status_cache['at']>600:
            try:
                ai_status_cache['value']=paper.ai.status()
            except Exception as exc:  # noqa: BLE001
                ai_status_cache['value']={'installed':False,'authenticated':False,'message':str(exc)[:200]}
            ai_status_cache['at']=now
        return ai_status_cache['value']

    @app.get('/api/dashboard')
    def dashboard(mode:str='',fast:bool=False):
        """첫 화면. 연결 상태 + 단타 계좌 요약 + 오늘 시장 여부. 한 번의 호출로 그린다.

        mode 를 안 주면: 실거래 계좌가 운용 중이면 실거래, 아니면 모의."""
        from . import notify
        paper_view=daytrade.current('paper'); live_view=daytrade.current('live')
        if mode not in ('paper','live'):
            mode='live' if live_view and live_view['status']=='running' else ('paper' if paper_view else ('live' if live_view else 'paper'))
        view=live_view if mode=='live' else paper_view
        rows=[r for r in store.all(daytrade.account_kind) if not r.get('archived')]
        now=utcnow()
        day=now.astimezone(KST).strftime('%Y-%m-%d')
        try:
            session=calendar('KRX').is_session(day)
        except Exception:  # noqa: BLE001
            session=True
        return {'now':now.isoformat(),'day':day,'market_session':session,
                'connections':{'toss':toss.configured,'claude':dashboard_ai_status(fast),'telegram':notify.configured()},
                'accounts':[{'id':r['id'],'mode':r.get('mode','paper'),'status':r['status'],'phase':r['phase'],'day':r['day'],
                             'positions':len(r['positions']),'created_at':r['created_at']} for r in rows],
                'mode':mode,'has':{'paper':bool(paper_view),'live':bool(live_view)},
                'live_running':bool(live_view and live_view['status']=='running'),
                'live_status':daytrade.live.status(),
                'account':view}

    @app.get('/api/news/kr')
    def news_kr(hours:int=24):
        """국내 시장·정책·해외 요인 기사 제목. 10분 캐시. 실패해도 빈 목록."""
        from . import krnews
        rows=krnews.market_news(hours=max(1,min(hours,72)))
        return {'rows':rows,'topics':{'market':'국내 증시','policy':'금리·정책','global':'해외 요인'},
                'note':'제목만 수집합니다. 종목별 기사 본문은 당일 단타 후보 안에 들어가고, AI 장전 판단은 시장 제목 15건 + 후보 종목 기사 본문 + 미국 거시 30건을 봅니다.'}

    def news_account():
        live_view=daytrade.current('live')
        if live_view and live_view['status']=='running':
            return live_view
        return daytrade.current('paper') or live_view

    @app.get('/api/news/center')
    def news_center_view(hours:int=72):
        """흩어진 뉴스를 보유·후보·국내·정책·해외·테마로 분류해 한 목록으로. AI 호출 없음."""
        hours=max(6,min(hours,168))
        try:
            rows=krnews.market_news(hours=min(hours,72))
        except Exception:  # noqa: BLE001 - 공급원 장애여도 나머지는 보여준다
            rows=[]
        return news_center.build(store,news_account(),rows,hours)

    diagnostics_cache={'at':0.0,'ai':None}

    @app.get('/api/diagnostics')
    def diagnostics_view(fresh:bool=False):
        """운영 점검: 회의가 왜 안 열렸는지, 알림이 왜 안 왔는지. 모델 호출·주문 없음."""
        from .diagnostics import checks
        if fresh or diagnostics_cache['ai'] is None or time.time()-diagnostics_cache['at']>60:
            try:
                diagnostics_cache['ai']=paper.ai.status()
            except Exception as exc:  # noqa: BLE001
                diagnostics_cache['ai']={'installed':False,'authenticated':False,'message':str(exc)[:160]}
            diagnostics_cache['at']=time.time()
        day=utcnow().astimezone(KST).strftime('%Y-%m-%d')
        try:
            session_day=calendar('KRX').is_session(day)
        except Exception:  # noqa: BLE001
            session_day=True
        return checks(news_account(),ai_status=diagnostics_cache['ai'],toss_configured=toss.configured,
                      session=session_day,telegram=notify_module.stats(),storage=ai_auth.storage())

    @app.post('/api/news/translate')
    def news_translate():
        """번역 대기 제목을 백그라운드에서 한 번 번역한다. 하루 상한·판단 우선 규칙은 같다."""
        titles=news_center.pending_titles(store,news_account())
        threading.Thread(target=lambda:translate.run_pending(store,paper.ai,titles),daemon=True).start()
        return dict(translate.status(store),started=True)

    @app.get('/api/live/status')
    def live_status():
        return {'mode':'live','enabled':live.orders.enabled,'account_configured':bool(live.orders.account_seq),
                'broker_configured':toss.configured,'live_orders':live.orders.enabled,
                'capital_caps':{c:live.cap(c) for c in ('KRW','USD')},
                'accounts':[{'id':s['id'],'created_at':s['created_at'],'currency':s['config']['currency'],
                             'status':s['status']} for s in store.all('live_accounts')]}

    @app.get('/api/live/validation/{paper_id}')
    def live_validation(paper_id:str):
        return live.validation(paper_id)

    @app.get('/api/live/broker-accounts')
    def live_broker_accounts():
        return {'accounts':live.orders.accounts()}

    @app.post('/api/live/accounts')
    def live_create(payload:LiveConfig):
        return live.create(payload.model_dump(mode='json'))

    @app.get('/api/live/accounts/{id}')
    def live_view(id:str):
        return live.view(id)

    @app.post('/api/live/accounts/{id}/control')
    def live_control(id:str,payload:PaperControl):
        if payload.action=='start' and payload.acknowledge_live!='실거래 시작':
            raise ValueError('실제 자금 주문입니다. 화면에서 실거래 시작을 입력해 확인하세요.')
        return live.control(id,payload.action)

    @app.get('/api/live/accounts/{id}/export')
    def live_export(id:str):
        return live.export(id)

    @app.post('/api/live/orders/{id}/bind')
    def live_bind(id:str,payload:BindOrder):
        return live.bind_unknown(id,payload.broker_order_id)

    app.mount('/static',StaticFiles(directory=STATIC),name='static')

    @app.get('/')
    def index():
        return FileResponse(STATIC/'index.html')

    return app


app = create_app()
