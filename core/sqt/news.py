"""Public official releases and a general news index. No arbitrary URL fetching."""
import calendar as timecalendar
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import feedparser
import requests
from bs4 import BeautifulSoup
from icalendar import Calendar

from .models import Evidence, Event, utcnow

FED_POLICY = 'https://www.federalreserve.gov/feeds/press_monetary.xml'
FED_SPEECH = 'https://www.federalreserve.gov/feeds/speeches.xml'
WHITE_HOUSE = 'https://www.whitehouse.gov/news/'
FOMC = 'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
BLS = 'https://www.bls.gov/schedule/news_release/bls.ics'
BEA = 'https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics'
BEA_NEWS = 'https://apps.bea.gov/rss/rss.xml'
GENERAL = 'https://news.google.com/rss/search?q=stock+market+Federal+Reserve+president+tariffs&hl=en-US&gl=US&ceid=US:en'
DOMAINS = {'fed':'www.federalreserve.gov', 'white_house':'www.whitehouse.gov',
           'bls':'www.bls.gov', 'bea':'www.bea.gov'}


def identifier(text):
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def fetch(url):
    with requests.get(url, headers={'User-Agent':'StockQuantTraining/1.0 (public research reader)'},
                      timeout=(5, 15), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(32768):
            size += len(chunk)
            if size > 3_000_000:
                raise ValueError('응답 크기 제한 초과')
            chunks.append(chunk)
        return b''.join(chunks)


def source_matches(source, url):
    if source in DOMAINS:
        return urlparse(url).hostname in (DOMAINS[source], DOMAINS[source].removeprefix('www.'),
                                         'apps.bea.gov' if source == 'bea' else DOMAINS[source])
    return url.startswith('https://')


def rss_evidence(body, source, now):
    parsed = feedparser.parse(body)
    if not parsed.entries:
        raise ValueError('RSS 항목이 없습니다. 공급원 형식을 확인하세요.')
    result = []
    for entry in parsed.entries[:40]:
        when = entry.get('published_parsed') or entry.get('updated_parsed')
        url = entry.get('link', '')
        if not when or not source_matches(source, url):
            continue
        published = datetime.fromtimestamp(timecalendar.timegm(when), timezone.utc)
        if published > now:
            continue
        title = BeautifulSoup(entry.get('title', ''), 'html.parser').get_text(' ', strip=True)[:500]
        result.append(Evidence(id=identifier(url), title=title, url=url, source=source,
            published_at=published, available_at=now,
            impact='high' if source == 'fed' else 'medium').model_dump(mode='json'))
    if not result:
        raise ValueError('유효한 발표 시각·원문 링크가 있는 RSS 항목이 없습니다.')
    return result


def white_house_evidence(body, now):
    soup = BeautifulSoup(body, 'html.parser')
    result, seen = [], set()
    for a in soup.select('h2 a[href], h3 a[href]'):
        url = a['href']
        match = re.search(r'/([12]\d{3})/(\d{2})/', url)
        if not match or not source_matches('white_house', url) or url in seen:
            continue
        seen.add(url)
        # List pages often show only a calendar date. Use first observation time rather than
        # inventing a precise release time and accidentally backdating availability.
        title = a.get_text(' ', strip=True)[:500]
        if not title:
            continue
        high = any(word in title.lower() for word in ('tariff','trade','sanction','econom','tax','energy','oil','rate'))
        result.append(Evidence(id=identifier(url), title=title, url=url, source='white_house',
            published_at=now, available_at=now, impact='high' if high else 'medium',
            assessment='목록에 정확한 발표시각 없음: 최초 수집시각을 사용. 대통령 발언·행정조치 원문 확인 필요.').model_dump(mode='json'))
    if not result:
        raise ValueError('백악관 목록을 해석하지 못했습니다. 수집 실패로 표시합니다.')
    return result[:30]


def ics_events(body, source, url, now):
    result = []
    for event in Calendar.from_ical(body).walk('VEVENT'):
        when = event.decoded('DTSTART', None)
        if not isinstance(when, datetime):
            continue  # no invented time for all-day entries
        if when.tzinfo is None:
            when = when.replace(tzinfo=ZoneInfo('America/New_York'))
        title = str(event.get('SUMMARY', ''))[:300]
        if when < now-timedelta(days=7) or when > now+timedelta(days=370):
            continue
        high = any(t in title.lower() for t in ('consumer price','employment situation','producer price',
                                               'gross domestic','personal income','pce','gdp'))
        result.append(Event(id=identifier(source+str(event.get('UID', title+when.isoformat()))),
            title=title, source=source, url=url, scheduled_at=when, known_at=now,
            impact='high' if high else 'medium').model_dump(mode='json'))
    if not result:
        raise ValueError('유효한 경제 발표 일정이 없습니다.')
    return result


def fomc_events(body, now):
    soup = BeautifulSoup(body, 'html.parser')
    months = {datetime(2000,m,1).strftime('%B'):m for m in range(1,13)}
    result = []
    for panel in soup.select('.panel'):
        heading = panel.select_one('.panel-heading')
        year_match = re.search(r'20\d{2}', heading.get_text() if heading else '')
        if not year_match:
            continue
        year = int(year_match.group())
        for meeting in panel.select('.fomc-meeting'):
            month_el, day_el = meeting.select_one('.fomc-meeting__month'), meeting.select_one('.fomc-meeting__date')
            if not month_el or not day_el:
                continue
            month_name = month_el.get_text(' ',strip=True).split('/')[-1].strip()
            days = re.findall(r'\d+', day_el.get_text())
            if month_name not in months or not days or len(days) > 2:
                continue
            when = datetime(year, months[month_name], int(days[-1]), 14, tzinfo=ZoneInfo('America/New_York'))
            if now-timedelta(days=7) <= when <= now+timedelta(days=370):
                result.append(Event(id='fomc-'+when.date().isoformat(), title='FOMC 금리 결정 (14:00 ET 예정, 원문 일정 확인)',
                    source='fed', url=FOMC, scheduled_at=when, known_at=now).model_dump(mode='json'))
    if not result:
        raise ValueError('FOMC 일정 해석 실패: 캘린더 수동 등록이 필요합니다.')
    return result


def fed_calendar_events(body,now,year,month,url):
    """Official monthly calendar, actual posted time (Eastern), including minutes/speeches."""
    soup=BeautifulSoup(body,'html.parser');result=[]
    for row in soup.select('.panel-body > .row'):
        clock,detail,dates=(row.select_one('.col-xs-'+str(n)) for n in (2,7,3))
        if not all((clock,detail,dates)):continue
        match=re.fullmatch(r'(\d{1,2}):(\d{2})\s*([ap])\.m\.',clock.get_text(' ',strip=True),re.I)
        if not match:continue
        h,m,ap=match.groups();hour=int(h)%12+(12 if ap.lower()=='p' else 0)
        title=detail.get_text(' ',strip=True)[:300]
        text=dates.get_text(' ',strip=True)
        if not re.fullmatch(r'\d+(?:\s*,\s*\d+)*',text):continue
        for day in map(int,re.findall(r'\d+',text)):
            when=datetime(year,month,day,hour,int(m),tzinfo=ZoneInfo('America/New_York'))
            if not now-timedelta(days=2)<=when<=now+timedelta(days=60):continue
            result.append(Event(id=identifier(url+title+when.isoformat()),title=title,source='fed',
                url=url,known_at=now,scheduled_at=when,
                impact='high' if any(x in title.lower() for x in ('fomc','monetary','economic outlook','chair','beige')) else 'medium').model_dump(mode='json'))
    if not result:raise ValueError('연준 월간 일정의 확정 시각을 찾지 못했습니다.')
    return result


def collect(store):
    now = utcnow()
    jobs = [
        ('fed_policy', FED_POLICY, 'evidence', lambda b:rss_evidence(b,'fed',now)),
        ('fed_speech', FED_SPEECH, 'evidence', lambda b:rss_evidence(b,'fed',now)),
        ('white_house', WHITE_HOUSE, 'evidence', lambda b:white_house_evidence(b,now)),
        ('general_news', GENERAL, 'evidence', lambda b:rss_evidence(b,'news',now)),
        ('bls_calendar', BLS, 'events', lambda b:ics_events(b,'bls',BLS,now)),
        ('fomc_calendar', FOMC, 'events', lambda b:fomc_events(b,now)),
        ('bea_calendar', BEA, 'events', lambda b:ics_events(b,'bea',BEA,now)),
        ('bea_news', BEA_NEWS, 'evidence', lambda b:rss_evidence(b,'bea',now)),
    ]
    for month in ((now.year,now.month),(now.year+now.month//12,now.month%12+1)):
        year,number=month
        url=f'https://www.federalreserve.gov/newsevents/{year}-{datetime(year,number,1).strftime("%B").lower()}.htm'
        jobs.append((f'fed_events_{year}_{number}',url,'events',
                     lambda b,y=year,m=number,u=url:fed_calendar_events(b,now,y,m,u)))
    def run(job):
        name,url,kind,parse=job
        health = {'id':name, 'url':url, 'checked_at':now.isoformat(), 'ok':False, 'count':0}
        try:
            items = parse(fetch(url))
            with store.connect(write=True) as con:
                for item in items:
                    try:
                        old = store.get(kind,item['id'],con)
                    except KeyError:
                        old = None
                    if old and kind == 'evidence':
                        continue  # retain earliest seen time and any separately supplied assessment
                    if old and kind == 'events':
                        if old['scheduled_at'] == item['scheduled_at']:
                            item['known_at'] = old['known_at']
                        else:
                            # Preserve old schedule revision for reproducible historical sessions.
                            item['id'] += '-'+identifier(item['scheduled_at'])[:8]
                            item['revision_of']=old.get('revision_of',old['id'])
                            try:item['known_at']=store.get(kind,item['id'],con)['known_at']
                            except KeyError:pass
                    store.put(kind,item['id'],item,con)
            health.update(ok=True,count=len(items))
        except Exception as error:
            health['error'] = type(error).__name__+': 공급원에 연결하지 못했거나 응답 형식이 바뀌었습니다.'
        store.put('health',name,health)
        return health
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='official-news') as pool:
        return list(pool.map(run,jobs))
