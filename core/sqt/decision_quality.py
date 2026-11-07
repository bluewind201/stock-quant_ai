"""Observable entry evidence and payoff checks; model confidence is not a win probability."""
from datetime import timedelta
from .models import stamp
from .session_policy import account_context, context


def net_payoff(config,proposal,quote):
    buy=quote['ask']*(1+config['slippage_bps']/10000)
    cost=buy*(1+config['fee_bps']/10000)
    exit_factor=(1-config['slippage_bps']/10000)*(1-(config['fee_bps']+config['sell_tax_bps'])/10000)
    gain=buy*(1+proposal.take_profit_pct/100)*exit_factor-cost
    loss=cost-buy*(1-proposal.stop_pct/100)*exit_factor
    return {'reward_risk':gain/loss if loss>0 else 0,'net_target_pct':gain/cost*100,
            'roundtrip_cost_bps':(cost-buy*exit_factor)/cost*10000+quote['spread_bps']}


def buy_reasons(state,proposal,now):
    cfg=state['config']
    if not cfg.get('decision_policy_v2',True): return []
    d=proposal; q=state['quotes'].get(d.symbol); reasons=[]
    if not q:return ['시세 없음']
    phase=context(q['exchange'],now); feat=state['features'].get(d.symbol,{})
    daily=feat.get('daily') or {}; minute=feat.get('minute') or {}; intraday=feat.get('intraday') or {}
    if any(len(getattr(d,k,'').strip())<12 for k in ('thesis','counter_case','invalidation')):
        reasons.append('매수 논거·반대 근거·무효화 조건이 구체적이지 않음')
    if phase['planning_only']:reasons.append('개장 전·마감 후는 분석만 허용')
    if phase['regular_open'] and phase['minutes_since_open']<cfg.get('opening_wait_minutes',5):
        reasons.append('개장 초기 가격 확인 대기')
    if phase['regular_open'] and phase['minutes_to_close']<=10:
        reasons.append('마감 10분 전 신규 매수 제한')
    payoff=net_payoff(cfg,d,q)
    required=cfg.get('min_reward_risk',1.5)+(0.3 if d.bucket=='scalp' else 0)
    if payoff['net_target_pct']<=0 or payoff['reward_risk']<required:
        reasons.append('비용 반영 목표 손익비 부족')
    recent=[t for t in state['trades'] if t['symbol']==d.symbol and t['side']=='sell' and
            timedelta(0)<=now-stamp(t['at'])<timedelta(minutes=cfg.get('reentry_cooldown_minutes',30))]
    if any((t.get('pnl') or 0)<0 for t in recent):reasons.append('손실 매도 후 재진입 대기')
    if d.bucket=='scalp':
        if d.setup not in ('opening_breakout','momentum_breakout','trend_pullback'):
            reasons.append('단타 진입 유형 미확인')
        if not intraday or intraday.get('bars',0)<20:reasons.append('장중 완료 봉 표본 부족')
        vwap=intraday.get('vwap'); volume=minute.get('volume_ratio',0)
        if not vwap or q['bid']<vwap:reasons.append('장중 거래량 가중 가격 아래')
        if volume<(1.5 if phase['phase']=='midday' else 1.2):reasons.append('시간대별 거래량 확인 부족')
        if minute.get('macd_hist',-1)<=0 or q['bid']<minute.get('sma20',float('inf')):
            reasons.append('완료 분봉 추세 확인 부족')
        if abs(intraday.get('gap_pct') or 0)>cfg.get('max_open_gap_pct',6):reasons.append('과도한 시초가 갭 추격 제한')
        if d.setup=='opening_breakout' and (not intraday.get('opening_range_high') or q['bid']<=intraday['opening_range_high']):
            reasons.append('완료 개장 15분 범위 돌파 미확인')
        atr=minute.get('atr14',0)
        if not atr or d.take_profit_pct>max(1,atr/q['ask']*100*8):reasons.append('단타 목표가가 장중 변동폭에 비해 과도함')
    else:
        if d.setup not in ('trend_pullback','trend_follow','long_accumulation'):
            reasons.append('중장기 진입 유형 미확인')
        if q['bid']<daily.get('sma60',float('inf')):reasons.append('일봉 중기 추세 아래')
        if d.bucket=='long' and (not daily.get('sma200') or q['bid']<daily['sma200']):
            reasons.append('장기 200일 추세 확인 부족')
        atr=daily.get('atr14',0)
        if not atr or q['ask']>daily.get('sma20',0)+atr*2:reasons.append('일봉 평균 대비 과도한 이격')
    return list(dict.fromkeys(reasons))


def performance(state):
    groups={}; sell=[t for t in state['trades'] if t['side']=='sell']
    for t in sell:
        key=t.get('entry_phase','legacy')+'/'+t['bucket']
        g=groups.setdefault(key,{'sell_fills':0,'net_pnl':0.,'wins':0,'gross_wins':0.,'gross_losses':0.})
        p=t.get('pnl') or 0;g['sell_fills']+=1;g['net_pnl']+=p;g['wins']+=int(p>0)
        g['gross_wins']+=max(0,p);g['gross_losses']+=max(0,-p)
    for g in groups.values():
        g['mean_net_pnl']=g['net_pnl']/g['sell_fills'];g['profit_factor']=g['gross_wins']/g['gross_losses'] if g['gross_losses'] else None
    for adjustment in state.get('fee_adjustments',[]):
        key=adjustment.get('entry_phase','legacy')+'/'+adjustment.get('bucket','swing')
        if key in groups:
            groups[key]['net_pnl']+=adjustment.get('realized_delta',0)
            groups[key]['mean_net_pnl']=groups[key]['net_pnl']/groups[key]['sell_fills']
            groups[key]['profit_factor']=None  # Late fee corrections need lot-level attribution for a meaningful ratio.
    return {'by_entry_phase_bucket':groups,'fees_paid':sum(t.get('fee',0) for t in state['trades'])+sum(a['fee_delta'] for a in state.get('fee_adjustments',[])),
            'note':'매도 부분 체결 단위 통계. AI 신뢰도는 검증된 승률이 아니며 미래 수익을 보장하지 않음.'}
