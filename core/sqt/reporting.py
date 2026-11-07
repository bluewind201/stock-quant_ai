"""Read-only performance and holding-horizon reporting; no price or order calls."""
from datetime import timezone,timedelta
import math
from .models import stamp

KST=timezone(timedelta(hours=9))
PERIODS={'short':'단기','medium':'중기','long':'장기','legacy':'기존 당일/미분류'}


def horizon(row):
    h=row.get('horizon','legacy') if row.get('strategy')=='portfolio' else 'legacy'
    return h if h in PERIODS else 'legacy'


def exit_kind(reason):
    for text,kind in [('수동','manual'),('보호 가격','stop'),('손절','stop'),('추세 이탈','trend'),
                      ('AI 투자','ai'),('기간 종료','expiry'),('마감 전','day_end'),('오전장 정리','day_end'),('익절','target')]:
        if text in reason:return kind
    return 'other'


def summary(state):
    buckets={h:{'label':label,'holdings':[],'realized':0,'today_realized':0,'today_buys':0,'today_sells':0} for h,label in PERIODS.items()}
    for p in state.get('positions',{}).values():
        if p['qty']>0:buckets[horizon(p)]['holdings'].append({'symbol':p['symbol'],'name':p.get('name') or p['symbol'],'qty':p['qty']})
    exits=[];settled={}
    for t in state.get('trades',[]):
        h=horizon(t);b=buckets[h];today=stamp(t['at']).astimezone(KST).date().isoformat()==state.get('day')
        if today:b['today_buys' if t['side']=='buy' else 'today_sells']+=1
        if t['side']!='sell':continue
        pnl=t.get('pnl',0);b['realized']+=pnl
        if today:b['today_realized']+=pnl
        entry=t.get('entry_at');same_day=bool(entry and stamp(entry).astimezone(KST).date()==stamp(t['at']).astimezone(KST).date())
        symbol=t.get('symbol','미기록')
        exits.append({'symbol':symbol,'name':t.get('name') or symbol,'horizon':h,'at':t['at'],
                      'pnl':pnl,'same_day':same_day,'reason':t.get('reason',''),'kind':exit_kind(t.get('reason',''))})
        if entry:
            key=(symbol,entry);settled[key]=settled.get(key,0)+pnl
    closed=[pnl for (symbol,entry),pnl in settled.items() if not (
        state.get('positions',{}).get(symbol,{}).get('entry_at')==entry)]
    peak=None;drawdown=[]
    for p in state.get('equity_curve',[]):
        value=p.get('equity',0)
        if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:continue
        peak=max(peak or value,value)
        if peak<=0:continue
        drawdown.append({'at':p['at'],'pct':(value/peak-1)*100})
    return {'buckets':buckets,'exits':exits[-30:][::-1],'closed_count':len(closed),
            'win_rate':sum(v>0 for v in closed)/len(closed)*100 if closed else None,
            'max_drawdown_pct':min((p['pct'] for p in drawdown),default=None),
            'drawdown':drawdown,'strategy':state.get('config',{}).get('strategy','ai')}
