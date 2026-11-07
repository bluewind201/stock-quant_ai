"""Evidence-linked strategy memory. Never writes engine code or account limits."""
import copy
import hashlib
import json
import uuid
from datetime import timedelta, timezone
from .models import stamp

KST=timezone(timedelta(hours=9))
PROMPT_VERSION='321-office-review-v1'

def ensure(state):
    return state.setdefault('learning',{'active_lessons':[],'proposals':[], 'changes':[],
                                      'observations':{},'trial':None})

def policy(state):
    run=ensure(state)
    payload={'config':state['config'],'lessons':run['active_lessons'],'prompt':PROMPT_VERSION,'ai':run.get('ai_identity')}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]

def record(store,state,kind,snapshot,now):
    run=ensure(state);id=uuid.uuid4().hex
    version=policy(state)
    store.put('learning_policies',version,{'id':version,'config':copy.deepcopy(state['config']),
              'lessons':list(run['active_lessons']),'prompt_version':PROMPT_VERSION})
    store.put('decision_evidence',id,{'id':id,'account_id':state['id'],'kind':kind,
        'day':now.astimezone(KST).date().isoformat(),'at':now.isoformat(),
        'policy_version':version,'snapshot':copy.deepcopy(snapshot)})
    run['last_decision_id']=id
    run['current_version']=version
    run['decision_index']=(run.get('decision_index',[])+[id])[-60:]
    return id

def result(store,id,result,error,usage,seconds,now):
    store.put('decision_results',id,{'id':id,'at':now.isoformat(),'result':result,
        'error':error,'usage':dict(usage),'seconds':round(seconds,2)})

def rounds(state,version=None):
    groups={}
    for t in state.get('trades',[]):
        if t.get('side')!='sell' or not t.get('policy_version'):continue
        if version and t['policy_version']!=version:continue
        # Partial fills of one exit remain one sample, not multiple wins.
        key=(t['symbol'],t.get('entry_at') or t.get('decision_id') or t['at'],t['policy_version'])
        row=groups.setdefault(key,{'day':stamp(t['at']).astimezone(KST).date().isoformat(),
                                  'pnl':0,'basis':0,'decision_id':t.get('decision_id')})
        row['pnl']+=t.get('pnl',0)
        if t.get('cost_basis'):row['basis']+=t['cost_basis']
    return [r for key,r in groups.items() if not (state.get('positions',{}).get(key[0],{}).get('entry_at')==key[1]
            and state['positions'][key[0]].get('qty',0)>0)]

def metrics(rows):
    values=[r['pnl']/r['basis']*100 for r in rows if r['basis']>0]
    return {'trades':len(values),'days':len({r['day'] for r in rows if r['basis']>0}),
            'pnl':round(sum(r['pnl'] for r in rows),2),
            'mean_return_pct':sum(values)/len(values) if values else None}

def context(store,state):
    run=ensure(state)
    rows=[]
    for id in run.get('decision_index',[])[-30:]:
        try:rows.append(store.get('decision_evidence',id))
        except KeyError:pass
    rows=sorted(rows,key=lambda r:r['at'])[-30:]
    closed=rounds(state)
    return {'active_lessons':run['active_lessons'],'policy_version':policy(state),
        'metrics':metrics(closed),'evidence':[{'id':r['id'],'at':r['at'],'kind':r['kind'],
        'policy_version':r['policy_version'],
        'closed_trades':sum(1 for t in closed if t['decision_id']==r['id'])} for r in rows],
        'proposals':run['proposals'][-5:],'trial':run.get('trial'),
        'note':'evidence_ids는 제공된 실제 결정 ID만 사용. 가정 수익은 실현손익과 분리.'}

