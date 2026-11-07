"""장전 후보 스캐너. 프로그램이 숫자로 거른 종목만 AI 에게 넘긴다.

역할 분담 - 이게 이 파일의 존재 이유다
    프로그램  : 후보 풀을 만들고, 유동성·경고·수급·뉴스 조건으로 걸러 점수를 매긴다.
    AI        : 걸러진 소수 후보 중 무엇을 얼마에 살지, 익절·손절 폭만 정한다.
    AI 가 전 종목을 훑게 하면 사용량만 늘고 근거 없는 종목이 섞인다. 후보를 좁히는 건
    사람이 정한 숫자 조건이어야 나중에 실적을 보고 조일 수 있다.

후보 풀 (합집합)
    테마 상위 N 의 구성종목  - "무엇이 함께 움직이는가"
    거래대금 상위 M          - 유동성. 못 팔면 손절도 못 한다
    상승률 상위 K (전일)      - 이미 움직이기 시작한 것

후보별 수집 (종목당 최대 3회 호출이라 풀 크기를 제한한다)
    수급   토스 investor-trading   외국인·기관·개인 순매수 최근 3일
    경고   토스 warnings           투자경고·위험·거래정지 -> 제외
    뉴스   네이버 stock news        최근 기사 제목·본문

점수는 순위표일 뿐 예측이 아니다
    같은 조건을 두 번 백테스트했고(코인 급등추격, 주식 추세 게이트) 둘 다 무작위와
    구분되지 않았다. 이 점수는 "AI 가 볼 순서" 를 정하는 것이지 수익을 약속하지 않는다.
"""
from __future__ import annotations

import logging
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from . import krnews, themes as theme_source
from .models import Instrument
from .technical import analyze, intraday_features

log = logging.getLogger('bqt.scanner')

DEFAULTS = {
    'scan_theme_count': 20,          # 테마 상위 N
    'scan_ranking_count': 50,        # 거래대금·상승률 상위 M
    'min_trading_amount_krw': 3e9,   # 유동성 하한 30억. 못 팔면 손절도 못 한다
    'max_prev_change_pct': 25.0,     # 전일 이미 이만큼 오른 건 추격이다. 상한가 근처 제외
    'require_flow': False,           # 외국인 또는 기관 순매수 양수 요구
    'require_news': False,           # 최근 24시간 기사 1건 이상 요구
    'news_hours': 24,
    'max_candidates': 15,            # AI 에 넘길 상한
    'enrich_limit': 40,              # 수급·경고·뉴스를 조회할 후보 상한 (호출 비용)
}


def _cfg(overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    if overrides:
        merged.update({k: v for k, v in overrides.items() if k in DEFAULTS})
    return merged


def _pool(broker, cfg) -> dict[str, dict[str, Any]]:
    """후보 풀. 출처를 기록해 둔다 - 여러 출처에 겹치면 그만큼 신호가 겹친 것이다."""
    pool: dict[str, dict[str, Any]] = {}

    def add(symbol: str, origin: str, **fields):
        entry = pool.setdefault(symbol, {'symbol': symbol, 'origins': [], 'themes': []})
        if origin not in entry['origins']:
            entry['origins'].append(origin)
        for k, v in fields.items():
            if v is not None and entry.get(k) in (None, 0, 0.0, ''):
                entry[k] = v

    try:
        for row in broker.rankings('MARKET_TRADING_AMOUNT', count=cfg['scan_ranking_count']):
            add(row['symbol'], 'trading_amount', last_price=row['last_price'],
                prev_change_pct=row['change_pct'], trading_amount=row['trading_amount'],
                amount_rank=row['rank'])
    except Exception as exc:  # noqa: BLE001
        log.warning('거래대금 랭킹 실패: %s', exc)
    try:
        for row in broker.rankings('TOP_GAINERS', count=cfg['scan_ranking_count']):
            add(row['symbol'], 'top_gainer', last_price=row['last_price'],
                prev_change_pct=row['change_pct'], trading_amount=row['trading_amount'],
                gainer_rank=row['rank'])
    except Exception as exc:  # noqa: BLE001
        log.warning('상승률 랭킹 실패: %s', exc)

    try:
        data = theme_source.overview(limit=cfg['scan_theme_count'])
    except Exception:
        log.warning('테마 조회 실패: 거래대금·상승률 후보는 유지')
        data = {}
    for theme in data.get('themes', []):
        for stock in theme.get('stocks', []):
            if not stock.get('tradable', True):
                continue
            add(stock['symbol'], 'theme', name=stock.get('name'))
            pool[stock['symbol']]['themes'].append({
                'name': theme['name'], 'change_pct': theme['change_pct'],
                'change_3d_avg_pct': theme['change_3d_avg_pct'],
                'up': theme['up'], 'down': theme['down'],
                'description': (theme.get('description') or '')[:300],
            })
    return pool


def _enrich(broker, entry: dict[str, Any], cfg) -> None:
    symbol = entry['symbol']
    try:
        rows = broker.investor_trading(symbol, 3)
        entry['flow'] = rows
        entry['flow_foreigner_1d'] = rows[0]['foreigner'] if rows else 0
        entry['flow_institution_1d'] = rows[0]['institution'] if rows else 0
        entry['flow_foreigner_3d'] = sum(r['foreigner'] for r in rows)
        entry['flow_institution_3d'] = sum(r['institution'] for r in rows)
    except Exception:  # noqa: BLE001
        entry['flow'] = []
    try:
        warnings = broker.warnings(symbol)
        entry['warnings'] = warnings if isinstance(warnings, list) else [warnings]
        entry['warnings_checked'] = True
    except Exception:  # noqa: BLE001
        entry['warnings'] = []
        entry['warnings_checked'] = False
    news = krnews.stock_news(symbol, 5)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=cfg['news_hours'])
    entry['news'] = [n for n in news if not n['published_at']
                     or datetime.fromisoformat(n['published_at']) >= cutoff]
    entry['news_count'] = len(entry['news'])


