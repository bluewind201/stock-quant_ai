"""Does the live entry gate beat random entry? Four years, 50 symbols, real costs.

    python scripts/backtest_gate.py
    python scripts/backtest_gate.py --market KRX --hold 20 --resamples 500

What this measures
    The system that actually trades is `GPT proposes -> decision_quality.buy_reasons()
    decides`. GPT cannot be replayed over four years, but it only *proposes*. So we
    replace it with a generator that proposes a swing entry on every symbol every day
    and let the real gate do the selecting. Whatever survives is what the deployed
    code would have allowed.

    Arm A (gate)    entries where buy_reasons() returned no objection
    Arm B (random)  the same number of entries drawn uniformly at random
    Arm C (hold)    Arm A's entry days, but no stop/target - just hold and exit

    Arm B is the null hypothesis. A gate with no edge produces Arm A results inside
    Arm B's resampling band. Arm C separates "the gate picked good days" from
    "the stop/target structure did the work".

What this cannot measure
    - Scalp setups. Toss serves ~100 recent minute bars, so intraday history does
      not exist. Those stay forward-test-only.
    - News and macro gates. `news.py` reads current RSS; there is no point-in-time
      archive, so the backtest runs news-blind while live trading does not.
    - Whether GPT adds anything on top of the gate. Only forward paper answers that.
      Arm A is the bar GPT has to clear.

Known biases, stated rather than hidden
    - Survivorship: only currently-listed symbols are queryable, so delisted losers
      are absent and every arm is flattered.
    - The window (2022-08 ~ 2026-09) is mostly a bull market. Read Arm C first.
    - `reentry_cooldown_minutes` is an intraday rule; on daily bars it never binds.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import sys
from collections import Counter
from datetime import timedelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'core'))

from sqt.calendar import session                      # noqa: E402
from sqt.decision_quality import buy_reasons          # noqa: E402
from sqt.technical import analyze                     # noqa: E402

HISTORY = ROOT / 'backtest' / 'history'
WARMUP = 200          # SMA200 needs this much completed history before the first decision
FILLER = '백테스트 자동 생성 근거 문자열'   # the gate only checks that these are non-trivial


class Candidate:
    """Stands in for the GPT proposal. Same attribute surface the gate reads."""
    __slots__ = ('symbol', 'bucket', 'setup', 'take_profit_pct', 'stop_pct',
                 'thesis', 'counter_case', 'invalidation')

    def __init__(self, symbol, bucket, setup, take_profit_pct, stop_pct):
        self.symbol = symbol
        self.bucket = bucket
        self.setup = setup
        self.take_profit_pct = take_profit_pct
        self.stop_pct = stop_pct
        self.thesis = self.counter_case = self.invalidation = FILLER


def load(market: str) -> list[dict]:
    files = sorted(HISTORY.glob('*.json'))
    if not files:
        sys.exit('과거 데이터가 없습니다. 먼저 scripts/fetch_history.py 를 실행하세요.')
    out = []
    for path in files:
        data = json.loads(path.read_text(encoding='utf-8'))
        krx = data['exchange'] == 'KRX'
        if market == 'KRX' and not krx:
            continue
        if market == 'US' and krx:
            continue
        out.append(data)
    return out


def config(exchange: str, args) -> dict:
    """Live defaults from PaperConfig, plus the sell tax that applies to the market."""
    return {
        'decision_policy_v2': True,
        'fee_bps': args.fee_bps,
        'slippage_bps': args.slippage_bps,
        # 국내는 증권거래세가 매도에 붙는다. 미국 주식에는 없다.
        'sell_tax_bps': args.krx_sell_tax_bps if exchange == 'KRX' else 0.0,
        'min_reward_risk': args.min_reward_risk,
        'opening_wait_minutes': 5,
        'reentry_cooldown_minutes': 30,
        'max_open_gap_pct': 6,
    }


def decision_time(exchange: str, day: str):
    """Just past the opening-wait window, so session gates evaluate the way they do live."""
    try:
        opened, _ = session(exchange, day)
    except ValueError:
        return None
    return opened + timedelta(minutes=6)


def exit_trade(bars, start, entry_fill, stop_pct, tp_pct, max_hold, use_targets=True):
    """Walk daily bars forward. Gaps fill at the open, and a stop wins any same-bar tie."""
    stop = entry_fill * (1 - stop_pct / 100)
    target = entry_fill * (1 + tp_pct / 100)
    last = min(start + max_hold, len(bars) - 1)
    for i in range(start, last + 1):
        bar = bars[i]
        if use_targets:
            if i > start:
                # 갭은 어제의 손절가로 체결되지 않는다. 실제 시가로 체결된다.
                if bar['open'] <= stop:
                    return bar['open'], i - start, 'stop_gap'
                if bar['open'] >= target:
                    return bar['open'], i - start, 'target_gap'
            if bar['low'] <= stop:
                return stop, i - start, 'stop'
            if bar['high'] >= target:
                return target, i - start, 'target'
    return bars[last]['close'], last - start, 'timeout'


def run_trade(bars, index, cfg, stop_pct, tp_pct, max_hold, use_targets=True):
    ask = bars[index]['open']
    if ask <= 0:
        return None
    entry_fill = ask * (1 + cfg['slippage_bps'] / 10000)
    cost = entry_fill * (1 + cfg['fee_bps'] / 10000)
    price, held, reason = exit_trade(bars, index, entry_fill, stop_pct, tp_pct,
                                     max_hold, use_targets)
    proceeds = (price * (1 - cfg['slippage_bps'] / 10000)
                * (1 - (cfg['fee_bps'] + cfg['sell_tax_bps']) / 10000))
    return {'return_pct': (proceeds - cost) / cost * 100, 'held': held, 'reason': reason}


def evaluate(datasets, args):
    """One pass over history: every symbol, every day, one swing proposal, real gate."""
    passed, rejects, eligible = [], Counter(), []
    for data in datasets:
        symbol, exchange, bars = data['symbol'], data['exchange'], data['bars']
        cfg = config(exchange, args)
        state = {'config': cfg, 'quotes': {}, 'features': {}, 'trades': []}
        busy_until = -1
        for i in range(WARMUP, len(bars) - 1):
            history, bar = bars[:i], bars[i]
            if bar['open'] <= 0 or bar['volume'] <= 0:
                continue
            try:
                daily = analyze(history)
            except ValueError:
                continue
            atr_pct = daily['atr14'] / bar['open'] * 100 if bar['open'] else 0
            if atr_pct <= 0:
                continue
            eligible.append((symbol, exchange, i))
            if i <= busy_until:
                continue          # 같은 종목에 포지션이 열려 있는 동안은 새로 사지 않는다

            stop_pct = max(0.2, min(20.0, atr_pct * args.stop_atr))
            tp_pct = max(0.2, min(100.0, stop_pct * args.reward_multiple))
            spread = args.spread_bps
            state['quotes'][symbol] = {
                'exchange': exchange, 'bid': bar['open'] * (1 - spread / 20000),
                'ask': bar['open'] * (1 + spread / 20000), 'spread_bps': spread,
            }
            state['features'][symbol] = {'daily': daily, 'minute': {}, 'intraday': {}}
            now = decision_time(exchange, bar['date'])
            if now is None:
                continue
            candidate = Candidate(symbol, args.bucket, args.setup, tp_pct, stop_pct)
            reasons = buy_reasons(state, candidate, now)
            if reasons:
                for reason in reasons:
                    rejects[reason] += 1
                continue
            trade = run_trade(bars, i, cfg, stop_pct, tp_pct, args.hold)
            if trade is None:
                continue
            trade.update(symbol=symbol, exchange=exchange, index=i, date=bar['date'],
                         stop_pct=stop_pct, tp_pct=tp_pct,
                         technical_score=daily['technical_score'])
            passed.append(trade)
            busy_until = i + trade['held']
    return passed, rejects, eligible


def random_arm(datasets, eligible, count, args, seed):
    """Same trade count, same stop/target policy, entry days drawn at random."""
    rng = random.Random(seed)
    by_symbol = {d['symbol']: d for d in datasets}
    picks = rng.sample(eligible, min(count, len(eligible)))
    busy: dict[str, int] = {}
    results = []
    for symbol, exchange, i in picks:
        if i <= busy.get(symbol, -1):
            continue
        bars = by_symbol[symbol]['bars']
        cfg = config(exchange, args)
        try:
            daily = analyze(bars[:i])
        except ValueError:
            continue
        atr_pct = daily['atr14'] / bars[i]['open'] * 100 if bars[i]['open'] else 0
        if atr_pct <= 0:
            continue
        stop_pct = max(0.2, min(20.0, atr_pct * args.stop_atr))
        tp_pct = max(0.2, min(100.0, stop_pct * args.reward_multiple))
        trade = run_trade(bars, i, cfg, stop_pct, tp_pct, args.hold)
        if trade:
            busy[symbol] = i + trade['held']
            results.append(trade)
    return results


def hold_arm(datasets, passed, args):
    """Arm A's entry days with the stop and target switched off."""
    by_symbol = {d['symbol']: d for d in datasets}
    out = []
    for entry in passed:
        bars = by_symbol[entry['symbol']]['bars']
        cfg = config(entry['exchange'], args)
        trade = run_trade(bars, entry['index'], cfg, entry['stop_pct'], entry['tp_pct'],
                          args.hold, use_targets=False)
        if trade:
            out.append(trade)
    return out


