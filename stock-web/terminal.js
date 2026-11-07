'use strict';
// Presentation only. Existing account, order and AI controls keep their handlers.
const terminalViews={market:'차트 · 성과',portfolio:'기간별 포트폴리오',activity:'매매 · 기록',analysis:'분석 · 복기'};
let terminalView='market',terminalAuth=null,terminalAuthAt=0,terminalAuthBusy=false,terminalAuthFailed=false;
const terminalMenu=document.querySelector('nav[aria-label="주 메뉴"]');
terminalMenu.classList.add('terminal-sidebar');
terminalMenu.insertAdjacentHTML('afterbegin','<div class="sidebar-label">메뉴</div>');
const terminalNavLabels={home:['01','홈'],office:['02','AI 회의실'],news:['03','뉴스'],dashboard:['04','상세 분석'],daytrade:['05','투자 운용'],settings:['06','연결 · 설정']};
terminalMenu.querySelectorAll('[data-tab]').forEach(b=>{const v=terminalNavLabels[b.dataset.tab];if(v)b.innerHTML=`<span>${v[0]}</span>${v[1]}`;});
terminalMenu.insertAdjacentHTML('beforeend','<div class="sidebar-bottom"><i></i> AI 투자 관리<br><small>단기 · 중기 · 장기<br>AI 판단과 실제 체결은 따로 표시합니다.</small><button data-goto="settings" class="quiet small">연결 관리 ↗</button></div>');
document.querySelector('main').insertAdjacentHTML('afterbegin',`<section class="terminal-connections" aria-label="AI 연결 상태"><div class="connection-heading"><span class="eyebrow">AI 연결</span><b>투자 판단 연결</b></div><div id="terminal-auth" aria-live="polite"><span class="auth-chip pending">로그인 후 확인합니다</span></div><button id="terminal-auth-refresh" class="small">연결 다시 확인</button><small id="terminal-auth-time">모델 호출 없이 인증 상태 확인</small></section>`);

function renderTerminalAuth(){
  const entries=terminalAuth?.providers||{};
  $('terminal-auth').innerHTML=['codex','claude'].map(key=>{
    const s=entries[key],tokenOnly=s?.authenticated&&s.message?.includes('토큰이 설정');
    const status=terminalAuthFailed?'조회 실패':terminalAuthBusy?'확인 중':!s?'아직 확인 전':tokenOnly?'토큰 설정':s.authenticated?'인증 확인':'로그인 필요';
    const cls=terminalAuthFailed||tokenOnly?'warn':terminalAuthBusy||!s?'pending':s.authenticated?'connected':'disconnected';
    return `<div class="auth-chip ${cls}" title="${esc(s?.message||'연결 상태를 확인합니다')}"><i></i><span>${key==='codex'?'ChatGPT':'Claude'}<b>${status}</b></span>${terminalAuth?.selected===key?'<em>선택 AI</em>':''}</div>`;
  }).join('');
  $('terminal-auth-refresh').disabled=terminalAuthBusy;
  $('terminal-auth-time').textContent=terminalAuthFailed?'상태 조회 실패 · 재확인하세요. 기존 인증이 해제됐다는 뜻은 아닙니다.':terminalAuthBusy?'인증을 확인하는 동안 화면을 사용할 수 있습니다.':terminalAuthAt?`확인 ${hm(new Date(terminalAuthAt).toISOString())} · 모델 응답 성공 여부와는 별도`:'모델 호출 없이 인증 상태 확인';
}
async function refreshTerminalAuth(force=false){
  if(!authenticated||terminalAuthBusy||(!force&&terminalAuthAt&&Date.now()-terminalAuthAt<300000))return;
  terminalAuthBusy=true;terminalAuthFailed=false;renderTerminalAuth();
  try{terminalAuth=await api('/ai/providers');terminalAuthAt=Date.now();}
  catch(e){terminalAuthFailed=true;}
  finally{terminalAuthBusy=false;renderTerminalAuth();}
}
$('terminal-auth-refresh').addEventListener('click',()=>refreshTerminalAuth(true));
const terminalBaseTab=tab;
tab=function(id){terminalBaseTab(id);refreshTerminalAuth();};

