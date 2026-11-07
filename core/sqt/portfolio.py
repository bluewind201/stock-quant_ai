"""Long-only, daily-bar adaptations. Research-inspired rules, not replicated returns."""
import copy
import math
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from .models import Instrument, stamp
from .calendar import krx_sessions
from . import scanner, learning, notify

KST=timezone(timedelta(hours=9))
LABELS={'short':'단기 · 수일~수주','medium':'중기 · 수주~수개월','long':'장기 · 6개월 이상'}
CAPS={'balanced':{'short':20,'medium':30,'long':30},
      'short':{'short':80,'medium':0,'long':0},
      'medium':{'short':0,'medium':80,'long':0},
      'long':{'short':0,'medium':0,'long':80}}
MARKET_REFERENCE='069500'  # KODEX 200: 시장이 열리면 항상 거래된다
MAX_UNKNOWN_CLOSURES=5


def slot_caps(cfg):
    return {h:math.ceil(cfg['max_positions']*cap/80) for h,cap in CAPS[cfg['portfolio_style']].items()}


def bucket_status(service,state):
    pending=service.live.pending(state['id'],'buy') if service.is_live(state) else []
    result={}
    for h in LABELS:
        held=[p['symbol'] for p in state['positions'].values() if p.get('horizon','short')==h]
        reserved={o['symbol'] for o in pending if o.get('meta',{}).get('horizon','short')==h}
        cap=slot_caps(state['config'])[h]
        result[h]={'held':held,'pending':sorted(reserved),'slot_limit':cap,
                   'open_slots':max(0,cap-len(set(held)|reserved)),
                   'candidates':[r['symbol'] for r in (state.get('scan') or {}).get('candidates',[])
                                 if h in r.get('eligible_horizons',[])]}
    return result


def universes(state,pool):
    cfg=state['config'];watch=cfg.get('portfolio_watchlists',{})
    core=cfg['portfolio_core']
    active=[r['symbol'] for r in sorted(pool.values(),key=scanner._score,reverse=True)]
    liquid=[r['symbol'] for r in sorted(pool.values(),key=lambda r:r.get('trading_amount') or 0,reverse=True)]
    lists={'short':watch.get('short',[])+active+core,'medium':watch.get('medium',[])+liquid+core,
           'long':watch.get('long',[])+core+liquid}
    # Rotate across horizons so one busy intraday ranking cannot consume the scan budget.
    for h in LABELS:
        held=[s for s,p in state['positions'].items() if p.get('horizon','short')==h]
        lists[h]=list(dict.fromkeys(held+(lists[h] if CAPS[cfg['portfolio_style']][h] else [])))[:20]
    symbols=list(state['positions'])
    for i in range(20):
        for h in ('long','medium','short'):
            if i<len(lists[h]) and lists[h][i] not in symbols and len(symbols)<48:
                symbols.append(lists[h][i])
    return symbols,lists


def partition(rows,state):
    """Independent ranked queues, disjoint assignments; held horizon never changes."""
    assigned={s:p.get('horizon','short') for s,p in state['positions'].items()}
    queues={h:sorted([r for r in rows if h in r['eligible_horizons']],
                     key=lambda r:r['portfolio']['signals'][h]['rank_score'],reverse=True) for h in LABELS}
    # Round-robin reserves a turn for every period instead of giving all names to one.
    for i in range(5):
        for h in ('long','medium','short'):
            available=[r for r in queues[h] if r['symbol'] not in assigned]
            if available:assigned[available[0]['symbol']]=h
    for r in rows:
        h=assigned.get(r['symbol'])
        r['eligible_horizons']=[h] if h in r['eligible_horizons'] else []


def market_closures(service,state,now):
    """달력엔 거래일인데 시장 기준 ETF 일봉에 없는 날 = 달력이 모르는 휴장일.

    달력 오류 하루가 전 종목을 1년 동안 탈락시키지 않게 한다. 기준 일봉의 마지막 날 이후는
    판단하지 않으므로, 이전 거래일 자료가 갱신됐는지 보는 신선도 검사는 그대로 엄격하다.
    """
    day=now.astimezone(KST).date().isoformat()
    cached=state.get('market_closures') or {}
    if cached.get('day')==day:return set(cached['dates'])
    try:
        rows=service.broker.history(Instrument(symbol=MARKET_REFERENCE,exchange='KRX'),count=300)
        have={b['date'] for b in rows if b['date']<day}
        gaps=[d for d in krx_sessions(min(have),max(have)) if d not in have]
    except Exception:
        return set()
    if len(gaps)>MAX_UNKNOWN_CLOSURES:return set()  # 기준 종목 자체의 자료 문제로 본다
    state['market_closures']={'day':day,'dates':gaps}
    if gaps:service._log(state,'달력에 없는 휴장일로 처리: '+', '.join(gaps))
    return set(gaps)


