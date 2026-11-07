import json
import threading
import time
import pytest
from sqt import ai_office as office
from sqt.subscription_ai import SubscriptionAI,AIError
from sqt.paper_models import PortfolioPlan
from tests.test_portfolio_317 import setup,NOW


def snapshot():
    return {'at':NOW.isoformat(),'day':'2026-10-21','held':[],
            'portfolio_policy':{'caps':{'short':20,'medium':30,'long':30}},
            'candidates':[{'symbol':'005930','name':'검증 회사','eligible_horizons':['long'],'news':[]}]}

@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(office.krnews,'stock_news',lambda *a,**k:[])
    monkeypatch.setattr(office.krnews,'search_news',lambda *a,**k:[])

def test_collection_provenance_dates_dedup_and_no_new_symbols(sources,monkeypatch):
    rows=[{'title':'실적 개선','published_at':NOW.isoformat(),'source':'test','url':'https://example.org/news'},
          {'title':'미래','published_at':'2099-01-01T00:00:00Z'},
          {'title':'시각 불명','published_at':''},{'title':'과거','published_at':'2020-01-01T00:00:00Z'}]
    monkeypatch.setattr(office.krnews,'stock_news',lambda *a,**k:rows)
    monkeypatch.setattr(office.krnews,'search_news',lambda *a,**k:rows[:1])
    original=snapshot();data=office.collect(original)
    assert len(data['office_evidence'])==1
    e=data['office_evidence'][0]
    assert e['symbols']==['005930'] and e['kind']=='news' and e['retrieved_at']
    counts=data['office_coverage']['excluded']
    assert all(counts[k]>0 for k in ('duplicate','future','undated','old'))
    assert 'office_evidence' not in original and len(data['candidates'])==1

class FakeOfficeAI(SubscriptionAI):
    def __init__(self):
        super().__init__();self.calls=[];self.allow=True;self.forge=False;self.fail=False
    def _guard(self):pass
    def _run(self,contract,prompt,timeout=180,label=''):
        assert self.lock.locked();self.calls.append((contract.__name__,prompt));self.last_usage={'provider':'fake','model':'fake-model'}
        if self.fail and len(self.calls)==2:raise AIError('fake timeout')
        if contract==PortfolioPlan:
            return {'summary':'장기 추세 검토','picks':[{'symbol':'005930','horizon':'long','max_entry_price':10000,
                'take_profit_pct':12,'stop_loss_pct':4,'weight_pct':10,'thesis':'확인된 일봉의 장기 추세가 유지됨',
                'counter_case':'시장의 추세 전환으로 손실 가능성 있음','invalidation':'보호 가격과 장기 추세 이탈'}]}
        if contract==office.RiskReview:
            return {'summary':'위험 조건 확인','approved_buys':['OUTSIDE'] if self.forge else ['005930'] if self.allow else [],'approved_exits':[]}
        return {'summary':'검증 보고서','findings':[{'symbol':'005930','positive':'장기 추세','negative':'변동성',
                 'evidence_ids':['fabricated' if self.forge else 'market:005930'],'missing':['재무 원문']}]}

def test_seven_real_role_calls_independent_analysts_and_risk_veto(sources):
    ai=FakeOfficeAI();events=[]
    result=ai.plan_office(snapshot(),lambda *event:events.append(event))
    assert len(ai.calls)==7 and len(result['picks'])==1
    assert all('office_reports' not in p for _,p in ai.calls[:4])
    assert 'office_reports' in ai.calls[4][1] and 'trader_plan' in ai.calls[6][1]
    assert ai.last_usage['office_calls']==7 and not ai.lock.locked()
    assert events[-1][:2]==('office','done')
    ai=FakeOfficeAI();ai.allow=False
    assert ai.plan_office(snapshot(),lambda *a:None)['picks']==[]

def test_failed_stage_and_fabricated_evidence_never_return_a_plan(sources):
    for attr in ('fail','forge'):
        ai=FakeOfficeAI();setattr(ai,attr,True);events=[]
        with pytest.raises(AIError):ai.plan_office(snapshot(),lambda *e:events.append(e))
        assert not ai.lock.locked() and events[-1][1]=='failed'
        assert not any(e[:2]==('office','done') for e in events)

def test_service_persists_progress_and_applies_only_completed_plan(tmp_path,monkeypatch,sources):
    service,state=setup(tmp_path,monkeypatch);service.ai=FakeOfficeAI()
    service.store.put(service.account_kind,state['id'],state)
    done=threading.Event();applied=[]
    def apply(s,result,now):applied.append(result);done.set()
    with service.lock:
        service._ai_call(state,'장전 판단',lambda s:None,snapshot(),apply,counts=False)
        service.store.put(service.account_kind,state['id'],state)
    assert done.wait(5)
    with service.lock:view=service.view(state['id'])
    assert view['office']['status']=='done' and applied[0]['picks']
    assert view['office']['stages']['risk']['output']['usage']['provider']=='fake'
    assert not view['positions'] and not view['trades']

def test_stopped_account_cancels_office_before_model_calls(tmp_path,monkeypatch,sources):
    service,state=setup(tmp_path,monkeypatch);service.ai=FakeOfficeAI()
    with service.lock:
        service._ai_call(state,'장전 판단',lambda s:None,snapshot(),lambda *a:pytest.fail('must not apply'),counts=False)
        state['status']='stopped';service.store.put(service.account_kind,state['id'],state)
    for _ in range(100):
        with service.lock:view=service.view(state['id'])
        if not view['ai']['pending']:break
        time.sleep(.01)
    assert not view['ai']['pending'] and not service.ai.calls
    assert view['office']['status'] in ('failed','cancelled')


def test_quota_switch_mid_office_keeps_all_roles_and_single_final_plan(tmp_path,monkeypatch,sources):
    from sqt.ai_router import AIRouter
    from sqt.subscription_ai import AIQuotaError
    from sqt.store import Store
    ai=AIRouter(Store(tmp_path/'router.db'));ai.provider='claude'
    monkeypatch.setattr(ai,'_guard',lambda:None)
    fake=FakeOfficeAI();fake.lock=ai.lock;calls={'claude':0,'codex':0}
    for key,child in ai.providers.items():
        monkeypatch.setattr(child,'status',lambda:{'authenticated':True})
        def run(contract,prompt,timeout,label,key=key,child=child):
            calls[key]+=1
            if key=='claude' and calls[key]==2:raise AIQuotaError('quota')
            answer=fake._run(contract,prompt,timeout,label)
            child.last_usage={'model':child.model}
            return answer
        monkeypatch.setattr(child,'_run',run)
    result=ai.plan_office(snapshot(),lambda *a:None)
    assert len(result['picks'])==1 and calls=={'claude':2,'codex':6}
    assert ai.provider=='claude' and ai.last_usage['office_calls']==7
    assert ai.last_usage['provider']=='codex'


def test_collection_includes_held_name_outside_candidates(sources,monkeypatch):
    snap=snapshot();snap['portfolio_policy']['held']=[{'symbol':'000660','name':'보유 회사','entry_at':NOW.isoformat()}]
    monkeypatch.setattr(office.krnews,'stock_news',lambda symbol,*a,**k:[{'title':symbol+' 뉴스','published_at':NOW.isoformat(),'source':'test'}])
    data=office.collect(snap)
    assert any(e['symbol']=='000660' for e in data['office_exit_evidence'])
    assert any(e['symbol']=='000660' for e in data['portfolio_policy']['exit_evidence'])
    assert [c['symbol'] for c in data['candidates']]==['005930']