def describe(name, trades):
    if not trades:
        return {'name': name, 'trades': 0}
    values = [t['return_pct'] for t in trades]
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    return {
        'name': name, 'trades': len(values), 'mean_pct': statistics.fmean(values),
        'median_pct': statistics.median(values),
        'win_rate': len(wins) / len(values) * 100,
        'total_pct': sum(values),
        'stdev': statistics.stdev(values) if len(values) > 1 else 0.0,
        'profit_factor': (sum(wins) / -sum(losses)) if losses and sum(losses) else None,
        'mean_hold': statistics.fmean([t['held'] for t in trades]),
    }


def line(row):
    if not row['trades']:
        return f"  {row['name']:14s}  거래 없음"
    pf = f"{row['profit_factor']:.2f}" if row['profit_factor'] else '—'
    return (f"  {row['name']:14s} {row['trades']:6d}건  평균 {row['mean_pct']:+7.3f}%  "
            f"중앙값 {row['median_pct']:+7.3f}%  승률 {row['win_rate']:5.1f}%  "
            f"PF {pf:>5s}  보유 {row['mean_hold']:4.1f}일")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--market', choices=['ALL', 'KRX', 'US'], default='ALL')
    p.add_argument('--hold', type=int, default=20, help='최대 보유 거래일')
    p.add_argument('--stop-atr', type=float, default=1.5, dest='stop_atr',
                   help='손절폭 = ATR(14) x 이 값')
    p.add_argument('--reward-multiple', type=float, default=2.0, dest='reward_multiple',
                   help='목표폭 = 손절폭 x 이 값 (비용 차감 전)')
    p.add_argument('--bucket', choices=['swing', 'long'], default='swing')
    p.add_argument('--setup', default='trend_follow')
    p.add_argument('--resamples', type=int, default=300, help='무작위 대조군 재추출 횟수')
    p.add_argument('--fee-bps', type=float, default=10, dest='fee_bps')
    p.add_argument('--slippage-bps', type=float, default=10, dest='slippage_bps')
    p.add_argument('--spread-bps', type=float, default=10, dest='spread_bps')
    p.add_argument('--krx-sell-tax-bps', type=float, default=15, dest='krx_sell_tax_bps',
                   help='국내 매도 증권거래세(bp). 미국은 0으로 둔다.')
    p.add_argument('--min-reward-risk', type=float, default=1.5, dest='min_reward_risk')
    p.add_argument('--json', help='결과를 JSON 으로 저장할 경로')
    args = p.parse_args()

    datasets = load(args.market)
    span = f"{datasets[0]['bars'][0]['date']} ~ {datasets[0]['bars'][-1]['date']}"
    print(f'대상 {len(datasets)}종목 · {span} · 최대보유 {args.hold}일 · '
          f'손절 ATRx{args.stop_atr} · 목표 {args.reward_multiple}R')
    print(f'비용: 수수료 {args.fee_bps}bp · 슬리피지 {args.slippage_bps}bp · '
          f'스프레드 {args.spread_bps}bp · 국내 거래세 {args.krx_sell_tax_bps}bp')
    print('=' * 96)

    passed, rejects, eligible = evaluate(datasets, args)
    checked = sum(rejects.values()) + len(passed)
    print(f'검토한 매수 후보 {len(eligible):,}일 중 게이트 평가 {checked:,}건 → '
          f'통과 {len(passed):,}건 ({len(passed)/max(checked,1)*100:.2f}%)')
    print()
    print('게이트가 막은 사유 (한 후보가 여러 사유에 걸릴 수 있음):')
    for reason, n in rejects.most_common(10):
        print(f'  {n:7,d}회  {reason}')
    print()

    if not passed:
        print('게이트를 통과한 진입이 없습니다. 비용 대비 목표 손익비 조건이 지배적입니다.')
        return 0

    gate = describe('게이트', passed)
    hold = describe('보유만(A일자)', hold_arm(datasets, passed, args))

    means, wins = [], []
    for seed in range(args.resamples):
        row = describe('random', random_arm(datasets, eligible, len(passed), args, seed))
        if row['trades']:
            means.append(row['mean_pct'])
            wins.append(row['win_rate'])
    means.sort()
    wins.sort()

    def band(values):
        lo = values[int(len(values) * 0.025)]
        hi = values[int(len(values) * 0.975)]
        return statistics.fmean(values), lo, hi

    rmean, rlo, rhi = band(means)
    wmean, wlo, whi = band(wins)

    print('결과')
    print(line(gate))
    print(line(hold))
    print(f"  {'무작위 진입':14s} {args.resamples}회 재추출  "
          f"평균 {rmean:+7.3f}%  (95% 구간 {rlo:+.3f}% ~ {rhi:+.3f}%)  "
          f"승률 {wmean:5.1f}% ({wlo:.1f}~{whi:.1f})")
    print()

    edge = gate['mean_pct'] - rmean
    inside = rlo <= gate['mean_pct'] <= rhi
    above = sum(1 for m in means if m >= gate['mean_pct'])
    pvalue = above / len(means) if means else 1.0
    print(f'게이트 − 무작위 = {edge:+.3f}%p   무작위 분포 상위 {pvalue*100:.1f}% 위치')
    if inside:
        print('판정: 무작위 진입의 95% 구간 안에 있습니다. '
              '이 데이터에서 게이트의 우위는 확인되지 않았습니다.')
    elif gate['mean_pct'] > rhi:
        print('판정: 무작위 구간 위입니다. 다만 생존 편향과 강세장 구간을 감안해야 하고, '
              '표본 밖 검증(다른 기간·종목)을 거치기 전에는 확정할 수 없습니다.')
    else:
        print('판정: 무작위 구간 아래입니다. 게이트가 오히려 나쁜 날을 고르고 있습니다.')

    if args.json:
        path = pathlib.Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'window': span, 'symbols': len(datasets), 'args': vars(args),
            'gate': gate, 'hold': hold,
            'random': {'mean': rmean, 'lo': rlo, 'hi': rhi,
                       'win_mean': wmean, 'win_lo': wlo, 'win_hi': whi},
            'edge_pp': edge, 'percentile': pvalue,
            'rejects': rejects.most_common(20),
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'\n저장: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
