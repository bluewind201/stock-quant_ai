'use strict';
// 공통 도우미 · 로그인 · 탭 · 뉴스/테마 · 설정. 대시보드는 dashboard.js, 단타는 daytrade.js, 관심종목 운용은 paper.js.
const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (n,d=2) => Number.isFinite(n) ? n.toLocaleString('ko-KR',{maximumFractionDigits:d}) : '—';
const time = value => value ? new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false}) : '—';
const hm = value => value ? new Date(value).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false,hour:'2-digit',minute:'2-digit'}) : '—';
const mdhm = value => value ? new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';
const external = (url,title) => String(url).startsWith('https://') ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(title)} ↗</a>` : esc(title);
const won = (v) => Number.isFinite(v) ? Math.round(v).toLocaleString('ko-KR')+'원' : '—';
const signed = (v) => Number.isFinite(v) ? (v>0?'+':'')+Math.round(v).toLocaleString('ko-KR')+'원' : '—';
function themeRanking(plan){
  const rows=plan?.top_themes||[];
  if(!rows.length)return '';
  return '<h4>AI 유망 테마 TOP 5 · 매수 우선 검토 순위</h4>'+rows.map((t,i)=>`<div class="feed-item"><strong>${i+1}. ${esc(t.name)}</strong><p>${esc(t.catalyst)}</p><p class="small muted">위험 · ${esc(t.risk)}<br>무효화 · ${esc(t.invalidation)}<br>후보 · ${esc((t.symbols||[]).join(', '))}</p></div>`).join('');
}
const pctc = (v,d=2) => Number.isFinite(v) ? `<span class="${v>0?'up':v<0?'down':''}">${v>0?'+':''}${v.toFixed(d)}%</span>` : '—';
let overview, research, busy=false, authenticated=false, toastTimer;

function notify(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,7000);}
async function api(path,method='GET',body){const r=await fetch('/api'+path,{method,headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const d=await r.json();if(!r.ok){if(r.status===401&&path!='/login'){authenticated=false;$('workspace').hidden=true;$('login').hidden=false;}throw Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));}return d;}
function action(id,fn,event='click'){const el=$(id);if(!el)return;el.addEventListener(event,async e=>{e.preventDefault();const target=e.currentTarget;try{target.disabled=true;await fn(e);}catch(error){notify(error.message);}finally{target.disabled=false;}});}
function download(name,data){const url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}

// ── 탭 ──────────────────────────────────────────────────────────────────
// 각 탭은 열릴 때 한 번 불러오고, 대시보드·단타는 열려 있는 동안 15초마다 갱신한다.
let dtTimer=null;
function tab(id){
  document.querySelectorAll('.tab').forEach(s=>s.hidden=s.id!==id);
  document.querySelectorAll('nav [data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===id));
  clearInterval(dtTimer);
  if(id==='dashboard'||id==='home'||id==='office'){loadDashboard().catch(e=>notify(e.message));dtTimer=setInterval(()=>{if(!$(id).hidden)loadDashboard().catch(()=>{});},15000);}
  if(id==='daytrade'){loadDaytrade().catch(e=>notify(e.message));dtTimer=setInterval(()=>{if(!$('daytrade').hidden)loadDaytrade().catch(()=>{});},15000);}
  if(id==='paper')loadPaper().catch(e=>notify(e.message));
  if(id==='news'){loadNews().catch(e=>notify(e.message));}
  if(id==='settings')loadSettings().catch(e=>notify(e.message));
  try{localStorage.setItem('sq-tab-322',id);}catch(e){}
}
document.querySelectorAll('nav [data-tab]').forEach(b=>b.addEventListener('click',()=>tab(b.dataset.tab)));
document.addEventListener('click',e=>{const a=e.target.closest('[data-goto]');if(a){e.preventDefault();tab(a.dataset.goto);}});
function subtab(container,id){container.querySelectorAll('[data-sub]').forEach(b=>b.classList.toggle('active',b.dataset.sub===id));['news-feed','news-themes'].forEach(k=>$(k).hidden=k!==id);if(id==='news-themes')loadThemes().catch(e=>notify(e.message));}
document.querySelectorAll('#news .subnav [data-sub]').forEach(b=>b.addEventListener('click',()=>subtab(b.closest('.subnav'),b.dataset.sub)));

// 헤더 시계. 브라우저가 어디 있든 KST.
setInterval(()=>{const el=$('clock');if(el)el.textContent=new Date().toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false,hour:'2-digit',minute:'2-digit'})+' KST';},1000);

async function loadOverview(){
  overview=await api('/session');authenticated=true;$('login').hidden=true;$('workspace').hidden=false;
  $('storage-notice').textContent=overview.storage_notice||'';$('storage-notice').hidden=!overview.storage_notice;
  let start='home';try{start=localStorage.getItem('sq-tab-322')||'home';}catch(e){}
  if(!document.querySelector(`nav [data-tab="${start}"]`))start='home';
  tab(start);
}

// ── 뉴스 ────────────────────────────────────────────────────────────────
const sourceNames={fed:'연준',white_house:'백악관',bls:'미 노동통계국',bea:'미 경제분석국',news:'언론 색인',company:'기업',manual:'직접 등록'};
const feedNames={fed_policy:'연준 통화정책',fed_speech:'연준 연설',white_house:'백악관 발표',general_news:'시장 뉴스 색인',bls_calendar:'BLS 발표 일정',fomc_calendar:'FOMC 일정',bea_calendar:'BEA GDP·PCE 일정',bea_news:'BEA 경제발표'};
function renderHealth(items){$('feed-health').innerHTML=items.length?items.map(h=>`<div class="health"><strong>${esc(feedNames[h.id]||h.id)}</strong> <span class="${h.ok?'ok':'up'}">${h.ok?'정상 · '+h.count+'건':'확인 필요'}</span><small>${esc(h.error||time(h.checked_at))}</small></div>`).join(''):'<p class="muted">아직 수집 결과가 없습니다. "지금 수집"을 누르세요.</p>';}
function newsItem(n,extra=''){return `<div class="feed-item"><small>${extra}${esc(n.source||'')} · ${mdhm(n.published_at)}</small><strong>${external(n.url,n.title_ko||n.title)}</strong>${n.title_ko?`<p class="small muted">원문 · ${esc(n.title)}</p>`:''}${n.body?`<p class="body">${esc(String(n.body).slice(0,220))}${n.body.length>220?'…':''}</p>`:''}</div>`;}

function renderAiInputs(account){
  const box=$('news-ai-inputs');if(!box)return;
  if(!account){box.innerHTML='<p class="muted">단타 계좌가 없습니다. 계좌를 만들고 운용을 시작하면 매일 07:30 장전 판단에 쓴 자료가 여기에 남습니다.</p>';return;}
  const inp=account.ai_inputs;
  if(!inp){box.innerHTML=`<p class="muted">아직 오늘 장전 판단이 없습니다. 스캔 시각(${esc(account.config.scan_at)})에 자동으로 실행되며, 당일 단타 화면의 "지금 스캔 · AI 판단"으로 바로 볼 수도 있습니다.</p>`;return;}
  const cands=(account.scan&&account.scan.candidates)||[];
  const picked=new Set(((account.plan||{}).picks||[]).map(p=>p.symbol));
  const stockNews=cands.flatMap(c=>(c.news||[]).map(n=>({...n,_name:c.name||c.symbol,_symbol:c.symbol,_picked:picked.has(c.symbol)})));
  box.innerHTML=`<p class="small"><span class="tag ok">${mdhm(inp.at)} ${esc(inp.kind)}</span> 후보 <b>${inp.candidates}종</b>의 기사 <b>${inp.candidate_news}건</b>(본문 포함) · 국내 시장 제목 <b>${inp.kr_market_news.length}건</b> · 미국 거시 <b>${inp.us_evidence.length}건</b> · 후보별 외국인·기관 순매수 3일 · 소속 테마</p>
  <details open><summary>후보 종목 기사 ${stockNews.length}건 — AI가 본문 요약까지 읽은 것</summary>${stockNews.length?stockNews.map(n=>newsItem(n,`<span class="tag ${n._picked?'up':''}">${esc(n._name)}${n._picked?' · 선정':''}</span>`)).join(''):'<p class="muted">후보 종목에 최근 기사가 없었습니다.</p>'}</details>
  <details><summary>국내 시장 · 정책 제목 ${inp.kr_market_news.length}건 — 그날 분위기 파악용</summary>${inp.kr_market_news.map(n=>newsItem(n)).join('')||'<p class="muted">없음</p>'}</details>
  <details><summary>미국 거시 ${inp.us_evidence.length}건 — 제목만</summary>${inp.us_evidence.map(n=>newsItem({...n,source:sourceNames[n.source]||n.source})).join('')||'<p class="muted">없음</p>'}</details>`;
}
async function loadResearch(){
  research=await api('/research');renderHealth(research.health);
  const upcoming=research.events.filter(e=>new Date(e.scheduled_at)>Date.now()-86400000*2);
  $('event-list').innerHTML=upcoming.length?upcoming.slice(0,40).map(e=>`<div class="feed-item"><small>${mdhm(e.scheduled_at)} · ${esc(sourceNames[e.source]||e.source)} ${e.impact==='high'?'<span class="tag up">중요</span>':''}</small><strong>${external(e.url,e.title_ko||e.title)}</strong>${e.title_ko?`<p class="small muted">원문 · ${esc(e.title)}</p>`:''}${e.actual!==null&&e.actual!==undefined?`<p>발표 ${fmt(e.actual)} / 예상 ${fmt(e.consensus)} / 이전 ${fmt(e.previous)} ${esc(e.unit)}</p>`:''}</div>`).join(''):'<p class="muted">예정된 일정이 없습니다. "지금 수집"을 누르세요.</p>';
}
async function loadNews(){
  const jobs=[loadResearch(),loadNewsCenter()];
  try{const d=await api('/dashboard');renderAiInputs(d.account);}catch(e){renderAiInputs(null);}
  await Promise.allSettled(jobs);
}
action('refresh-news',async()=>{await api('/research/refresh','POST',{});notify('미국 발표·경제일정 수집을 시작했습니다. 잠시 뒤 새로고침하세요.');setTimeout(()=>loadResearch().catch(()=>{}),8000);});

// ── 테마 ────────────────────────────────────────────────────────────────
// 관심종목 운용에 넣을 종목을 고르는 화면. 여기서 주문은 일어나지 않는다. 당일 단타는 이 목록을 쓰지 않는다.
let themeData=null; const themePicked=new Map();
function renderBasket(){const n=themePicked.size;$('themes-count').textContent=`${n} / 12`;$('themes-count').className=n>12?'up small':'muted small';
$('themes-basket').innerHTML=n?[...themePicked.entries()].map(([code,name])=>`<button class="chip picked" data-drop="${esc(code)}">${esc(name)} (${esc(code)}) ×</button>`).join(' ')+(n>12?'<br><strong class="up">12개를 넘었습니다.</strong>':''):'아래 테마에서 종목을 눌러 담으세요. 최대 12개.';
document.querySelectorAll('#themes-list .chip').forEach(b=>b.classList.toggle('picked',themePicked.has(b.dataset.pick)));}
function renderThemes(){const d=themeData;if(!d)return;
$('themes-status').textContent=d.error?d.error:`전체 ${d.total}개 테마 중 상위 ${d.themes.length}개 · 출처 네이버 증권`;
$('themes-list').innerHTML=d.themes.map(th=>{const day=th.change_pct,three=th.change_3d_avg_pct,fade=day>0&&three<0;
return `<article class="panel"><h2 style="justify-content:flex-start">${esc(th.name)} <span class="tag ${day>=0?'up':'down'}">전일 ${fmt(day)}%</span> <span class="tag ${three>=0?'up':'down'}">3일평균 ${fmt(three)}%</span></h2>
<p class="small muted">구성 ${th.members}종 · 상승 ${th.up} / 보합 ${th.flat} / 하락 ${th.down}${th.leaders.length?' · 주도주 '+th.leaders.map(l=>esc(l.name)).join(' · '):''}</p>
${fade?'<p class="small up">전일만 오르고 3일 평균은 마이너스 — 하루 반짝일 수 있습니다.</p>':''}
${th.description?`<p class="small muted">${esc(th.description)}</p>`:''}
<div class="chips">${(th.stocks||[]).map(s=>s.tradable===false?`<span class="chip" style="opacity:.45;cursor:not-allowed" title="거래정지·관리종목">${esc(s.name)} <small>거래불가</small></span>`:`<button class="chip" data-pick="${esc(s.symbol)}" data-name="${esc(s.name)}">${esc(s.name)} <small>${esc(s.symbol)}</small>${s.managed?' <small>관리</small>':''}</button>`).join('')}</div>
${th.members_error?`<p class="small muted">${esc(th.members_error)}</p>`:''}</article>`;}).join('')||'<p class="empty">불러온 테마가 없습니다.</p>';
renderBasket();}
async function loadThemes(force){if(themeData&&!force)return renderThemes();$('themes-status').textContent='불러오는 중…';themeData=await api('/themes?limit='+$('themes-limit').value);renderThemes();}
$('themes-list').addEventListener('click',e=>{const b=e.target.closest('[data-pick]');if(!b)return;const code=b.dataset.pick;themePicked.has(code)?themePicked.delete(code):themePicked.set(code,b.dataset.name);renderBasket();});
$('themes-basket').addEventListener('click',e=>{const b=e.target.closest('[data-drop]');if(!b)return;themePicked.delete(b.dataset.drop);renderBasket();});
$('themes-limit').addEventListener('change',()=>loadThemes(true).catch(e=>notify(e.message)));
action('themes-refresh',()=>loadThemes(true));
action('themes-clear',()=>{themePicked.clear();renderBasket();});
action('themes-ai',async()=>{$('themes-ai-status').textContent='AI가 판단하는 중… 최대 4분 걸립니다.';$('themes-ai-result').innerHTML='';
try{const r=await api('/themes/recommend','POST',{});
$('themes-ai-status').textContent=`후보 ${r.candidate_count}종 중 ${r.picks.length}종 선정 · ${r.theme_spread}개 테마에 분산`;
$('themes-ai-result').innerHTML=`<div class="notice info"><strong>요약</strong><p>${esc(r.summary)}</p>${r.us_context?`<strong>미국 상황과의 연결</strong><p>${esc(r.us_context)}</p>`:''}<p class="small">${esc(r.note)}</p></div>`
+r.picks.map(p=>`<div class="feed-item"><small>${esc(p.theme)}${p.themes&&p.themes.length>1?' · 소속 '+p.themes.length+'개 테마':''}</small><strong>${esc(p.name)} <span class="muted">${esc(p.symbol)}</span></strong><p>${esc(p.thesis)}</p><p class="small muted">반대 근거 · ${esc(p.counter_case)}</p></div>`).join('')
+`<div class="row" style="margin-top:10px"><button id="themes-ai-apply">이 ${r.picks.length}종을 관심종목에 넣기</button></div>`;
$('themes-ai-apply').addEventListener('click',()=>{themePicked.clear();r.picks.forEach(p=>themePicked.set(p.symbol,p.name));renderBasket();
$('paper-currency').value='KRW';$('paper-cash').value='10000000';$('paper-watchlist').value=r.watchlist.map(w=>w.exchange+':'+w.symbol).join(', ');$('paper-form').hidden=false;
tab('paper');notify(`AI가 고른 ${r.picks.length}종을 관심종목 입력란에 넣었습니다. 계좌를 만드세요.`);});
}catch(e){$('themes-ai-status').textContent='';throw e;}});
action('themes-apply',()=>{if(!themePicked.size)throw Error('먼저 종목을 선택하세요.');
if(themePicked.size>12)throw Error('한 계좌의 관심 종목은 최대 12개입니다.');
$('paper-currency').value='KRW';$('paper-cash').value='10000000';
$('paper-watchlist').value=[...themePicked.keys()].map(c=>'KRX:'+c).join(', ');$('paper-form').hidden=false;
tab('paper');notify(`${themePicked.size}개 종목을 관심종목 입력란에 넣었습니다. 계좌를 만드세요.`);});

// ── 설정 · 연결 ─────────────────────────────────────────────────────────
function claudeText(c){if(!c)return '확인할 수 없음';const name=c.model||c.provider||'AI';return name+' : '+(c.message|| (c.authenticated?'연결됨':'로그인 필요'))+(c.automatic_failover?' · 한도 초과 시 다른 구독 AI로 자동 전환':'');}
async function loadSettings(){
  const d=await api('/dashboard');const c=d.connections;
  const catalog=await api('/ai/models');
  $('st-ai-provider').innerHTML=Object.entries(catalog.providers).map(([provider,rows])=>`<optgroup label="${provider==='codex'?'ChatGPT 구독':'Claude 구독'}">${rows.map(r=>`<option value="${esc(provider+':'+r.id)}">${esc(r.label)}</option>`).join('')}</optgroup>`).join('');
  $('st-toss').innerHTML=c.toss?'✅ 키 설정됨 · 실제 조회는 "연결 확인"으로':'❌ TOSS_CLIENT_ID / TOSS_CLIENT_SECRET 이 없습니다';
  $('st-claude').textContent=claudeText(c.claude);
  $('st-ai-provider').value=(c.claude?.preferred_provider||c.claude?.provider||'claude')+':'+(c.claude?.preferred_model||c.claude?.model||'claude-opus-5-5');
  $('st-tg').textContent=c.telegram?'✅ 설정됨 · 보낼 알림 범위는 아래에서 고릅니다':'❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 없습니다';
  $('st-tg-test').disabled=!c.telegram;
  const ls=d.live_status;liveStatus=ls;
  const target=ls.account_seq?`<br>주문이 나갈 계좌 <b>TOSS_ACCOUNT_SEQ=${esc(ls.account_seq)}</b> <button id="st-live-accounts" class="small">어느 계좌인지 확인</button>`
    :`<br>주문이 나갈 계좌가 <b>지정되지 않았습니다</b>. 아래에서 식별자를 확인해 NAS <code>.env</code> 의 <code>TOSS_ACCOUNT_SEQ</code> 에 넣으세요. <button id="st-live-accounts" class="small">토스 계좌 목록 조회</button>`;
  $('st-live').innerHTML=(ls.available?`✅ 켜짐 · 투입 상한 ${won(ls.capital_cap_krw)} · 주문 1건 상한 ${won(ls.order_cap_krw)}${d.live_running?' · <b class="up">지금 실거래 운용 중</b>':''}`
    :`⛔ 꺼짐 (안전) · ${[ls.enabled?'':'ENABLE_LIVE_TRADING=true 필요',ls.account_seq_set?'':'TOSS_ACCOUNT_SEQ 필요'].filter(Boolean).join(' · ')}`)+target;
  bindLiveAccounts();
}
let liveStatus=null;
function bindLiveAccounts(){
  const btn=$('st-live-accounts');if(!btn||btn.dataset.bound)return;btn.dataset.bound='1';
  btn.addEventListener('click',async()=>{
    const out=$('st-live-accounts-out');out.textContent='조회 중…';
    try{
      const r=await api('/live/broker-accounts');const cur=(liveStatus||{}).account_seq;
      out.innerHTML=r.accounts.length?'<table><thead><tr><th>계좌</th><th>종류</th><th>TOSS_ACCOUNT_SEQ</th><th></th></tr></thead><tbody>'
        +r.accounts.map(a=>`<tr><td>${esc(a.masked_number)}</td><td>${esc(a.account_type)}</td><td><code>${esc(a.account_seq)}</code></td><td>${String(a.account_seq)===String(cur)?'<span class="tag ok">지금 이 계좌로 주문</span>':''}</td></tr>`).join('')
        +'</tbody></table>'+(cur&&!r.accounts.some(a=>String(a.account_seq)===String(cur))?'<p class="small up">설정된 TOSS_ACCOUNT_SEQ 가 이 목록에 없습니다. 값을 확인하세요.</p>':'')
        +'<p class="small muted">단타는 <b>보유 주식이 없는 전용 계좌</b>를 쓰세요. 장기 보유 계좌를 지정하면 계좌 대사에서 막힙니다.</p>'
        :'조회된 계좌가 없습니다.';
    }catch(e){out.textContent=e.message;}
  });
}
action('st-refresh',loadSettings);
action('st-toss-check',async()=>{$('st-toss').textContent='조회 중…';try{const r=await api('/toss/check','POST',{exchange:'KRX',symbol:'005930',name:''});$('st-toss').textContent=`✅ 인증·조회 성공 · ${r.stock.name} (${r.stock.symbol}) ${r.stock.market}`;}catch(e){$('st-toss').textContent='❌ '+e.message;throw e;}});
action('st-claude-check',async()=>{$('st-claude').textContent='확인 중…';const s=await api('/paper/ai-check','POST',{});$('st-claude').textContent=claudeText(s);});
action('st-tg-test',async()=>{await api('/notify/test','POST',{});notify('텔레그램으로 확인 메시지를 보냈습니다.');});

// ── 로그인 ─────────────────────────────────────────────────────────────
action('login-form',async()=>{try{await api('/login','POST',{password:$('password').value});$('password').value='';await loadOverview();}catch(e){$('login-error').textContent=e.message;}},'submit');
action('logout',async()=>{await api('/logout','POST',{});authenticated=false;location.reload();});
api('/health').then(h=>{if(!h.password_configured)$('login-error').textContent='서버 ADMIN_PASSWORD를 설정하고 다시 실행하세요.';});loadOverview().catch(()=>{});

action('st-ai-save',async()=>{const [provider,model]=$('st-ai-provider').value.split(':');const result=await api('/ai/provider','POST',{provider,model,allow_usage_credits:$('st-ai-credits').checked});$('st-claude').textContent=claudeText(result);if(typeof refreshTerminalAuth==='function')refreshTerminalAuth(true);});

async function checkAllAiAuth(){
  const box=$('st-ai-auth-status');box.textContent='GPT · Claude 인증 상태를 확인하고 있습니다…';
  const d=await api('/ai/providers');
  box.innerHTML=Object.entries(d.providers).map(([key,s])=>`<p><b>${key==='codex'?'ChatGPT':'Claude'} · ${s.authenticated?'인증 확인':'확인 필요'}</b><br>${esc(s.message||'상태 미확인')}</p>`).join('');
}
action('st-ai-check-all',checkAllAiAuth);
