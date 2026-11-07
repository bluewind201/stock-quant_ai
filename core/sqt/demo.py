"""Synthetic prices and policy events for offline software training, never real returns."""
import math
import random
from datetime import timedelta

from .calendar import calendar, session


def build_demo():
    instrument = {'symbol':'DEMO','exchange':'NASDAQ','name':'학습용 가상 주식','currency':'USD'}
    days = calendar('NASDAQ').sessions_in_range('2025-01-02','2026-06-30')[:300]
    rng, price, bars = random.Random(52), 100.0, []
    news, events = [], []
    for i, day in enumerate(days):
        op = price * (1+rng.gauss(0,0.003))
        trend = 0.002 if i < 110 or i > 200 else -0.0018
        price = max(10, op*(1+trend+math.sin(i/7)*0.004+rng.gauss(0,0.009)))
        date = day.strftime('%Y-%m-%d')
        bars.append({'date':date, 'open':round(op,4), 'high':round(max(op,price)*(1+rng.uniform(.003,.018)),4),
                     'low':round(min(op,price)*(1-rng.uniform(.003,.018)),4),
                     'close':round(price,4), 'volume':rng.randint(700000,1600000)})
        opened, closed = session('NASDAQ',date)
        published = closed-timedelta(minutes=45)
        news.append({'id':f'demo-news-{i}', 'title':'[가상 시나리오] 실적·정책 관찰 자료',
            'url':'https://example.com/synthetic-news/'+str(i),'source':'demo',
            'published_at':published.isoformat(),'available_at':published.isoformat(),
            'assessed_at':(published+timedelta(minutes=5)).isoformat(),'symbols':['DEMO'],
            'sentiment':0.4 if trend>0 else -0.4,'confidence':0.7,'impact':'medium',
            'assessment':'프로그램 동작 확인용으로 생성한 평가입니다. 실제 뉴스가 아닙니다.'})
        if i in (90,150,220):
            events.append({'id':f'demo-event-{i}','title':'[가상 시나리오] 중요 경제지표 발표',
                'source':'demo','url':'https://example.com/synthetic-event/'+str(i),
                'scheduled_at':(opened-timedelta(minutes=60)).isoformat(),
                'known_at':(opened-timedelta(days=7)).isoformat(),'impact':'high','symbols':['DEMO'],
                'actual':None,'consensus':None,'previous':None,'unit':''})
    return {'id':'demo','instrument':instrument,'bars':bars,'source':'합성 데이터 · 실제 주가 아님',
            'demo':True,'adjusted':True}, news, events
