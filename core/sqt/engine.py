"""Daily OHLC simulation: previous close decision, next open execution, no live orders."""
import math
from datetime import timedelta

from .calendar import session
from .context import evaluate, relevant
from .models import Strategy, stamp
from .technical import analyze


def create_state(dataset, config, start_index=61):
    cfg = Strategy.model_validate(config).model_dump()
    if not 61 <= start_index < len(dataset['bars']):
        raise ValueError('시작 지점은 최소 61개 준비 일봉 이후여야 합니다.')
    return {'config': cfg, 'cursor': start_index, 'start_index': start_index,
            'cash': cfg['initial_cash'], 'position': None, 'trades': [], 'decisions': [],
            'equity': [], 'auto': False, 'entries_enabled': True,
            'done': False, 'mode': 'historical_paper', 'currency': dataset['instrument']['currency']}


def step(state, dataset, evidence, events):
    s = state  # caller owns the state; persistence supplies transaction isolation
    if s['cursor'] >= len(dataset['bars']):
        s['done'], s['auto'] = True, False
        return s
    cfg, i = s['config'], s['cursor']
    bars, instrument = dataset['bars'], dataset['instrument']
    bar, history = bars[i], bars[:i]
    opened, closed = session(instrument['exchange'], bar['date'])
    _, previous_close = session(instrument['exchange'], history[-1]['date'])
    technical = analyze(history)
    # Price direction is determined with completed previous-day data only.
    ctx = evaluate(evidence, events, instrument['symbol'], previous_close.isoformat(), cfg)
    entry_ctx = evaluate(evidence, events, instrument['symbol'], opened.isoformat(), cfg)
    # A daily-bar strategy cannot pause midway through a session. Avoid a new position
    # for the whole session if a known high-impact release falls within its risk window.
    for event in events:
        when = stamp(event['scheduled_at'])
        if (event['impact'] == 'high' and relevant(event,instrument['symbol'])
                and stamp(event['known_at']) <= opened
                and when-timedelta(minutes=cfg['event_before_minutes']) <= closed
                and when+timedelta(minutes=cfg['event_after_minutes']) >= opened):
            entry_ctx['blocked'] = True
            entry_ctx['event_ids'].append(event['id'])
            entry_ctx['reasons'].append('중요 발표가 있는 거래일 신규 진입 제한: '+event['title'])
    score = max(0, min(100, technical['technical_score'] + ctx['news_score'] + ctx['macro_score']))
    reasons = list(ctx['reasons'])
    if entry_ctx['blocked']:
        reasons.extend(entry_ctx['reasons'])
    position = s['position']
    starting_equity = s['cash'] + (position['qty'] * history[-1]['close'] if position else 0)
    closed_today = False

    def sell(reference, why):
        nonlocal closed_today
        p = s['position']
        price = reference * (1 - cfg['slippage_bps'] / 10000)
        gross = price * p['qty']
        costs = gross * (cfg['fee_bps'] + cfg['sell_tax_bps']) / 10000
        net = gross - costs
        s['cash'] += net
        s['trades'].append(dict(p, exit_date=bar['date'], exit_price=price,
                                exit_cost=costs, pnl=net-p['cost'], reason=why))
        s['position'] = None
        closed_today = True

    if position and bar['volume'] > 0:
        # Gap risk: a stop cannot fill at yesterday's stop price if today's open gaps below it.
        if bar['open'] <= position['stop']:
            sell(bar['open'], '갭 하락 손절')
        elif bar['open'] >= position['target']:
            sell(bar['open'], '갭 상승 익절')
        elif technical['technical_score'] < 25 or ctx['news_score'] + ctx['macro_score'] <= -8:
            sell(bar['open'], '전일 추세·뉴스 악화')
    gap = abs(bar['open'] / history[-1]['close'] - 1) * 100
    allowed = (s['entries_enabled'] and not ctx['blocked'] and not entry_ctx['blocked']
               and score >= cfg['threshold'] and technical['technical_score'] >= cfg['min_technical']
               and gap <= cfg['max_gap_pct'] and bar['volume'] > 0 and not closed_today)
    # Volume of the execution day is unknown at open: use it ONLY to reject a zero-volume
    # untradeable day, never to score or size an entry. Previous-day volume scores the signal.
    action = '보유' if s['position'] else '대기'
    if gap > cfg['max_gap_pct']:
        reasons.append('시초가 갭 한도 초과')
    if not s['entries_enabled']:
        reasons.append('신규 진입 정지')
    if technical['technical_score'] < cfg['min_technical']:
        reasons.append('기술 점수 하한 미달')
    if score < cfg['threshold']:
        reasons.append('총점 기준 미달')
    if s['position'] is None and allowed:
        price = bar['open'] * (1 + cfg['slippage_bps'] / 10000)
        stop_distance = max(technical['atr14'] * cfg['stop_atr'], price * 0.005)
        unit_cost = price * (1 + cfg['fee_bps'] / 10000)
        qty = math.floor(min(s['cash'] / unit_cost,
                             starting_equity * cfg['position_pct'] / 100 / unit_cost,
                             starting_equity * cfg['risk_pct'] / 100 / stop_distance))
        if qty > 0 and stop_distance < price:
            cost = qty * unit_cost
            s['cash'] -= cost
            s['position'] = {'qty': qty, 'entry_date': bar['date'], 'entry_price': price,
                             'cost': cost, 'stop': price-stop_distance,
                             'target': price+stop_distance*cfg['reward_risk']}
            action = '매수'
        else:
            reasons.append('정수 1주 이상 매수할 자금·리스크 한도 부족')
    position = s['position']
    if position and bar['volume'] > 0:
        # If both levels are touched, daily bars cannot establish the order. Take the loss first.
        if bar['low'] <= position['stop']:
            sell(min(bar['open'], position['stop']), 'ATR 손절 (동일 일봉 양방향 도달 시 손절 우선)')
        elif bar['high'] >= position['target']:
            sell(position['target'], 'ATR 익절')
    equity = s['cash'] + (s['position']['qty'] * bar['close'] if s['position'] else 0)
    if equity < starting_equity * (1 - cfg['daily_loss_pct'] / 100):
        s['entries_enabled'] = False
        reasons.append('하루 평가손실 한도 초과: 이후 신규 진입 정지')
    if closed_today:
        action = '매수·청산' if action == '매수' else '청산'
    decision = {'date': bar['date'], 'decision_at': previous_close.isoformat(),
                'execution_at': opened.isoformat(), 'action': action, 'score': score,
                'technical': technical, 'context': ctx, 'entry_context': entry_ctx,
                'reasons': list(dict.fromkeys(reasons))}
    s['decisions'].append(decision)
    s['equity'].append({'date': bar['date'], 'value': equity})
    s['cursor'] += 1
    s['done'] = s['cursor'] >= len(bars)
    if s['done']:
        s['auto'] = False
    return s


