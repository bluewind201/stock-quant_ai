"""Read-only performance, holding-horizon, and KR/US market reporting; no price or order calls."""
from datetime import timezone, timedelta
import math
from .models import stamp

KST = timezone(timedelta(hours=9))
PERIODS = {'short': '단기', 'medium': '중기', 'long': '장기', 'legacy': '기존 당일/미분류'}
MARKETS = {'KR': '국내(KRX)', 'US': '해외(미국)'}


def _is_us(symbol: str) -> bool:
    s = str(symbol or '').strip()
    return bool(s) and not (len(s) == 6 and s[0].isdigit())


def _mkt(row: dict) -> str:
    m = row.get('market')
    if m in ('KR', 'US'):
        return m
    return 'US' if _is_us(row.get('symbol', '')) else 'KR'


def horizon(row):
    h = row.get('horizon', 'legacy') if row.get('strategy') == 'portfolio' else 'legacy'
    return h if h in PERIODS else 'legacy'


def exit_kind(reason):
    for text, kind in [
        ('분할 익절', 'target'), ('수동', 'manual'), ('보호 가격', 'stop'), ('손절', 'stop'),
        ('추세 이탈', 'trend'), ('AI 투자', 'ai'), ('기간 종료', 'expiry'),
        ('마감 전', 'day_end'), ('오전장 정리', 'day_end'), ('익절', 'target'),
    ]:
        if text in reason:
            return kind
    return 'other'


def summary(state):
    fx = float(state.get('fx_usd_krw') or (state.get('fx_cache') or {}).get('rate') or 1385.0)
    quotes = state.get('quotes') or {}
    buckets = {
        h: {
            'label': label, 'holdings': [], 'invested_krw': 0.0, 'unrealized': 0.0,
            'realized': 0.0, 'today_realized': 0.0, 'today_buys': 0, 'today_sells': 0,
            'closed_count': 0, 'win_rate': None,
        }
        for h, label in PERIODS.items()
    }
    market_buckets = {
        m: {
            'label': label, 'holdings': 0, 'invested_krw': 0.0, 'unrealized': 0.0,
            'realized': 0.0, 'buys': 0, 'sells': 0,
        }
        for m, label in MARKETS.items()
    }

    for p in state.get('positions', {}).values():
        if p['qty'] <= 0:
            continue
        h = horizon(p)
        m = _mkt(p)
        q = quotes.get(p['symbol'])
        is_usd = p.get('currency', 'KRW') == 'USD' or m == 'US'
        entry_px = float(p.get('entry_price') or 0.0)
        cur_bid = q['bid'] if q and q.get('bid') else entry_px
        val_krw = p['qty'] * cur_bid * (fx if is_usd else 1.0)
        unr = (val_krw - p.get('cost', val_krw)) if is_usd else ((cur_bid - entry_px) * p['qty'] if (q and entry_px) else 0.0)
        buckets[h]['holdings'].append({
            'symbol': p['symbol'], 'name': p.get('name') or p['symbol'],
            'qty': p['qty'], 'market': m, 'currency': 'USD' if is_usd else 'KRW',
            'invested_krw': round(val_krw), 'unrealized': round(unr),
        })
        buckets[h]['invested_krw'] += val_krw
        buckets[h]['unrealized'] += unr
        market_buckets[m]['holdings'] += 1
        market_buckets[m]['invested_krw'] += val_krw
        market_buckets[m]['unrealized'] += unr

    exits = []
    settled = {}
    settled_horizon = {}
    for t in state.get('trades', []):
        h = horizon(t)
        m = _mkt(t)
        b = buckets[h]
        today = stamp(t['at']).astimezone(KST).date().isoformat() == state.get('day')
        if t['side'] == 'buy':
            market_buckets[m]['buys'] += 1
            if today:
                b['today_buys'] += 1
            continue
        market_buckets[m]['sells'] += 1
        if today:
            b['today_sells'] += 1
        pnl = t.get('pnl', 0)
        b['realized'] += pnl
        market_buckets[m]['realized'] += pnl
        if today:
            b['today_realized'] += pnl
        entry = t.get('entry_at')
        same_day = bool(entry and stamp(entry).astimezone(KST).date() == stamp(t['at']).astimezone(KST).date())
        symbol = t.get('symbol', '미기록')
        exits.append({
            'symbol': symbol, 'name': t.get('name') or symbol, 'market': m,
            'horizon': h, 'at': t['at'], 'pnl': pnl, 'partial': bool(t.get('partial')),
            'same_day': same_day, 'reason': t.get('reason', ''), 'kind': exit_kind(t.get('reason', '')),
        })
        if entry:
            key = (symbol, entry)
            settled[key] = settled.get(key, 0) + pnl
            settled_horizon[key] = h

    closed = []
    horizon_closed = {h: [] for h in PERIODS}
    for (symbol, entry), pnl in settled.items():
        pos = state.get('positions', {}).get(symbol, {})
        if pos.get('entry_at') == entry and pos.get('qty', 0) > 0:
            continue
        closed.append(pnl)
        horizon_closed[settled_horizon.get((symbol, entry), 'legacy')].append(pnl)

    for h, vals in horizon_closed.items():
        buckets[h]['closed_count'] = len(vals)
        buckets[h]['win_rate'] = sum(v > 0 for v in vals) / len(vals) * 100 if vals else None
        buckets[h]['invested_krw'] = round(buckets[h]['invested_krw'])
        buckets[h]['unrealized'] = round(buckets[h]['unrealized'])

    for m in market_buckets:
        market_buckets[m]['invested_krw'] = round(market_buckets[m]['invested_krw'])
        market_buckets[m]['unrealized'] = round(market_buckets[m]['unrealized'])
        market_buckets[m]['realized'] = round(market_buckets[m]['realized'])

    peak = None
    drawdown = []
    for p in state.get('equity_curve', []):
        value = p.get('equity', 0)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            continue
        peak = max(peak or value, value)
        if peak <= 0:
            continue
        drawdown.append({'at': p['at'], 'pct': (value / peak - 1) * 100})

    return {
        'buckets': buckets,
        'markets': market_buckets,
        'exits': exits[-30:][::-1],
        'closed_count': len(closed),
        'win_rate': sum(v > 0 for v in closed) / len(closed) * 100 if closed else None,
        'max_drawdown_pct': min((p['pct'] for p in drawdown), default=None),
        'drawdown': drawdown,
        'strategy': state.get('config', {}).get('strategy', 'ai'),
    }
