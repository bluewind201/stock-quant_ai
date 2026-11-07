'use strict';
// 홈 · 뉴스 센터 · 연결 설정 보강. 숫자는 /api/dashboard 한 번으로 받고 여기서 주문은 나가지 않는다.
const homeHorizon={short:'단기',medium:'중기',long:'장기',legacy:'기존 방식'};
const homeMixColors={cash:'#5d6b7e',short:'#3987e5',medium:'#d95926',long:'#199e70',legacy:'#9085e9'};
let homeRange=0,homeNews=null,homeNewsAt=0;

function homeGreeting(){const h=Number(new Date().toLocaleString('en-US',{timeZone:'Asia/Seoul',hour:'numeric',hour12:false}))%24;
  return h<5?'늦은 밤이에요':h<11?'좋은 아침이에요':h<17?'좋은 오후예요':h<22?'좋은 저녁이에요':'늦은 밤이에요';}
function homeAgo(value){const t=new Date(value).getTime();if(!t)return '';const m=Math.round((Date.now()-t)/60000);
  return m<1?'방금':m<60?m+'분 전':m<1440?Math.round(m/60)+'시간 전':Math.round(m/1440)+'일 전';}
function homePrice(a,p){const q=a.quotes?.[p.symbol];return q&&Number.isFinite(q.bid)&&q.bid>0?q.bid:q&&Number.isFinite(q.price)&&q.price>0?q.price:p.entry_price;}
function homeExitTitle(t){const r=t.reason||'',pnl=t.pnl||0;
  if(/보호 가격|손절/.test(r))return pnl>0?'추적 손절 · 이익 확정':'손절';if(/익절/.test(r))return '익절';if(/추세 이탈/.test(r))return '추세 이탈 매도';
  if(/AI 투자/.test(r))return 'AI 판단 매도';if(/수동/.test(r))return '수동 매도';return pnl>=0?'이익 실현':'손실 매도';}
function homeCountUp(el,target,fmtFn){
  if(!el)return;const prev=Number(el.dataset.value);el.dataset.value=target;
  if(!Number.isFinite(prev)||prev===target||(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)){el.textContent=fmtFn(target);return;}
  const start=performance.now(),from=prev;const step=now=>{const k=Math.min(1,(now-start)/600),e=1-Math.pow(1-k,3);el.textContent=fmtFn(from+(target-from)*e);if(k<1)requestAnimationFrame(step);};requestAnimationFrame(step);
}

