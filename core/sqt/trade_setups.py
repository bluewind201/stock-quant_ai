"""Explicit, long-only adaptations of public setups; thresholds are our policy."""
from datetime import timedelta
from .models import stamp
from .session_policy import context

PROFILES = {
    'portfolio': {'name':'단기·중기·장기 포트폴리오','rule':'완료 일봉 기반 눌림·모멘텀·장기 추세. 기간별 자금 상한과 변동성 위험 예산, 추세 이탈 및 추적 손절. 당일 강제 청산 없음.'},
    'top5_morning': {'name':'시나리오2 · TOP5 오전 바스켓','rule':'장전 순위 상위 5개 종목을 같은 비중으로 오전 진입 창에서 매수. 별도 돌파·거래량 필터 없이 설정 익절·손절 또는 오전 종료 시각에 청산. 오후 신규 진입·익일 보유 없음.'},
    'in_play': {'name':'AI 승인·당일 주도주 ORB', 'rule':'AI의 유효한 조건부 승인 안에서 첫 5분 고가 돌파를 확인. 동시간 거래량 이력 부족 시 당일 봉 거래량 확장을 명시적으로 사용. 구조·ATR 손절과 손실 예산으로 수량 제한.'},
    'ai': {'name':'AI 종합 판단', 'rule':'기존 뉴스·테마·차트 종합 판단. 가격 상한·비용 검사는 공통 적용.'},
    'orb': {'name':'시초 15분 범위 돌파', 'rule':'09:30~11:00, 완료 5분봉이 시초 15분 고가를 처음 돌파, 최근 5개 봉 대비 거래량 1.5배 이상. 범위 저점 이탈 청산.'},
    'retest': {'name':'돌파 후 눌림 재돌파', 'rule':'시초 고가 돌파 뒤 재시험, 직전 봉 고가 재돌파와 거래량 1.2배 회복. 눌림 저점 이탈 청산.'},
    'bull_flag': {'name':'상승 깃발형', 'rule':'5분 상승봉 1% 이상 뒤 2개 저거래량 조정봉, 상승폭 절반 유지, 조정 고가 재돌파·거래량 1.2배 회복. 조정 저점 이탈 청산.'},
}


def evaluate(strategy, chart, quote, now):
    if strategy in ('ai','top5_morning'):return {'allowed':True,'reason':PROFILES[strategy]['name'],'stop':None}
    def no(reason):return {'allowed':False,'reason':reason,'stop':None}
    if strategy not in PROFILES:return no('알 수 없는 전략')
    session=context('KRX',now)
    if not session['regular_open'] or session['minutes_to_close']<=10:return no('정규장 진입 시간 밖')
    intraday=(chart or {}).get('intraday') or {}
    frame=intraday.get('frames',{}).get('5m',{})
    bars=frame.get('recent_bars',[])
    if len(bars)<(2 if strategy=='in_play' else 4) or intraday.get('vwap_scope')!='session':return no('완료 연속 분봉·세션 VWAP 부족')
    bars=bars[-6:]
    if any(stamp(b['timestamp'])+timedelta(minutes=5)!=stamp(c['timestamp']) for b,c in zip(bars,bars[1:])):
        return no('분봉 누락 구간')
    last=bars[-1]; age=(now-stamp(last['timestamp'])-timedelta(minutes=5)).total_seconds()
    if not 0<=age<=120:return no('최근 완료 5분봉이 오래됨')
    price=quote['ask']; vwap=intraday.get('vwap')
    bar_vwap=last.get('vwap_at_close') or vwap
    if not vwap or price<=vwap or last['close']<=bar_vwap:return no('VWAP 위 가격 확인 필요')
    if price>last['close']*1.01:return no('마지막 확인 종가보다 1% 초과 추격')
    level=intraday.get('opening_range_high')
    stop=None
    if strategy=='in_play':
        level=intraday.get('opening_5m_high')
        if not level or not (bars[-2]['close']<=level<last['close'] and price>level):return no('첫 5분 고가 신규 돌파 아님')
        metrics=chart.get('in_play') or {}
        rvol=metrics.get('opening_rvol')
        if rvol is not None:
            if rvol<2:return no('동시간 시초 거래량 2배 미충족')
        else:
            prior=bars[-6:-1]; baseline=sum(b['volume'] for b in prior)/len(prior)
            if baseline<=0 or last['volume']<baseline*1.5:return no('이력 부족: 당일 돌파 거래량 확장 미충족')
        if metrics.get('relative_strength_pct') is not None and metrics['relative_strength_pct']<=0:
            return no('시장 대비 상대강도 부족')
        atr=(chart.get('daily') or {}).get('atr14')
        if not atr or atr<=0:return no('완료 일봉 ATR 부족')
        stop=max(intraday['opening_5m_low'],price-atr*.25)
    elif strategy=='orb':
        if not 30<=session['minutes_since_open']<120:return no('시초 돌파 진입 시간 밖')
        ratio=frame.get('volume_ratio_prior_bars')
        if not level or not (bars[-2]['close']<=level<last['close'] and price>level):return no('시초 고가 신규 돌파 아님')
        if ratio is None or ratio<1.5:return no('돌파 거래량 1.5배 미충족')
        stop=intraday.get('opening_range_low')
    elif strategy=='retest':
        first,pullback,last=bars[-3:]
        if not level or not (first['close']>level and level*.995<=pullback['close'] and pullback['low']<=level*1.002
                             and last['close']>pullback['high'] and price>pullback['high']):return no('돌파·재시험·재돌파 구조 미충족')
        if pullback['volume']<=0 or last['volume']<pullback['volume']*1.2:return no('재돌파 거래량 부족')
        stop=pullback['low']
    else:
        impulse,a,b,last=bars[-4:]; gain=impulse['close']-impulse['open']
        if gain<impulse['open']*.01:return no('선행 상승봉 부족')
        if not (a['close']<=impulse['close'] and b['close']<=a['close']
                and min(a['low'],b['low'])>=impulse['open']+gain*.5):return no('얕은 조정 구조 미충족')
        volume=(a['volume']+b['volume'])/2
        if not 0<volume<impulse['volume'] or last['volume']<volume*1.2:return no('조정 수축·돌파 거래량 부족')
        if min(last['close'],price)<=max(a['high'],b['high']):return no('깃발 고가 재돌파 미충족')
        stop=min(a['low'],b['low'])
    if not stop or stop>=price:return no('유효한 구조 손절 가격 없음')
    return {'allowed':True,'reason':PROFILES[strategy]['name']+' 확인','stop':stop}
