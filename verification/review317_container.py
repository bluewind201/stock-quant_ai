"""Run only inside the isolated, network-none release smoke container."""
import json
import os
import time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

assert os.getuid()==10001
assert not list(Path('/app').rglob('.env*'))
assert not Path('/app/verification').exists()
assert not list(Path('/home/stock').rglob('auth.json'))
opener=build_opener(HTTPCookieProcessor(CookieJar()))
def request(path,body=None):
    data=None if body is None else json.dumps(body).encode()
    return json.load(opener.open(Request('http://127.0.0.1:8080/api/'+path,
                         data=data,headers={'Content-Type':'application/json'}),timeout=10))
for attempt in range(20):
    try:
        health=request('health');break
    except OSError:time.sleep(1)
else:raise RuntimeError('Health check did not become ready')
assert health['ok'] and not health['live_orders']
request('login',{'password':'local-release-smoke-only-317'})
assert request('daytrade/status')['defaults']['strategy']=='portfolio'
account=request('daytrade/accounts',{'mode':'paper','strategy':'portfolio','auto_tune':True,'max_positions':8,'position_pct':10})
assert account['status']=='stopped'
cfg=account['config']
assert cfg['max_positions']==8 and cfg['entry_at']=='09:05' and cfg['force_close_at']=='15:30'
assert not cfg['auto_tune'] and not cfg['close_entry']
assert cfg['self_improve']
brief=request('financial-brief')
assert not brief['events'] and brief['missing_sources'] and brief['as_of']
request('daytrade/accounts/'+account['id']+'/config',dict(cfg,portfolio_style='long',portfolio_core=['005930','069500']))
saved=request('daytrade/accounts/'+account['id'])
assert saved['config']['portfolio_style']=='long' and saved['config']['portfolio_core']==['005930','069500']
assert saved['status']=='stopped' and not saved['positions'] and not saved['trades']
html=opener.open('http://127.0.0.1:8080/',timeout=10).read().decode()
assert '/static/portfolio.js' in html and 'data-tab="paper"' not in html
assert opener.open('http://127.0.0.1:8080/static/portfolio.js',timeout=10).status==200
print('Container health, login, portfolio defaults/config persistence, financial brief, UI assets, UID and excluded credentials: PASS')
