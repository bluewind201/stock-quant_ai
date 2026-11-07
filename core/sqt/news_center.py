"""뉴스 한곳에 모으기. 출처별로 흩어진 기사를 분류·중복 제거해 화면 하나로 보여준다.

여기서 주문이나 AI 호출은 하지 않는다. 번역은 이미 저장된 결과만 붙인다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import translate

CATEGORIES = {
    'holding': '보유 종목',
    'candidate': '관심 후보',
    'market': '국내 증시',
    'policy': '금리·정책',
    'global': '미국·해외',
    'theme': '테마',
}
PRIORITY = list(CATEGORIES)
OFFICIAL = {'fed': '연준', 'white_house': '백악관', 'bls': '미 노동통계국', 'bea': '미 경제분석국'}
SOURCE_NAMES = dict(OFFICIAL, news='해외 언론 색인', company='기업', manual='직접 등록')
MACRO_HINTS = [('한국은행', 'policy'), ('금리', 'policy'), ('환율', 'policy'), ('정책', 'policy'),
               ('연준', 'global'), ('미국', 'global'), ('FOMC', 'global'), ('CPI', 'global'), ('관세', 'global')]


def _time(value):
    try:
        t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _norm(title: str) -> str:
    return ' '.join(str(title or '').lower().split())


def _origin_category(origin: str) -> str:
    origin = str(origin or '')
    if origin == 'official_feed':
        return 'global'
    if origin == 'market':
        return 'market'
    for hint, category in MACRO_HINTS:
        if hint in origin:
            return category
    return 'theme' if origin and ':' not in origin else 'market'


def build(store, account: dict | None, kr_rows: list[dict], hours: int = 72, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(hours=hours)
    account = account or {}
    holdings = {s: (p.get('name') or s) for s, p in (account.get('positions') or {}).items()}
    candidates = {c['symbol']: (c.get('name') or c['symbol'])
                  for c in ((account.get('scan') or {}).get('candidates') or []) if c.get('symbol')}
    names = {**candidates, **holdings}
    office = account.get('office') or {}
    stages = office.get('stages') or {}
    cited = set()
    for stage in stages.values():
        output = stage.get('output') or {}
        for row in (output.get('findings') or []) + (output.get('reasons') or []):
            cited.update(row.get('evidence_ids') or [])

    merged: dict[str, dict] = {}

    def add(title, url, source, published, categories, symbols=(), body='', evidence_id='', official=False, impact=''):
        title = ' '.join(str(title or '').split())
        t = _time(published)
        if not title or t is None or t < oldest or t > now + timedelta(minutes=5):
            return
        key = _norm(title)
        row = merged.get(key)
        if row is None:
            row = merged[key] = {'title': title[:350], 'url': str(url or '') if str(url or '').startswith('https://') else '',
                                 'source': str(source or '')[:120], 'published_at': t.isoformat(),
                                 'categories': [], 'symbols': [], 'body': str(body or '')[:400],
                                 'cited': False, 'official': bool(official), 'important': impact == 'high'}
        for c in categories:
            if c in CATEGORIES and c not in row['categories']:
                row['categories'].append(c)
        for s in symbols:
            if s and s not in row['symbols']:
                row['symbols'].append(s)
        if evidence_id and evidence_id in cited:
            row['cited'] = True
        if impact == 'high':
            row['important'] = True
        if not row['url'] and str(url or '').startswith('https://'):
            row['url'] = str(url)

    def stock_categories(symbols):
        found = []
        if any(s in holdings for s in symbols):
            found.append('holding')
        if any(s in candidates for s in symbols):
            found.append('candidate')
        return found

    for row in kr_rows or []:
        add(row.get('title'), row.get('url'), row.get('source') or '국내 언론', row.get('published_at'),
            [row.get('topic') if row.get('topic') in CATEGORIES else 'market'])
    for c in ((account.get('scan') or {}).get('candidates') or []):
        for n in c.get('news') or []:
            add(n.get('title'), n.get('url'), n.get('source') or '종목 뉴스', n.get('published_at'),
                stock_categories([c['symbol']]) or ['candidate'], [c['symbol']], n.get('body', ''))
    collected = ((stages.get('collect') or {}).get('output') or {}).get('evidence') or []
    for e in collected:
        symbols = e.get('symbols') or []
        categories = stock_categories(symbols) if symbols else [_origin_category(e.get('origin'))]
        add(e.get('title'), e.get('url'), e.get('source'), e.get('published_at'), categories or ['candidate'],
            symbols, e.get('body', ''), e.get('id', ''), e.get('kind') == 'official')
    for e in store.all('evidence'):
        if e.get('source') == 'demo':
            continue
        add(e.get('title'), e.get('url'), SOURCE_NAMES.get(e.get('source'), e.get('source')),
            e.get('published_at') or e.get('available_at'), ['global'], body=e.get('assessment', ''),
            official=e.get('source') in OFFICIAL, impact=e.get('impact', ''))

    rows = sorted(merged.values(), key=lambda r: r['published_at'], reverse=True)[:400]
    for row in rows:
        row['categories'].sort(key=PRIORITY.index)
        row['category'] = row['categories'][0] if row['categories'] else 'market'
        row['names'] = [names.get(s, s) for s in row['symbols']]
    table = translate.cache(store)
    translate.apply(rows, table)
    counts = {c: sum(1 for r in rows if c in r['categories']) for c in CATEGORIES}
    english = [r for r in rows if r.get('english')]
    return {'as_of': now.isoformat(), 'hours': hours, 'rows': rows, 'categories': CATEGORIES, 'counts': counts,
            'cited': sum(1 for r in rows if r['cited']),
            'translation': dict(translate.status(store), english=len(english),
                                waiting=sum(1 for r in english if not r.get('title_ko'))),
            'note': '분류는 출처·종목 연결 기준입니다. 기사 수나 AI 인용이 수익 가능성을 뜻하지는 않습니다.'}


def pending_titles(store, account: dict | None) -> list[str]:
    """번역 대기 제목 후보. 최근 공식 발표와 AI 오피스 수집 기사를 우선한다."""
    titles = [e.get('title') for e in sorted(store.all('evidence'), key=lambda e: e.get('available_at', ''), reverse=True)[:120]]
    office = (account or {}).get('office') or {}
    collected = (((office.get('stages') or {}).get('collect') or {}).get('output') or {}).get('evidence') or []
    titles += [e.get('title') for e in collected]
    titles += [e.get('title') for e in sorted(store.all('events'), key=lambda e: e.get('scheduled_at', ''), reverse=True)[:60]]
    return [t for t in titles if t]
