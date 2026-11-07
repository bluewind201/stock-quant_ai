"""Point-in-time news and event gates. Unknown information never earns bonus points."""
from .models import stamp


def relevant(item, symbol):
    return not item.get('symbols') or symbol in item['symbols']


def evaluate(evidence, events, symbol, at, config):
    at = stamp(at)
    candidates = [n for n in evidence if relevant(n, symbol)
            and stamp(n['available_at']) <= at and stamp(n['published_at']) <= at
            and 0 <= (at - stamp(n['published_at'])).total_seconds() <= config['news_max_hours'] * 3600]
    # URL-identical copies and syndicated headline copies must not multiply a signal.
    # Choose the most recent interpretation that actually existed at the simulation time.
    candidates.sort(key=lambda n: (stamp(n['assessed_at']) if n.get('assessed_at')
                    and stamp(n['assessed_at']) <= at else stamp(n['available_at'])), reverse=True)
    seen, urls, titles = [], set(), set()
    for item in candidates:
        url = item['url'].split('#')[0].split('?')[0].rstrip('/')
        title = ' '.join(item['title'].lower().split())
        if url in urls or title in titles:
            continue
        seen.append(item)
        urls.add(url)
        titles.add(title)
    reasons, blocked = [], False
    if config['news_required'] and not seen:
        blocked = True
        reasons.append('해당 시점에 확인 가능한 최신 뉴스가 없습니다.')
    company, macro = 0.0, 0.0
    assessed_ids, event_ids = [], []
    for n in seen:
        # A later human/agent interpretation must not leak into an earlier simulation.
        if n.get('assessed_at') and stamp(n['assessed_at']) <= at and n['confidence'] >= 0.5:
            age = (at - stamp(n['published_at'])).total_seconds() / 3600
            value = n['sentiment'] * n['confidence'] * (0.5 ** (age / 24))
            if n['source'] in ('fed', 'white_house', 'bls', 'bea'):
                macro += value * 10
            else:
                company += value * 20
            assessed_ids.append(n['id'])
        if n['source'] in ('fed', 'white_house') and n['impact'] == 'high':
            # Pause on newly observed policy information; do not infer direction from its title.
            if (at - stamp(n['available_at'])).total_seconds() <= config['event_after_minutes'] * 60:
                blocked = True
                reasons.append('중요 정책 발표 확인 직후: ' + n['title'])
    for event in events:
        if not relevant(event, symbol) or stamp(event['known_at']) > at or event['impact'] != 'high':
            continue
        delta = (at - stamp(event['scheduled_at'])).total_seconds() / 60
        if -config['event_before_minutes'] <= delta <= config['event_after_minutes']:
            blocked = True
            event_ids.append(event['id'])
            reasons.append('주요 경제·정책 일정 진입 제한: ' + event['title'])
    if company <= -8 or macro <= -5:
        blocked = True
        reasons.append('부정 뉴스·거시 평가가 진입 제한 수준입니다.')
    if seen and not assessed_ids:
        reasons.append('뉴스는 수집되었으나 방향 평가가 없어 뉴스 가점은 0입니다.')
    return {'news_score': round(max(-20, min(20, company)), 2),
            'macro_score': round(max(-10, min(10, macro)), 2), 'blocked': blocked,
            'reasons': reasons, 'evidence_ids': [n['id'] for n in seen],
            'assessed_ids': assessed_ids, 'event_ids': event_ids}