function renderHome(){
  const d=dbData;if(!d||!$('home'))return;const a=d.account,live=d.mode==='live';
  $('home-greeting').textContent=homeGreeting();
  $('home-mode').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b.dataset.mode===d.mode);b.textContent=(b.dataset.mode==='live'?'실거래':'모의 운용')+(d.has[b.dataset.mode]?'':' (없음)');});
  const ai=d.connections?.claude||{},aiOk=ai.installed&&ai.authenticated;
  $('home-chips').innerHTML=[
    a?`<span class="hchip ${a.status==='running'?(live?'live':'ok'):'warn'}"><i></i>${a.status==='running'?(live?'실거래 운용 중':'모의 운용 중'):'운용 정지'}</span>`:'',
    `<span class="hchip ${d.market_session?'':'muted'}"><i></i>${d.market_session?'오늘 거래일':'오늘 휴장'}</span>`,
    `<span class="hchip ${ai.checking?'muted':aiOk?'ok':'bad'}" title="${esc(ai.message||'')}"><i></i>AI ${ai.checking?'확인 중':aiOk?'연결됨':'연결 필요'}</span>`,
    `<span class="hchip ${d.connections?.telegram?'ok':'muted'}"><i></i>텔레그램 ${d.connections?.telegram?'켜짐':'꺼짐'}</span>`].join('');
  $('home-empty').hidden=!!a;$('home-view').hidden=!a;
  loadHealth().catch(()=>{});
  if(!a){$('home-sentence').textContent=live?'실거래 계좌가 아직 없습니다. 모의 운용으로 먼저 확인한 뒤 만드세요.':'아직 계좌가 없습니다. 투자 운용에서 모의 계좌를 만들면 AI 투자팀이 일을 시작합니다.';return;}
  const cfg=a.config,init=cfg.initial_cash,eq=a.equity,ret=(eq/init-1)*100;
  const todaySells=(a.trades||[]).filter(t=>t.side==='sell'&&(t.at||'')&&new Date(t.at).toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'})===a.day);
  const todayRealized=todaySells.reduce((s,t)=>s+(t.pnl||0),0),unreal=a.unrealized||0;
  const next=a.office?.status==='running'?'AI 투자팀이 지금 회의 중이에요.':a.status==='running'?'AI 투자팀은 다음 예약 판단을 기다리고 있어요.':'운용이 정지되어 있어요. 투자 운용 화면에서 시작할 수 있어요.';
  $('home-sentence').innerHTML=`${live?'실거래':'모의 운용'} 평가자산은 <b>${won(eq)}</b>, 시작 금액 대비 <b class="${ret>0?'up':ret<0?'down':''}">${ret>0?'+':''}${ret.toFixed(2)}%</b>입니다. ${esc(next)}`;
  // KPI
  const curve=(a.equity_curve||[]).map(p=>p.equity).filter(Number.isFinite);
  const pos=Object.values(a.positions||{});
  const kpis=[
    {k:'평가자산',v:eq,f:won,sub:`현금 ${won(a.cash)} · 시작 ${won(init)}`,spark:true},
    {k:'오늘 손익',v:todayRealized+unreal,f:signed,sub:`실현 ${signed(todayRealized)} · 보유 평가 ${signed(unreal)}`,cls:true},
    {k:'누적 수익률',v:ret,f:v=>(v>0?'+':'')+v.toFixed(2)+'%',sub:`누적 실현 ${signed(a.realized_pnl||0)}`,cls:true},
    {k:'보유 종목',v:pos.length,f:v=>Math.round(v)+'개',sub:`최대 ${cfg.max_positions}개 · AI 판단 오늘 ${a.ai?.calls_today||0}회`}];
  const box=$('home-kpis');
  if(box.children.length!==kpis.length)box.innerHTML=kpis.map((x,i)=>`<div class="hkpi"><span>${x.k}</span><strong id="home-kpi-${i}"></strong><small id="home-kpi-sub-${i}"></small>${x.spark?'<canvas id="home-spark" class="hspark" aria-hidden="true"></canvas>':''}</div>`).join('');
  kpis.forEach((x,i)=>{const el=$('home-kpi-'+i);el.className=x.cls?(x.v>0?'up':x.v<0?'down':''):'';homeCountUp(el,x.v,x.f);$('home-kpi-sub-'+i).textContent=x.sub;});
  SQCharts.spark($('home-spark'),curve.slice(-200));
  // 회의실 상태 문장
  $('home-office-state').textContent=$('office-launch-state')?.textContent||'';
  // 최근 매매 보고
  const trades=(a.trades||[]).slice(-8).reverse();
  $('home-trades').innerHTML=trades.length?trades.map(t=>{const sell=t.side==='sell',title=sell?homeExitTitle(t):'매수',pnl=t.pnl||0;
    return `<div class="htrade ${sell?(pnl>=0?'gain':'loss'):'buy'}"><i>${sell?(pnl>=0?'▲':'▼'):'＋'}</i><div><b>${esc(title)} · ${esc(t.name||t.symbol)}</b><small>${mdhm(t.at)} · ${t.qty}주 × ${won(t.price)}${t.horizon&&t.strategy==='portfolio'?' · '+esc(homeHorizon[t.horizon]||''):''}</small>${sell&&t.reason?`<small class="faint">${esc(String(t.reason).slice(0,60))}</small>`:''}</div>${sell?`<em class="${pnl>=0?'up':'down'}">${signed(pnl)}</em>`:''}</div>`;}).join(''):'<p class="muted">아직 체결된 매매가 없습니다. 매수·익절·손절이 생기면 여기에 쌓입니다.</p>';
  // 자산 흐름
  let pts=(a.equity_curve||[]).filter(p=>Number.isFinite(p.equity));
  if(homeRange)pts=pts.filter(p=>Date.now()-new Date(p.at).getTime()<=homeRange*86400000);
  const stepN=Math.max(1,Math.ceil(pts.length/240));pts=pts.filter((p,i)=>i%stepN===0||i===pts.length-1);
  SQCharts.line($('home-equity'),pts.map(p=>({y:p.equity,label:new Date(p.at).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric'}),tip:mdhm(p.at),extra:p.cash!=null?'현금 '+won(p.cash):''})),
    {baseline:init,name:'평가자산',format:(v,axis)=>axis?Math.round(v/10000).toLocaleString('ko-KR')+'만':won(v),empty:'자산 기록이 쌓이면 그래프가 그려집니다'});
  $('home-equity-note').textContent=pts.length>1?`점선은 시작 금액 ${won(init)} · 5분 간격 기록 ${pts.length}점`:'';
  // 자산 구성
  const mix={cash:Math.max(0,a.cash||0)};
  for(const p of pos){const h=p.strategy==='portfolio'?(p.horizon||'short'):'legacy';mix[h]=(mix[h]||0)+homePrice(a,p)*p.qty;}
  const labels={cash:'현금',...homeHorizon},segs=Object.entries(mix).filter(([,v])=>v>0).map(([k,v])=>({label:labels[k],value:v,color:homeMixColors[k]}));
  const total=segs.reduce((s,x)=>s+x.value,0);
  SQCharts.donut($('home-mix'),segs,{title:'투자 비중',value:total?((1-(mix.cash||0)/total)*100).toFixed(0)+'%':'—'});
  $('home-mix-legend').innerHTML=segs.map(s=>`<li><i style="background:${s.color}"></i><span>${esc(s.label)}</span><b>${(s.value/total*100).toFixed(1)}%</b><small>${won(s.value)}</small></li>`).join('');
  // 일별 손익
  const days=Object.entries(a.days||{}).sort(([x],[y])=>x<y?-1:1).slice(-30);
  SQCharts.bars($('home-days'),days.map(([day,v])=>({label:day.slice(5).replace('-','/'),value:v.realized_pnl||0,tip:day,extra:`매수 ${v.buys||0} · 매도 ${v.sells||0}건`})),
    {name:'실현손익',format:(v,axis)=>axis?Math.round(v/1000).toLocaleString('ko-KR')+'천':signed(v),empty:'장 마감 정리 후 날마다 막대가 쌓입니다'});
  const sum=days.reduce((s,[,v])=>s+(v.realized_pnl||0),0);
  $('home-days-note').textContent=days.length?`최근 ${days.length}거래일 실현손익 합계 ${signed(sum)}`:'';
  // 보유 종목 수익률
  const rows=pos.map(p=>{const cur=homePrice(a,p),pct=(cur/p.entry_price-1)*100;return {label:(p.name||p.symbol)+(p.strategy==='portfolio'?' · '+(homeHorizon[p.horizon]||''):''),value:pct,sub:`${p.qty}주 · 평균 ${won(p.entry_price)} → 현재 ${won(cur)} · 평가 ${signed((cur-p.entry_price)*p.qty)}`};}).sort((x,y)=>y.value-x.value);
  SQCharts.hbars($('home-holdings'),rows,{empty:'보유 종목이 없습니다'});
  $('home-holdings-note').textContent=rows.length?'막대 위에 마우스를 올리면 평균 매수가와 현재가를 볼 수 있어요':'';
  // 매매 성적
  const rep=a.reporting||{},exits=rep.exits||[],wins=exits.filter(e=>e.pnl>0),losses=exits.filter(e=>e.pnl<=0);
  const avg=list=>list.length?list.reduce((s,e)=>s+e.pnl,0)/list.length:null;
  $('home-stats').innerHTML=[
    ['승률',rep.win_rate==null?'—':rep.win_rate.toFixed(0)+'%',`정리한 거래 ${rep.closed_count||0}건`],
    ['평균 이익',avg(wins)==null?'—':signed(avg(wins)),`이익 ${wins.length}건`],
    ['평균 손실',avg(losses)==null?'—':signed(avg(losses)),`손실 ${losses.length}건`],
    ['최대 하락폭',rep.max_drawdown_pct==null?'—':rep.max_drawdown_pct.toFixed(2)+'%','고점 대비']].map(([k,v,s])=>`<div><span>${k}</span><b>${v}</b><small>${s}</small></div>`).join('');
  const dd=(rep.drawdown||[]).filter((p,i,arr)=>i%Math.max(1,Math.ceil(arr.length/200))===0);
  SQCharts.line($('home-dd'),dd.map(p=>({y:p.pct,label:new Date(p.at).toLocaleDateString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric'}),tip:mdhm(p.at)})),
    {color:SQCharts.palette().down,name:'고점 대비',format:v=>v.toFixed(2)+'%',empty:'하락폭 기록이 아직 없습니다'});
  loadHomeNews().catch(()=>{});
}

async function loadHomeNews(force){
  if(!force&&homeNews&&Date.now()-homeNewsAt<300000)return renderHomeNews();
  homeNews=await api('/news/center?hours=48');homeNewsAt=Date.now();renderHomeNews();
}
function renderHomeNews(){
  const box=$('home-news');if(!box||!homeNews)return;
  const score=r=>(r.categories.includes('holding')?4:0)+(r.cited?2:0)+(r.important?2:0)+(r.categories.includes('candidate')?1:0);
  const rows=[...homeNews.rows].sort((x,y)=>score(y)-score(x)||(x.published_at<y.published_at?1:-1)).slice(0,6);
  box.innerHTML=rows.length?rows.map(r=>`<div class="hnews"><span class="tag cat-${esc(r.category)}">${esc(homeNews.categories[r.category]||'')}</span>${r.names?.length?`<span class="hnews-names">${esc(r.names.slice(0,2).join(', '))}</span>`:''}${r.cited?'<span class="tag ai">AI 인용</span>':''}${r.english&&!r.title_ko?'<span class="tag warn">번역 대기</span>':''}<a ${r.url?`href="${esc(r.url)}" target="_blank" rel="noopener noreferrer"`:''}>${esc(r.title_ko||r.title)}</a><small>${esc(r.source||'')} · ${homeAgo(r.published_at)}</small></div>`).join(''):'<p class="muted">최근 48시간 기사가 없거나 아직 수집 전입니다.</p>';
}

// ── 운영 점검 ────────────────────────────────────────────────────────────
let homeHealth=null,homeHealthAt=0;
const healthIcon={ok:'✓',warn:'!',bad:'✕',running:'…'};
const healthAction={daytrade:'투자 운용으로',settings:'연결 · 설정으로',office:'회의실 보기'};
async function loadHealth(force){
  if(!force&&homeHealth&&Date.now()-homeHealthAt<60000)return renderHealthCard();
  homeHealth=await api('/diagnostics'+(force?'?fresh=true':''));homeHealthAt=Date.now();renderHealthCard();
}
function renderHealthCard(){
  const d=homeHealth,card=$('home-health');if(!d||!card)return;
  const bad=d.checks.filter(c=>c.status==='bad'),warn=d.checks.filter(c=>c.status==='warn');
  card.className='panel home-health '+d.overall;
  $('home-health-summary').textContent=bad.length?`확인이 필요한 항목 ${bad.length}개가 있습니다. 아래 빨간 항목부터 해결하면 회의와 매매가 다시 진행됩니다.`:
    warn.length?`주의할 항목 ${warn.length}개가 있습니다.`:'모든 항목이 정상입니다. 예약 시각에 AI 회의와 매매가 자동으로 진행됩니다.';
  const order={bad:0,warn:1,running:2,ok:3};
  $('home-health-list').innerHTML=[...d.checks].sort((x,y)=>order[x.status]-order[y.status]).map(c=>`<div class="hcheck ${c.status}"><i aria-hidden="true">${healthIcon[c.status]||'·'}</i><div><b>${esc(c.label)}</b><p>${esc(c.detail)}</p></div>${c.action&&c.status!=='ok'?`<button class="small" data-goto="${esc(c.action)}">${healthAction[c.action]||'이동'}</button>`:''}</div>`).join('');
  $('home-health-log').innerHTML=(d.ai_history.length?'<h4>AI 판단 기록</h4>'+d.ai_history.map(h=>`<p class="${h.ok?'':'bad-text'}">${mdhm(h.at)} · ${esc(h.kind||'')} · ${h.ok?'성공':'실패 · '+esc(h.error||'')}${h.seconds?` · ${Math.round(h.seconds)}초`:''}</p>`).join(''):'<p class="muted">AI 판단 기록이 아직 없습니다.</p>')+
    '<h4>활동 기록</h4>'+(d.recent_log.map(r=>`<p>${mdhm(r.at)} · ${esc(r.message)}</p>`).join('')||'<p class="muted">기록 없음</p>');
  const m=d.checks.find(c=>c.id==='meeting');window.officeMeetingReason=m&&m.status!=='ok'?m.detail:'';
}
action('home-health-refresh',()=>loadHealth(true));

// ── 뉴스 센터 ────────────────────────────────────────────────────────────
let ncData=null,ncCat='',ncLimit=40;
async function loadNewsCenter(){
  $('nc-status').textContent='불러오는 중…';
  ncData=await api('/news/center?hours='+$('nc-hours').value);ncLimit=40;renderNewsCenter();
}
function ncDayLabel(value){const day=new Date(value).toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});const today=new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});
  const y=new Date(Date.now()-86400000).toLocaleDateString('sv-SE',{timeZone:'Asia/Seoul'});return day===today?'오늘':day===y?'어제':day.slice(5).replace('-','월 ')+'일';}
function renderNewsCenter(){
  const d=ncData;if(!d)return;
  const q=$('nc-search').value.trim().toLowerCase(),cited=$('nc-cited').checked;
  $('nc-cats').innerHTML=[['','전체',d.rows.length],...Object.entries(d.categories).map(([k,v])=>[k,v,d.counts[k]||0])].map(([k,v,n])=>`<button class="${ncCat===k?'active':''} cat-${k||'all'}" data-cat="${k}" ${n||!k?'':'disabled'}>${esc(v)} <small>${n}</small></button>`).join('');
  let rows=d.rows.filter(r=>(!ncCat||r.categories.includes(ncCat))&&(!cited||r.cited)&&(!q||(r.title+' '+(r.title_ko||'')+' '+(r.names||[]).join(' ')+' '+r.source).toLowerCase().includes(q)))
    .sort((x,y)=>x.published_at<y.published_at?1:-1);
  const tr=d.translation||{};
  $('nc-status').textContent=`${rows.length}건 · 최근 ${d.hours}시간 · AI 인용 ${d.cited}건`+(tr.english?` · 영어 제목 ${tr.english}건 중 번역 대기 ${tr.waiting}건`:'')+(tr.paused_until?' · 번역 잠시 중단(사용 한도 대기)':'');
  $('nc-translate').hidden=!tr.waiting;
  const shown=rows.slice(0,ncLimit);let lastDay='';
  $('nc-list').innerHTML=shown.length?shown.map(r=>{const day=ncDayLabel(r.published_at),head=day!==lastDay?`<h3 class="nc-day">${day}</h3>`:'';lastDay=day;
    const title=r.title_ko||r.title;
    return head+`<article class="nc-item ${r.cited?'cited':''} ${r.important?'important':''}"><div class="nc-meta">${r.categories.map(c=>`<span class="tag cat-${esc(c)}">${esc(d.categories[c])}</span>`).join('')}${r.names?.length?`<span class="nc-names">${esc(r.names.slice(0,3).join(', '))}</span>`:''}${r.cited?'<span class="tag ai">AI 인용</span>':''}${r.official?'<span class="tag">공식 발표</span>':''}${r.important?'<span class="tag up">중요</span>':''}<span class="nc-src">${esc(r.source||'')} · ${homeAgo(r.published_at)}</span></div>`+
      (r.url?`<a class="nc-title" href="${esc(r.url)}" target="_blank" rel="noopener noreferrer">${esc(title)} ↗</a>`:`<span class="nc-title">${esc(title)}</span>`)+
      (r.english&&r.title_ko?`<p class="nc-original">원문 · ${esc(r.title)}</p>`:'')+(r.english&&!r.title_ko?'<p class="nc-original"><span class="tag warn">번역 대기</span> 영어 원문 그대로 표시 중</p>':'')+
      (r.body?`<p class="nc-body">${esc(String(r.body).slice(0,180))}${r.body.length>180?'…':''}</p>`:'')+`</article>`;}).join(''):'<p class="muted">조건에 맞는 기사가 없습니다.</p>';
  $('nc-more').hidden=rows.length<=ncLimit;
}
$('nc-cats').addEventListener('click',e=>{const b=e.target.closest('[data-cat]');if(!b||b.disabled)return;ncCat=b.dataset.cat;ncLimit=40;renderNewsCenter();});
$('nc-search').addEventListener('input',()=>{ncLimit=40;renderNewsCenter();});
$('nc-cited').addEventListener('change',renderNewsCenter);
$('nc-hours').addEventListener('change',()=>loadNewsCenter().catch(e=>notify(e.message)));
action('nc-refresh',loadNewsCenter);
$('nc-more').addEventListener('click',()=>{ncLimit+=40;renderNewsCenter();});
action('nc-translate',async()=>{const r=await api('/news/translate','POST',{});notify(r.calls_today>=r.daily_cap?'오늘 번역 한도를 모두 썼습니다. 내일 다시 번역합니다.':'영어 제목 번역을 시작했습니다. 1~2분 뒤 새로고침하세요. 매매 판단 중이면 다음 기회로 미룹니다.');});

// ── 홈 보조 동작 ──────────────────────────────────────────────────────────
$('home-mode').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{dbMode=b.dataset.mode;try{localStorage.setItem('sq-db-mode',dbMode);}catch(e){}loadDashboard().catch(e=>notify(e.message));}));
$('home-range').addEventListener('click',e=>{const b=e.target.closest('[data-range]');if(!b)return;homeRange=Number(b.dataset.range);$('home-range').querySelectorAll('button').forEach(x=>x.classList.toggle('active',x===b));renderHome();});
// 탭을 바꾸면 맨 위에서 시작하고, 홈·회의실에서는 상단 AI 연결 띠를 숨긴다(홈 요약에 같은 정보가 있다).
const homeBaseTab=tab;
tab=function(id){const changed=document.body.dataset.tab!==id;document.body.dataset.tab=id;homeBaseTab(id);if(changed)window.scrollTo(0,0);};
const homeBaseRender=renderDashboard;
renderDashboard=function(){homeBaseRender();renderHome();};

