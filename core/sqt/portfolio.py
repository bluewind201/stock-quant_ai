"""Long-only, daily-bar adaptations for Global Dual-Market (KR + US) multi-horizon trading."""
import copy
import hashlib
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from .models import Instrument, stamp
from .calendar import calendar, exchange_sessions, krx_sessions, status as exchange_status
from . import scanner, learning, notify

KST = timezone(timedelta(hours=9))
ET = ZoneInfo('America/New_York')
LABELS = {'short': '단기 · 수일~수주', 'medium': '중기 · 수주~수개월', 'long': '장기 · 6개월 이상'}
CAPS = {
    'balanced': {'short': 20, 'medium': 30, 'long': 30},
    'growth': {'short': 30, 'medium': 35, 'long': 25},
    'all_weather': {'short': 15, 'medium': 35, 'long': 40},
    'short': {'short': 80, 'medium': 0, 'long': 0},
    'medium': {'short': 0, 'medium': 80, 'long': 0},
    'long': {'short': 0, 'medium': 0, 'long': 80},
}
MARKET_REFERENCE = '069500'  # KODEX 200: 국내 시장이 열리면 항상 거래된다
US_MARKET_REFERENCE = 'SPY'  # S&P 500 ETF: 미국 시장이 열리면 항상 거래된다
MAX_UNKNOWN_CLOSURES = 5
DEFAULT_USD_KRW = 1385.0

US_EXCHANGES = {
    # NASDAQ leaders & ETFs
    'NVDA': 'NASDAQ', 'MSFT': 'NASDAQ', 'AAPL': 'NASDAQ', 'AMZN': 'NASDAQ', 'GOOGL': 'NASDAQ',
    'GOOG': 'NASDAQ', 'META': 'NASDAQ', 'AVGO': 'NASDAQ', 'TSLA': 'NASDAQ', 'COST': 'NASDAQ',
    'NFLX': 'NASDAQ', 'AMD': 'NASDAQ', 'PLTR': 'NASDAQ', 'QQQ': 'NASDAQ', 'SOXX': 'NASDAQ',
    'MU': 'NASDAQ', 'PANW': 'NASDAQ', 'ISRG': 'NASDAQ', 'ADBE': 'NASDAQ', 'INTU': 'NASDAQ',
    'QCOM': 'NASDAQ', 'AMAT': 'NASDAQ', 'KLAC': 'NASDAQ', 'LRCX': 'NASDAQ', 'CRWD': 'NASDAQ',
    'ASML': 'NASDAQ', 'ARM': 'NASDAQ', 'LIN': 'NASDAQ', 'PEP': 'NASDAQ', 'CSCO': 'NASDAQ',
    'TMUS': 'NASDAQ', 'SNPS': 'NASDAQ', 'CDNS': 'NASDAQ', 'MRVL': 'NASDAQ', 'SMH': 'NASDAQ',
    'TLT': 'NASDAQ', 'IBIT': 'NASDAQ',
    # NYSE blue-chips
    'BRK.B': 'NYSE', 'JPM': 'NYSE', 'LLY': 'NYSE', 'V': 'NYSE', 'UNH': 'NYSE',
    'MA': 'NYSE', 'XOM': 'NYSE', 'WMT': 'NYSE', 'PG': 'NYSE', 'JNJ': 'NYSE',
    'HD': 'NYSE', 'ORCL': 'NYSE', 'CAT': 'NYSE', 'GE': 'NYSE', 'GS': 'NYSE',
    'CRM': 'NYSE', 'UBER': 'NYSE', 'NOW': 'NYSE', 'TSM': 'NYSE', 'ANET': 'NYSE',
    'KO': 'NYSE', 'BAC': 'NYSE', 'CVX': 'NYSE', 'ABBV': 'NYSE', 'MRK': 'NYSE',
    # AMEX / Arca ETFs
    'SPY': 'AMEX', 'IWM': 'AMEX', 'DIA': 'AMEX', 'XLK': 'AMEX', 'XLF': 'AMEX',
    'XLV': 'AMEX', 'XLE': 'AMEX', 'XLI': 'AMEX', 'GLD': 'AMEX', 'EWY': 'AMEX',
}

US_NAMES = {
    'NVDA': '엔비디아', 'MSFT': '마이크로소프트', 'AAPL': '애플', 'AMZN': '아마존',
    'GOOGL': '알파벳A', 'META': '메타', 'AVGO': '브로드컴', 'TSLA': '테슬라',
    'COST': '코스트코', 'NFLX': '넷플릭스', 'AMD': 'AMD', 'PLTR': '팔란티어',
    'QQQ': '나스닥100 ETF', 'SPY': 'S&P500 ETF', 'SOXX': '필라델피아반도체 ETF',
    'MU': '마이크론', 'PANW': '팔로알토', 'QCOM': '퀄컴', 'AMAT': '어플라이드머티리얼즈',
    'CRWD': '크라우드스트라이크', 'ARM': 'ARM홀딩스', 'MRVL': '마벨테크놀로지',
    'BRK.B': '버크셔해서웨이B', 'JPM': 'JP모건', 'LLY': '일라이릴리', 'V': '비자',
    'UNH': '유나이티드헬스', 'MA': '마스터카드', 'XOM': '엑슨모빌', 'WMT': '월마트',
    'HD': '홈디포', 'ORCL': '오라클', 'CAT': '캐터필러', 'GE': 'GE에어로스페이스',
    'GS': '골드만삭스', 'CRM': '세일즈포스', 'UBER': '우버', 'TSM': 'TSMC',
    'IWM': '러셀2000 ETF', 'DIA': '다우존스 ETF', 'XLK': '미국기술주 ETF', 'GLD': '금 ETF',
}

CURATED_US_UNIVERSE = [
    'NVDA', 'MSFT', 'AAPL', 'AMZN', 'GOOGL', 'META', 'AVGO', 'TSLA',
    'COST', 'NFLX', 'AMD', 'PLTR', 'LLY', 'JPM', 'ORCL', 'CRM',
    'UBER', 'TSM', 'MU', 'QCOM', 'AMAT', 'CRWD', 'GE', 'CAT',
    'QQQ', 'SPY', 'SOXX', 'XLK', 'IWM', 'GLD',
]


def is_us_symbol(symbol: str) -> bool:
    s = str(symbol or '').strip()
    return bool(s) and not (len(s) == 6 and s[0].isdigit())


def symbol_market(symbol: str) -> str:
    return 'US' if is_us_symbol(symbol) else 'KR'