def features(bars,now,closed=()):
    today=now.astimezone(KST).date()
    rows=[b for b in bars if b['date']<today.isoformat()]
    dates=[b['date'] for b in rows]
    if len(rows)<61:raise ValueError('완료 일봉 61개 미만')
    known=set(dates)
    missing=[d for d in krx_sessions(dates[0],dates[-1]) if d not in known and d not in closed]
    if missing:raise ValueError(f'일봉 누락 ({missing[0]} 외 {len(missing)-1}일)' if len(missing)>1 else f'일봉 누락 ({missing[0]})')
    if len(known)!=len(dates) or dates!=sorted(dates):raise ValueError('일봉 중복 또는 순서 오류')
    previous=[d for d in krx_sessions((today-timedelta(days=15)).isoformat(),(today-timedelta(days=1)).isoformat()) if d not in closed][-1]
    if dates[-1]!=previous:raise ValueError(f'이전 거래일 자료 미갱신 (마지막 {dates[-1]}, 기대 {previous})')
    for b in rows:
        if (any(not math.isfinite(float(b[k])) or b[k]<=0 for k in ('open','high','low','close'))
            or not math.isfinite(float(b['volume'])) or b['volume']<0
            or not b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']):
            raise ValueError('일봉 가격·거래량 오류')
    closes=[b['close'] for b in rows];price=closes[-1]
    avg=lambda n:sum(closes[-n:])/n if len(closes)>=n else None
    tr=[max(b['high']-b['low'],abs(b['high']-a['close']),abs(b['low']-a['close'])) for a,b in zip(rows,rows[1:])]
    atr=sum(tr[-14:])/14
    f={'price':price,'last_date':dates[-1],'bars':len(rows),'sma20':avg(20),'sma50':avg(50),
       'sma200':avg(200),'atr14':atr,'amount20':sum(b['close']*b['volume'] for b in rows[-20:])/20,
       'momentum63_pct':(price/closes[-64]-1)*100 if len(rows)>=64 else None,
       'momentum126_skip21_pct':(closes[-22]/closes[-127]-1)*100 if len(rows)>=127 else None,
       'momentum252_skip21_pct':(closes[-22]/closes[-253]-1)*100 if len(rows)>=253 else None}
    signals={}
    for h in LABELS:
        reason='추세 조건 미충족';allowed=False
        if h=='short':
            trend=price>f['sma50'] and f['sma20']>f['sma50']
            pullback=abs(price/f['sma20']-1)<=.04 and price>closes[-2]
            breakout=price>max(b['high'] for b in rows[-21:-1]) and price/f['sma20']<=1.10
            allowed=trend and (pullback or breakout)
            reason=('20일 고점 돌파·과열 상한 이내' if breakout else '상승 추세에서 20일 평균 부근 반등') if allowed else '눌림 반등 또는 20일 고점 돌파 대기'
        elif h=='medium':
            m=f['momentum126_skip21_pct']
            allowed=((m is not None and m>0) or (f['momentum63_pct'] is not None and f['momentum63_pct']>0)) and price>f['sma50'] and f['sma20']>f['sma50']
            reason='3개월 또는 최근 1개월 제외 6개월 모멘텀 양수·상승 추세' if allowed else '3/6개월 모멘텀 또는 상승 추세 부족'
        else:
            m=f['momentum252_skip21_pct']
            allowed=m is not None and m>0 and price>f['sma200']
            reason='최근 1개월 제외 12개월 모멘텀 양수·200일 평균 위' if allowed else '253일 이력 또는 장기 상승 추세 부족'
        floor,limit,mult={'short':(3,8,2),'medium':(5,12,3),'long':(8,18,4)}[h]
        raw=atr/price*100*mult
        stop=max(floor,min(limit,raw))
        if raw>limit:allowed=False;reason='변동성이 해당 기간의 손절 한도를 초과'
        signals[h]={'eligible':allowed,'reason':reason,'stop_pct':round(stop,2),
                    'rank_score':(f['momentum63_pct'] or 0) if h=='short' else
                    max(f['momentum63_pct'] or 0,f['momentum126_skip21_pct'] or 0) if h=='medium' else f['momentum252_skip21_pct'] or 0}
    chart=[]
    for i in range(max(0,len(rows)-60),len(rows)):
        point={k:rows[i][k] for k in ('date','open','high','low','close','volume')}
        for n in (20,50,200):point['sma'+str(n)]=sum(closes[i-n+1:i+1])/n if i+1>=n else None
        chart.append(point)
    return dict(f,signals=signals,chart=chart)


def scan(service,state,now):
    cfg=scanner._cfg(state['config'])
    try:pool=scanner._pool(service.broker,cfg)
    except Exception:pool={}
    # Stable universe and held names take priority over today's most active stocks.
    symbols,universe=universes(state,pool)
    try:names=service.broker.names(symbols)
    except Exception:names={}
    closed=market_closures(service,state,now)
    def read(symbol):
        row=copy.deepcopy(pool.get(symbol,{'symbol':symbol,'origins':['core'],'themes':[]}))
        row['name']=row.get('name') or names.get(symbol) or symbol
        try:scanner._enrich(service.broker,row,cfg)
        except Exception:
            row['news']=[];row['news_count']=0
            row['data_notes']=['보조 뉴스·수급 수집 일부 실패: 확인된 자료만 사용']
        try:
            cache=state.setdefault('portfolio_daily',{})
            hit=cache.get(symbol)
            if hit and 0<=(now-stamp(hit['at'])).total_seconds()<3600 and hit['day']==now.astimezone(KST).date().isoformat():
                daily=copy.deepcopy(hit['data'])
            else:
                daily=features(service.broker.history(Instrument(symbol=symbol,exchange='KRX'),count=300),now,closed)
                cache[symbol]={'at':now.isoformat(),'day':now.astimezone(KST).date().isoformat(),'data':daily}
            row['technical']={'daily':daily,'as_of':now.isoformat(),'errors':[]}
            row['portfolio']=daily
            row['last_price']=daily['price']
            row['trading_amount']=daily['amount20']
        except Exception as exc:
            row['portfolio']={'signals':{},'error':str(exc)[:150]}
        return row
    # Store cache writes are confined to this in-memory account; each task owns its symbol.
    with ThreadPoolExecutor(max_workers=3) as pool_exec:rows=list(pool_exec.map(read,symbols))
    state['portfolio_daily']={s:v for s,v in state.get('portfolio_daily',{}).items() if s in symbols}
    approved=[];checks=[]
    caps=CAPS[state['config']['portfolio_style']]
    for r in rows:
        f=r['portfolio'];error=f.get('error')
        if not error and not r.get('warnings_checked'):error='거래 경고·정지 여부 조회 실패'
        if not error and r.get('warnings'):error='투자 경고·거래정지 확인'
        if not error and f['amount20']<cfg['min_trading_amount_krw']:error='20일 평균 거래대금 부족'
        # 탐색 목록은 수집 순서일 뿐이다. 스캔한 종목은 규칙을 통과한 어느 기간이든 후보가 된다(3.22.2 사용자 결정).
        eligible=[h for h,s in f.get('signals',{}).items() if s['eligible'] and caps[h]>0]
        checks.append({'symbol':r['symbol'],'name':r['name'],'eligible':eligible if not error else [],
                       'reason':error or ('기간별 매수 검토 가능' if eligible else ' / '.join(
                           LABELS[h]+': '+f['signals'][h]['reason'] for h in LABELS if caps[h]))})
        r['eligible_horizons']=eligible if not error else []
        if not error:approved.append(r)
    partition(approved,state)
    by_symbol={r['symbol']:r for r in approved}
    for c in checks:
        c['period_reasons']={h:by_symbol[c['symbol']]['portfolio']['signals'][h]['reason']
                             for h in LABELS if caps[h]} if c['symbol'] in by_symbol else {}
        before=c['eligible']
        c['eligible']=by_symbol[c['symbol']]['eligible_horizons'] if c['symbol'] in by_symbol else []
        if before and not c['eligible']:c['reason']='가격 조건 통과 · 기간별 상위 5개 후보 밖 또는 보유 기간 유지'
    state['portfolio_checks']=checks
    state['portfolio_universes']=universe
    state['portfolio_held_data']={r['symbol']:r.get('portfolio',{}) for r in rows if r['symbol'] in state['positions']}
    return {'scanned_at':now.isoformat(),'pool_size':len(symbols),'enriched':len(rows),'candidates':approved,
            'rejected':[c for c in checks if not c['eligible']], 'note':'완료 일봉 기반 기간별 규칙. 재무제표·이익 추정치는 미수집이며 가치투자 판단으로 대체하지 않음.'}


def apply_plan(service,state,plan,now):
    candidates={c['symbol']:c for c in (state.get('scan') or {}).get('candidates',[])}
    picks=[];seen=set();slots=bucket_status(service,state)
    for p in plan.get('picks',[]):
        c=candidates.get(p['symbol']);h=p.get('horizon','short')
        if not c or h not in c.get('eligible_horizons',[]) or p['symbol'] in seen:
            service._log(state,f'{p["symbol"]} 매수 보류: AI가 제안한 종목·투자 기간이 검증된 후보와 다름')
            continue
        if p.get('max_entry_price',0)<=0:
            service._log(state,f'{p["symbol"]} 매수 보류: AI 최대 매수가 누락')
            continue
        if slots[h]['open_slots']<=sum(1 for pick in picks if pick['horizon']==h):
            service._log(state,f'{p["symbol"]} 매수 보류: {LABELS[h]} 종목 수 한도')
            continue
        seen.add(p['symbol']);f=c['portfolio'];sl=f['signals'][h]['stop_pct']
        picks.append(dict(p,name=c['name'],stop_loss_pct=sl,take_profit_pct=min(60,sl*3),
                          weight_pct=min(p['weight_pct'],state['config']['position_pct']),
                          max_entry_price=min(p['max_entry_price'],f['price']*1.05),_done=False))
    evidence=exit_evidence(state)
    exits={};deferred=[]
    for exit_plan in plan.get('exits',[]):
        symbol=exit_plan['symbol'];pos=state['positions'].get(symbol)
        if not pos:continue
        entered=stamp(pos['entry_at'])
        same_day=entered.astimezone(KST).date()==now.astimezone(KST).date()
        verified=[e for e in evidence if e['symbol']==symbol and e['id'] in exit_plan.get('evidence_ids',[])
                  and entered<=stamp(e['published_at'])<=now]
        if same_day and not verified:
            deferred.append({'symbol':symbol,'reason':'매수 당일 AI 매도 보류: 진입 이후 새 위험 근거 확인 필요'})
            service._log(state,deferred[-1]['reason']+' · '+symbol)
            continue
        exits[symbol]={'reason':exit_plan['reason'],'decided_at':now.isoformat(),
                       'entry_at':pos['entry_at'],'evidence_ids':[e['id'] for e in verified]}
    state['portfolio_exits']=exits
    state['portfolio_exit_deferred']=deferred
    reviews=plan.setdefault('horizon_reviews',{})
    for h in LABELS:
        if not reviews.get(h):
            reviews[h]=('선택하지 않은 투자 기간' if not CAPS[state['config']['portfolio_style']][h] else
                        '기간별 종목 수 한도 도달' if not slots[h]['open_slots'] else
                        '기간별 조건을 통과한 후보 없음' if not slots[h]['candidates'] else
                        'AI 개별 설명 미제공 · 아래 선정 종목과 전체 의견 확인')
    state['plan']=dict(plan,picks=picks,decided_at=now.isoformat(),market_stance='normal' if picks else 'no_trade')
    state['phase']='planned' if picks else 'monitoring'
    state['stamps']['plan']=now.isoformat()
    service._log(state,f'기간별 AI 판단: 매수 후보 {len(picks)}개 · '+plan['summary'])
    notify.post(notify.plan_message(state,state['plan'],candidates))


def exit_evidence(state):
    evidence=[e for e in state.get('office_exit_evidence',[]) if e.get('symbol') in state['positions']]
    for c in (state.get('scan') or {}).get('candidates',[]):
        if c['symbol'] not in state['positions']:continue
        for n in c.get('news',[]):
            try:stamp(n['published_at'])
            except (ValueError,KeyError,TypeError):continue
            material='|'.join([c['symbol'],n.get('title',''),n.get('source',''),n['published_at']])
            evidence.append({'id':hashlib.sha256(material.encode()).hexdigest()[:16],
                             'symbol':c['symbol'],'title':n.get('title',''),
                             'source':n.get('source',''),'published_at':n['published_at']})
    return evidence


def budget(service,state,horizon,requested,quote,sl_pct,now):
    equity=service.equity(state);cfg=state['config']
    pending=service.live.pending(state['id'],'buy') if service.is_live(state) else []
    if bucket_status(service,state)[horizon]['open_slots']<=0:return 0
    used={h:0.0 for h in LABELS}
    for p in state['positions'].values():
        used[p.get('horizon','short')]+=p['qty']*max(p['entry_price'],state.get('quotes',{}).get(p['symbol'],{}).get('bid',0))
    for o in pending:
        used[o.get('meta',{}).get('horizon','short')]+=max(0,o['qty']-o.get('filled_qty',0))*o['limit_price']*(1+cfg['fee_bps']/10000)
    cap=CAPS[cfg['portfolio_style']][horizon]/100*equity
    costs=(2*cfg['fee_bps']+cfg['sell_tax_bps']+2*cfg['slippage_bps']+quote.get('spread_bps',0))/100
    risk_size=equity*cfg['portfolio_risk_pct']/(sl_pct+costs)
    return max(0,min(requested,cap-used[horizon],equity*.8-sum(used.values()),risk_size,
                     state['cash']-sum(max(0,o['qty']-o.get('filled_qty',0))*o['limit_price']*(1+cfg['fee_bps']/10000) for o in pending)))


def monitor(service,state,now):
    service._refresh_quotes(state,list(state['positions']))
    for symbol,p in list(state['positions'].items()):
        q=state['quotes'].get(symbol)
        if p.get('closing') or not service._fresh_quote(q,now):continue
        h=p.get('horizon','short');bid=q['bid'];reason=''
        p['peak_price']=max(p.get('peak_price',p['entry_price']),bid)
        trail=p['peak_price']*(1-p['sl_pct']/100)
        p['sl_price']=max(p['sl_price'],trail)
        f=state.get('portfolio_held_data',{}).get(symbol,{})
        line=f.get({'short':'sma20','medium':'sma50','long':'sma200'}[h])
        entry_day=stamp(p['entry_at']).astimezone(KST).date().isoformat()
        after_entry=entry_day<=f.get('last_date','')<now.astimezone(KST).date().isoformat()
        decision=state.get('portfolio_exits',{}).get(symbol)
        if bid<=p['sl_price']:reason='보호 가격 이탈 · 추적 손절'
        elif after_entry and line and f.get('price',0)<line:reason='진입 이후 완료 일봉에서 투자 기간의 추세 이탈'
        elif isinstance(decision,dict) and decision.get('entry_at')==p['entry_at']:
            if entry_day<now.astimezone(KST).date().isoformat() or decision.get('evidence_ids'):
                reason='AI 투자 근거 재검토: '+decision['reason']
        age=(now-stamp(p['entry_at'])).days
        if not reason and ((h=='short' and age>=20) or (h=='medium' and age>=120)):reason='계획한 투자 기간 종료'
        if reason:service._fill_sell(state,symbol,bid,reason,now)


def tick(service,state,now):
    cfg=state['config'];hh=now.astimezone(KST).strftime('%H:%M')
    if service._needs_plan(state,now):service._try_plan(state,now)
    slot='midday' if '12:00'<=hh<'12:15' else 'afternoon' if '14:30'<=hh<'14:45' else None
    reviewed=state['events'].setdefault('portfolio_reviews',[])
    if slot and slot not in reviewed and service._ai_allowed(state,now,anchor=True):
        reviewed.append(slot)
        if service._do_scan(state,now) and (state['scan']['candidates'] or state['positions']):
            service._plan_call(state,now)
    if '09:10'<=hh<'15:30' and not state.get('plan') and not state['ai'].get('pending'):
        # 회의가 조용히 빠지지 않게: 이유와 함께 하루 한 번 알린다.
        from .diagnostics import meeting
        m=meeting(state,now)
        if m['status'] in ('bad','warn'):
            notify.post_once(f'no-meeting:{state["day"]}:{state["id"]}',
                             notify.error_message('오늘 장전 회의가 열리지 않았습니다',m['detail']))
    if '09:00'<=hh<'15:30' and service._tradable(state,now):
        monitor(service,state,now)
        if '09:05'<=hh<'15:10' and state['phase'] in ('planned','entering'):
            service._do_entry(state,now)
    if hh>='15:10' and service.is_live(state):service.live.cancel_all(state,'buy')
    if hh>='15:30' and state['phase']!='closed':
        state['phase']='closed';state['stamps']['closed']=now.isoformat()
        service._record_day(state,state['day'])
        state['tuning']['pending_snapshot']=service._review_snapshot(state,state['day'],now)
        service._log(state,'오늘 평가 완료 · 단기·중기·장기 보유는 유지')
        notify.post(notify.summary_message(state),notify.summary_kind(state))
    if cfg['daily_review']:service._maybe_review(state,now,state['day'])
    if service.is_live(state):service.live.pulse(state,now,service._log)
    service._record_equity(state,now);state['last_poll_at']=now.isoformat()

