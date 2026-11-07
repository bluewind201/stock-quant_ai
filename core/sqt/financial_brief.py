"""Verified schedules and separately labelled news attention items. No AI guesswork."""
from datetime import timedelta,timezone
from .models import stamp,utcnow
from . import notify
KST=timezone(timedelta(hours=9))

def build(store,now=None):
    now=now or utcnow();local=now.astimezone(KST)
    start=local.replace(hour=0,minute=0,second=0,microsecond=0)
    end=start+timedelta(days=1,hours=12)
    rows={}
    for e in sorted(store.all('events'),key=lambda e:e.get('known_at','')):
        try:
            if e.get('source')=='demo' or stamp(e['known_at'])>now:continue
            when=stamp(e['scheduled_at'])
        except (KeyError,ValueError):continue
        # Revisions replace the original schedule for current operation.
        key=e.get('revision_of',e['id'])
        if '-revision-' not in key and e['source'] in ('bls','bea'):
            key=e['id'].split('-')[0]
        rows[key]=e
    today=[];upcoming=[]
    for e in rows.values():
        when=stamp(e['scheduled_at'])
        if start<=when<end:today.append(e)
        if now<=when<now+timedelta(days=7):upcoming.append(e)
    health={r['id']:r for r in store.all('health')}
    missing=[]
    for source in ('bls_calendar','bea_calendar','fomc_calendar',f'fed_events_{now.year}_{now.month}'):
        row=health.get(source,{})
        try:fresh=0<=(now-stamp(row['checked_at'])).total_seconds()<=18*3600
        except (KeyError,ValueError):fresh=False
        if not row.get('ok') or not fresh:missing.append(source)
    evidence=[]
    for e in store.all('evidence'):
        try:
            if e.get('source')!='demo' and now-timedelta(hours=24)<=stamp(e['available_at'])<=now:
                evidence.append(e)
        except (KeyError,ValueError):continue
    evidence.sort(key=lambda e:(e.get('source') in ('white_house','fed'),e['available_at']),reverse=True)
    for r in store.all('market_attention'):
        try:
            if 0<=(now-stamp(r['at'])).total_seconds()<3600:evidence=r.get('items',[])[:2]+evidence
        except (KeyError,ValueError):pass
    return {'day':local.date().isoformat(),'as_of':now.isoformat(),
        'events':sorted(sorted(today,key=lambda e:(e.get('impact')!='high',e['scheduled_at']))[:6],key=lambda e:e['scheduled_at']),
        'upcoming':sorted(upcoming,key=lambda e:e['scheduled_at'])[:12],
        'attention':[{'title':e['title'],'source':e['source'],'url':e['url']} for e in evidence[:4]],
        'missing_sources':missing,'note':'확인한 공식 일정만 포함. 보도 제목은 확정 일정과 구분. 미국장 일정은 다음날 정오까지 포함.'}

def send_due(store,now=None):
    if not notify.configured() or not notify.allowed('detail'):return
    now=now or utcnow();local=now.astimezone(KST);hh=local.strftime('%H:%M')
    slot='morning' if '07:30'<=hh<'12:00' else 'us' if '20:30'<=hh<'23:59' else None
    if not slot:return
    id=local.date().isoformat()+'-'+slot
    try:store.get('financial_brief_sent',id);return
    except KeyError:pass
    brief=build(store,now)
    if notify.send(notify.financial_message(brief,slot)):
        store.put('financial_brief_sent',id,{'id':id,'at':now.isoformat()})
