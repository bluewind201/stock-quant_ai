"""Independent market/risk worker and asynchronous subscription-model decision worker."""
import copy
import os
import threading
import uuid
from datetime import timedelta
from . import forward
from .models import Instrument, stamp, utcnow
from .technical import analyze, intraday_features
from .session_policy import account_context
from .subscription_ai import SubscriptionAI, AIError
from . import themes as theme_source
from .toss import TossError


class PaperService:
    account_kind='paper_accounts'
    decision_kind='paper_decisions'
    def __init__(self,store,broker,ai=None):
        self.store,self.broker,self.ai=store,broker,ai or SubscriptionAI()
        self.lock=threading.RLock(); self.pulse_lock=threading.Lock(); self.stop_event=threading.Event()
        self.features_lock=threading.Lock()

    def initialize(self,resume=False):
        with self.lock:
            resumed=set()
            for state in self.store.all(self.account_kind):
                currency=state['config']['currency']
                restore=resume and currency not in resumed and state['status']=='running' and state['mode']=='forward_paper'
                state['status']='running' if restore else 'stopped'
                state['ai']['pending']=False; state['generation']+=1; state['last_poll_at']=None
                if restore:resumed.add(currency)
                forward.log(state,utcnow(),'서버 시작: 이전 모의 운용을 새 시세 확인부터 재개합니다.' if restore else
                            '서버 시작: 자동 운용은 직접 시작할 때까지 정지합니다.')
                self.store.put(self.account_kind,state['id'],state)

    def start_worker(self):
        def worker():
            while not self.stop_event.wait(1):
                for state in self.store.all(self.account_kind):
                    if state['status']=='running' and (not state['last_poll_at'] or
                        (utcnow()-stamp(state['last_poll_at'])).total_seconds()>=state['config']['poll_seconds']):
                        try: self.pulse(state['id'])
                        except Exception:
                            with self.lock:
                                latest=self.store.get(self.account_kind,state['id'])
                                latest['last_poll_at']=utcnow().isoformat()
                                latest['errors']['monitor']='시세 감시 오류. 최신 시세 없이는 가상 체결하지 않습니다.'
                                self.store.put(self.account_kind,state['id'],latest)
        threading.Thread(target=worker,daemon=True,name='paper-market-risk').start()

    def create(self,config):
        state=forward.create(config,utcnow())
        with self.lock: self.store.put(self.account_kind,state['id'],state)
        return self.view(state['id'])

    def view(self,id):
        state=self.store.get(self.account_kind,id)
        state['metrics']=forward.metrics(state)
        state['market_session']=account_context(state,utcnow())
        state['quote_status']={i['symbol']:forward.fresh(state,state['quotes'].get(i['symbol']),utcnow()) for i in state['config']['watchlist']}
        # Full evidence/snapshots are available in the explicit export, not every UI poll.
        state['trades']=state['trades'][-100:]
        return state

    def control(self,id,action):
        if action=='start':
            if not self.broker.configured: raise ValueError('토스 시세 API 키를 먼저 설정하세요.')
            if not self.ai.status()['authenticated']: raise ValueError('설정에서 선택한 AI의 구독 인증 상태를 먼저 확인하세요.')
        with self.lock:
            state=self.store.get(self.account_kind,id)
            if action=='start':
                if any(x['id']!=id and x['status']=='running' and x['config']['currency']==state['config']['currency'] for x in self.store.all(self.account_kind)):
                    raise ValueError('같은 통화에서는 계좌 한 개만 자동 운용합니다. 다른 계좌를 먼저 중지하세요.')
                state['status']='running'; state['last_poll_at']=None; state['generation']+=1
                forward.log(state,utcnow(),'실제 시세 기반 AI 가상 운용 시작')
            elif action=='stop':
                state['status']='stopped'; state['generation']+=1
                forward.log(state,utcnow(),'전체 운용 정지: 시세 감시와 가상 손절도 정지합니다.')
            elif action=='pause_entries': state['entries_enabled']=False
            elif action=='resume_entries':
                if state['halt_reason']=='일일 손실 한도' and forward.market_day(state,utcnow())>state.get('halted_day',state['market_day']):
                    if (1-forward.equity(state)/state['peak'])*100<state['config']['max_drawdown_pct']:
                        state['halt_reason']=''
                if state['liquidate'] and not state['positions']: state['liquidate']=False
                if state['halt_reason'] or state['liquidate']:
                    raise ValueError('위험 한도 또는 청산 중입니다. 일일 손실 정지는 다음 거래일에 직접 재개할 수 있습니다. 최대 낙폭 정지는 새 검증 계획이 필요합니다.')
                state['entries_enabled']=True
            elif action=='liquidate':
                state['liquidate']=True; state['entries_enabled']=False; state['last_poll_at']=None
                forward.log(state,utcnow(),'가상 포지션 전량 청산 요청: 운용 중 최신 시세와 잔량이 있을 때 체결합니다.')
            elif action=='review':
                if state['status']!='running': raise ValueError('AI 모의 운용을 먼저 시작하세요.')
                state['force_review']=True; state['last_poll_at']=None
            else: raise ValueError('지원하지 않는 운용 명령입니다.')
            state['revision']+=1
            self.store.put(self.account_kind,id,state)
        return self.view(id)

    def research(self,now):
        rows=[n for n in self.store.all('evidence') if n['source']!='demo' and
              timedelta(0)<=now-stamp(n['available_at'])<=timedelta(hours=72) and
              timedelta(0)<=now-stamp(n['published_at'])<=timedelta(days=14 if n['source'] in ('fed','white_house') else 3)]
        rows.sort(key=lambda n:(n['source'] in ('fed','white_house'),n['available_at']),reverse=True)
        news=[{k:n.get(k) for k in ('id','title','source','url','published_at','available_at','symbols','impact','assessment')}
              for n in rows[:50]]
        events=[e for e in self.store.all('events') if e['source']!='demo' and stamp(e['known_at'])<=now and
                now-timedelta(hours=2)<=stamp(e['scheduled_at'])<=now+timedelta(days=7)]
        events.sort(key=lambda e:e['scheduled_at'])
        return news,events[:40]

    def pulse(self,id):
        if not self.pulse_lock.acquire(blocking=False): return
        try:
            before=self.store.get(self.account_kind,id)
            if before['status']!='running': return
            now=utcnow(); errors={}; quotes={}
            instruments=[Instrument.model_validate(i) for i in before['config']['watchlist']]
            opened=[i for i in instruments if forward.status(i.exchange,now)['open']]
            if opened:
                try: quotes=self.broker.quotes(opened)
                except (ValueError, OSError): errors['quotes']='토스 현재가 조회 실패: 인증·허용 IP·시세 권한을 확인하세요.'
                for item in opened:
                    symbol=item.symbol
                    if symbol not in quotes: errors[symbol]='유효한 현재가·호가가 없습니다.'
            with self.lock:
                state=self.store.get(self.account_kind,id)
                if state['status']!='running' or state['generation']!=before['generation']: return
                # Reject time travel in the market stream; retain last known mark, but don't fill stale data.
                for symbol,q in quotes.items():
                    old=state['quotes'].get(symbol)
                    if old and (stamp(q['timestamp'])<stamp(old['timestamp']) or stamp(q['book_timestamp'])<stamp(old['book_timestamp'])):
                        errors[symbol]='이전 시각의 시세 응답을 거부했습니다.'
                        continue
                    state['quotes'][symbol]=q
                errors.update({k:v for k,v in state['errors'].items() if k.startswith('features_') or k in ('reconciliation','costs')})
                state['errors']=errors; state['last_poll_at']=utcnow().isoformat()
                self.protect_state(state,utcnow())
                self.store.put(self.account_kind,id,state)
            phase=account_context(before,now)['phase']
            if (opened or phase in ('preopen','postclose')) and self.features_lock.acquire(blocking=False):
                threading.Thread(target=self.refresh_features,args=(id,instruments),daemon=True,name='paper-chart-data').start()
            self.maybe_review(id)
        finally:
            self.pulse_lock.release()

    def refresh_features(self,id,instruments):
        try:
            for item in instruments:
                before=self.store.get(self.account_kind,id)
                if before['status']!='running' or self.stop_event.is_set(): return
                now=utcnow(); symbol=item.symbol; feat=copy.deepcopy(before['features'].get(symbol,{})); error=''
                day=forward.market_day(before,now)
                try:
                    if feat.get('reference_day')!=day:
                        bars=self.broker.history(item,220)
                        feat['daily']=analyze(bars); feat['daily_bars']=bars[-65:]
                        feat['daily_at']=bars[-1]['date']; feat['reference_day']=day
                        feat['daily_checked_at']=utcnow().isoformat()
                    if not feat.get('minute_checked_at') or (now-stamp(feat['minute_checked_at'])).total_seconds()>=60:
                        minutes=self.broker.minutes(item)
                        cached=feat.get('minute_cache',[])
                        minutes=sorted({b['timestamp']:b for b in cached+minutes}.values(),key=lambda b:b['timestamp'])[-1200:]
                        feat['minute_cache']=minutes
                        feat['minute']=analyze(minutes) if len(minutes)>=61 else None
                        feat['minute_bars']=minutes[-65:]
                        feat['minute_at']=minutes[-1]['timestamp'] if minutes else '1970-01-01T00:00:00Z'
                        feat['minute_checked_at']=utcnow().isoformat()
                        feat['intraday']=intraday_features(minutes,item.exchange,now,(feat.get('daily') or {}).get('price'))
                except (ValueError,OSError): error='종목·일봉·분봉 조회 실패. 신규 매수를 보류합니다.'
                with self.lock:
                    state=self.store.get(self.account_kind,id)
                    if state['generation']!=before['generation']: return
                    state['features'][symbol]=feat
                    if error: state['errors']['features_'+symbol]=error
                    else: state['errors'].pop('features_'+symbol,None)
                    self.store.put(self.account_kind,id,state)
        finally:
            self.features_lock.release()

    def maybe_review(self,id):
        now=utcnow(); news,events=self.research(now)
        with self.lock:
            state=self.store.get(self.account_kind,id); ai=state['ai']; cfg=state['config']
            if state['status']!='running' or ai['pending'] or (self.ai.lock.locked() and not getattr(self.ai,'background_task',False)) or self.stop_event.is_set(): return
            market=account_context(state,now)
            planning=market['phase'] in ('preopen','postclose')
            if not planning and not any(forward.fresh(state,q,now) for q in state['quotes'].values()): return
            if self.review_blocked(state):return
            if not state['positions'] and not any(f.get('daily') for f in state['features'].values()): return
            day=forward.market_day(state,now)
            if ai['calls_day']!=day: ai['calls_day']=day; ai['calls_today']=0
            elapsed=(now-stamp(ai['last_requested_at'])).total_seconds() if ai['last_requested_at'] else float('inf')
            if elapsed<cfg['ai_cooldown_seconds']: return
            if ai.get('retry_after') and now<stamp(ai['retry_after']):return
            if ai['calls_today']>=cfg['max_ai_calls_per_day']:
                ai['error']='하루 AI 판단 횟수 한도에 도달했습니다. 시세 감시·손절은 계속합니다.'
                self.store.put(self.account_kind,id,state); return
            moved=any(abs(q['price']/ai['last_prices'].get(s,q['price'])-1)>=.01 for s,q in state['quotes'].items())
            news_ids={n['id'] for n in news if n['source'] in ('fed','white_house') and n.get('impact')=='high'}
            changed=bool(news_ids-set(ai['last_news_ids']))
            phase_changed=ai.get('last_phase')!=market['key']
            if planning and not phase_changed and not state.get('force_review'):return
            interval=max(cfg['ai_cooldown_seconds'],min(ai['next_seconds'],market['review_seconds']))
            if market['phase']=='midday':interval=max(interval,1800)
            if not (state.get('force_review') or phase_changed or elapsed>=interval or moved or changed): return
            # Save several calls for the closing review instead of spending the budget in early churn.
            if ai['calls_today']>=max(1,cfg['max_ai_calls_per_day']-3) and market['phase'] not in ('closing','postclose') and not changed:return
            with self.store.connect(write=True) as con:
                budget_id=now.date().isoformat()
                try:budget=self.store.get('ai_budget',budget_id,con)
                except KeyError:budget={'calls':0}
                if budget['calls']>=int(os.getenv('SQT_AI_DAILY_BUDGET','64')):
                    ai['error']='통합 일일 AI 호출 한도. 시세 감시·손절은 계속합니다.'
                    self.store.put(self.account_kind,id,state,con);return
                budget['calls']+=1;self.store.put('ai_budget',budget_id,budget,con)
            trigger='직접 요청' if state.get('force_review') else '중요 발표' if changed else market['label'] if phase_changed else '가격 1% 변동' if moved else '정기 재검토'
            ai['last_phase']=market['key']
            ai['pending']=True; ai['last_requested_at']=now.isoformat(); ai['calls_today']+=1; ai['error']=''
            ai['last_prices']={s:q['price'] for s,q in state['quotes'].items()}; ai['last_news_ids']=list(news_ids)
            state['force_review']=False
            snapshot={'snapshot_id':uuid.uuid4().hex,'at':now.isoformat(),'revision':state['revision'],
                'generation':state['generation'],'trigger':trigger,'market_session':market,'planning_only':planning,'operating_mode':state['mode'],
                'config':cfg,'cash':state['cash'],
                'equity':forward.equity(state),'positions':state['positions'],'quotes':state['quotes'],
                'features':{s:{k:(v[-30:] if k.endswith('_bars') else v) for k,v in f.items() if k!='minute_cache'} for s,f in state['features'].items()},
                'allocation':state['allocation'],'news':news,'events':events,
                'news_scope':'수집 제목과 등록 평가. 원문 전문·실시간 속보 완전성은 보장되지 않음',
                'entry_blocks':{i['symbol']:forward.entry_reasons(state,i['symbol'],now,news,events) for i in cfg['watchlist']},
                'themes':self.watchlist_themes(cfg),
                'recent_trades':state['trades'][-15:],'performance':forward.metrics(state)['performance'],
                'other_market_context':[{'currency':s['config']['currency'],'last_risk_at':s['last_risk_at'],
                    'quotes':s['quotes'],'summary':s['ai']['summary']} for s in sorted(
                        [s for s in self.store.all(self.account_kind) if s['config']['currency']!=cfg['currency']],
                        key=lambda s:s['last_risk_at'] or '',reverse=True)[:1]],
                'research_health':self.store.all('health')}
            self.store.put(self.account_kind,id,state)
            self.store.put(self.decision_kind,snapshot['snapshot_id'],{'id':snapshot['snapshot_id'],'account_id':id,
                'status':'pending','snapshot':snapshot})
        threading.Thread(target=self.complete_review,args=(id,snapshot),daemon=True,name='paper-gpt-decision').start()

    def watchlist_themes(self,cfg):
        """관심 종목이 속한 테마만 추린다. 20개를 통째로 넣으면 스냅샷만 커지고 쓸모가 없다.

        수집 실패는 조용히 빈 목록이 된다. 외부 페이지 때문에 판단이 멈추면 안 된다.
        """
        if cfg['currency']!='KRW': return []
        held={i['symbol'] for i in cfg['watchlist']}
        try: data=theme_source.overview(limit=20)
        except Exception: return []
        if data.get('error'): return []
        out=[]
        for th in data['themes']:
            symbols=[s['symbol'] for s in th.get('stocks',[])]
            overlap=sorted(held&set(symbols))
            if not overlap: continue
            out.append({'name':th['name'],'change_pct':th['change_pct'],
                        'change_3d_avg_pct':th['change_3d_avg_pct'],
                        'up':th['up'],'down':th['down'],'members':th['members'],
                        'watchlist_members':overlap,
                        'other_members':[s for s in symbols if s not in held][:20]})
        return out

    def complete_review(self,id,snapshot):
        raw=None; error=''; results=[]
        try: raw=self.ai.decide(snapshot)
        except AIError as exc: error=str(exc)
        except Exception: error='AI 판단 처리 오류: 새 매수를 보류합니다.'
        now=utcnow(); news,events=self.research(now)
        with self.lock:
            state=self.store.get(self.account_kind,id)
            if not error and (state['status']!='running' or state['generation']!=snapshot['generation'] or self.stop_event.is_set()):
                error='운용 상태가 변경되어 이전 AI 판단을 폐기했습니다.'
            if not error:
                try:
                    # Work on a copy: reject the entire result on validation errors.
                    updated=copy.deepcopy(state)
                    results=self.apply_decision(updated,raw,snapshot,now,news,events)
                    state=updated
                except ValueError as exc: error=str(exc)
            state['ai']['pending']=False; state['ai']['last_completed_at']=now.isoformat(); state['ai']['error']=error
            state['ai']['failures']=state['ai'].get('failures',0)+1 if error else 0
            state['ai']['retry_after']=(now+timedelta(seconds=min(3600,180*2**min(state['ai']['failures'],5)))).isoformat() if error else None
            entry={'id':snapshot['snapshot_id'],'at':now.isoformat(),'trigger':snapshot['trigger'],
                'status':'rejected' if error else 'done','summary':error or raw['summary'],'results':results}
            state['decisions']=(state['decisions']+[entry])[-80:]
            self.store.put(self.account_kind,id,state)
            self.store.put(self.decision_kind,snapshot['snapshot_id'],dict(entry,account_id=id,snapshot=snapshot,plan=raw))

    def review_blocked(self,state):
        return False

    def protect_state(self,state,now):
        forward.mark_and_protect(state,now)

    def apply_decision(self,state,raw,snapshot,now,news,events):
        if snapshot.get('planning_only'):
            from .paper_models import Plan
            plan=Plan.model_validate(raw)
            if plan.snapshot_id!=snapshot['snapshot_id'] or state['revision']!=snapshot['revision']:
                raise ValueError('장전·장후 판단 중 계좌 상태가 변경되었습니다.')
            if not 0<=(now-stamp(snapshot['at'])).total_seconds()<=240:raise ValueError('장전·장후 판단 유효 시간 초과')
            if plan.decisions:raise ValueError('장전·장후 분석은 주문 판단을 포함할 수 없습니다.')
            state['ai']['summary']=plan.summary
            state['ai']['next_seconds']=state['config']['ai_review_seconds']
            return []
        return forward.apply_plan(state,raw,snapshot,now,news,events)

    def export(self,id):
        state=self.store.get(self.account_kind,id)
        return {'account':state,'metrics':forward.metrics(state),
                'decisions':[d for d in self.store.all(self.decision_kind) if d['account_id']==id],
                'limitations':['실시간 호가를 주기적으로 조회하는 가상 체결이며 실제 주문 대기열·시장 충격·배당·분할 자동 반영은 없음',
                    '단순보유 참고값은 같은 관심 종목에 최초 유효 시세로 동일 금액 투자·매수비용 적용',
                    'AI 구독 한도·지연으로 초단위 초단타는 지원하지 않음','수익률은 실계좌 수익을 보장하지 않음']}
