"""Background discovery and observed opening-volume history. No orders or AI."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta, timezone
from threading import Lock
from . import scanner, krnews, news_quality
from .technical import intraday_features
from .models import Instrument, stamp, utcnow

KST = timezone(timedelta(hours=9))


class InPlayFeed:
    def __init__(self, store, broker):
        self.store, self.broker = store, broker
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='in-play')
        self.jobs, self.last = {}, {}
        self.lock = Lock()
        self.rank_cache = None
        self.daily_cache = {}

    def poll(self, account, now, config, candidates):
        """Never waits for network work; completion carries the original trade day."""
        with self.lock:
            job = self.jobs.get(account)
            result = None
            if job and job.done():
                del self.jobs[account]
                try:
                    result = job.result()
                except Exception as exc:
                    result = {'error': type(exc).__name__, 'candidates': []}
            last = self.last.get(account)
            if account not in self.jobs and (not last or (now-last).total_seconds() >= 30):
                self.last[account] = now
                self.jobs[account] = self.pool.submit(self.collect, now, dict(config), candidates)
            return result

    def collect(self, now, config, existing):
        day = now.astimezone(KST).date().isoformat()
        rows = {c['symbol']: dict(c) for c in existing}
        ranked, errors = {}, []
        cache=self.rank_cache
        key=tuple(config[k] for k in ('scan_ranking_count','min_trading_amount_krw','max_prev_change_pct'))
        refresh=not cache or cache['day']!=day or cache.get('config')!=key or (now-stamp(cache['at'])).total_seconds()>=300
        if not refresh:ranked=dict(cache['rows'])
        for kind in ('MARKET_TRADING_AMOUNT', 'TOP_GAINERS') if refresh else ():
            try:
                for r in self.broker.rankings(kind, count=config['scan_ranking_count']):
                    if r['last_price'] <= 0 or r['trading_amount'] < config['min_trading_amount_krw']:
                        continue
                    if r['change_pct'] >= config['max_prev_change_pct']:
                        continue
                    ranked.setdefault(r['symbol'], r)
            except Exception as exc:
                errors.append(kind+':'+type(exc).__name__)
        if refresh:self.rank_cache={'day':day,'at':now.isoformat(),'rows':dict(ranked),'config':key}
        new = 0
        for r in sorted(ranked.values(), key=lambda r: r['trading_amount'], reverse=True):
            symbol = r['symbol']
            if symbol not in rows:
                if new >= 3:
                    continue
                e = {'symbol':symbol, 'origins':['in_play'], 'themes':[], 'name':symbol}
                try:
                    if self.broker.warnings(symbol):
                        continue
                    if hasattr(self.broker, 'names'):
                        e['name'] = self.broker.names([symbol]).get(symbol, symbol)
                    scanner._enrich(self.broker, e, scanner._cfg(config))
                    if scanner._reject_reason(e, scanner._cfg(config)):
                        continue
                except Exception as exc:
                    errors.append(symbol+':eligibility_'+type(exc).__name__)
                    continue
                rows[symbol] = e
                new += 1
            rows[symbol].update(last_price=r['last_price'], trading_amount=r['trading_amount'],
                                today_change_pct=r['change_pct'])
        try:
            market = self.broker.market_context() if hasattr(self.broker, 'market_context') else {}
        except Exception as exc:
            market={};errors.append('market:'+type(exc).__name__)
        indices = [r for r in market.get('indices_and_rates', []) if r.get('symbol') == 'KOSPI'
                   and not r.get('stale') and r.get('change_pct') is not None]
        selected = sorted(rows.values(), key=lambda c: c.get('trading_amount', 0), reverse=True)
        results = []
        for e in selected[:min(15, config['max_candidates'])]:
            acquired = utcnow()
            cached=self.daily_cache.get((day,e['symbol']))
            if cached:
                chart={'as_of':acquired.isoformat(),'daily':cached,'intraday':None,'errors':[]}
                try:
                    instrument=Instrument(symbol=e['symbol'],exchange='KRX')
                    reader=getattr(self.broker,'session_minutes',self.broker.minutes)
                    chart['intraday']=intraday_features(reader(instrument,count=1000),'KRX',acquired,cached['price'])
                except Exception:
                    chart['errors'].append('intraday_unavailable')
            else:
                chart = scanner.chart_context(self.broker, e['symbol'], acquired)
                if chart.get('daily'):self.daily_cache[(day,e['symbol'])]=chart['daily']
            self.daily_cache={k:v for k,v in self.daily_cache.items() if k[0]==day}
            articles=krnews.stock_news(e['symbol'],5,ttl=300)
            selected_news=[]
            for article in articles:
                if news_quality.relevant(article,e.get('name',''),e['symbol']) and not news_quality.duplicate(article,[n['title'] for n in selected_news]):
                    selected_news.append(article)
            e['news']=selected_news
            try:
                quote = self.broker.quotes([Instrument(symbol=e['symbol'], exchange='KRX')]).get(e['symbol'])
            except Exception as exc:
                quote=None;errors.append(e['symbol']+':quotes_'+type(exc).__name__)
            intraday = chart.get('intraday') or {}
            opening = intraday.get('opening_5m_volume')
            try:
                history = self.store.get('in_play_volume', e['symbol'])
            except KeyError:
                history = {}
            past = [v for d, v in sorted(history.items()) if d < day][-14:]
            rvol = opening/(sum(past)/len(past)) if opening and len(past) >= 3 and sum(past)>0 else None
            if opening and acquired.astimezone(KST).date().isoformat() == day:
                history[day] = opening
                self.store.put('in_play_volume', e['symbol'], dict(sorted(history.items())[-30:]))
            relative = None
            base=(chart.get('daily') or {}).get('price')
            change=(quote['price']/base-1)*100 if quote and base else None
            if indices and quote and change is not None:
                if abs((stamp(quote['timestamp'])-stamp(indices[0]['timestamp'])).total_seconds()) <= 120:
                    relative = change-indices[0]['change_pct']
            e.update(technical=chart, quote=quote, acquired_at=acquired.isoformat(),
                     in_play={'opening_rvol':rvol, 'rvol_samples':len(past), 'relative_strength_pct':relative,
                              'volume_basis':'opening_5m_vs_prior_days' if rvol is not None else 'intraday_bar_expansion_only'})
            results.append(e)
        results.sort(key=lambda e:(e['in_play']['opening_rvol'] or 0,
                                  e['in_play']['relative_strength_pct'] or 0, e.get('trading_amount',0)), reverse=True)
        return {'day':day, 'at':utcnow().isoformat(), 'candidates':results, 'errors':errors,'market_context':market}
