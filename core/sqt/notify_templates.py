"""Short Korean notifications; judgments, submissions and fills are distinct."""
import html
from .notify import esc,kst,won
from .models import stamp
from .notify import KST
from .reporting import summary,horizon,PERIODS


def holdings_text(state):
    if state is None:return ''
    report=summary(state)
    lines=[]
    for h,b in report['buckets'].items():
        if h=='legacy' and not b['holdings']:continue
        names=', '.join(f'{p["name"]} {p["qty"]}주' for p in b['holdings']) or '없음'
        lines.append(f'{b["label"]}: '+short(names,220))
    return '\n'+'\n'.join(lines)

def short(value,limit=80):
    text=' '.join(str(value or '').split())
    return esc(text[:limit]+('…' if len(text)>limit else ''))

def plan_message(state,plan,candidates):
    picks=plan.get('picks',[])
    if state.get('config',{}).get('strategy')=='portfolio':
        lines=[f'📋 <b>기간별 매수 계획 · 아직 미체결</b> · {kst()}',short(plan.get('summary'),90)]
        for h in ('short','medium','long'):
            names=', '.join(p.get('name') or p['symbol'] for p in picks if p.get('horizon')==h) or '없음'
            lines.append(f'<b>{PERIODS[h]}</b>: '+short(names,200))
            if plan.get('horizon_reviews',{}).get(h):lines.append(short(plan['horizon_reviews'][h],80))
        lines.append('실제 매수·매도는 체결 알림으로 확인')
        return '\n'.join(lines)
    lines=[f'📋 <b>장전 판단 · 매수 계획(미체결)</b> · {kst()}',short(plan.get('summary'),80)]
    if not picks:lines.append('매수 없음 · '+short(plan.get('no_trade_reason') or plan.get('summary'),60))
    for p in picks[:5]:
        c=candidates.get(p['symbol'],{})
        if state.get('config',{}).get('strategy')=='portfolio':
            label={'short':'단기','medium':'중기','long':'장기'}[p.get('horizon','short')]
            lines.append(f'• <b>{short(p.get("name") or p["symbol"],20)}</b> {label} · 최대 {p["weight_pct"]:g}% · 초기 손절 -{p["stop_loss_pct"]:g}%')
        else:
            lines.append(f'• <b>{short(p.get("name") or p["symbol"],20)}</b> {p["weight_pct"]:g}% · 익절 +{p["take_profit_pct"]:g}% / 손절 -{p["stop_loss_pct"]:g}%')
        news=c.get('news',[])[:1]
        if news:
            n=news[0]
            link=f' <a href="{html.escape(n["url"],quote=True)}">원문</a>' if str(n.get('url','')).startswith('https://') else ''
            lines.append(f'  [{kst(n.get("published_at"))}] {short(n.get("title"),35)}'+link)
        else:lines.append('  이유: '+short(p.get('thesis'),35))
        flow=[]
        for key,label in [('flow_foreigner_1d','외국인'),('flow_institution_1d','기관')]:
            if c.get(key) is not None:flow.append(f'{label} {c[key]:+,}')
        if flow:lines.append('  전일 '+ ' · '.join(flow))
    if state.get('config',{}).get('strategy')=='top5_morning':lines.append('TOP5 바스켓: 관망 의견과 별도로 오전 매수 시도')
    lines.append('체결 시 별도 알림 · 자세한 근거는 대시보드')
    return '\n'.join(lines)

def web_link():
    """PUBLIC_URL 이 있으면 대시보드 링크를, 없으면 안내 문구만."""
    import os
    url=os.getenv('PUBLIC_URL','').strip()
    if url.startswith(('http://','https://')):
        return f'\n<a href="{html.escape(url,quote=True)}">웹에서 자세히 보기</a>'
    return '\n자세한 근거는 웹 화면에서 확인'

def _period(row):
    return {'short':'단기','medium':'중기','long':'장기'}.get(row.get('horizon'),'') if row.get('strategy')=='portfolio' else ''

