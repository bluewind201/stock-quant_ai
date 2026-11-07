"""Exchange-local research/trading windows. Lunch is a policy window, never a market closure."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from .calendar import calendar, session

LABELS={'closed':'휴장·대기','preopen':'개장 전 준비','opening':'개장 초기','morning':'오전 추세',
        'midday':'점심 재평가','afternoon':'오후 추세','closing':'마감 대응','postclose':'마감 복기'}


def context(exchange,now):
    zone=ZoneInfo('Asia/Seoul' if exchange=='KRX' else 'America/New_York')
    local=now.astimezone(zone); day=local.date().isoformat(); cal=calendar(exchange)
    phase='closed'; opened=closed=None
    if cal.is_session(day):
        opened,closed=session(exchange,day)
        if opened-timedelta(minutes=60)<=now<opened: phase='preopen'
        elif opened<=now<closed:
            if now>=closed-timedelta(minutes=30): phase='closing'
            elif now<opened+timedelta(minutes=30): phase='opening'
            elif (local.hour,local.minute)<(11,30): phase='morning'
            elif (local.hour,local.minute)<(13,30): phase='midday'
            else: phase='afternoon'
        elif closed<=now<closed+timedelta(minutes=60): phase='postclose'
    return {'exchange':exchange,'timezone':str(zone),'day':day,'phase':phase,'label':LABELS[phase],
            'key':day+':'+phase,'regular_open':bool(opened and opened<=now<closed),
            'planning_only':phase in ('preopen','postclose','closed'),
            'open_at':opened.isoformat() if opened else None,'close_at':closed.isoformat() if closed else None,
            'minutes_since_open':(now-opened).total_seconds()/60 if opened else None,
            'minutes_to_close':(closed-now).total_seconds()/60 if closed else None,
            'review_seconds':{'opening':300,'closing':300,'midday':1800}.get(phase,900),
            'focus':{
                'preopen':'이전 미국장/국내장 자료, 연준·대통령 발표와 경제 일정, 갭·시나리오 준비. 주문 금지.',
                'opening':'초기 급등 추격 금지. 완료 개장 범위·거래량·VWAP와 스프레드 확인.',
                'morning':'거래량 동반 돌파와 눌림목을 구분하고 일봉·장중 방향 일치 여부 확인.',
                'midday':'거래량 둔화 여부를 실제 데이터로 확인. 신규 단타 기준 강화·기존 수익 보호.',
                'afternoon':'오전 고저점 재돌파, 추세 지속·반전 확인. 손실 만회 목적 진입 금지.',
                'closing':'단타 청산 우선. 중장기 익일 보유 위험과 다음 주요 발표 점검.',
                'postclose':'비용 차감 성과·실패 근거·다음 장 시나리오 복기. 주문 금지.',
                'closed':'다음 정규장 준비 시간까지 대기.'}[phase]}


def account_context(state,now):
    return context(state['config']['watchlist'][0]['exchange'],now)
