"""테마 기반 종목 선택이 무작위 선택보다 나은가? 4년, 국내 테마 구성종목.

    python scripts/fetch_history.py --from-themes 20     # 먼저 수집
    python scripts/backtest_themes.py

무엇을 재는가
    [1] 테마는 실제 그룹인가 - 같은 테마 종목쌍의 수익률 상관 vs 무작위 종목쌍
    [2] 테마 모멘텀이 통하는가 - 직전 N일 수익률 1위 테마를 사는 것 vs 무작위 12종
    [3] 집중의 대가 - 한 테마에 몰아넣은 포트폴리오의 변동성·최대낙폭

사후편향을 어떻게 다루는가 - 이게 이 스크립트의 핵심 설계다
    네이버 테마 구성은 **오늘 기준**이다. "2차전지(전고체)" 가 테마로 묶인 건 그 종목들이
    실제로 움직인 뒤의 일이다. 이 명단을 4년 전에 적용하면 미래를 아는 셈이 된다.

    그래서 **모든 실험군이 같은 종목 풀에서 뽑는다.** 무작위 대조군도 같은 327종에서
    고른다. 그러면 "이 명단에 오를 만한 종목이었다" 는 편향이 양쪽에 똑같이 걸리고,
    남는 차이는 **테마로 묶어 고른 것의 효과**뿐이다.

    다만 [2]의 절대 수익률은 여전히 부풀려져 있다. 비교값만 읽을 것.

무엇을 못 재는가
    과거 시점의 테마 순위. 네이버는 현재 순위만 준다. 그래서 [2]는 "오늘의 명단으로
    과거 수익률을 매겨 1위 테마를 산다" 는 근사이며, 실제 운용과 완전히 같지 않다.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import random
import statistics
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parent.parent
HISTORY = ROOT / 'backtest' / 'history'
THEMES = ROOT / 'backtest' / 'themes.json'


def load():
    if not THEMES.exists():
        sys.exit('backtest/themes.json 이 없습니다. '
                 'python scripts/fetch_history.py --from-themes 20 을 먼저 실행하세요.')
    meta = json.loads(THEMES.read_text(encoding='utf-8'))
    bars: dict[str, dict[str, float]] = {}
    for path in sorted(HISTORY.glob('KRX_*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        rows = {b['date']: b['close'] for b in data['bars'] if b.get('close')}
        if len(rows) >= 500:
            bars[data['symbol']] = rows
    return meta, bars


def build_matrix(bars):
    """모든 종목이 값을 가진 거래일만 남긴 (날짜목록, {종목: [종가]}) 행렬."""
    counts = defaultdict(int)
    for rows in bars.values():
        for day in rows:
            counts[day] += 1
    need = len(bars)
    days = sorted(day for day, c in counts.items() if c == need)
    if len(days) < 300:
        # 전 종목 공통일이 너무 적으면 90% 이상 채워진 날로 완화한다.
        days = sorted(day for day, c in counts.items() if c >= need * 0.9)
        bars = {s: r for s, r in bars.items() if sum(d in r for d in days) == len(days)}
    series = {s: [rows[d] for d in days] for s, rows in bars.items()
              if all(d in rows for d in days)}
    return days, series


def returns(series):
    return {s: [(p[i] / p[i - 1] - 1) for i in range(1, len(p))] for s, p in series.items()}


def correlation(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / (va ** 0.5 * vb ** 0.5)


def test_cohesion(meta, rets, rng, samples=4000):
    """같은 테마 종목쌍의 상관이 무작위 종목쌍보다 높은가. 예측이 아니라 서술이다."""
    symbols = [s for s in rets]
    inside: list[float] = []
    for theme in meta['themes']:
        members = [s for s in theme['symbols'] if s in rets]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                c = correlation(rets[members[i]], rets[members[j]])
                if c is not None:
                    inside.append(c)
    outside: list[float] = []
    membership = meta['membership']
    for _ in range(samples):
        a, b = rng.sample(symbols, 2)
        if set(membership.get(a, [])) & set(membership.get(b, [])):
            continue          # 같은 테마면 대조군에서 뺀다
        c = correlation(rets[a], rets[b])
        if c is not None:
            outside.append(c)
    return inside, outside


def hold_return(series, start, days, symbols, cfg):
    """동일 비중 매수 후 days 거래일 보유. 비용은 매수·매도 각각 반영."""
    end = min(start + days, len(next(iter(series.values()))) - 1)
    if end <= start:
        return None
    entry_cost = (1 + cfg['slippage'] / 10000) * (1 + cfg['fee'] / 10000)
    exit_factor = (1 - cfg['slippage'] / 10000) * (1 - (cfg['fee'] + cfg['tax']) / 10000)
    each = []
    for s in symbols:
        p = series[s]
        if p[start] <= 0:
            continue
        each.append((p[end] * exit_factor) / (p[start] * entry_cost) - 1)
    return statistics.fmean(each) * 100 if each else None


def test_momentum(meta, series, days, cfg, args, rng):
    """직전 lookback 일 수익률 1위 테마를 사는 것 vs 같은 풀에서 무작위 12종."""
    length = len(days)
    pool = list(series)
    themes = [(t['name'], [s for s in t['symbols'] if s in series]) for t in meta['themes']]
    themes = [(n, m) for n, m in themes if len(m) >= 3]

    picks: list[float] = []
    randoms: list[list[float]] = [[] for _ in range(args.resamples)]
    universe: list[float] = []
    momentum: list[float] = []          # 테마를 무시하고 개별 종목 모멘텀 상위만
    chosen: list[str] = []

    step = args.hold
    for start in range(args.lookback, length - step, step):
        ranked = []
        for name, members in themes:
            past = [series[s][start] / series[s][start - args.lookback] - 1
                    for s in members if series[s][start - args.lookback] > 0]
            if past:
                ranked.append((statistics.fmean(past), name, members))
        if not ranked:
            continue
        ranked.sort(reverse=True)
        _, name, members = ranked[0]
        take = members[:args.size]
        value = hold_return(series, start, step, take, cfg)
        if value is None:
            continue
        picks.append(value)
        chosen.append(name)

        whole = hold_return(series, start, step, pool, cfg)
        if whole is not None:
            universe.append(whole)

        # 핵심 대조군: 테마를 아예 무시하고 같은 풀에서 '직전 수익률 상위 종목' 만 산다.
        # 테마 팔이 이 팔을 못 이기면, 효과의 정체는 테마가 아니라 그냥 모멘텀이다.
        scored = sorted(((series[s][start] / series[s][start - args.lookback] - 1, s)
                         for s in pool if series[s][start - args.lookback] > 0), reverse=True)
        value = hold_return(series, start, step, [s for _, s in scored[:args.size]], cfg)
        if value is not None:
            momentum.append(value)
        for i in range(args.resamples):
            sample = rng.sample(pool, min(args.size, len(pool)))
            value = hold_return(series, start, step, sample, cfg)
            if value is not None:
                randoms[i].append(value)
    return picks, randoms, universe, momentum, chosen


def describe(name, values):
    if not values:
        return f'  {name:22s} 표본 없음'
    total = 1.0
    for v in values:
        total *= (1 + v / 100)
    return (f'  {name:22s} {len(values):3d}회  회당평균 {statistics.fmean(values):+7.3f}%  '
            f'중앙값 {statistics.median(values):+7.3f}%  '
            f'표준편차 {statistics.pstdev(values):6.3f}  누적 {(total-1)*100:+8.1f}%')


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--hold', type=int, default=20, help='보유 거래일 (리밸런싱 주기)')
    p.add_argument('--lookback', type=int, default=20, help='테마 순위를 매길 과거 구간')
    p.add_argument('--size', type=int, default=12, help='포트폴리오 종목 수 (계좌 상한과 동일)')
    p.add_argument('--resamples', type=int, default=200)
    p.add_argument('--fee-bps', type=float, default=10, dest='fee')
    p.add_argument('--slippage-bps', type=float, default=10, dest='slippage')
    p.add_argument('--sell-tax-bps', type=float, default=15, dest='tax')
    p.add_argument('--seed', type=int, default=7)
    args = p.parse_args()
    cfg = {'fee': args.fee, 'slippage': args.slippage, 'tax': args.tax}
    rng = random.Random(args.seed)

    meta, bars = load()
    days, series = build_matrix(bars)
    rets = returns(series)
    print(f'종목 {len(series)}개 · 공통 거래일 {len(days)}일 '
          f'({days[0]} ~ {days[-1]}) · 테마 {len(meta["themes"])}개')
    print(f'비용: 수수료 {args.fee}bp · 슬리피지 {args.slippage}bp · 매도세 {args.tax}bp')
    print('=' * 96)

    print('\n[1] 테마는 실제로 같이 움직이는 그룹인가  (서술 통계 · 사후편향 무관)')
    inside, outside = test_cohesion(meta, rets, rng)
    print(f'  같은 테마 종목쌍 {len(inside):6,d}쌍  평균 상관 {statistics.fmean(inside):+.3f}  '
          f'중앙값 {statistics.median(inside):+.3f}')
    print(f'  다른 테마 종목쌍 {len(outside):6,d}쌍  평균 상관 {statistics.fmean(outside):+.3f}  '
          f'중앙값 {statistics.median(outside):+.3f}')
    gap = statistics.fmean(inside) - statistics.fmean(outside)
    print(f'  차이 {gap:+.3f}  ->  ' + (
        '테마는 실제 그룹입니다. 한 테마에 몰면 분산이 거의 되지 않습니다.' if gap > 0.05
        else '테마 안팎의 동조화 차이가 거의 없습니다. 묶음으로서 의미가 약합니다.'))

    print(f'\n[2] 테마 모멘텀 vs 무작위  (같은 {len(series)}종 풀에서 양쪽 다 추출)')
    picks, randoms, universe, momentum, chosen = test_momentum(meta, series, days, cfg, args, rng)
    print(describe('1위 테마', picks))
    print(describe('종목 모멘텀 상위', momentum))
    print(describe('전 종목 동일비중', universe))
    means = sorted(statistics.fmean(r) for r in randoms if r)
    if means and picks:
        lo, hi = means[int(len(means) * .025)], means[int(len(means) * .975)]
        rmean = statistics.fmean(means)
        pmean = statistics.fmean(picks)
        above = sum(1 for m in means if m >= pmean) / len(means)
        print(f'  {"무작위 " + str(args.size) + "종":22s} {args.resamples}회 재추출  '
              f'회당평균 {rmean:+7.3f}%  (95% 구간 {lo:+.3f}% ~ {hi:+.3f}%)')
        print()
        print(f'  테마 − 무작위 = {pmean - rmean:+.3f}%p   무작위 분포 상위 {above*100:.1f}% 위치')

        # 위 재추출 구간은 '같은 24회차 안에서 종목만 바꿔 뽑은' 분포다. 시기 자체의
        # 불확실성은 반영하지 않는다. 회차 표본으로 다시 계산해야 과신하지 않는다.
        se = statistics.pstdev(picks) / (len(picks) ** 0.5)
        print(f'  회차 표본 기준: n={len(picks)}  표준오차 {se:.3f}%p  '
              f'95% 신뢰구간 {pmean - 1.96 * se:+.2f}% ~ {pmean + 1.96 * se:+.2f}%')
        if pmean - 1.96 * se <= rmean:
            print('  -> 회차 수를 감안하면 무작위 평균이 신뢰구간 안에 들어옵니다. '
                  '통계적으로 구분되지 않습니다.')
        if momentum:
            mmean = statistics.fmean(momentum)
            print(f'  테마 − 종목모멘텀 = {pmean - mmean:+.3f}%p  ' + (
                '-> 테마로 묶은 것이 개별 모멘텀보다 낫지 않습니다. 효과의 정체는 모멘텀입니다.'
                if pmean <= mmean else '-> 테마 쪽이 개별 모멘텀보다 앞섭니다.'))
        if lo <= pmean <= hi:
            print('  판정: 무작위 구간 안입니다. 테마로 고른 우위는 확인되지 않았습니다.')
        elif pmean > hi:
            print('  판정: 무작위 구간 위입니다. 다만 테마 명단 자체의 사후편향이 남아 있어 '
                  '표본 밖 검증 전에는 확정할 수 없습니다.')
        else:
            print('  판정: 무작위 구간 아래입니다. 테마 추종이 오히려 해가 됐습니다.')

        print(f'\n[3] 집중의 대가')
        rand_sd = statistics.fmean(statistics.pstdev(r) for r in randoms if len(r) > 1)
        print(f'  1위 테마 포트폴리오 회차 표준편차 {statistics.pstdev(picks):6.3f}')
        print(f'  무작위 {args.size}종 평균 표준편차       {rand_sd:6.3f}')
        print(f'  최악 회차 : 테마 {min(picks):+.2f}%  vs  무작위 평균 '
              f'{statistics.fmean(min(r) for r in randoms if r):+.2f}%')
        if statistics.pstdev(picks) > rand_sd:
            print(f'  -> 테마 집중이 변동을 {statistics.pstdev(picks)/rand_sd:.2f}배로 키웁니다.')

    top = sorted({n: chosen.count(n) for n in set(chosen)}.items(), key=lambda x: -x[1])[:6]
    print('\n  가장 자주 1위였던 테마: ' + ', '.join(f'{n}({c}회)' for n, c in top))
    return 0


if __name__ == '__main__':
    sys.exit(main())
