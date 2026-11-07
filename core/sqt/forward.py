"""Pure forward-paper accounting and risk rules. No brokerage order methods."""
import math
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo
from .calendar import session, status
from .models import stamp
from .paper_models import PaperConfig, Plan
from .session_policy import context as session_context
from .decision_quality import buy_reasons, performance


def market_day(state,now):
    return now.astimezone(ZoneInfo('Asia/Seoul' if state['config']['currency']=='KRW' else 'America/New_York')).date().isoformat()


def create(config,now):
    cfg=PaperConfig.model_validate(config).model_dump(mode='json')
    return {'id':uuid.uuid4().hex,'mode':'forward_paper','created_at':now.isoformat(),'config':cfg,
        'status':'stopped','entries_enabled':True,'halt_reason':'','halted_day':'','liquidate':False,'revision':0,'generation':0,
        'cash':cfg['initial_cash'],'positions':{},'quotes':{},'features':{},'errors':{},'trades':[],
        'realized_pnl':0.,'closed_lots':0,'winning_lots':0,'equity':cfg['initial_cash'],'peak':cfg['initial_cash'],
        'max_drawdown_pct':0.,'day_start_equity':cfg['initial_cash'],'market_day':'','observed_days':[],
        'allocation':{'scalp':0,'swing':0,'long':0,'cash':100},'marks':[],'liquidity':{},
        'ai':{'pending':False,'last_requested_at':None,'last_completed_at':None,'calls_day':'','calls_today':0,
              'last_prices':{},'last_news_ids':[],'error':'','summary':'아직 AI 판단이 없습니다.','next_seconds':cfg['ai_review_seconds']},
        'last_poll_at':None,'last_risk_at':None,'decisions':[],'benchmark':None,'logs':[]}


def log(state,now,message):
    state['logs'].append({'at':now.isoformat(),'message':message})
    state['logs']=state['logs'][-200:]


def fresh(state,quote,now,for_buy=False):
    if not quote: return False
    try:
        ages=[(now-stamp(quote[k])).total_seconds() for k in ('timestamp','book_timestamp')]
        if any(age < -2 or age>state['config']['quote_max_age_seconds'] for age in ages): return False
        if not status(quote['exchange'],now)['open']: return False
        if for_buy and quote['spread_bps']>state['config']['max_spread_bps']: return False
        return True
    except (ValueError,KeyError,TypeError): return False


def equity(state):
    return state['cash']+sum(p['qty']*state['quotes'].get(p['symbol'],{}).get('bid',p['entry_price'])
                             for p in state['positions'].values())


def capacity(state,quote,side):
    old=state['liquidity'].get(quote['symbol'])
    if not old or old['timestamp']!=quote['book_timestamp']:
        old={'timestamp':quote['book_timestamp'],'buy':0,'sell':0}
        state['liquidity'][quote['symbol']]=old
    return max(0,quote['ask_qty' if side=='buy' else 'bid_qty']-old[side])


def fill_sell(state,key,qty,now,reason):
    p=state['positions'].get(key)
    if not p: return 0
    q=state['quotes'].get(p['symbol'])
    if not fresh(state,q,now): return 0
    qty=min(p['qty'],int(qty),capacity(state,q,'sell'))
    if qty<1: return 0
    cfg=state['config']; price=q['bid']*(1-cfg['slippage_bps']/10000)
    gross=price*qty; fees=gross*(cfg['fee_bps']+cfg['sell_tax_bps'])/10000
    basis=p['unit_cost']*qty; pnl=gross-fees-basis
    state['cash']+=gross-fees; state['realized_pnl']+=pnl
    state['closed_lots']+=1; state['winning_lots']+=int(pnl>0)
    state['trades'].append({'id':uuid.uuid4().hex,'at':now.isoformat(),'side':'sell','symbol':p['symbol'],
        'bucket':p['bucket'],'qty':qty,'price':price,'fee':fees,'pnl':pnl,'reason':reason,
        'entry_phase':p.get('entry_phase','legacy'),'exit_phase':session_context(q['exchange'],now)['phase'],
        'quote_at':q['book_timestamp'],'holding_seconds':(now-stamp(p['entered_at'])).total_seconds()})
    state['liquidity'][p['symbol']]['sell']+=qty; p['qty']-=qty; state['revision']+=1
    if not p['qty']: del state['positions'][key]
    return qty