def metrics(state, dataset):
    cfg = state['config']
    current = state['equity'][-1]['value'] if state['equity'] else cfg['initial_cash']
    peak, max_dd = cfg['initial_cash'], 0
    for row in state['equity']:
        peak = max(peak, row['value'])
        max_dd = max(max_dd, (peak-row['value'])/peak*100)
    trades = state['trades']
    wins = sum(t['pnl'] > 0 for t in trades)
    benchmark = 0
    if state['equity']:
        first = dataset['bars'][state['start_index']]['open'] * (1+cfg['slippage_bps']/10000)
        qty = math.floor(cfg['initial_cash'] / (first*(1+cfg['fee_bps']/10000)))
        cash = cfg['initial_cash'] - qty * first * (1+cfg['fee_bps']/10000)
        mark = dataset['bars'][state['cursor']-1]['close']
        benchmark = ((cash+qty*mark)/cfg['initial_cash']-1)*100
    return {'equity': current, 'return_pct': (current/cfg['initial_cash']-1)*100,
            'max_drawdown_pct': max_dd, 'closed_trades': len(trades),
            'win_rate_pct': wins/len(trades)*100 if trades else None,
            'realized_pnl': sum(t['pnl'] for t in trades), 'buy_hold_pct': benchmark,
            'note': '미청산 보유분은 마지막 종가 평가. 양쪽 모두 미청산 매도비용 미차감. 배당·환전 미반영.'}


def backtest(dataset, config, evidence, events, holdout=False):
    start = max(61, int(len(dataset['bars']) * 0.7)) if holdout else 61
    state = create_state(dataset, config, start)
    while not state['done']:
        state = step(state, dataset, evidence, events)
    return {'metrics': metrics(state, dataset), 'equity': state['equity'],
            'trades': state['trades'], 'decisions': state['decisions'],
            'evaluation': '시간순 마지막 30% (자동 최적화 없음)' if holdout else '전체 준비 기간 이후',
            'config': config, 'demo': dataset['demo']}