def instrument_for(symbol: str, hints: dict | None = None) -> Instrument:
    if not is_us_symbol(symbol):
        return Instrument(symbol=symbol, exchange='KRX')
    hint = (hints or {}).get(symbol) or {}
    ex = hint.get('exchange') if isinstance(hint, dict) else (hint if isinstance(hint, str) else None)
    if ex not in ('NASDAQ', 'NYSE', 'AMEX'):
        ex = US_EXCHANGES.get(symbol, 'NASDAQ')
    return Instrument(symbol=symbol, exchange=ex)


def fx_rate(service, state: dict, now=None) -> float:
    cached = state.get('fx_cache') or {}
    if now and cached.get('at') and cached.get('rate'):
        try:
            if 0 <= (now - stamp(cached['at'])).total_seconds() < 600:
                return float(cached['rate'])
        except (ValueError, TypeError):
            pass
    getter = getattr(service.broker, 'market_context', None)
    if getter:
        try:
            ctx = getter() or {}
            rate = float((ctx.get('fx') or {}).get('usd_krw') or 0)
            if math.isfinite(rate) and rate > 500:
                state['fx_cache'] = {'rate': round(rate, 2), 'at': (now.isoformat() if now else ctx.get('fetched_at') or '')}
                return rate
        except Exception:  # noqa: BLE001
            pass
    if cached.get('rate') and math.isfinite(float(cached['rate'])) and float(cached['rate']) > 500:
        return float(cached['rate'])
    return DEFAULT_USD_KRW


def us_session_open(now) -> bool:
    return bool(exchange_status('NASDAQ', now).get('open'))


def us_preopen_or_open(now) -> bool:
    et_now = now.astimezone(ET)
    day = et_now.date().isoformat()
    if not calendar('NASDAQ').is_session(day):
        return False
    hh = et_now.strftime('%H:%M')
    return '08:00' <= hh < '16:05'


def kr_tradable_window(now) -> bool:
    kst_now = now.astimezone(KST)
    day = kst_now.date().isoformat()
    if not calendar('KRX').is_session(day) or day in {'2026-06-03', '2026-07-17'}:
        return False
    hh = kst_now.strftime('%H:%M')
    return '09:00' <= hh < '15:30'


def kr_entry_window(now) -> bool:
    kst_now = now.astimezone(KST)
    day = kst_now.date().isoformat()
    if not calendar('KRX').is_session(day) or day in {'2026-06-03', '2026-07-17'}:
        return False
    hh = kst_now.strftime('%H:%M')
    return '09:05' <= hh < '15:10'


def us_entry_window(now) -> bool:
    if not us_session_open(now):
        return False
    et_now = now.astimezone(ET)
    hh = et_now.strftime('%H:%M')
    return '09:35' <= hh < '15:50'


def style_caps(cfg: dict) -> dict[str, int]:
    return CAPS.get(cfg.get('portfolio_style', 'balanced'), CAPS['balanced'])


def slot_caps(cfg: dict) -> dict[str, int]:
    caps = style_caps(cfg)
    denom = max(80, sum(caps.values()))
    return {h: math.ceil(cfg['max_positions'] * cap / denom) for h, cap in caps.items()}


def bucket_status(service, state):
    pending = service.live.pending(state['id'], 'buy') if service.is_live(state) else []
    result = {}
    caps = slot_caps(state['config'])
    for h in LABELS:
        held = [p['symbol'] for p in state['positions'].values() if p.get('horizon', 'short') == h]
        reserved = {o['symbol'] for o in pending if o.get('meta', {}).get('horizon', 'short') == h}
        cap = caps[h]
        cands = [r['symbol'] for r in (state.get('scan') or {}).get('candidates', [])
                 if h in r.get('eligible_horizons', [])]
        result[h] = {
            'held': held, 'pending': sorted(reserved), 'slot_limit': cap,
            'open_slots': max(0, cap - len(set(held) | reserved)),
            'candidates': cands,
            'kr_held': [s for s in held if not is_us_symbol(s)],
            'us_held': [s for s in held if is_us_symbol(s)],
            'kr_candidates': [s for s in cands if not is_us_symbol(s)],
            'us_candidates': [s for s in cands if is_us_symbol(s)],
        }
    return result


def _us_pool(broker, cfg: dict) -> dict[str, dict]:
    pool: dict[str, dict] = {}
    rankings_fn = getattr(broker, 'rankings', None)
    if rankings_fn:
        for kind, origin in (('MARKET_TRADING_AMOUNT', 'us_amount'), ('TOP_GAINERS', 'us_gainers')):
            try:
                rows = rankings_fn(kind, count=min(40, int(cfg.get('scan_ranking_count', 40))), market_country='US')
            except Exception:  # noqa: BLE001
                rows = []
            for r in rows or []:
                sym = str(r.get('symbol') or '').strip().upper()
                if not sym or not is_us_symbol(sym):
                    continue
                if kind == 'TOP_GAINERS' and (r.get('change_pct') or 0) > 18.0:
                    continue  # 과도한 급등 추격 방지
                item = pool.setdefault(sym, {
                    'symbol': sym, 'name': r.get('name') or US_NAMES.get(sym, sym),
                    'market': 'US', 'currency': 'USD',
                    'exchange': r.get('market') if r.get('market') in ('NASDAQ', 'NYSE', 'AMEX') else US_EXCHANGES.get(sym, 'NASDAQ'),
                    'origins': [], 'themes': [],
                    'last_price': r.get('last_price') or 0,
                    'prev_change_pct': r.get('change_pct') or 0,
                    'trading_amount': r.get('trading_amount') or 0,
                })
                if origin not in item['origins']:
                    item['origins'].append(origin)
    for sym in CURATED_US_UNIVERSE:
        pool.setdefault(sym, {
            'symbol': sym, 'name': US_NAMES.get(sym, sym), 'market': 'US', 'currency': 'USD',
            'exchange': US_EXCHANGES.get(sym, 'NASDAQ'), 'origins': ['us_curated'], 'themes': [],
        })
    return pool


