'use strict';
// Presentation only. Existing account, order and AI controls keep their handlers.
const terminalViews={market:'차트 · 성과',portfolio:'기간별 포트폴리오',activity:'매매 · 기록',analysis:'분석 · 복기'};
let terminalView='market',terminalAuth=null,terminalAuthAt=0,terminalAuthBusy=false,terminalAuthFailed=false;
const terminalMenu=document.querySelector('nav[aria-label="주 메뉴"]');
terminalMenu.classList.add('terminal-sidebar');
terminalMenu.insertAdjacentHTML('afterbegin','<div class="sidebar-label">메뉴</div>');
const terminalNavIcons={
  home:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5L12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1v-9.5z"/></svg>',
  office:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="7" r="3"/><circle cx="5" cy="17" r="2.5"/><circle cx="19" cy="17" r="2.5"/><path d="M12 10v3.2m0 0l-5 2.4m5-2.4l5 2.4"/></svg>',
  news:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 9h8M8 13h8M8 17h5"/></svg>',
  dashboard:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19V5m0 14h16"/><path d="M8 15l3.5-4.5 3.5 2.8L19.5 7"/></svg>',
  daytrade:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 5-3 8.5-7 10-4-1.5-7-5-7-10V6l7-3z"/><path d="M9.5 12l2 2 3.5-4"/></svg>',
  settings:'<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.08a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.08a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>'
};
const terminalNavLabels={home:['홈','실시간 관제탑'],office:['AI 회의실','7역할 AI 토론'],news:['뉴스','시장 · AI 브리핑'],dashboard:['상세 분석','팩터 · 자산 리서치'],daytrade:['투자 운용','계좌 · 전략 · 주문'],settings:['연결 · 설정','API · 알림 · 연결']};
terminalMenu.querySelectorAll('[data-tab]').forEach(b=>{const key=b.dataset.tab,v=terminalNavLabels[key],ico=terminalNavIcons[key]||'';if(v)b.innerHTML=`<span class="nav-ico" aria-hidden="true">${ico}</span><span class="nav-copy"><span class="nav-title">${v[0]}</span><span class="nav-sub">${v[1]}</span></span>`;});
terminalMenu.insertAdjacentHTML('beforeend','<div class="sidebar-bottom"><div class="sidebar-ai-head"><span class="pulse-dot" aria-hidden="true"></span><span>AI 24h 자율 감시</span></div><small>🇰🇷 국내 · 🇺🇸 미국 24h<br>단기 · 중기 · 장기 멀티팩터</small><button type="button" data-goto="settings" class="quiet small">연결 관리 ↗</button></div>');
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

// Organize #dt-view with non-collapsing zone tabs so content stays scannable and order panels stay intact.
const terminalDt=$('dt-view');
const terminalDtChildren=[...terminalDt.children];
const dtClassify=node=>{
  if(node.id==='dt-orders-panel')return'cmd';
  const title=node.querySelector('h2')?.textContent||'';
  if(node.querySelector('#dt-trades,#dt-logs,#dt-orders')||/체결|주문|활동|기록/.test(title))return'orders';
  if(node.querySelector('#dt-learning,#portfolio-learning')||/복기|학습|튜닝|진화/.test(title))return'learn';
  return'ops';
};
terminalDtChildren.forEach((node,idx)=>{
  if(idx===0){node.classList.add('terminal-command');node.dataset.dtZone='cmd';return;}
  node.dataset.dtZone=dtClassify(node);
});
const dtSectionBar=document.createElement('div');
dtSectionBar.id='dt-section-tabs';
dtSectionBar.className='dt-switch';
dtSectionBar.setAttribute('role','tablist');
dtSectionBar.setAttribute('aria-label','투자 운용 섹션 필터');
dtSectionBar.innerHTML='<button type="button" class="active" data-dt-filter="all">전체 보기</button><button type="button" data-dt-filter="ops">AI 판단 · 보유 포지션</button><button type="button" data-dt-filter="orders">체결 · 주문 · 활동</button><button type="button" data-dt-filter="learn">마감 복기 · 자가학습</button>';
terminalDt.insertBefore(dtSectionBar,terminalDtChildren[1]||null);
dtSectionBar.addEventListener('click',e=>{
  const b=e.target.closest('button[data-dt-filter]');
  if(!b)return;
  const f=b.dataset.dtFilter;
  dtSectionBar.querySelectorAll('button[data-dt-filter]').forEach(x=>x.classList.toggle('active',x===b));
  [...terminalDt.children].forEach(node=>{
    const z=node.dataset.dtZone;
    if(!z||z==='cmd')return;
    node.classList.toggle('dt-zone-hidden',f!=='all'&&z!==f);
  });
});
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