def buy_message(pos,mode='가상',state=None):
    mode='모의' if mode=='가상' else mode
    is_usd = pos.get('currency') == 'USD'
    mkt_tag = '🇺🇸미국' if is_usd else ''
    label=' · '.join(x for x in (mkt_tag,_period(pos),mode) if x)
    qty=pos['qty'];price=pos['entry_price']
    if is_usd:
        cost_line = f'{qty}주 × ${price:,.2f} (약 {pos.get("cost",0):,.0f}원) · {kst(pos.get("entry_at"))}'
        stop_line = (f'손절가 ${pos["sl_price"]:,.2f} · 본절잠금·추세추종 보유'
                     if pos.get('strategy')=='portfolio'
                     else f'목표가 ${pos["tp_price"]:,.2f} / 손절가 ${pos["sl_price"]:,.2f}')
    else:
        cost_line = f'{qty}주 × {price:,.0f}원 = {qty*price:,.0f}원 · {kst(pos.get("entry_at"))}'
        stop_line = (f'손절가 {pos["sl_price"]:,.0f}원 · 추세를 따라 보유'
                     if pos.get('strategy')=='portfolio'
                     else f'목표가 {pos["tp_price"]:,.0f}원 / 손절가 {pos["sl_price"]:,.0f}')
    lines=[f'🟢 <b>매수</b> · {short(pos.get("name") or pos["symbol"],24)} ({label})',
           cost_line, stop_line]
    if pos.get('thesis'):lines.append('이유: '+short(pos.get('thesis'),60))
    return '\n'.join(lines)+web_link()

SELL_TITLES={'stop':'손절 매도','target':'익절 매도','trend':'추세 이탈 매도','ai':'AI 판단 매도','manual':'수동 매도',
             'expiry':'보유 기간 종료 매도','day_end':'장 마감 정리 매도'}

def sell_title(trade):
    from .reporting import exit_kind
    pnl=trade.get('pnl',0) or 0
    kind=exit_kind(trade.get('reason',''))
    if kind=='stop' and pnl>0:return '추적 손절 매도 · 이익 확정'
    return SELL_TITLES.get(kind) or ('이익 실현 매도' if pnl>0 else '손실 매도')

def sell_message(trade,name,mode='가상',state=None):
    pnl=trade.get('pnl',0)
    mode='모의' if mode=='가상' else mode
    is_usd = trade.get('currency') == 'USD'
    mkt_tag = '🇺🇸미국' if is_usd else ''
    label=' · '.join(x for x in (mkt_tag,PERIODS[horizon(trade)] if horizon(trade)!='legacy' else '',mode) if x)
    px_str = f'${trade["price"]:,.2f}' if is_usd else f'{trade["price"]:,.0f}원'
    lines=[f'{"🔴" if pnl<0 else "🔵"} <b>{sell_title(trade)}</b> · {short(name or trade["symbol"],24)} ({label})',
           f'{trade["qty"]}주 × {px_str} · {kst(trade.get("at"))}',
           f'실현손익 <b>{won(pnl)}</b> ({trade.get("pnl_pct",0):+.2f}%)',
           '사유: '+short(trade.get('reason'),65)]
    if state is not None:lines.append(f'누적 실현손익 {won(state.get("realized_pnl",0))}')
    return '\n'.join(lines)+web_link()

def order_message(intent):
    label='매수' if intent['side']=='buy' else '매도'
    return (f'📨 <b>{label} 주문 접수 · 미체결 수량 확인 중</b> (실거래)\n'
            f'{short(intent.get("name") or intent["symbol"],24)} {intent["qty"]}주 · 지정가 {intent["limit_price"]:,.0f}원\n'
            '실제 체결 시 별도 알림')

def event_message(kind,symbol,name,payload,plan):
    label={'midday':'정오 재평가','opening':'개장 재평가','news':'뉴스 판단','surge':'급등 판단'}.get(kind,'장중 판단')
    lines=[f'⚡ <b>{label}</b> · {kst()}',short(plan.get('summary'),85)]
    labels={'buy':'매수 검토(체결 아님)','close':'매도 판단(체결 아님)','hold':'보유 유지'}
    for d in plan.get('decisions',[])[:5]:
        lines.append(f'• {labels.get(d["action"],d["action"])} {esc(d["symbol"])} · {short(d.get("reason"),45)}')
    if not plan.get('decisions'):lines.append('추가 매수·매도 없음')
    return '\n'.join(lines)