def universes(state, pool, us_pool=None):
    cfg = state['config']
    markets = cfg.get('markets') or ['KR']
    caps = style_caps(cfg)
    watch = cfg.get('portfolio_watchlists', {})
    us_watch = cfg.get('us_watchlists', {})
    core = cfg.get('portfolio_core', []) if 'KR' in markets else []
    us_core = cfg.get('us_core', []) if 'US' in markets else []

    lists = {'short': [], 'medium': [], 'long': []}
    if 'KR' in markets:
        active = [r['symbol'] for r in sorted(pool.values(), key=scanner._score, reverse=True)]
        liquid = [r['symbol'] for r in sorted(pool.values(), key=lambda r: r.get('trading_amount') or 0, reverse=True)]
        lists['short'].extend(watch.get('short', []) + active + core)
        lists['medium'].extend(watch.get('medium', []) + liquid + core)
        lists['long'].extend(watch.get('long', []) + core + liquid)

    if 'US' in markets:
        upool = us_pool or {}
        us_active = [r['symbol'] for r in sorted(upool.values(), key=lambda r: (r.get('trading_amount') or 0), reverse=True)]
        us_leaders = list(dict.fromkeys(us_core + us_active + CURATED_US_UNIVERSE))
        # Interleave KR and US inside each horizon list so both markets get scanned fairly
        for h, extra in (('short', us_watch.get('short', []) + us_leaders),
                         ('medium', us_watch.get('medium', []) + us_leaders),
                         ('long', us_watch.get('long', []) + us_core + us_leaders)):
            kr_part = list(dict.fromkeys(lists[h]))
            us_part = list(dict.fromkeys(extra))
            merged = []
            for idx in range(max(len(kr_part), len(us_part))):
                if idx < len(kr_part):
                    merged.append(kr_part[idx])
                if idx < len(us_part):
                    merged.append(us_part[idx])
            lists[h] = merged

    per_list_cap = 28 if len(markets) >= 2 else 20
    total_cap = 64 if len(markets) >= 2 else 48
    for h in LABELS:
        held = [s for s, p in state['positions'].items() if p.get('horizon', 'short') == h]
        lists[h] = list(dict.fromkeys(held + (lists[h] if caps[h] else [])))[:per_list_cap]
    symbols = list(state['positions'])
    for i in range(per_list_cap):
        for h in ('long', 'medium', 'short'):
            if i < len(lists[h]) and lists[h][i] not in symbols and len(symbols) < total_cap:
                symbols.append(lists[h][i])
    return symbols, lists


def partition(rows, state):
    """Independent ranked queues, disjoint assignments; held horizon never changes.

    When both KR and US markets are active, guarantees representation for BOTH
    markets in each horizon (up to 5 KR + 5 US = 10 per horizon).
    """
    assigned = {s: p.get('horizon', 'short') for s, p in state.get('positions', {}).items()}
    markets = (state.get('config') or {}).get('markets') or ['KR']
    queues = {
        h: sorted([r for r in rows if h in r['eligible_horizons']],
                  key=lambda r: r['portfolio']['signals'][h]['rank_score'], reverse=True)
        for h in LABELS
    }
    if len(markets) >= 2:
        for mkt_check in (lambda s: not is_us_symbol(s), is_us_symbol):
            for _ in range(5):
                for h in ('long', 'medium', 'short'):
                    available = [r for r in queues[h] if r['symbol'] not in assigned and mkt_check(r['symbol'])]
                    if available:
                        assigned[available[0]['symbol']] = h
    else:
        for _ in range(5):
            for h in ('long', 'medium', 'short'):
                available = [r for r in queues[h] if r['symbol'] not in assigned]
                if available:
                    assigned[available[0]['symbol']] = h
    for r in rows:
        h = assigned.get(r['symbol'])
        r['eligible_horizons'] = [h] if h in r['eligible_horizons'] else []


def market_closures(service, state, now, exchange='KRX'):
    """달력엔 거래일인데 시장 기준 ETF 일봉에 없는 날 = 달력이 모르는 휴장일."""
    tz = KST if exchange == 'KRX' else ET
    ref = MARKET_REFERENCE if exchange == 'KRX' else US_MARKET_REFERENCE
    ref_ex = 'KRX' if exchange == 'KRX' else 'AMEX'
    cache_key = 'market_closures' if exchange == 'KRX' else 'us_market_closures'
    day = now.astimezone(tz).date().isoformat()
    cached = state.get(cache_key) or {}
    if cached.get('day') == day:
        return set(cached['dates'])
    try:
        rows = service.broker.history(Instrument(symbol=ref, exchange=ref_ex), count=300)
        have = {b['date'] for b in rows if b['date'] < day}
        if not have:
            return set()
        gaps = [d for d in exchange_sessions(exchange, min(have), max(have)) if d not in have]
    except Exception:
        return set()
    if len(gaps) > MAX_UNKNOWN_CLOSURES:
        return set()
    state[cache_key] = {'day': day, 'dates': gaps}
    if gaps:
        label = '국내' if exchange == 'KRX' else '미국'
        service._log(state, f'{label} 달력에 없는 휴장일로 처리: ' + ', '.join(gaps))
    return set(gaps)