def propose(store,state,review,now):
    run=ensure(state)
    changes=review.get('improvements') or [{'lesson':x,'evidence_ids':[]} for x in review.get('lessons',[])]
    closed=rounds(state)
    for change in changes[:3]:
        lesson=str(change.get('lesson','')).strip()[:400]
        ids=list(dict.fromkeys(change.get('evidence_ids',[])))[:10]
        if not lesson or any(p['lesson']==lesson and p['status'] not in ('rejected','rolled_back') for p in run['proposals']):continue
        valid=True
        for id in ids:
            try:
                evidence=store.get('decision_evidence',id)
                valid &= evidence['account_id']==state['id'] and stamp(evidence['at'])<=now
            except KeyError:valid=False
        supported=[r for r in closed if r['decision_id'] in ids]
        ready=valid and len(supported)>=3 and len({r['day'] for r in supported})>=2
        proposal={'id':uuid.uuid4().hex,'lesson':lesson,'evidence_ids':ids,
            'created_at':now.isoformat(),'day':now.astimezone(KST).date().isoformat(),
            'status':'ready' if ready else 'needs_evidence' if valid else 'rejected',
            'reason':'3건 이상·2거래일 이상 실제 청산 근거' if ready else '근거 표본 부족' if valid else '근거 ID 검증 실패'}
        run['proposals'].append(proposal)
    run['proposals']=run['proposals'][-60:]

def advance(state,now):
    """One prospectively measured prompt trial at a time, at the next trade day."""
    run=ensure(state)
    if not state['config'].get('self_improve',True):return
    trial=run.get('trial')
    if trial:
        if policy(state)!=trial['version']:
            for p in run['proposals']:
                if p['id']==trial['proposal_id']:p['status']='interrupted'
            run['changes'].append(dict(trial,status='interrupted',evaluated_at=now.isoformat()))
            run['trial']=None
            return
        after=metrics(rounds(state,trial['version']))
        trial['after']=after
        if after['trades']>=10 and after['days']>=3:
            worse=after['pnl']<0 and after['mean_return_pct']<trial['baseline']['mean_return_pct']-.3
            status='rolled_back' if worse else 'retained'
            if worse:run['active_lessons']=trial['previous_lessons']
            for p in run['proposals']:
                if p['id']==trial['proposal_id']:p['status']=status
            run['changes'].append(dict(trial,status=status,evaluated_at=now.isoformat()))
            run['trial']=None
        else:return
    version=policy(state)
    baseline=metrics(rounds(state,version)[-30:])
    if baseline['trades']<10 or baseline['days']<3:return
    for p in reversed(run['proposals']):
        if p['status']!='ready' or p['day']>=now.astimezone(KST).date().isoformat():continue
        previous=list(run['active_lessons'])
        run['active_lessons']=(previous+[p['lesson']])[-5:]
        p['status']='trial'
        run['trial']={'proposal_id':p['id'],'version':policy(state),'baseline_version':version,
            'baseline':baseline,'previous_lessons':previous,'started_at':now.isoformat(),
            'note':'전후 비교는 시장 차이를 포함하며 인과적 수익 개선의 증명이 아님'}
        break
    run['changes']=run['changes'][-30:]

def observe(state,now):
    """Contemporaneous candidate quotes; hypothetical mark moves are not profits."""
    run=ensure(state)
    for c in (state.get('scan') or {}).get('candidates',[])[:30]:
        q=state.get('quotes',{}).get(c['symbol'])
        try:
            if not q or any(not 0<q[k] for k in ('bid','ask')) or q['bid']>q['ask']:continue
            if any(not 0<=(now-stamp(q[k])).total_seconds()<=30 for k in ('timestamp','book_timestamp')):continue
        except (KeyError,ValueError,TypeError):continue
        row=run['observations'].setdefault(c['symbol'],{'symbol':c['symbol'],'first_at':now.isoformat(),
            'first_ask':q['ask'],'high_bid':q['bid'],'low_bid':q['bid']})
        row.update(last_at=now.isoformat(),last_bid=q['bid'],high_bid=max(row['high_bid'],q['bid']),
                   low_bid=min(row['low_bid'],q['bid']),mark_move_pct=(q['bid']/row['first_ask']-1)*100)