def mark_and_protect(state,now):
    """Runs independently from the model, even while a model response is pending or quota is exhausted."""
    cfg=state['config']; value=equity(state); day=market_day(state,now)
    if state['market_day']!=day:
        state['market_day']=day
        state['day_start_equity']=state['equity']  # include the overnight gap in the new day's loss
    state['peak']=max(state['peak'],value)
    drawdown=(1-value/state['peak'])*100
    state['max_drawdown_pct']=max(state['max_drawdown_pct'],drawdown)
    day_loss=(1-value/state['day_start_equity'])*100
    if not state['halt_reason'] and (day_loss>=cfg['daily_loss_pct'] or drawdown>=cfg['max_drawdown_pct']):
        state['halt_reason']='일일 손실 한도' if day_loss>=cfg['daily_loss_pct'] else '최대 낙폭 한도'
        state['halted_day']=day
        state['entries_enabled']=False; state['revision']+=1
        log(state,now,state['halt_reason']+' 도달: 신규 매수 중지, 체결 가능한 시세에서 청산')
    for key,p in list(state['positions'].items()):
        q=state['quotes'].get(p['symbol'])
        if not fresh(state,q,now): continue
        p['peak_price']=max(p['peak_price'],q['bid'])
        p['stop']=max(p['stop'],p['peak_price']*(1-p['trailing_pct']/100))
        age=(now-stamp(p['entered_at'])).total_seconds()/86400
        _,closed=session(q['exchange'],day)
        scalp_exit=p['bucket']=='scalp' and (now>=closed-timedelta(minutes=5) or market_day(state,stamp(p['entered_at']))!=day)
        reason=('요청한 전량 청산' if state['liquidate'] else state['halt_reason'] or
                ('자동 손절·추적 손절' if q['bid']<=p['stop'] else
                 '자동 익절' if q['bid']>=p['target'] else
                 '단타 장 마감 청산' if scalp_exit else
                 '보유 기간 한도' if age>({'scalp':1,'swing':30,'long':365}[p['bucket']]) else ''))
        if reason: fill_sell(state,key,p['qty'],now,reason)
    state['equity']=equity(state)
    state['peak']=max(state['peak'],state['equity'])
    state['max_drawdown_pct']=max(state['max_drawdown_pct'],(1-state['equity']/state['peak'])*100)
    state['last_risk_at']=now.isoformat()
    if any(fresh(state,q,now) for q in state['quotes'].values()):
        if day not in state['observed_days']: state['observed_days'].append(day)
        if not state['marks'] or (now-stamp(state['marks'][-1]['at'])).total_seconds()>=60:
            state['marks'].append({'at':now.isoformat(),'equity':state['equity'],'cash':state['cash']})
            state['marks']=state['marks'][-5000:]
    if state['benchmark'] is None and all(fresh(state,state['quotes'].get(i['symbol']),now) for i in cfg['watchlist']):
        cash=cfg['initial_cash']; positions={}; per=cash*cfg['max_invested_pct']/100/len(cfg['watchlist'])
        for item in cfg['watchlist']:
            q=state['quotes'][item['symbol']]; unit=q['ask']*(1+cfg['slippage_bps']/10000)*(1+cfg['fee_bps']/10000)
            qty=math.floor(per/unit); cash-=qty*unit; positions[item['symbol']]=qty
        state['benchmark']={'cash':cash,'positions':positions,'at':now.isoformat()}


def entry_reasons(state,symbol,now,news,events):
    cfg=state['config']; reasons=[]
    if not state['entries_enabled'] or state['halt_reason'] or state['liquidate']: reasons.append('신규 진입 중지')
    if state['errors'].get('costs') or state['errors'].get('reconciliation'):reasons.append('실계좌 잔고·비용 확정 확인 필요')
    if not fresh(state,state['quotes'].get(symbol),now,True): reasons.append('휴장·오래된 시세·넓은 스프레드')
    if state['errors'].get(symbol) or state['errors'].get('quotes') or state['errors'].get('features_'+symbol):
        reasons.append('시세·기술 데이터 공급 오류')
    q=state['quotes'].get(symbol)
    if q and any(p['symbol']==symbol and (q['bid']<=p['stop'] or q['bid']>=p['target']) for p in state['positions'].values()):
        reasons.append('손절·익절 가격에 도달한 보유분의 청산 대기')
    relevant=lambda n:not n.get('symbols') or symbol in n['symbols']
    recent=[n for n in news if relevant(n) and n.get('source')!='demo' and
            timedelta(0)<=now-stamp(n['available_at'])<=timedelta(hours=72) and
            timedelta(0)<=now-stamp(n.get('published_at',n['available_at']))<=timedelta(hours=72)]
    if cfg['require_news'] and not recent: reasons.append('최근 72시간 뉴스 없음')
    for event in events:
        if relevant(event) and event['impact']=='high' and stamp(event['known_at'])<=now:
            delta=(stamp(event['scheduled_at'])-now).total_seconds()
            if -7200<=delta<=3600: reasons.append('주요 경제 발표 전후 신규 진입 제한')
    for n in recent:
        if n['source'] in ('fed','white_house') and n.get('impact')=='high' and now-stamp(n['available_at'])<timedelta(minutes=30):
            reasons.append('연준·대통령 중요 발표 직후 대기')
    return list(dict.fromkeys(reasons))