// ── 연결 설정: 웹 로그인 · 인증 보관 위치 · 텔레그램 범위 ───────────────────────
const loginTimers={};
function renderLogin(provider,view){
  const out=$(provider==='claude'?'st-claude-login':'st-codex-login');if(!out)return;
  const waiting=['starting','waiting','verifying'].includes(view.state);
  out.innerHTML=view.state==='idle'?'':`<p class="${view.state==='done'?'ok-text':view.state==='failed'?'bad-text':''}">${esc(view.message)}</p>`+
    (view.url&&waiting?`<p><a href="${esc(view.url)}" target="_blank" rel="noopener noreferrer" class="login-link">로그인 주소 열기 ↗</a></p>`:'')+
    (view.code&&waiting?`<p class="device-code" aria-label="기기 코드">${esc(view.code)}</p>`:'');
  $(provider==='claude'?'st-claude-start':'st-codex-start').textContent=waiting?'다시 시작':'웹에서 로그인';
  $(provider==='claude'?'st-claude-cancel':'st-codex-cancel').hidden=!waiting;
  if(provider==='claude')$('st-claude-code-row').hidden=!(waiting&&view.needs_code&&view.state!=='verifying');
  clearTimeout(loginTimers[provider]);
  if(waiting)loginTimers[provider]=setTimeout(()=>api('/ai/login/'+provider).then(v=>{renderLogin(provider,v);if(v.state==='done')afterLogin();}).catch(()=>{}),3000);
}
function afterLogin(){notify('AI 로그인을 마쳤습니다. 연결 상태를 다시 확인합니다.');loadAuthStorage().catch(()=>{});if(typeof refreshTerminalAuth==='function')refreshTerminalAuth(true);checkAllAiAuth().catch(()=>{});}
async function loadAuthStorage(){
  const d=await api('/ai/auth');
  $('st-auth-storage').innerHTML=Object.entries(d.storage||{}).map(([k,s])=>`<span class="hchip ${s.persistent?'ok':'bad'}"><i></i>${k==='codex'?'ChatGPT':'Claude'} 인증 · ${esc(s.message)}</span>`).join(' ')||'<span class="hchip muted"><i></i>인증 보관 위치는 NAS 컨테이너에서 확인됩니다</span>';
  $('st-claude-source').textContent='현재 Claude 인증 방식: '+d.claude_source;
  for(const p of ['claude','codex'])renderLogin(p,d.logins[p]);
}
for(const p of ['claude','codex']){
  action(`st-${p}-start`,async()=>{const out=$(p==='claude'?'st-claude-login':'st-codex-login');out.textContent='로그인 주소를 받는 중입니다…';renderLogin(p,await api(`/ai/login/${p}/start`,'POST',{}));});
  action(`st-${p}-cancel`,async()=>renderLogin(p,await api(`/ai/login/${p}/cancel`,'POST',{})));
}
action('st-claude-code-send',async()=>{const v=await api('/ai/login/claude/code','POST',{code:$('st-claude-code').value.trim()});$('st-claude-code').value='';renderLogin('claude',v);if(v.state==='done')afterLogin();});
action('st-claude-token-save',async()=>{const r=await api('/ai/claude-token','POST',{action:'save',token:$('st-claude-token').value});$('st-claude-token').value='';notify(r.status?.authenticated?'Claude 토큰을 저장했습니다. 업데이트 후에도 유지됩니다.':'저장했지만 확인되지 않았습니다: '+(r.status?.message||''));loadAuthStorage().catch(()=>{});if(typeof refreshTerminalAuth==='function')refreshTerminalAuth(true);});
action('st-claude-token-reset',async()=>{await api('/ai/claude-token','POST',{action:'reset'});notify('Claude 인증을 .env 방식으로 되돌렸습니다.');loadAuthStorage().catch(()=>{});});
async function loadNotifyLevel(){const s=await api('/notify/status');document.querySelectorAll('input[name="tg-level"]').forEach(r=>r.checked=r.value===s.level);
  $('st-tg-link').textContent=s.public_url_set?'알림에 웹 화면 링크가 붙습니다.':'.env 에 PUBLIC_URL=http://NAS주소:8430 을 넣으면 알림에 웹 화면 링크가 붙습니다.';}
document.querySelectorAll('input[name="tg-level"]').forEach(r=>r.addEventListener('change',async()=>{try{const s=await api('/notify/level','POST',{level:r.value});notify(s.level==='trades'?'텔레그램은 이제 매수·익절·손절·결산만 보냅니다.':'텔레그램에 계획·판단·복기도 함께 보냅니다.');}catch(e){notify(e.message);loadNotifyLevel().catch(()=>{});}}));
const homeBaseSettings=loadSettings;
loadSettings=async function(){await homeBaseSettings();await Promise.allSettled([loadAuthStorage(),loadNotifyLevel()]);};
