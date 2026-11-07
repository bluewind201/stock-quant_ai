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
request('login',{'password':'local-release-smoke-only-315'})
account=request('daytrade/accounts',{'mode':'paper','strategy':'top5_morning','auto_tune':True})
assert account['status']=='stopped'
cfg=account['config']
assert cfg['max_positions']==5 and cfg['entry_at']=='09:05' and cfg['force_close_at']=='11:50'
assert not cfg['auto_tune'] and not cfg['close_entry']
request('daytrade/accounts/'+account['id']+'/config',{'basket_exit_at':'11:45'})
saved=request('daytrade/accounts/'+account['id'])
assert saved['config']['force_close_at']=='11:45'
assert saved['status']=='stopped' and not saved['positions'] and not saved['trades']
print('Container health, login, saved TOP5 config, UID and excluded credentials: PASS')