def apply_plan(state,raw,snapshot,now,news,events):
    plan=Plan.model_validate(raw)
    if plan.snapshot_id!=snapshot['snapshot_id'] or state['revision']!=snapshot['revision']:
        raise ValueError('판단 중 포지션이 변경되었거나 판단 식별자가 달라 결과를 폐기했습니다.')
    if not 0<=(now-stamp(snapshot['at'])).total_seconds()<=240:
        raise ValueError('AI 판단의 유효 시간 240초가 지났습니다.')
    cfg=state['config']; alloc=plan.allocation.model_dump()
    if 100-alloc['cash']>cfg['max_invested_pct']+.01 or alloc['scalp']>cfg['max_scalp_pct']+.01 or any(alloc[b]>cfg['max_bucket_pct']+.01 for b in ('scalp','swing','long')):
        raise ValueError('AI 배분이 설정한 위험 한도를 초과했습니다.')
    symbols={i['symbol'] for i in cfg['watchlist']}; evidence={n['id'] for n in snapshot['news']}
    if any(d.symbol not in symbols or not set(d.news_ids)<=evidence for d in plan.decisions):
        raise ValueError('AI가 관심 종목 밖의 종목 또는 없는 뉴스 근거를 제시했습니다.')
    state['allocation']=alloc
    results=[]
    # Reductions precede purchases, permitting reallocation while preserving cash constraints.
    for d in sorted(plan.decisions,key=lambda x:x.action=='buy'):
        key=d.symbol+':'+d.bucket; p=state['positions'].get(key); q=state['quotes'].get(d.symbol)
        result={'symbol':d.symbol,'bucket':d.bucket,'action':d.action,'reason':d.reason,'filled':0,'blocked':[]}
        result['reasoning']={'setup':d.setup,'thesis':d.thesis,'counter_case':d.counter_case,'invalidation':d.invalidation}
        results.append(result)
        if d.action=='hold': continue
        if not fresh(state,q,now): result['blocked']=['시세 유효성·정규장 확인 실패']; continue
        if d.confidence<cfg['min_confidence']: result['blocked']=['AI 신뢰도 하한 미달']; continue
        if d.action=='sell':
            if p:
                desired=math.floor(equity(state)*d.target_pct/100/q['bid'])
                result['filled']=fill_sell(state,key,max(0,p['qty']-desired),now,'AI: '+d.reason)
            continue
        if d.action=='protect':
            if p:
                p['stop']=max(p['stop'],q['bid']*(1-d.stop_pct/100))
                p['trailing_pct']=min(p['trailing_pct'],d.trailing_pct)
                state['revision']+=1
            continue
        result['blocked']=entry_reasons(state,d.symbol,now,news,events)+buy_reasons(state,d,now)
        feat=state['features'].get(d.symbol,{})
        if not feat.get('daily') or (now-stamp(feat.get('daily_checked_at','1970-01-01T00:00:00Z'))).total_seconds()>86400:
            result['blocked'].append('최신 일봉 기술 분석 없음')
        if d.bucket=='scalp' and (not feat.get('minute') or (now-stamp(feat['minute_at'])).total_seconds()>180):
            result['blocked'].append('최신 완료 분봉 부족')
        snapquote=snapshot['quotes'].get(d.symbol)
        if not snapquote or abs(q['ask']/snapquote['ask']-1)>.02: result['blocked'].append('판단 이후 가격 2% 초과 변동')
        if d.bucket=='scalp':
            _,close=session(q['exchange'],market_day(state,now))
            if close-now<timedelta(minutes=10): result['blocked'].append('단타 마감 전 진입 제한')
        if result['blocked']: continue
        value=equity(state); price=q['ask']*(1+cfg['slippage_bps']/10000); unit=price*(1+cfg['fee_bps']/10000)
        if price*(1-d.stop_pct/100)>=q['bid']:
            result['blocked']=['손절폭이 스프레드·매수 비용에 비해 너무 좁음']; continue
        symbol_value=sum(x['qty']*state['quotes'].get(x['symbol'],{}).get('bid',x['entry_price']) for x in state['positions'].values() if x['symbol']==d.symbol)
        bucket_value=sum(x['qty']*state['quotes'].get(x['symbol'],{}).get('bid',x['entry_price']) for x in state['positions'].values() if x['bucket']==d.bucket)
        current_qty=p['qty'] if p else 0
        money=min(state['cash']-value*alloc['cash']/100,
                  value*cfg['max_invested_pct']/100-(value-state['cash']),
                  value*cfg['max_position_pct']/100-symbol_value,
                  value*alloc[d.bucket]/100-bucket_value,
                  value*d.target_pct/100-current_qty*price)
        stop_net=price*(1-d.stop_pct/100)*(1-cfg['slippage_bps']/10000)*(1-(cfg['fee_bps']+cfg['sell_tax_bps'])/10000)
        qty=max(0,math.floor(min(money/unit,value*cfg['risk_per_trade_pct']/100/(unit-stop_net))))
        qty=min(qty,capacity(state,q,'buy'))
        if qty<1: result['blocked']=['자금·종목·운용 구간 한도 또는 호가 잔량 부족']; continue
        total_qty=current_qty+qty; cost=qty*unit
        old_cost=p['unit_cost']*current_qty if p else 0
        state['cash']-=cost
        state['positions'][key]={'symbol':d.symbol,'bucket':d.bucket,'qty':total_qty,
            'unit_cost':(old_cost+cost)/total_qty,'entry_price':(p['entry_price']*current_qty+price*qty)/total_qty if p else price,
            'entered_at':p['entered_at'] if p else now.isoformat(),
            'entry_phase':p.get('entry_phase','legacy') if p else session_context(q['exchange'],now)['phase'],
            'setup':p.get('setup','none') if p else d.setup,
            'stop':max(p['stop'] if p else 0,price*(1-d.stop_pct/100)),
            'target':p['target'] if p else price*(1+d.take_profit_pct/100),
            'trailing_pct':min(p['trailing_pct'],d.trailing_pct) if p else d.trailing_pct,
            'peak_price':max(p['peak_price'] if p else 0,q['bid'])}
        state['liquidity'][d.symbol]['buy']+=qty; state['revision']+=1; result['filled']=qty
        state['trades'].append({'id':uuid.uuid4().hex,'at':now.isoformat(),'side':'buy','symbol':d.symbol,
            'bucket':d.bucket,'qty':qty,'price':price,'fee':cost-qty*price,'pnl':None,
            'reason':'AI: '+d.reason,'quote_at':q['book_timestamp'],
            'entry_phase':state['positions'][key]['entry_phase'],'setup':d.setup})
    state['equity']=equity(state)
    state['ai']['summary']=plan.summary
    state['ai']['next_seconds']=max(cfg['ai_cooldown_seconds'],min(plan.next_review_seconds,cfg['ai_review_seconds']))
    return results


def metrics(state):
    value=equity(state); cfg=state['config']; bench=state['benchmark']; bench_value=None
    if bench:
        bench_value=bench['cash']+sum(qty*state['quotes'].get(symbol,{}).get('bid',0) for symbol,qty in bench['positions'].items())
    return {'equity':value,'cash':state['cash'],'return_pct':(value/cfg['initial_cash']-1)*100,
        'realized_pnl':state['realized_pnl'],'unrealized_pnl':value-state['cash']-sum(p['qty']*p['unit_cost'] for p in state['positions'].values()),
        'max_drawdown_pct':state['max_drawdown_pct'],'day_return_pct':(value/state['day_start_equity']-1)*100,
        'sell_fills':state['closed_lots'],'win_rate':state['winning_lots']/state['closed_lots']*100 if state['closed_lots'] else None,
        'observed_days':len(state['observed_days']),'benchmark_return_pct':(bench_value/cfg['initial_cash']-1)*100 if bench_value is not None else None,
        'sufficient_sample':len(state['observed_days'])>=20 and state['closed_lots']>=30,
        'performance':performance(state),
        'live_ready':False,'live_note':'모의 성과만으로 실거래를 자동 활성화하지 않습니다. 실거래 설정과 별도 시작이 필요합니다.'}