// Partition the dashboard into workspaces instead of an indefinitely long page.
const terminalDb=$('db-view');
const terminalOriginal=[...terminalDb.children];
const terminalSwitch=document.createElement('div');terminalSwitch.className='terminal-switch';terminalSwitch.setAttribute('role','tablist');terminalSwitch.setAttribute('aria-label','트레이딩 데스크 보기');
terminalSwitch.innerHTML=Object.entries(terminalViews).map(([id,label])=>`<button id="desk-tab-${id}" role="tab" aria-controls="desk-${id}" data-desk="${id}">${label}</button>`).join('');
terminalDb.insertBefore(terminalSwitch,$('db-kpis').nextSibling);
const terminalZones={};
for(const id of Object.keys(terminalViews)){const zone=document.createElement('div');zone.id='desk-'+id;zone.className='terminal-zone';zone.setAttribute('role','tabpanel');zone.setAttribute('aria-labelledby','desk-tab-'+id);terminalDb.append(zone);terminalZones[id]=zone;}
function deskMove(node,zone){if(node)terminalZones[zone].append(node);}
deskMove(document.querySelector('.research-layout'),'market');
deskMove($('research-selection'),'portfolio');deskMove($('research-periods'),'portfolio');
deskMove($('research-exits').closest('section'),'activity');
const terminalResearch=document.querySelector('.research-workspace');
terminalResearch.querySelectorAll(':scope > details').forEach(n=>deskMove(n,'analysis'));
// Keep the render target but replace the duplicated hero with a compact desk heading.
$('research-mode').classList.add('terminal-research-heading');
terminalDb.insertBefore($('research-mode'),$('db-kpis'));
terminalResearch.remove();
for(const node of terminalOriginal){
  if(node.id==='db-kpis'||node===terminalResearch)continue;
  const zone=node.querySelector('#db-log')?'activity':node.querySelector('#db-review')||node.id==='db-overnight-panel'?'analysis':node.querySelector('#db-positions')?'portfolio':'market';
  deskMove(node,zone);
}
function setTerminalView(id){
  terminalView=terminalViews[id]?id:'market';
  for(const [key,zone] of Object.entries(terminalZones))zone.hidden=key!==terminalView;
  terminalSwitch.querySelectorAll('button').forEach(b=>{const active=b.dataset.desk===terminalView;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;});
  if(dbData?.account)renderDashboard();
}
terminalSwitch.addEventListener('click',e=>{const b=e.target.closest('[data-desk]');if(b)setTerminalView(b.dataset.desk);});
terminalSwitch.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const keys=[...terminalSwitch.querySelectorAll('[data-desk]')].map(b=>b.dataset.desk),i=keys.indexOf(terminalView);const next=e.key==='Home'?0:e.key==='End'?keys.length-1:(i+(e.key==='ArrowRight'?1:-1)+keys.length)%keys.length;setTerminalView(keys[next]);$('desk-tab-'+keys[next]).focus();});
setTerminalView('market');

// Separate operating controls and records without changing order behavior.
const terminalDt=$('dt-view');
const terminalDtChildren=[...terminalDt.children];
for(const node of terminalDtChildren){
  if(node===terminalDtChildren[0]){node.classList.add('terminal-command');continue;}
  if(node.tagName==='DETAILS'||node.id==='dt-orders-panel')continue;
  const details=document.createElement('details');details.className='terminal-detail';
  const title=node.querySelector('h2')?.textContent||'운용 상세';
  const summary=document.createElement('summary');summary.textContent=title;details.append(summary);
  details.open=!!node.querySelector('#portfolio-decisions,#dt-positions');
  terminalDt.insertBefore(details,node);details.append(node);
}
const terminalRender=renderDashboard;
renderDashboard=function(){
  terminalRender();
  if(dbData?.account)$('research-mode').hidden=dbData.account.config.strategy==='portfolio';
  refreshTerminalAuth();
  if(dbData){$('db-title').textContent='상세 분석';$('db-sub').textContent=`${dbData.mode==='live'?'실거래':'모의 운용'} · ${dbData.day} · 차트, 투자 기간, 판단 근거를 한 화면에서 확인하세요.`;}
  const pending=dbData?.connections?.claude?.checking;
  if(pending){const badges=$('db-status').querySelectorAll('.badge');for(const b of badges)if(b.textContent.trim()==='○ AI'){b.className='badge warn';b.textContent='◌ AI 상태 확인 중';}}
};
if(authenticated)refreshTerminalAuth();
