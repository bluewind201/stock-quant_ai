"""Opt-in, cash-only live execution with a durable order journal and actual fill accounting.

Paper fills are used ONLY as a sizing preview. They never change live cash/holdings.
Every network mutation is preceded by a committed journal entry. Ambiguous writes
remain blocked across restarts and require explicit order-ID reconciliation.
"""
import copy
import os
import threading
import uuid
from datetime import timedelta
from pydantic import Field
from . import forward
from .models import stamp, utcnow
from .paper_models import PaperConfig, Plan
from .paper_service import PaperService
from .session_policy import account_context
from .decision_quality import buy_reasons
from .toss_orders import TossOrders, TossError, OrderUncertain, number

ACTIVE={'DRAFT','SUBMITTING','OPEN','CANCELING','UNKNOWN'}
TERMINAL={'FILLED','CANCELED','REJECTED'}


class LiveConfig(PaperConfig):
    paper_account_id: str = Field(min_length=1,max_length=100)


class LiveService(PaperService):
    account_kind='live_accounts'
    decision_kind='live_decisions'

    def __init__(self,store,broker,ai=None,orders=None):
        super().__init__(store,broker,ai)
        self.orders=orders or TossOrders(broker)
        self.execution_lock=threading.Lock()

    def initialize(self,resume=False):
        if not getattr(self,'owner_file',None):
            from pathlib import Path
            handle=open(str(Path(self.store.path))+'.live.lock','a+b')
            try:
                if os.name=='nt':
                    import msvcrt
                    if handle.tell()==0:handle.write(b'0');handle.flush()
                    handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:
                handle.close();raise ValueError('같은 데이터 저장소에서 다른 주문 관리 프로세스가 실행 중입니다.') from None
            self.owner_file=handle
        super().initialize(resume=False)  # Live automation always requires an explicit restart.
        for intent in self.store.all('live_orders'):
            if intent['status']=='SUBMITTING':
                intent['status']='UNKNOWN';intent['error']='재시작 전에 주문 응답을 저장하지 못했습니다. 실제 주문 ID 확인 필요.'
                self.store.put('live_orders',intent['id'],intent)
            elif intent['status']=='DRAFT':
                intent['status']='BLOCKED';intent['error']='서버 재시작으로 이전 미전송 판단 폐기'
                self.store.put('live_orders',intent['id'],intent)

    def start_worker(self):
        super().start_worker()
        def worker():
            while not self.stop_event.wait(2):
                try:self.execution_pulse()
                except Exception:
                    # Never clear a pending order because a polling iteration failed.
                    continue
        threading.Thread(target=worker,daemon=True,name='live-order-reconciliation').start()

    def cap(self,currency,order=False):
        name=('LIVE_MAX_ORDER_' if order else 'LIVE_CAPITAL_')+currency
        default='1000000' if currency=='KRW' else '1000'
        return float(number(os.getenv(name,default),True))

    def validation(self,paper_id):
        s=self.store.get('paper_accounts',paper_id);m=forward.metrics(s);reasons=[]
        if m['observed_days']<20:reasons.append('실제 시세 관찰 20거래일 미만')
        if m['sell_fills']<30:reasons.append('가상 매도 체결 30회 미만')
        if m['return_pct']<=0:reasons.append('비용 반영 총수익이 양수가 아님')
        if s['halt_reason'] or m['max_drawdown_pct']>=s['config']['max_drawdown_pct']:reasons.append('계좌 위험 한도 위반')
        if not s['config'].get('decision_policy_v2',False):reasons.append('시간대·판단 품질 규칙 검증 계좌가 아님')
        return {'eligible_for_manual_setup':not reasons,'reasons':reasons,'metrics':m,
                'note':'최소 모의 관찰 조건입니다. 실거래 수익성·안전성 증명 또는 자동 승인 기준이 아닙니다.'}

    def create(self,config):
        payload=LiveConfig.model_validate(config)
        if not self.orders.enabled:raise ValueError('NAS에서 ENABLE_LIVE_TRADING=true를 직접 설정한 뒤 실거래 계좌를 준비하세요.')
        report=self.validation(payload.paper_account_id)
        if not report['eligible_for_manual_setup']:raise ValueError('모의 검증 조건 미충족: '+', '.join(report['reasons']))
        cfg=payload.model_dump(mode='json',exclude={'paper_account_id'})
        paper=self.store.get('paper_accounts',payload.paper_account_id)
        if cfg['watchlist']!=paper['config']['watchlist'] or cfg['currency']!=paper['config']['currency']:
            raise ValueError('모의 검증과 같은 통화·관심 종목으로 준비하세요.')
        for key in ('max_position_pct','max_invested_pct','max_scalp_pct','max_bucket_pct','risk_per_trade_pct','daily_loss_pct','max_drawdown_pct'):
            if cfg[key]>paper['config'][key]:raise ValueError('모의 검증보다 위험 한도를 높일 수 없습니다: '+key)
        if not cfg['decision_policy_v2'] or not cfg['require_news']:raise ValueError('실거래는 판단 품질·최신 뉴스 검증을 유지해야 합니다.')
        if cfg['initial_cash']>self.cap(cfg['currency']):raise ValueError('NAS의 통화별 실거래 자금 상한을 초과했습니다.')
        with self.lock:
            if any(s['config']['currency']==cfg['currency'] for s in self.store.all(self.account_kind)):
                raise ValueError('실계좌 장부는 통화별 한 개만 만듭니다. 기존 계좌 기록을 사용하세요.')
            self.orders.validate_account()
            if any(r['currency']==cfg['currency'] and number(r['quantity'])>0 for r in self.orders.holdings()):
                raise ValueError('첫 연결은 해당 통화의 보유 주식이 없는 상태만 지원합니다. 기존 보유분 자동 편입은 하지 않습니다.')
            if self.orders.open_orders():raise ValueError('첫 연결 전 기존 미체결 주문을 확인하세요.')
            if self.orders.buying_power(cfg['currency'])<cfg['initial_cash']:raise ValueError('실계좌 현금 매수 가능 금액 부족')
            actual_fee=self.orders.commission(cfg['currency'])
            if actual_fee>paper['config']['fee_bps']:raise ValueError('실제 수수료가 모의 가정보다 높습니다. 실제 비용으로 다시 검증하세요.')
            if any(cfg[k]<paper['config'].get(k,cfg[k]) for k in ('fee_bps','sell_tax_bps','slippage_bps','min_confidence','min_reward_risk','opening_wait_minutes','reentry_cooldown_minutes')):
                raise ValueError('실거래 비용·판단 기준을 모의 검증보다 낮출 수 없습니다.')
            if cfg['max_open_gap_pct']>paper['config'].get('max_open_gap_pct',6):raise ValueError('검증보다 시초가 갭 제한을 완화할 수 없습니다.')
            cfg['fee_bps']=max(cfg['fee_bps'],actual_fee)
            state=forward.create(cfg,utcnow());state['mode']='live';state['source_paper_id']=payload.paper_account_id
            state['validation_report']=report;state['account_seq']=self.orders.account_seq
            state['reconciled_at']=None
            self.store.put(self.account_kind,state['id'],state)
        return self.view(state['id'])

    def control(self,id,action):
        if action=='cancel_orders':
            with self.lock:
                for intent in self.pending(id):
                    if intent.get('broker_order_id') and intent['status'] in ('OPEN','CANCELING'):
                        intent['cancel_requested']=True;intent['cancel_attempted']=False
                        self.store.put('live_orders',intent['id'],intent)
            return self.view(id)
        if action=='start':
            if not self.orders.enabled:raise ValueError('실거래 비활성화 상태')
            if any(s.get('mode')=='live' and s['status']=='running' for s in self.store.all('daytrade_accounts')):
                raise ValueError('당일 단타 실거래가 운용 중입니다. 같은 계좌로 두 방식을 동시에 돌리지 않습니다.')
            state=self.store.get(self.account_kind,id)
            if state.get('source_paper_id') and not self.validation(state['source_paper_id'])['eligible_for_manual_setup']:
                raise ValueError('원본 모의 계좌가 현재 검증 조건을 충족하지 않습니다.')
            if state.get('account_seq')!=self.orders.account_seq:raise ValueError('실계좌 식별자가 기존 장부와 다릅니다.')
            if self.pending(id):raise ValueError('기존 주문의 체결·취소·불명확 상태를 먼저 확인하세요.')
            self.check_holdings(state)
        return super().control(id,action)

    def pending(self,id):
        return [o for o in self.store.all('live_orders') if o['account_id']==id and o['status'] in ACTIVE]

    def view(self,id):
        state=super().view(id)
        state.pop('account_seq',None)
        state['orders']=[o for o in self.store.all('live_orders') if o['account_id']==id][-100:]
        state['metrics']['live_note']='실제 주문 모드. 현금·보유량은 토스 확인 체결만 반영합니다.'
        return state

    def review_blocked(self,state):
        return bool(self.pending(state['id'])) or not self.orders.enabled or bool(state['errors'].get('reconciliation'))

    def _queue(self,state,trade,proposal,now):
        if self.pending(state['id']):return None
        q=state['quotes'][trade['symbol']]
        intent={'id':uuid.uuid4().hex,'account_id':state['id'],'status':'DRAFT','created_at':now.isoformat(),
                'side':trade['side'],'symbol':trade['symbol'],'bucket':trade['bucket'],'qty':trade['qty'],
                'currency':state['config']['currency'],'limit_price':q['ask' if trade['side']=='buy' else 'bid'],
                'generation':state['generation'],'reason':trade['reason'],'proposal':proposal,
                'entry_phase':trade.get('entry_phase',account_context(state,now)['phase']),'broker_order_id':None,
                'filled_qty':0,'filled_amount':0.,'fees':0.,'fees_estimated':False,'error':'','cancel_attempted':False}
        state['revision']+=1;intent['revision']=state['revision']
        self.store.put('live_orders',intent['id'],intent)
        return intent['id']

    def protect_state(self,state,now):
        preview=copy.deepcopy(state);before=len(preview['trades']);forward.mark_and_protect(preview,now)
        for key in ('entries_enabled','halt_reason','halted_day','liquidate',
                    'day_start_equity','market_day','last_risk_at','observed_days','marks','benchmark','logs'):
            state[key]=preview[key]
        for key,p in state['positions'].items():
            if key in preview['positions']:
                p['stop']=preview['positions'][key]['stop'];p['peak_price']=preview['positions'][key]['peak_price']
        exits=preview['trades'][before:]
        if exits:
            for pending in self.pending(state['id']):
                if pending['side']=='buy':
                    pending['cancel_requested']=True
                    self.store.put('live_orders',pending['id'],pending)
        if exits:self._queue(state,exits[0],None,now)
        state['equity']=forward.equity(state)
        state['peak']=max(state['peak'],state['equity'])
        state['max_drawdown_pct']=max(state['max_drawdown_pct'],(1-state['equity']/state['peak'])*100)
        if state['marks'] and state['marks'][-1]['at']==now.isoformat():
            state['marks'][-1].update(equity=state['equity'],cash=state['cash'])

    def apply_decision(self,state,raw,snapshot,now,news,events):
        if snapshot.get('planning_only'):return super().apply_decision(state,raw,snapshot,now,news,events)
        if self.pending(state['id']):raise ValueError('실제 주문 확인 중이므로 새 주문 판단을 폐기합니다.')
        preview=copy.deepcopy(state);before=len(state['trades'])
        results=forward.apply_plan(preview,raw,snapshot,now,news,events)
        plan=Plan.model_validate(raw)
        state['allocation']=preview['allocation'];state['ai']=preview['ai']
        for d in plan.decisions:
            key=d.symbol+':'+d.bucket
            if d.action=='protect' and key in state['positions'] and key in preview['positions']:
                for field in ('stop','trailing_pct'):state['positions'][key][field]=preview['positions'][key][field]
                state['revision']+=1
        trades=preview['trades'][before:]
        selected=trades[0] if trades else None
        order_id=None
        if selected:
            proposal=next(d.model_dump() for d in plan.decisions if d.symbol==selected['symbol'] and d.bucket==selected['bucket'])
            order_id=self._queue(state,selected,proposal,now)
        for r in results:
            r['preview_qty']=r['filled'];r['filled']=0
            if selected and r['symbol']==selected['symbol'] and r['bucket']==selected['bucket']:
                r['order_intent_id']=order_id
            elif r['preview_qty']:r['blocked'].append('선행 주문 체결 확인 후 새 판단에서 재검토')
        return results

    def check_holdings(self,state):
        if state.get('account_seq')!=self.orders.account_seq:raise ValueError('실계좌 식별자 불일치')
        actual={r['symbol']:number(r['quantity']) for r in self.orders.holdings()
                if r['currency']==state['config']['currency'] and number(r['quantity'])>0}
        expected={}
        for p in state['positions'].values():expected[p['symbol']]=expected.get(p['symbol'],0)+p['qty']
        if actual!=expected:raise ValueError('토스 보유량과 프로그램 장부 불일치. 수동 거래·분할·이전 체결을 확인하세요.')
        known={o['broker_order_id'] for o in self.store.all('live_orders') if o.get('broker_order_id')}
        if any(o['orderId'] not in known for o in self.orders.open_orders()):raise ValueError('프로그램 외부 미체결 주문 발견')

    def _submit(self,intent):
        id=intent['account_id'];now=utcnow()
        try:
            if not self.orders.enabled:raise ValueError('실거래 비활성화')
            state=self.store.get(self.account_kind,id)
            self.check_holdings(state)
            if intent['side']=='buy':
                if self.orders.buying_power(intent['currency'])<intent['qty']*intent['limit_price']*(1+state['config']['fee_bps']/10000):
                    raise ValueError('실제 현금 매수 가능 금액 부족')
            elif self.orders.sellable(intent['symbol'])<intent['qty']:raise ValueError('실제 판매 가능 수량 부족')
            with self.lock:
                state=self.store.get(self.account_kind,id);now=utcnow()
                if state['status']!='running' or state['generation']!=intent['generation'] or state['revision']!=intent['revision']:
                    raise ValueError('주문 전 운용 상태·계좌 변경으로 판단 폐기')
                if now-stamp(intent['created_at'])>timedelta(seconds=60):raise ValueError('미전송 주문 60초 유효 시간 초과')
                q=state['quotes'].get(intent['symbol'])
                if not forward.fresh(state,q,now,intent['side']=='buy'):raise ValueError('최신 정규장 호가 없음')
                if intent['side']=='buy':
                    news,events=self.research(now)
                    from .paper_models import Proposal
                    reasons=forward.entry_reasons(state,intent['symbol'],now,news,events)+buy_reasons(state,Proposal.model_validate(intent['proposal']),now)
                    if reasons:raise ValueError(' / '.join(reasons))
                    if abs(q['ask']/intent['limit_price']-1)>.005:raise ValueError('주문 직전 가격 0.5% 초과 변동')
                    if intent['qty']*intent['limit_price']>self.cap(intent['currency'],True):raise ValueError('실거래 단일 매수 금액 상한 초과')
                    if intent['qty']*intent['limit_price']*(1+state['config']['fee_bps']/10000)>state['cash']:
                        raise ValueError('할당 예산의 가용 현금 부족')
                intent['status']='SUBMITTING';intent['submitted_at']=now.isoformat()
                self.store.put('live_orders',intent['id'],intent)
            broker_id=self.orders.create(intent)
            intent['broker_order_id']=broker_id;intent['status']='OPEN'
        except OrderUncertain as exc:intent['status']='UNKNOWN';intent['error']=str(exc)
        except (ValueError,OSError) as exc:
            intent['status']='UNKNOWN' if intent['status']=='SUBMITTING' and not isinstance(exc,TossError) else 'BLOCKED'
            intent['error']=str(exc) if isinstance(exc,ValueError) else '주문 처리 오류'
        except Exception:
            intent['status']='UNKNOWN' if intent['status']=='SUBMITTING' else 'BLOCKED'
            intent['error']='주문 처리 예외. 결과 확인 전 자동 재전송하지 않습니다.'
        self.store.put('live_orders',intent['id'],intent)

    def ingest(self,intent,detail,now):
        """Cumulative broker fills -> atomic deltas. Repeated polls do not repeat a fill."""
        saved=self.store.get('live_orders',intent['id'])
        for field in ('filled_qty','filled_amount','fees'):intent[field]=saved[field]
        if any(detail.get(k)!=v for k,v in {'orderId':intent['broker_order_id'],'symbol':intent['symbol'],
                 'side':intent['side'].upper(),'currency':intent['currency'],'orderType':'LIMIT','timeInForce':'DAY'}.items()):
            raise ValueError('실제 주문 상세와 요청 식별 정보 불일치')
        if number(detail['quantity'])!=intent['qty'] or number(detail['price'])!=number(intent['limit_price']):
            raise ValueError('주문 수량·지정가 불일치')
        execution=detail['execution'];qty=number(execution['filledQuantity'])
        if qty!=qty.to_integral_value() or not intent['filled_qty']<=qty<=intent['qty']:raise ValueError('누적 체결 수량 역행·범위 오류')
        qty=int(qty);amount=float(number(execution['filledAmount'])) if qty else 0.
        if amount<intent['filled_amount']:raise ValueError('누적 체결 금액 역행')
        if qty==intent['filled_qty'] and abs(amount-intent['filled_amount'])>1e-6:
            raise ValueError('수량 변화 없는 체결 금액 정정은 수동 대사가 필요합니다.')
        with self.lock,self.store.connect(write=True) as con:
            state=self.store.get(self.account_kind,intent['account_id'],con)
            realized_before=state['realized_pnl']
            cfg=state['config'];estimated=qty>0 and (execution.get('commission') is None or execution.get('tax') is None)
            fees=float(number(execution['commission'])) if execution.get('commission') is not None else amount*cfg['fee_bps']/10000
            fees+=float(number(execution['tax'])) if execution.get('tax') is not None else amount*(cfg['sell_tax_bps'] if intent['side']=='sell' else 0)/10000
            delta=qty-intent['filled_qty'];gross=amount-intent['filled_amount'];fee=fees-intent['fees']
            key=intent['symbol']+':'+intent['bucket'];p=state['positions'].get(key);pnl=None
            if delta or abs(fee)>1e-9:
                if intent['side']=='buy':
                    state['cash']-=gross+fee
                    if delta:
                        d=intent['proposal'];price=gross/delta
                        if price<=0 or price>intent['limit_price']+1e-6:raise ValueError('지정가 매수 체결 가격 범위 오류')
                        old_qty=p['qty'] if p else 0;total=old_qty+delta
                        basis=p['qty']*p['unit_cost'] if p else 0
                        state['positions'][key]={'symbol':intent['symbol'],'bucket':intent['bucket'],'qty':total,
                            'unit_cost':(basis+gross+fee)/total,'entry_price':((p['entry_price']*old_qty if p else 0)+gross)/total,
                            'entered_at':p['entered_at'] if p else now.isoformat(),'entry_phase':p.get('entry_phase','legacy') if p else intent['entry_phase'],
                            'setup':d['setup'],'stop':max(p['stop'] if p else 0,price*(1-d['stop_pct']/100)),
                            'target':p['target'] if p else price*(1+d['take_profit_pct']/100),
                            'trailing_pct':min(p['trailing_pct'],d['trailing_pct']) if p else d['trailing_pct'],
                            'peak_price':max(p['peak_price'] if p else 0,price),
                            'lots':(p.get('lots',[]) if p else [])+[{'order_id':intent['id'],'qty':delta}]}
                    else:
                        remaining=sum(lot['qty'] for lot in p.get('lots',[]) if lot['order_id']==intent['id']) if p else 0
                        held_fraction=remaining/qty if qty else 0
                        if p:p['unit_cost']+=fee*held_fraction/p['qty']
                        state['realized_pnl']-=fee*(1-held_fraction)
                else:
                    if delta and (not p or p['qty']<delta):raise ValueError('체결 매도량이 프로그램 보유량을 초과함')
                    if delta and (gross/delta<intent['limit_price']-1e-6):raise ValueError('지정가 매도 체결 가격 범위 오류')
                    pnl=gross-fee-(p['unit_cost']*delta if p else 0)
                    state['cash']+=gross-fee;state['realized_pnl']+=pnl
                    if delta:
                        state['closed_lots']+=1;state['winning_lots']+=int(pnl>0);p['qty']-=delta
                        remaining=delta
                        for lot in p.get('lots',[]):
                            used=min(remaining,lot['qty']);lot['qty']-=used;remaining-=used
                        if not p['qty']:del state['positions'][key]
                if delta:
                    state['trades'].append({'id':intent['id']+':'+str(qty),'order_id':intent['broker_order_id'],
                        'at':now.isoformat(),'side':intent['side'],'symbol':intent['symbol'],'bucket':intent['bucket'],
                        'qty':delta,'price':gross/delta,'fee':fee,'pnl':pnl,'reason':intent['reason'],
                        'entry_phase':p.get('entry_phase','legacy') if p else intent['entry_phase'],'fees_estimated':estimated})
                elif abs(fee)>1e-9:
                    state.setdefault('fee_adjustments',[]).append({'order_id':intent['broker_order_id'],'fee_delta':fee,'at':now.isoformat(),
                        'realized_delta':state['realized_pnl']-realized_before,'bucket':intent['bucket'],'entry_phase':intent['entry_phase']})
                state['revision']+=1
            intent.update(filled_qty=qty,filled_amount=amount,fees=fees,fees_estimated=estimated,broker_status=detail['status'],last_checked_at=now.isoformat(),error='')
            if intent['status']=='UNKNOWN' and detail['status'] in ('PENDING','PARTIAL_FILLED','PENDING_CANCEL'):intent['status']='OPEN'
            if detail['status'] in TERMINAL and (detail['status']!='FILLED' or qty==intent['qty']):intent['status']='DONE'
            elif detail['status'] not in ('PENDING','PARTIAL_FILLED','PENDING_CANCEL'):
                intent['status']='UNKNOWN';intent['error']='지원하지 않는 실제 주문 상태. 주문 상세 확인 필요.'
            state['equity']=forward.equity(state)
            if state['cash']<0:state['entries_enabled']=False;state['errors']['reconciliation']='할당 예산보다 실제 체결 비용이 큼'
            self.store.put('live_orders',intent['id'],intent,con)
            if any(o['account_id']==state['id'] and o.get('fees_estimated') for o in self.store.all('live_orders',con)):
                state['errors']['costs']='실제 수수료·세금 확정 대기. 추정 비용 반영 중이며 신규 매수는 보류합니다.'
            else:state['errors'].pop('costs',None)
            self.store.put(self.account_kind,state['id'],state,con)

    def execution_pulse(self):
        if not self.execution_lock.acquire(blocking=False):return
        try:
            for intent in self.store.all('live_orders'):
                if intent['status']=='DRAFT':self._submit(intent);continue
                fee_refresh=intent['status']=='DONE' and intent.get('fees_estimated') and (
                    not intent.get('last_checked_at') or utcnow()-stamp(intent['last_checked_at'])>=timedelta(seconds=60))
                if (intent['status'] not in ('OPEN','CANCELING','UNKNOWN') and not fee_refresh) or not intent.get('broker_order_id'):continue
                try:
                    self.ingest(intent,self.orders.detail(intent['broker_order_id']),utcnow())
                    if intent['status']=='DONE':continue
                    state=self.store.get(self.account_kind,intent['account_id'])
                    overdue=utcnow()-stamp(intent['submitted_at'])>timedelta(seconds=30)
                    adverse=intent['side']=='buy' and (not state['entries_enabled'] or state['halt_reason'] or state['liquidate'])
                    if self.orders.enabled and (state['status']!='running' or overdue or adverse or intent.get('cancel_requested')) and not intent['cancel_attempted'] and intent.get('broker_status')!='PENDING_CANCEL':
                        intent['cancel_attempted']=True;intent['status']='CANCELING'
                        self.store.put('live_orders',intent['id'],intent)
                        response=self.orders.cancel(intent['broker_order_id'])
                        # Cancellation has a NEW operation ID; original ID remains the fill ledger authority.
                        intent['cancel_operation_id']=response.get('orderId')
                        self.store.put('live_orders',intent['id'],intent)
                except Exception as exc:
                    intent['error']=str(exc) if isinstance(exc,ValueError) else '실제 주문 확인 실패'
                    self.store.put('live_orders',intent['id'],intent)
            for state in self.store.all(self.account_kind):
                if state['status']!='running' or self.pending(state['id']):continue
                if state.get('reconciled_at') and utcnow()-stamp(state['reconciled_at'])<timedelta(seconds=60):continue
                try:self.check_holdings(state);error=''
                except (ValueError,OSError):error='실제 보유량·외부 미체결 주문 대사 실패. 신규 매수를 중단합니다.'
                with self.lock:
                    latest=self.store.get(self.account_kind,state['id']);latest['reconciled_at']=utcnow().isoformat()
                    if error:latest['errors']['reconciliation']=error
                    else:latest['errors'].pop('reconciliation',None)
                    self.store.put(self.account_kind,state['id'],latest)
        finally:self.execution_lock.release()

    def bind_unknown(self,intent_id,broker_order_id):
        with self.execution_lock:
            intent=self.store.get('live_orders',intent_id)
            if intent['status']!='UNKNOWN' or intent.get('broker_order_id'):raise ValueError('주문 ID가 없는 미확인 접수만 연결할 수 있습니다.')
            detail=self.orders.detail(broker_order_id)
            if not abs((stamp(detail['orderedAt'])-stamp(intent['submitted_at'])).total_seconds())<=120:
                raise ValueError('실제 주문 시각이 미확인 전송 시각과 다릅니다.')
            if any(o.get('broker_order_id')==broker_order_id for o in self.store.all('live_orders')):raise ValueError('이미 연결된 실제 주문 ID')
            intent['broker_order_id']=broker_order_id;intent['status']='OPEN'
            self.ingest(intent,detail,utcnow())
            return intent

    def export(self,id):
        result=super().export(id)
        result['account'].pop('account_seq',None)
        result['orders']=[o for o in self.store.all('live_orders') if o['account_id']==id]
        result['limitations']=['실제 확인 체결만 장부 반영. 수수료 미확정 시 추정값 표시.',
            '프로그램 밖 수동 거래·분할·입출금은 자동 장부 편입하지 않음.',
            '지정가 주문은 급락·거래정지·통신 장애에서 즉시 손절 체결을 보장하지 않음.']
        return result
