"""Stock trend scoring. Indicator math lives in indicators.py and stays pure."""
from .indicators import sma, rsi, bollinger


def ema(values, period):
    result = [values[0]]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def analyze(bars):
    if len(bars) < 61:
        raise ValueError('기술 분석에 최소 61개 완료 일봉이 필요합니다.')
    closes = [b['close'] for b in bars]
    volumes = [b['volume'] for b in bars]
    ma20, ma60, ma200 = (sma(closes, n)[-1] for n in (20, 60, 200))
    r = rsi(closes)[-1]
    if max(closes[-15:]) == min(closes[-15:]):
        r = 50.0  # legacy RSI reports 100 for completely flat prices
    mid, upper, lower = bollinger(closes)
    macd_series = [a-b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    signal = ema(macd_series, 9)[-1]
    hist = macd_series[-1] - signal
    tr = [max(b['high'] - b['low'], abs(b['high'] - bars[i-1]['close']),
              abs(b['low'] - bars[i-1]['close'])) for i, b in enumerate(bars) if i]
    atr = sum(tr[:14]) / 14
    for value in tr[14:]:
        atr = (atr * 13 + value) / 14
    previous_volume = sum(volumes[-21:-1]) / 20
    volume_ratio = volumes[-1] / previous_volume if previous_volume else 0
    resistance = max(b['high'] for b in bars[-21:-1])
    support = min(b['low'] for b in bars[-21:-1])
    slope = ma20 > sma(closes[:-1], 20)[-1]
    price = closes[-1]
    components = {
        'trend': (15 if price > ma60 else 0) + (10 if price > ma20 and slope else 0),
        'macd': 15 if macd_series[-1] > 0 and hist > 0 else (6 if hist > 0 else 0),
        'rsi': 10 if 45 <= r <= 68 else (4 if 35 <= r < 45 else 0),
        'volume': 10 if volume_ratio >= 1.2 else (5 if volume_ratio >= 0.8 else 0),
        'structure': 10 if price > resistance else (5 if price > ma20 and price < upper[-1] else 0),
    }
    return dict(price=price, sma20=ma20, sma60=ma60, sma200=ma200, rsi14=r,
                macd=macd_series[-1], macd_signal=signal, macd_hist=hist,
                atr14=atr, bb_upper=upper[-1], bb_lower=lower[-1],
                volume_ratio=volume_ratio, resistance20=resistance, support20=support,
                technical_score=sum(components.values()), components=components)


def chart_series(bars):
    closes = [b['close'] for b in bars]
    m20, m60 = sma(closes, 20), sma(closes, 60)
    return [dict(b, sma20=m20[i], sma60=m60[i]) for i, b in enumerate(bars)]


def intraday_features(bars,exchange,now,previous_close=None):
    from datetime import timedelta
    from .models import stamp
    from .session_policy import context
    window=context(exchange,now)
    if not window['open_at']:return {'bars':0}
    start=stamp(window['open_at']); end=stamp(window['close_at'])
    rows=sorted({b['timestamp']:b for b in bars if start<=stamp(b['timestamp'])<end and
                 stamp(b['timestamp'])+timedelta(minutes=1)<=now}.values(),key=lambda b:b['timestamp'])
    if not rows:return {'bars':0}
    volume=sum(b['volume'] for b in rows)
    running_volume=running_value=0
    vwap_at={}
    for b in rows:
        running_volume+=b['volume']; running_value+=(b['high']+b['low']+b['close'])/3*b['volume']
        vwap_at[b['timestamp']]=running_value/running_volume if running_volume else None
    opening=[b for b in rows if stamp(b['timestamp'])<start+timedelta(minutes=15)]
    complete_open=len(opening)==15 and all(stamp(b['timestamp'])==start+timedelta(minutes=i) for i,b in enumerate(opening))
    continuous=all(stamp(b['timestamp'])==start+timedelta(minutes=i) for i,b in enumerate(rows))
    result={'bars':len(rows),'first_at':rows[0]['timestamp'],'last_at':rows[-1]['timestamp'],
            'session_open':rows[0]['open'] if stamp(rows[0]['timestamp'])==start else None,
            'observed_volume':volume,
            'observed_turnover_estimate':sum(b['close']*b['volume'] for b in rows),
            'coverage':'complete_from_open' if continuous else 'partial',
            'vwap':sum((b['high']+b['low']+b['close'])/3*b['volume'] for b in rows)/volume if volume else None,
            'vwap_scope':'session' if continuous else 'available_window',
            'opening_range_high':max(b['high'] for b in opening) if complete_open else None,
            'opening_range_low':min(b['low'] for b in opening) if complete_open else None,
            'gap_pct':(rows[0]['open']/previous_close-1)*100 if previous_close and stamp(rows[0]['timestamp'])==start else None,
            'session_high':max(b['high'] for b in rows),'session_low':min(b['low'] for b in rows),
            'frames':{}}
    first5=[b for b in rows if stamp(b['timestamp'])<start+timedelta(minutes=5)]
    if len(first5)==5 and all(stamp(b['timestamp'])==start+timedelta(minutes=i) for i,b in enumerate(first5)):
        result['opening_5m_volume']=sum(b['volume'] for b in first5)
        result['opening_5m_high']=max(b['high'] for b in first5)
        result['opening_5m_low']=min(b['low'] for b in first5)
    for n in (5,15):
        groups={}
        for b in rows:
            k=int((stamp(b['timestamp'])-start).total_seconds()//(60*n));groups.setdefault(k,[]).append(b)
        completed=[items for k,items in sorted(groups.items()) if len(items)==n and
                   all(stamp(b['timestamp'])==start+timedelta(minutes=k*n+i) for i,b in enumerate(items))]
        closes=[g[-1]['close'] for g in completed]
        result['frames'][str(n)+'m']={'completed_bars':len(closes),'last_close':closes[-1] if closes else None,
            'previous_close':closes[-2] if len(closes)>1 else None,
            'recent_bars':[{'timestamp':g[0]['timestamp'],'open':g[0]['open'],
                'high':max(b['high'] for b in g),'low':min(b['low'] for b in g),
                'close':g[-1]['close'],'volume':sum(b['volume'] for b in g)} for g in completed[-12:]],
            'volume_ratio_prior_bars':(sum(b['volume'] for b in completed[-1]) /
                (sum(b['volume'] for g in completed[-6:-1] for b in g)/5)
                if len(completed)>=6 and sum(b['volume'] for g in completed[-6:-1] for b in g)>0 else None),
            'ema9':ema(closes,9)[-1] if len(closes)>=9 else None,'ema20':ema(closes,20)[-1] if len(closes)>=20 else None}
        for record, group in zip(result['frames'][str(n)+'m']['recent_bars'],completed[-12:]):
            record['vwap_at_close']=vwap_at[group[-1]['timestamp']]
    result['volume_note']='volume_ratio_prior_bars는 직전 5개 동일 길이 봉 대비 거래량이며 전일 동시간 RVOL이 아닙니다.'
    return result