def _ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi14(closes: list[float]) -> float:
    if len(closes) < 15:
        return 50.0
    diffs = [b - a for a, b in zip(closes[-15:], closes[-14:])]
    gains = sum(max(0.0, d) for d in diffs) / 14.0
    losses = sum(max(0.0, -d) for d in diffs) / 14.0
    if losses <= 1e-12:
        return 100.0 if gains > 0 else 50.0
    rs = gains / losses
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def features(bars, now, closed=(), exchange='KRX', max_missing=0):
    tz = KST if exchange == 'KRX' else ET
    today = now.astimezone(tz).date()
    rows = [b for b in bars if b['date'] < today.isoformat()]
    dates = [b['date'] for b in rows]
    if len(rows) < 61:
        raise ValueError('완료 일봉 61개 미만')
    known = set(dates)
    if len(known) != len(dates) or dates != sorted(dates):
        raise ValueError('일봉 중복 또는 순서 오류')
    sessions_range = exchange_sessions(exchange, dates[0], dates[-1])
    missing = [d for d in sessions_range if d not in known and d not in closed]
    if len(missing) > max_missing:
        raise ValueError(f'일봉 누락 ({missing[0]} 외 {len(missing)-1}일)' if len(missing) > 1 else f'일봉 누락 ({missing[0]})')
    if missing and max_missing > 0:
        recent_sessions = set(sessions_range[-15:])
        if any(d in recent_sessions for d in missing):
            raise ValueError(f'최근 15거래일 내 일봉 누락 ({missing[-1]})')
    recent_cal = [d for d in exchange_sessions(exchange, (today - timedelta(days=15)).isoformat(),
                                               (today - timedelta(days=1)).isoformat()) if d not in closed]
    if not recent_cal:
        raise ValueError('최근 거래일 달력 확인 불가')
    previous = recent_cal[-1]
    if dates[-1] != previous:
        raise ValueError(f'이전 거래일 자료 미갱신 (마지막 {dates[-1]}, 기대 {previous})')
    for b in rows:
        if (any(not math.isfinite(float(b[k])) or b[k] <= 0 for k in ('open', 'high', 'low', 'close'))
                or not math.isfinite(float(b['volume'])) or b['volume'] < 0
                or not b['low'] <= min(b['open'], b['close']) <= max(b['open'], b['close']) <= b['high']):
            raise ValueError('일봉 가격·거래량 오류')

    closes = [float(b['close']) for b in rows]
    price = closes[-1]
    avg = lambda n: sum(closes[-n:]) / n if len(closes) >= n else None  # noqa: E731
    tr = [max(b['high'] - b['low'], abs(b['high'] - a['close']), abs(b['low'] - a['close']))
          for a, b in zip(rows, rows[1:])]
    atr = sum(tr[-14:]) / 14
    atr_pct = atr / price * 100

    # Multi-factor quant indicators
    rsi14 = _rsi14(closes)
    ema12 = _ema(closes[-60:], 12)
    ema26 = _ema(closes[-60:], 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    macd_sig = _ema(macd_line, 9)
    macd_hist = round(macd_line[-1] - macd_sig[-1], 4) if macd_line and macd_sig else 0.0

    sma10 = avg(10)
    sma20 = avg(20)
    sma50 = avg(50)
    sma120 = avg(120)
    sma200 = avg(200)

    std20 = math.sqrt(sum((c - sma20) ** 2 for c in closes[-20:]) / 20.0) if sma20 else 0.0
    bb_width = max(4.0 * std20, price * 1e-4)
    bb_pos = round((price - (sma20 - 2.0 * std20)) / bb_width, 3) if sma20 else 0.5

    vol5 = sum(b['volume'] for b in rows[-5:]) / 5.0
    vol20 = sum(b['volume'] for b in rows[-20:]) / 20.0
    vol_ratio20 = round(vol5 / vol20, 2) if vol20 > 0 else 1.0

    ret1d_pct = (price / closes[-2] - 1) * 100 if len(closes) >= 2 else 0.0
    ret5d_pct = (price / closes[-6] - 1) * 100 if len(closes) >= 6 else 0.0
    ret20d_pct = (price / closes[-21] - 1) * 100 if len(closes) >= 21 else 0.0
    rets20 = [(b / a - 1) * 100 for a, b in zip(closes[-21:], closes[-20:])]
    mean_r = sum(rets20) / len(rets20) if rets20 else 0.0
    daily_vol_pct = max(0.4, math.sqrt(sum((r - mean_r) ** 2 for r in rets20) / max(1, len(rets20))))

    f = {
        'price': price, 'last_date': dates[-1], 'bars': len(rows),
        'exchange': exchange, 'currency': 'KRW' if exchange == 'KRX' else 'USD',
        'sma10': sma10, 'sma20': sma20, 'sma50': sma50, 'sma120': sma120, 'sma200': sma200,
        'atr14': atr, 'atr_pct': round(atr_pct, 2),
        'rsi14': rsi14, 'macd_hist': macd_hist, 'bb_pos': bb_pos,
        'vol_ratio20': vol_ratio20,
        'ret1d_pct': round(ret1d_pct, 2), 'ret5d_pct': round(ret5d_pct, 2), 'ret20d_pct': round(ret20d_pct, 2),
        'daily_vol_pct': round(daily_vol_pct, 2),
        'amount20': sum(b['close'] * b['volume'] for b in rows[-20:]) / 20,
        'momentum63_pct': (price / closes[-64] - 1) * 100 if len(rows) >= 64 else None,
        'momentum126_skip21_pct': (closes[-22] / closes[-127] - 1) * 100 if len(rows) >= 127 else None,
        'momentum252_skip21_pct': (closes[-22] / closes[-253] - 1) * 100 if len(rows) >= 253 else None,
    }

    # 고점 추격(개미 털기 구간) 차단 필터: 단기 급등 피로 구간 진입 금지
    overheated = bool((rsi14 > 82 and sma20 and price / sma20 > 1.12) or ret1d_pct > 18.0 or ret5d_pct > 28.0)
    f['overheated'] = overheated
    bounce = price > closes[-2]

    signals = {}
    for h in LABELS:
        reason = '추세 조건 미충족'
        allowed = False
        setup_tag = 'none'
        if h == 'short':
            trend = price > f['sma50'] and f['sma20'] >= f['sma50'] * 0.99
            pullback = trend and 0.94 <= price / f['sma20'] <= 1.055 and bounce
            breakout = trend and price > max(b['high'] for b in rows[-21:-1]) and price / f['sma20'] <= 1.12
            ema_cont = (trend and sma10 is not None and abs(price / sma10 - 1) <= 0.035
                        and bounce and ret20d_pct > 2.5)
            oversold_rebound = (
                ((f['sma50'] > (f['sma200'] or f['sma50'] * 0.98)) or (f['momentum126_skip21_pct'] or 0) > 3)
                and (rsi14 < 42 or bb_pos < 0.22)
                and bounce
                and price >= f['sma50'] * 0.90
            )
            allowed = (not overheated) and (pullback or breakout or ema_cont or oversold_rebound)
            if overheated:
                reason = '단기 급등·과열 구간 추격 매수 제외'
            elif breakout:
                setup_tag = 'breakout'
                reason = '20일 고점 돌파·과열 상한 이내'
            elif oversold_rebound:
                setup_tag = 'rebound'
                reason = '우량 상승주 과매도 기술적 반등'
            elif pullback:
                setup_tag = 'pullback'
                reason = '상승 추세에서 20일 평균 부근 반등'
            elif ema_cont:
                setup_tag = 'continuation'
                reason = '주도 상승 추세 10일선 지지 반등'
            else:
                reason = '눌림 반등·과매도 반등 또는 20일 고점 돌파 대기'
        elif h == 'medium':
            m6 = f['momentum126_skip21_pct']
            m3 = f['momentum63_pct']
            mom_ok = ((m6 is not None and m6 > 0)
                      or (m3 is not None and m3 > 0)
                      or (ret20d_pct > 4.0 and price > (sma120 or f['sma50'])))
            trend_ok = ((price > f['sma50'] and f['sma20'] >= f['sma50'] * 0.985)
                        or (sma200 is not None and f['sma50'] > sma200 and price >= f['sma50'] * 0.96 and bounce))
            allowed = (not overheated) and mom_ok and trend_ok
            setup_tag = 'medium_trend' if allowed else 'none'
            reason = ('단기 급등 과열로 눌림 대기' if overheated else
                      '3/6개월 위험조정 모멘텀 양수·중기 상승 추세' if allowed else
                      '3/6개월 모멘텀 또는 50일선 추세 부족')
        else:
            m12 = f['momentum252_skip21_pct']
            m6 = f['momentum126_skip21_pct']
            if f['sma200'] is not None:
                classic_long = m12 is not None and m12 > 0 and price > f['sma200']
                secular_dip = (f['sma50'] > f['sma200'] and price >= f['sma200'] * 0.96
                               and (m12 if m12 is not None else (m6 or 0)) > -3 and bounce)
                secular_200 = (m12 is None and m6 is not None and m6 > 5
                               and price > f['sma200'] and f['sma50'] > f['sma200'])
                allowed = classic_long or secular_dip or secular_200
                setup_tag = 'secular_dip' if (secular_dip and not classic_long) else ('secular_trend' if allowed else 'none')
                reason = ('장기 우상향 추세·200일선 지지 구간' if secular_dip and not classic_long else
                          '최근 1개월 제외 12개월 모멘텀 양수·200일 평균 위' if allowed else
                          '장기 상승 추세 또는 200일선 지지 부족')
            else:
                allowed = False
                reason = '200일 이상 장기 이력 부족'

        # 변동성 기반 손절폭 산출: 우량 반도체·빅테크(일 ATR 4~6.5%)가 억울하게 탈락하지 않도록 현실적 한도 적용
        # (위험 금액은 budget() 에서 stop_pct 에 반비례하여 자동으로 포지션 크기를 줄여 통제한다)
        floor, limit, mult = {'short': (3, 14, 2), 'medium': (5, 20, 3), 'long': (8, 26, 4)}[h]
        raw = atr_pct * mult
        stop = max(floor, min(limit, raw))
        if raw > limit:
            allowed = False
            reason = '변동성이 해당 기간의 손절 한도를 초과'

        # 위험조정 멀티팩터 점수 (모멘텀 / 일간변동성 + 거래량 유입 + 적정 RSI 가산점)
        base_mom = ((f['momentum63_pct'] or ret20d_pct or 0) if h == 'short' else
                    max(f['momentum63_pct'] or 0, f['momentum126_skip21_pct'] or 0) if h == 'medium' else
                    (f['momentum252_skip21_pct'] if f['momentum252_skip21_pct'] is not None else (f['momentum126_skip21_pct'] or 0)))
        sharpe_proxy = base_mom / max(1.0, daily_vol_pct)
        vol_bonus = min(3.0, max(0.0, (vol_ratio20 - 1.0) * 2.5))
        rsi_bonus = 2.0 if 42 <= rsi14 <= 68 else (-2.0 if rsi14 > 76 else 1.0)
        macd_bonus = 1.5 if macd_hist > 0 else 0.0
        rank_score = round(sharpe_proxy * 4.0 + base_mom * 0.35 + vol_bonus + rsi_bonus + macd_bonus, 2)

        signals[h] = {
            'eligible': allowed, 'reason': reason, 'setup': setup_tag,
            'stop_pct': round(stop, 2), 'rank_score': rank_score,
        }

    chart = []
    for i in range(max(0, len(rows) - 60), len(rows)):
        point = {k: rows[i][k] for k in ('date', 'open', 'high', 'low', 'close', 'volume')}
        for n in (20, 50, 200):
            point['sma' + str(n)] = sum(closes[i - n + 1:i + 1]) / n if i + 1 >= n else None
        chart.append(point)
    return dict(f, signals=signals, chart=chart)


def scan(service, state, now):
    cfg = scanner._cfg(state['config'])
    markets = state['config'].get('markets') or ['KR']
    pool = {}
    if 'KR' in markets:
        try:
            pool = scanner._pool(service.broker, cfg)
        except Exception:
            pool = {}
    us_pool = _us_pool(service.broker, cfg) if 'US' in markets else {}

    symbols, universe = universes(state, pool, us_pool)
    meta_map = state.setdefault('symbol_meta', {})
    stock_meta_fn = getattr(service.broker, 'stock_meta', None)
    names = {}
    if stock_meta_fn:
        try:
            fetched_meta = stock_meta_fn(symbols)
            meta_map.update(fetched_meta)
            names = {k: v.get('name', '') for k, v in fetched_meta.items() if v.get('name')}
        except Exception:
            names = {}
    if not names:
        try:
            names = service.broker.names(symbols)
        except Exception:
            names = {}

    kr_closed = market_closures(service, state, now, 'KRX') if 'KR' in markets else set()
    us_closed = market_closures(service, state, now, 'NASDAQ') if 'US' in markets else set()
    usd_krw = fx_rate(service, state, now) if 'US' in markets else DEFAULT_USD_KRW

    def read(symbol):
        us = is_us_symbol(symbol)
        inst = instrument_for(symbol, meta_map)
        base_pool = us_pool if us else pool
        row = copy.deepcopy(base_pool.get(symbol, {
            'symbol': symbol, 'origins': ['us_core' if us else 'core'], 'themes': [],
        }))
        row['market'] = 'US' if us else 'KR'
        row['exchange'] = inst.exchange
        row['currency'] = inst.currency
        row['name'] = row.get('name') or names.get(symbol) or US_NAMES.get(symbol) or symbol
        if us:
            # 미국 종목은 토스 종목 상태 및 공개 뉴스 확인 (국내 전용 투자자 수급 API 호출 제외)
            meta_info = meta_map.get(symbol) or {}
            status_val = str(meta_info.get('status') or '').upper()
            row['warnings_checked'] = True
            row['warnings'] = [status_val] if status_val in ('HALTED', 'SUSPENDED', 'DELISTED') else []
            try:
                row['news'] = scanner.krnews.search_news(f'{row["name"]} {symbol}', 3)[:3]
                row['news_count'] = len(row['news'])
            except Exception:
                row['news'] = []
                row['news_count'] = 0
                row['data_notes'] = ['해외 뉴스 수집 생략: 일봉·가격 지표 기준 판단']
        else:
            try:
                scanner._enrich(service.broker, row, cfg)
            except Exception:
                row['news'] = []
                row['news_count'] = 0
                row['data_notes'] = ['보조 뉴스·수급 수집 일부 실패: 확인된 자료만 사용']
        try:
            cache = state.setdefault('portfolio_daily', {})
            tz = ET if us else KST
            local_day = now.astimezone(tz).date().isoformat()
            hit = cache.get(symbol)
            if hit and 0 <= (now - stamp(hit['at'])).total_seconds() < 3600 and hit['day'] == local_day:
                daily = copy.deepcopy(hit['data'])
            else:
                bars = service.broker.history(inst, count=300)
                closed_set = us_closed if us else kr_closed
                try:
                    daily = features(bars, now, closed_set, exchange=inst.exchange, max_missing=0)
                except ValueError as gap_err:
                    if '일봉 누락' in str(gap_err):
                        daily = features(bars, now, closed_set, exchange=inst.exchange, max_missing=4)
                    else:
                        raise
                cache[symbol] = {'at': now.isoformat(), 'day': local_day, 'data': daily}
            row['technical'] = {'daily': daily, 'as_of': now.isoformat(), 'errors': []}
            row['portfolio'] = daily
            row['last_price'] = daily['price']
            row['trading_amount'] = daily['amount20'] * (usd_krw if us else 1.0)
            row['trading_amount_native'] = daily['amount20']
        except Exception as exc:
            row['portfolio'] = {'signals': {}, 'error': str(exc)[:150]}
        return row

    with ThreadPoolExecutor(max_workers=4) as pool_exec:
        rows = list(pool_exec.map(read, symbols))
    state['portfolio_daily'] = {s: v for s, v in state.get('portfolio_daily', {}).items() if s in symbols}

    approved = []
    checks = []
    caps = style_caps(state['config'])
    for r in rows:
        f = r['portfolio']
        error = f.get('error')
        if not error and not r.get('warnings_checked'):
            error = '거래 경고·정지 여부 조회 실패'
        if not error and r.get('warnings'):
            error = '투자 경고·거래정지 확인'
        min_amt = cfg['min_trading_amount_krw']
        if not error and r.get('trading_amount', f.get('amount20', 0)) < min_amt:
            error = '20일 평균 거래대금 부족'
        eligible = [h for h, s in f.get('signals', {}).items() if s['eligible'] and caps[h] > 0]
        checks.append({
            'symbol': r['symbol'], 'name': r['name'], 'market': r.get('market', 'KR'),
            'currency': r.get('currency', 'KRW'), 'exchange': r.get('exchange', 'KRX'),
            'price': f.get('price'), 'rsi14': f.get('rsi14'), 'ret20d_pct': f.get('ret20d_pct'),
            'eligible': eligible if not error else [],
            'reason': error or ('기간별 매수 검토 가능' if eligible else ' / '.join(
                LABELS[h] + ': ' + f['signals'][h]['reason'] for h in LABELS if caps[h])),
        })
        r['eligible_horizons'] = eligible if not error else []
        if not error:
            approved.append(r)

    partition(approved, state)
    by_symbol = {r['symbol']: r for r in approved}
    for c in checks:
        c['period_reasons'] = ({h: by_symbol[c['symbol']]['portfolio']['signals'][h]['reason']
                                for h in LABELS if caps[h]} if c['symbol'] in by_symbol else {})
        c['rank_scores'] = ({h: by_symbol[c['symbol']]['portfolio']['signals'][h]['rank_score']
                             for h in LABELS if caps[h]} if c['symbol'] in by_symbol else {})
        before = c['eligible']
        c['eligible'] = by_symbol[c['symbol']]['eligible_horizons'] if c['symbol'] in by_symbol else []
        if before and not c['eligible']:
            c['reason'] = '가격 조건 통과 · 기간별 상위 후보 순위 밖 또는 보유 기간 유지'

    state['portfolio_checks'] = checks
    state['portfolio_universes'] = universe
    state['portfolio_held_data'] = {r['symbol']: r.get('portfolio', {}) for r in rows if r['symbol'] in state['positions']}
    active_candidates = [r for r in approved if r.get('eligible_horizons')]
    # Preserve `approved` list compatibility when tests inspect `result['candidates']`
    # Note: candidates with non-empty eligible_horizons come first, sorted by best rank_score
    approved.sort(key=lambda r: (
        1 if r.get('eligible_horizons') else 0,
        max((r['portfolio']['signals'][h]['rank_score'] for h in r.get('eligible_horizons', [])), default=-999),
    ), reverse=True)
    return {
        'scanned_at': now.isoformat(), 'pool_size': len(symbols), 'enriched': len(rows),
        'markets': markets, 'usd_krw': usd_krw,
        'kr_candidate_count': sum(1 for r in active_candidates if not is_us_symbol(r['symbol'])),
        'us_candidate_count': sum(1 for r in active_candidates if is_us_symbol(r['symbol'])),
        'candidates': approved,
        'rejected': [c for c in checks if not c['eligible']],
        'note': '완료 일봉 멀티팩터(추세·RSI·거래량·위험조정 모멘텀) 기간별 규칙 · 국내(KRX)+미국(NASDAQ/NYSE/AMEX) 통합 스캔.',
    }


def apply_plan(service, state, plan, now):
    candidates = {c['symbol']: c for c in (state.get('scan') or {}).get('candidates', [])}
    picks = []
    seen = set()
    slots = bucket_status(service, state)
    for p in plan.get('picks', []):
        c = candidates.get(p['symbol'])
        h = p.get('horizon', 'short')
        if not c or h not in c.get('eligible_horizons', []) or p['symbol'] in seen:
            service._log(state, f'{p["symbol"]} 매수 보류: AI가 제안한 종목·투자 기간이 검증된 후보와 다름')
            continue
        if p.get('max_entry_price', 0) <= 0:
            service._log(state, f'{p["symbol"]} 매수 보류: AI 최대 매수가 누락')
            continue
        if slots[h]['open_slots'] <= sum(1 for pick in picks if pick['horizon'] == h):
            service._log(state, f'{p["symbol"]} 매수 보류: {LABELS[h]} 종목 수 한도')
            continue
        seen.add(p['symbol'])
        f = c['portfolio']
        sl = f['signals'][h]['stop_pct']
        picks.append(dict(
            p, name=c['name'],
            market=c.get('market', symbol_market(p['symbol'])),
            currency=c.get('currency', 'USD' if is_us_symbol(p['symbol']) else 'KRW'),
            exchange=c.get('exchange', instrument_for(p['symbol'], state.get('symbol_meta')).exchange),
            stop_loss_pct=sl,
            take_profit_pct=min(60, sl * 3),
            weight_pct=min(p['weight_pct'], state['config']['position_pct']),
            # 전일 종가 대비 최대 +6% 상한(미국·한국 갭상승 허용 범위 현실화, 과열 추격은 차단)
            max_entry_price=min(p['max_entry_price'], round(f['price'] * 1.06, 4)),
            _done=False,
        ))
    evidence = exit_evidence(state)
    exits = {}
    deferred = []
    for exit_plan in plan.get('exits', []):
        symbol = exit_plan['symbol']
        pos = state['positions'].get(symbol)
        if not pos:
            continue
        entered = stamp(pos['entry_at'])
        tz = ET if is_us_symbol(symbol) else KST
        same_day = entered.astimezone(tz).date() == now.astimezone(tz).date()
        verified = [e for e in evidence if e['symbol'] == symbol and e['id'] in exit_plan.get('evidence_ids', [])
                    and entered <= stamp(e['published_at']) <= now]
        if same_day and not verified:
            deferred.append({'symbol': symbol, 'reason': '매수 당일 AI 매도 보류: 진입 이후 새 위험 근거 확인 필요'})
            service._log(state, deferred[-1]['reason'] + ' · ' + symbol)
            continue
        exits[symbol] = {'reason': exit_plan['reason'], 'decided_at': now.isoformat(),
                         'entry_at': pos['entry_at'], 'evidence_ids': [e['id'] for e in verified]}
    state['portfolio_exits'] = exits
    state['portfolio_exit_deferred'] = deferred
    reviews = plan.setdefault('horizon_reviews', {})
    caps = style_caps(state['config'])
    for h in LABELS:
        if not reviews.get(h):
            reviews[h] = ('선택하지 않은 투자 기간' if not caps[h] else
                          '기간별 종목 수 한도 도달' if not slots[h]['open_slots'] else
                          '기간별 조건을 통과한 후보 없음' if not slots[h]['candidates'] else
                          'AI 개별 설명 미제공 · 아래 선정 종목과 전체 의견 확인')
    # If an earlier session (e.g., KR) still had unexecuted picks for the other market (US), keep non-duplicate pending picks alive
    prev_picks = (state.get('plan') or {}).get('picks') or []
    for old in prev_picks:
        if (not old.get('_done') and old['symbol'] not in seen and old['symbol'] not in state['positions']
                and symbol_market(old['symbol']) not in {symbol_market(x['symbol']) for x in picks}):
            h = old.get('horizon', 'short')
            if slots[h]['open_slots'] > sum(1 for pick in picks if pick['horizon'] == h):
                picks.append(old)
                seen.add(old['symbol'])

    state['plan'] = dict(plan, picks=picks, decided_at=now.isoformat(), market_stance='normal' if picks else 'no_trade')
    state['phase'] = 'planned' if picks else 'monitoring'
    state['stamps']['plan'] = now.isoformat()
    kr_n = sum(1 for x in picks if not is_us_symbol(x['symbol']))
    us_n = sum(1 for x in picks if is_us_symbol(x['symbol']))
    mkt_desc = f'국내 {kr_n}·해외 {us_n}' if us_n else f'{len(picks)}개'
    service._log(state, f'기간별 AI 판단: 매수 후보 {mkt_desc} · ' + plan['summary'])
    notify.post(notify.plan_message(state, state['plan'], candidates))


def exit_evidence(state):
    evidence = [e for e in state.get('office_exit_evidence', []) if e.get('symbol') in state['positions']]
    for c in (state.get('scan') or {}).get('candidates', []):
        if c['symbol'] not in state['positions']:
            continue
        for n in c.get('news', []):
            try:
                stamp(n['published_at'])
            except (ValueError, KeyError, TypeError):
                continue
            material = '|'.join([c['symbol'], n.get('title', ''), n.get('source', ''), n['published_at']])
            evidence.append({
                'id': hashlib.sha256(material.encode()).hexdigest()[:16],
                'symbol': c['symbol'], 'title': n.get('title', ''),
                'source': n.get('source', ''), 'published_at': n['published_at'],
            })
    return evidence


def _pos_value_krw(service, state: dict, p: dict, now=None) -> float:
    q_bid = state.get('quotes', {}).get(p['symbol'], {}).get('bid', 0)
    unit_native = max(p['entry_price'], q_bid)
    mult = fx_rate(service, state, now) if p.get('currency', 'KRW') == 'USD' else 1.0
    return p['qty'] * unit_native * mult


def budget(service, state, horizon, requested, quote, sl_pct, now):
    equity = service.equity(state)
    cfg = state['config']
    pending = service.live.pending(state['id'], 'buy') if service.is_live(state) else []
    if bucket_status(service, state)[horizon]['open_slots'] <= 0:
        return 0
    used = {h: 0.0 for h in LABELS}
    for p in state['positions'].values():
        used[p.get('horizon', 'short')] += _pos_value_krw(service, state, p, now)
    for o in pending:
        used[o.get('meta', {}).get('horizon', 'short')] += (
            max(0, o['qty'] - o.get('filled_qty', 0)) * o['limit_price'] * (1 + cfg['fee_bps'] / 10000)
        )
    caps = style_caps(cfg)
    cap = caps[horizon] / 100 * equity
    total_invest_ratio = sum(caps.values()) / 100.0
    is_usd = (quote or {}).get('currency') == 'USD'
    sell_tax = 0.0 if is_usd else cfg['sell_tax_bps']
    costs = (2 * cfg['fee_bps'] + sell_tax + 2 * cfg['slippage_bps'] + max(0, quote.get('spread_bps', 0))) / 100
    risk_size = equity * cfg['portfolio_risk_pct'] / (sl_pct + costs)
    reserved_cash = sum(
        max(0, o['qty'] - o.get('filled_qty', 0)) * o['limit_price'] * (1 + cfg['fee_bps'] / 10000)
        for o in pending
    )
    return max(0, min(requested, cap - used[horizon], equity * total_invest_ratio - sum(used.values()),
                      risk_size, state['cash'] - reserved_cash))


def monitor(service, state, now, market_filter: str | None = None):
    targets = [
        s for s in state['positions']
        if market_filter is None or symbol_market(s) == market_filter
    ]
    if not targets:
        return
    service._refresh_quotes(state, targets)
    cfg = state.get('config', {})
    use_be = cfg.get('breakeven_lock', True)
    use_partial = cfg.get('partial_tp', True) and not service.is_live(state)

    for symbol in list(targets):
        p = state['positions'].get(symbol)
        if not p:
            continue
        q = state['quotes'].get(symbol)
        if p.get('closing') or not service._fresh_quote(q, now):
            continue
        h = p.get('horizon', 'short')
        bid = q['bid']
        reason = ''
        p['peak_price'] = max(p.get('peak_price', p['entry_price']), bid)
        trail = p['peak_price'] * (1 - p['sl_pct'] / 100)
        p['sl_price'] = max(p['sl_price'], trail)

        # 1) 본절·수익 잠금 (Breakeven + 수수료·세금 보전 스탑): 수익권에 안착한 종목이 손실로 뒤집히는 것을 차단
        peak_gain_pct = (p['peak_price'] / p['entry_price'] - 1) * 100 if p['entry_price'] > 0 else 0
        be_trigger = {'short': 2.5, 'medium': 4.5, 'long': 7.0}.get(h, 3.5)
        if use_be and peak_gain_pct >= be_trigger:
            be_floor = p['entry_price'] * (1.004 if p.get('currency') == 'USD' else 1.006)
            if be_floor > p['sl_price']:
                p['sl_price'] = be_floor
            p['breakeven_locked'] = True
        # 장기(long) 큰 추세 수익 시 단계적 이익 보존선 상향 (+20% 도달 시 최소 +10% 수익 확보)
        if use_be and h == 'long' and peak_gain_pct >= 20.0:
            p['sl_price'] = max(p['sl_price'], p['entry_price'] * 1.10)

        # 2) 단기·중기 1차 분할 익절 (Partial Profit Taking): 확정 수익을 계좌에 쌓고 나머지로 큰 추세를 추종
        cur_gain_pct = (bid / p['entry_price'] - 1) * 100 if p['entry_price'] > 0 else 0
        partial_target = (max(4.5, p['sl_pct'] * 1.1) if h == 'short'
                          else max(10.0, p['sl_pct'] * 1.4) if h == 'medium' else None)
        if (use_partial and partial_target and not p.get('partial_tp_done')
                and p['qty'] >= 2 and cur_gain_pct >= partial_target and bid > p['sl_price']):
            sell_ratio = 0.5 if h == 'short' else 0.4
            sell_qty = max(1, min(p['qty'] - 1, int(round(p['qty'] * sell_ratio))))
            if hasattr(service, '_fill_partial_sell') and service._fill_partial_sell(
                state, symbol, sell_qty, bid, f'1차 분할 익절 (+{cur_gain_pct:.1f}% 수익 확정)', now
            ):
                p['partial_tp_done'] = True
                p['partial_tp_at'] = now.isoformat()
                be_after = p['entry_price'] * (1.005 if p.get('currency') == 'USD' else 1.007)
                p['sl_price'] = max(p['sl_price'], be_after)
                continue

        # 3) 손절 / 추세 이탈(중·장기 1% 노이즈 버퍼 적용) / AI 재검토 / 기간 만료 청산
        f = state.get('portfolio_held_data', {}).get(symbol, {})
        line = f.get({'short': 'sma20', 'medium': 'sma50', 'long': 'sma200'}[h])
        whipsaw_mult = 1.0 if h == 'short' else 0.99
        tz = ET if is_us_symbol(symbol) else KST
        entry_day = stamp(p['entry_at']).astimezone(tz).date().isoformat()
        today_local = now.astimezone(tz).date().isoformat()
        after_entry = entry_day <= f.get('last_date', '') < today_local
        decision = state.get('portfolio_exits', {}).get(symbol)
        if bid <= p['sl_price']:
            reason = '보호 가격 이탈 · 추적 손절'
        elif after_entry and line and f.get('price', 0) < line * whipsaw_mult:
            reason = '진입 이후 완료 일봉에서 투자 기간의 추세 이탈'
        elif isinstance(decision, dict) and decision.get('entry_at') == p['entry_at']:
            if entry_day < today_local or decision.get('evidence_ids'):
                reason = 'AI 투자 근거 재검토: ' + decision['reason']
        age = (now - stamp(p['entry_at'])).days
        if not reason and ((h == 'short' and age >= 25) or (h == 'medium' and age >= 120)):
            reason = '계획한 투자 기간 종료'
        if reason:
            service._fill_sell(state, symbol, bid, reason, now)


def tick(service, state, now):
    cfg = state['config']
    markets = cfg.get('markets') or ['KR']
    hh = now.astimezone(KST).strftime('%H:%M')
    kr_open_day = calendar('KRX').is_session(now.astimezone(KST).date().isoformat()) and now.astimezone(KST).date().isoformat() not in {'2026-06-03', '2026-07-17'}

    # 1) 국내 장전·수동 스캔 및 정기 리뷰 (10:15, 12:00, 14:30 KST)
    if service._needs_plan(state, now):
        service._try_plan(state, now)

    reviewed = state['events'].setdefault('portfolio_reviews', [])
    if 'KR' in markets and kr_open_day:
        slot = ('morning_1015' if '10:15' <= hh < '10:30' else
                'midday' if '12:00' <= hh < '12:15' else
                'afternoon' if '14:30' <= hh < '14:45' else None)
        if slot and slot not in reviewed and service._ai_allowed(state, now, anchor=True):
            reviewed.append(slot)
            if service._do_scan(state, now) and (state['scan']['candidates'] or state['positions']):
                service._plan_call(state, now)

        if '09:10' <= hh < '15:30' and not state.get('plan') and not state['ai'].get('pending'):
            from .diagnostics import meeting
            m = meeting(state, now)
            if m['status'] in ('bad', 'warn'):
                notify.post_once(f'no-meeting:{state["day"]}:{state["id"]}',
                                 notify.error_message('오늘 장전 회의가 열리지 않았습니다', m['detail']))

        if '09:00' <= hh < '15:30' and service._tradable(state, now):
            monitor(service, state, now, market_filter='KR' if 'US' in markets else None)
            if '09:05' <= hh < '15:10' and state['phase'] in ('planned', 'entering'):
                service._do_entry(state, now, market_filter='KR' if 'US' in markets else None)

        if hh >= '15:10' and service.is_live(state):
            service.live.cancel_all(state, 'buy')

        if hh >= '15:30' and state['phase'] != 'closed' and not ('US' in markets and us_preopen_or_open(now)):
            state['phase'] = 'closed'
            state['stamps']['closed'] = now.isoformat()
            service._record_day(state, state['day'])
            state['tuning']['pending_snapshot'] = service._review_snapshot(state, state['day'], now)
            service._log(state, '오늘 국내장 평가 완료 · 단기·중기·장기 보유는 유지')
            notify.post(notify.summary_message(state), notify.summary_kind(state))

    # 2) 미국(US) 정규장 프리오픈 스캔·회의 (08:30 ET) + 장중 감시·진입 (09:30~16:00 ET) + 장중 재점검 (12:30 ET)
    if 'US' in markets:
        et_now = now.astimezone(ET)
        us_day = et_now.date().isoformat()
        us_hh = et_now.strftime('%H:%M')
        if calendar('NASDAQ').is_session(us_day):
            us_slot = (f'us_preopen:{us_day}' if '08:30' <= us_hh < '09:25' else
                       f'us_midday:{us_day}' if '12:30' <= us_hh < '12:50' else None)
            if us_slot and us_slot not in reviewed and service._ai_allowed(state, now, anchor=True):
                reviewed.append(us_slot)
                reviewed[:] = reviewed[-30:]
                service._log(state, f'미국장 정기 스캔·AI 판단 시작 ({us_slot.split(":")[0]} · 뉴욕 {us_hh})')
                if service._do_scan(state, now) and (state['scan']['candidates'] or state['positions']):
                    service._plan_call(state, now)

            if us_session_open(now):
                monitor(service, state, now, market_filter='US')
                if us_entry_window(now):
                    has_us_picks = any(is_us_symbol(p['symbol']) and not p.get('_done')
                                       for p in (state.get('plan') or {}).get('picks', []))
                    if has_us_picks or state['phase'] in ('planned', 'entering'):
                        service._do_entry(state, now, market_filter='US')

            if us_hh >= '16:05':
                done_key = f'us_closed:{us_day}'
                if done_key not in reviewed:
                    reviewed.append(done_key)
                    service._record_day(state, state['day'])
                    service._log(state, f'미국장({us_day}) 마감 평가 완료 · 단기·중기·장기 보유 유지')

    if cfg['daily_review'] and state['phase'] == 'closed':
        service._maybe_review(state, now, state['day'])
    if service.is_live(state):
        service.live.pulse(state, now, service._log)
    service._record_equity(state, now)
    state['last_poll_at'] = now.isoformat()