def _score(entry: dict[str, Any]) -> float:
    """AI 가 볼 순서. 예측이 아니라 '신호가 몇 겹 겹쳤나' 다."""
    score = 0.0
    score += 10.0 * len(entry.get('origins', []))
    score += min(len(entry.get('themes', [])), 3) * 5.0
    if entry.get('flow_foreigner_1d', 0) > 0:
        score += 8.0
    if entry.get('flow_institution_1d', 0) > 0:
        score += 8.0
    score += min(entry.get('news_count', 0), 3) * 4.0
    amount = entry.get('trading_amount') or 0
    if amount >= 1e10:
        score += 6.0
    elif amount >= 3e9:
        score += 3.0
    return round(score, 1)


def _reject_reason(entry: dict[str, Any], cfg, *, enriched: bool = True) -> str:
    amount = entry.get('trading_amount') or 0
    if amount and amount < cfg['min_trading_amount_krw']:
        return f'거래대금 {amount/1e8:.2f}억 < 하한 {cfg["min_trading_amount_krw"]/1e8:.2f}억'
    change = entry.get('prev_change_pct')
    if change is not None and change >= cfg['max_prev_change_pct']:
        return f'전일 {change:+.1f}% 이미 급등'
    if not enriched:
        return ''
    if entry.get('warnings'):
        return '투자경고·거래정지 등 경고 있음'
    if cfg['require_flow'] and not (entry.get('flow_foreigner_1d', 0) > 0
                                    or entry.get('flow_institution_1d', 0) > 0):
        return '외국인·기관 순매수 없음'
    if cfg['require_news'] and entry.get('news_count', 0) == 0:
        return f'최근 {cfg["news_hours"]}시간 기사 없음'
    return ''