def eod_message(plan,positions,bought=None):
    lines=[f'🌆 <b>마감 판단</b> · {kst()}',short(plan.get('summary'),80)]
    for d in plan.get('decisions',[])[:5]:
        label='익일 보유' if d['action']=='hold_overnight' else '매도 판단(체결 아님)'
        p=positions.get(d['symbol'],{})
        lines.append(f'• {label} {short(p.get("name") or d["symbol"],20)} · {short(d.get("reason"),40)}')
    for b in plan.get('buys',[])[:2]:
        lines.append(f'• 마감 매수 {"요청" if bought and b["symbol"] in bought else "보류"} · {esc(b["symbol"])}')
    return '\n'.join(lines)

def review_message(record):
    lines=[f'🧾 <b>{esc(record.get("day",""))} 마감 복기</b>',short(record.get('summary'),90)]
    if record.get('diagnosis'):lines.append('원인: '+short(record['diagnosis'],100))
    for lesson in record.get('lessons',[])[:2]:lines.append('개선안: '+short(lesson,65))
    lines.append('설정 변경 '+(str(len(record['applied']))+'건' if record.get('applied') else '없음 · 개선안은 근거 평가 후 적용'))
    return '\n'.join(lines)

def _today_trades(state):
    day=state.get('day','')
    rows=[t for t in state.get('trades',[]) if stamp(t['at']).astimezone(KST).date().isoformat()==day]
    return [t for t in rows if t['side']=='buy'],[t for t in rows if t['side']=='sell']

def summary_kind(state):
    """결산은 매일 보낸다. 거래가 없는 날에도 '살아 있고, 왜 매매가 없었는지'를 알려야 한다."""
    return 'trade'

def no_trade_reason(state):
    from datetime import datetime,timezone
    from .diagnostics import meeting
    m=meeting(state,datetime.now(timezone.utc));plan=state.get('plan') or {}
    if m['status']=='ok' and plan:
        picks=len(plan.get('picks') or [])
        return f'장전 회의 매수 계획 {picks}건'+(' · 가격·자금 조건을 통과하지 못해 체결 없음' if picks else ' · 조건에 맞는 매수 없음')
    return m['detail']

def summary_message(state):
    day=state.get('day','')
    buys,sells=_today_trades(state)
    pnl=sum(t.get('pnl',0) for t in sells)
    lines=[f'📊 <b>{esc(day)} 결산</b>',f'매수 체결 {len(buys)}건 / 매도 체결 {len(sells)}건',
           f'오늘 실현손익 <b>{won(pnl)}</b> · 누적 {won(state.get("realized_pnl",0))}']
    if not buys and not sells:lines.append('오늘 매매 없음 · '+short(no_trade_reason(state),110))
    equity=state.get('equity');initial=(state.get('config') or {}).get('initial_cash')
    if isinstance(equity,(int,float)) and initial:
        lines.append(f'평가자산 {equity:,.0f}원 ({(equity/initial-1)*100:+.2f}%)')
    return '\n'.join(lines)+holdings_text(state)+web_link()

def financial_message(brief,slot='morning'):
    names={'Consumer Price Index':'미국 소비자물가(CPI)','Employment Situation':'미국 고용보고서',
        'Producer Price Index':'미국 생산자물가(PPI)','Gross Domestic Product':'미국 GDP',
        'Personal Income and Outlays':'미국 개인소득·소비(PCE)','FOMC Minutes':'FOMC 회의록',
        'Speech -':'연준 발언 -'}
    lines=[f'🗓 <b>{esc(brief["day"])} {"오늘 금융 일정" if slot=="morning" else "미국장 주요 일정"}</b> (한국시간)']
    for e in brief.get('events',[])[:6]:
        title=e['title']
        for a,b in names.items():title=title.replace(a,b)
        lines.append(f'• {kst(e["scheduled_at"])} {short(title,62)}')
    if not brief.get('events'):lines.append('수집된 확정 일정 없음')
    lines.append('미국장 일정은 내일 정오까지 · 금리·환율 변동에 주의')
    attention=brief.get('attention',[])[:2]
    if attention:
        lines.append('관심 이슈(보도·발표, 행사 시각 미확인)')
        lines.extend('• '+short(e['title'],65) for e in attention)
    if brief.get('missing_sources'):lines.append('⚠ 일부 일정 공급원 미확인 · 일정 없음으로 해석하지 마세요')
    lines.append('확인: '+kst(brief.get('as_of'))+' · 상세 출처는 대시보드')
    return '\n'.join(lines)

def error_message(what,detail):
    return f'⚠️ <b>{short(what,50)}</b> · {kst()}\n{short(detail,130)}'