def premarket_scan(broker, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """장전 스캔. AI 에 넘길 후보와, 걸러진 이유를 함께 돌려준다."""
    cfg = _cfg(overrides)
    started = datetime.now(timezone.utc)
    pool = _pool(broker, cfg)

    # 거래대금이 없는 종목(테마에서만 온 것)은 먼저 값싼 조건으로 거른 뒤 조회한다.
    ordered = sorted(pool.values(),
                     key=lambda e: (len(e['origins']), e.get('trading_amount') or 0), reverse=True)
    rejected: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []
    for entry in ordered:
        why = _reject_reason(entry, cfg, enriched=False)
        if why:
            rejected.append({'symbol': entry['symbol'], 'name': entry.get('name', ''), 'reason': why})
        else:
            survivors.append(entry)
        if len(survivors) >= cfg['enrich_limit']:
            break

    missing = [e['symbol'] for e in survivors if not e.get('name')]
    if missing and hasattr(broker, 'names'):
        for symbol, name in broker.names(missing).items():
            if symbol in pool:
                pool[symbol]['name'] = name

    with ThreadPoolExecutor(max_workers=3,thread_name_prefix='scan-enrich') as executor:
        list(executor.map(lambda e:_enrich(broker,e,cfg),survivors))

    candidates: list[dict[str, Any]] = []
    for entry in survivors:
        why = _reject_reason(entry, cfg)
        if why:
            rejected.append({'symbol': entry['symbol'], 'name': entry.get('name', ''), 'reason': why})
            continue
        entry['score'] = _score(entry)
        candidates.append(entry)
    candidates.sort(key=lambda e: e['score'], reverse=True)
    # Preserve representatives of up to five themes before filling by score.
    # Otherwise one crowded theme can monopolize every AI candidate slot.
    shortlist, covered = [], set()
    for entry in candidates:
        names = {t['name'] for t in entry.get('themes', [])}
        if names - covered and len(covered) < 5 and len(shortlist) < cfg['max_candidates']:
            shortlist.append(entry)
            covered.update(names)
    for entry in candidates:
        if len(shortlist) >= cfg['max_candidates']:
            break
        if entry not in shortlist:
            shortlist.append(entry)
    candidates = shortlist
    # Expensive chart reads only for the shortlisted names. Missing data is explicit.
    charts=chart_batch(broker,[entry['symbol'] for entry in candidates],started)
    for entry in candidates:entry['technical']=charts[entry['symbol']]

    return {
        'scanned_at': started.isoformat(timespec='seconds'),
        'config': cfg,
        'pool_size': len(pool),
        'enriched': len(survivors),
        'candidates': candidates,
        'rejected': rejected[:60],
        'note': ('점수는 신호가 몇 겹 겹쳤는지의 순위이지 수익 예측이 아닙니다. '
                 '급등 추격 백테스트는 무작위와 구분되지 않았습니다.'),
    }


_chart_lock=threading.Lock()

def chart_batch(broker,symbols,now):
    symbols=list(dict.fromkeys(symbols))
    with ThreadPoolExecutor(max_workers=3,thread_name_prefix='chart-read') as pool:
        return dict(zip(symbols,pool.map(lambda s:chart_context(broker,s,now),symbols)))

def chart_context(broker,symbol,now):
    with _chart_lock:
        cache=getattr(broker,'_sqt_chart_cache',{})
        hit=cache.get(symbol)
        if hit and 0<=(now-datetime.fromisoformat(hit['as_of'])).total_seconds()<(2 if hit.get('errors') else 15):
            return copy.deepcopy(hit)
    result=_read_chart(broker,symbol,now)
    with _chart_lock:
        cache=getattr(broker,'_sqt_chart_cache',{})
        cache[symbol]=copy.deepcopy(result)
        if len(cache)>100:cache.pop(next(iter(cache)))
        try:broker._sqt_chart_cache=cache
        except AttributeError:pass
    return result

def _read_chart(broker, symbol: str, now: datetime) -> dict:
    result = {'as_of': now.isoformat(), 'daily': None, 'intraday': None, 'errors': []}
    instrument = Instrument(symbol=symbol, exchange='KRX')
    try:
        today = now.astimezone(timezone(timedelta(hours=9))).date().isoformat()
        with _chart_lock:
            daily_cache=getattr(broker,'_sqt_daily_cache',{})
            hit=daily_cache.get(symbol)
            valid=hit and hit['day']==today and 0<=(now-datetime.fromisoformat(hit['at'])).total_seconds()<3600
            if valid:result['daily']=copy.deepcopy(hit['data'])
        if not valid:
            bars = [b for b in broker.history(instrument, count=100) if b['date'] < today]
            result['daily'] = {'last_date': bars[-1]['date'], **analyze(bars)}
            with _chart_lock:
                daily_cache=getattr(broker,'_sqt_daily_cache',{})
                daily_cache[symbol]={'day':today,'at':now.isoformat(),'data':copy.deepcopy(result['daily'])}
                if len(daily_cache)>100:daily_cache.pop(next(iter(daily_cache)))
                try:broker._sqt_daily_cache=daily_cache
                except AttributeError:pass
    except Exception:
        result['errors'].append('daily_unavailable')
    try:
        minute_reader = getattr(broker, 'session_minutes', broker.minutes)
        result['intraday'] = intraday_features(minute_reader(instrument, count=1000), 'KRX', now,
                                               (result['daily'] or {}).get('price'))
    except Exception:
        result['errors'].append('intraday_unavailable')
    return result
