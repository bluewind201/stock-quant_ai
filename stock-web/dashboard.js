'use strict';
// 대시보드. 단타 계좌 하나를 골라 "오늘 어디까지 왔고, 돈이 어떻게 움직였나" 를 한 화면에 그린다.
// 숫자는 전부 /api/dashboard 한 번으로 받는다. 여기서 주문은 나가지 않는다.
let dbData=null,dbMode='',dbLoading=false;
try{dbMode=localStorage.getItem('sq-db-mode')||'';}catch(e){}
const dbPhase={idle:'대기',holiday:'휴장일',scanned:'스캔 완료',planning:'AI 판단 중',planned:'매수 대기',entering:'매수 중',monitoring:'장중 감시',eod_reviewed:'마감 판단 완료',closed:'정리 완료'};

async function loadDashboard(){
  if(dbLoading||!authenticated)return;dbLoading=true;
  try{
    dbData=await api('/dashboard?fast=true'+(dbMode?'&mode='+dbMode:''));
    renderDashboard();
  }finally{dbLoading=false;}
}

function connBadge(label,ok,title){return `<span class="badge ${ok?'ok':'bad'}" title="${esc(title||'')}">${ok?'●':'○'} ${label}</span>`;}

function renderDashboard(){
  const d=dbData;if(!d)return;
  const a=d.account;const c=d.connections;const live=d.mode==='live';
  // 모드 선택 (모의 / 실거래)
  $('db-mode').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b.dataset.mode===d.mode);b.textContent=(b.dataset.mode==='live'?'실거래':'모의')+(d.has[b.dataset.mode]?'':' (없음)');});
  $('db-live-banner').hidden=!live;
  $('db-title').textContent=live?'오늘 현황 · 실거래':'오늘 현황 · 모의';
  $('trade-mode-badge').textContent=d.live_running?'실거래 운용 중':'가상 주문';$('trade-mode-badge').className='badge '+(d.live_running?'bad':'');
  // 상태 줄
  const claudeOk=c.claude&&c.claude.installed&&c.claude.authenticated&&!c.claude.api_key_used;
  $('db-status').innerHTML=[
    `<span class="badge accent">${esc(d.day)} · ${d.market_session?'거래일':'휴장'}</span>`,
    a?`<span class="badge ${a.status==='running'?(live?'bad':'ok'):'warn'}">${a.status==='running'?(live?'실거래 운용 중':'모의 운용 중'):'운용 정지'}</span>`:'',
    a?`<span class="badge">${dbPhase[a.phase]||a.phase}</span>`:'',
    connBadge('토스 시세',c.toss,'TOSS_CLIENT_ID / SECRET'),
    connBadge(c.claude?.model||'AI',claudeOk,c.claude&&c.claude.message||''),
    connBadge('텔레그램',c.telegram,'TELEGRAM_BOT_TOKEN / CHAT_ID'),
  ].join('');
  const problems=[];
  if(!c.toss)problems.push('토스 키가 없어 시세·수급을 못 봅니다.');
  if(!claudeOk&&!c.claude?.checking)problems.push('선택한 AI의 구독 연결을 확인하세요.');
  if(a){for(const v of Object.values(a.errors||{}))if(v)problems.push(v);if(a.ai&&a.ai.error)problems.push(a.ai.error);}
  $('db-alert').textContent=problems.join(' · ');
  $('db-empty').hidden=!!a;$('db-view').hidden=!a;
  if(!a){$('db-guide').open=!d.has.paper&&!d.has.live;
    $('db-empty-title').textContent=live?'실거래 계좌가 없습니다':'모의 계좌가 없습니다';
    $('db-empty-text').textContent=live?(d.live_status.available?'토스 실계좌에 실제 주문을 내는 계좌입니다. 모의로 며칠 검증한 뒤 만드세요.':'실거래가 꺼져 있습니다. NAS .env 에 ENABLE_LIVE_TRADING=true 와 TOSS_ACCOUNT_SEQ 를 넣고 컨테이너를 다시 만든 뒤 만들 수 있습니다.'):'실제 돈은 쓰지 않습니다. 같은 규칙을 가상자금으로 돌려 잘 맞는지 봅니다.';
    $('db-goto-create').textContent=live?'실거래 계좌 만들기':'모의 계좌 만들기';
    $('db-sub').textContent='';return;}
  $('db-sub').textContent=`${a.status==='running'?'프로그램이 시각에 맞춰 자동으로 진행합니다.':'운용이 정지되어 있습니다. 당일 단타 화면에서 "운용 시작"을 누르세요.'} 마지막 확인 ${hm(a.last_poll_at)}`;

  // KPI
  const cfg=a.config,eq=a.equity,init=cfg.initial_cash,ret=(eq/init-1)*100;
  const todaySells=(a.trades||[]).filter(t=>t.side==='sell'&&(t.at||'').startsWith(a.day));
  const todayPnl=todaySells.reduce((s,t)=>s+(t.pnl||0),0)+(a.unrealized||0);
  const wins=todaySells.filter(t=>t.pnl>0).length;
  $('db-kpis').innerHTML=[
    [live?'평가자산 (실제)':'평가자산',won(eq),`${live?'투입':'시작'} ${won(init)} · 현금 ${won(a.cash)}`],
    ['오늘 손익',`<span class="${todayPnl>0?'up':todayPnl<0?'down':''}">${signed(todayPnl)}</span>`,`실현 ${todaySells.length}건 (승 ${wins} 패 ${todaySells.length-wins}) + 평가 ${signed(a.unrealized||0)}`],
    ['누적 수익률',pctc(ret),`누적 실현 ${signed(a.realized_pnl)}`],
    ['보유 종목',`${Object.keys(a.positions).length} <small style="font-size:14px;color:var(--muted)">/ ${cfg.max_positions}</small>`,`AI 호출 오늘 ${a.ai.calls_today} / ${cfg.max_ai_calls_per_day}회`],
  ].map(([k,v,s])=>`<div class="kpi"><span>${k}</span><strong>${v}</strong><small>${s}</small></div>`).join('');

  // 오늘의 흐름. 시각은 "예정" 이 아니라 실제로 한 시각을 보여준다.
  const order=['idle','scanned','planning','planned','entering','monitoring','eod_reviewed','closed'];
  const idx=order.indexOf(a.phase);
  const st=a.stamps||{};
  const steps=[
    ['장전 스캔 · AI 판단',cfg.scan_at,st.plan||st.scan,'후보를 거르고 AI가 고릅니다',['scanned','planning']],
    ['매수',cfg.entry_at,st.entry,`${cfg.entry_window_minutes}분 안에 계획대로 삽니다`,['planned','entering']],
    ['장중 감시','~'+cfg.eod_review_at,null,'목표가·손절가를 지킵니다. AI는 쉽니다',['monitoring']],
    ['마감 판단',cfg.eod_review_at,st.eod,'내일까지 들고 갈지, 눌린 종목을 살지 AI가 봅니다',['eod_reviewed']],
    ['정리 · 복기',cfg.force_close_at,st.review||st.closed,'남은 것을 팔고, 오늘을 복기해 내일 숫자를 조정합니다',['closed']],
  ];
  const stepIdx=steps.map(s=>Math.max(...s[4].map(p=>order.indexOf(p))));
  $('db-timeline').innerHTML=steps.map((s,i)=>{const done=a.phase==='holiday'?false:idx>stepIdx[i];const now=s[4].includes(a.phase);
    const actual=s[2]?hm(s[2]):'';
    const when=actual?`<b>${actual}</b>`:`<span class="faint">${esc(s[1])}</span>`;
    const late=actual&&actual>s[1].slice(0,5)?' title="예정 '+esc(s[1])+'"':'';
    return `<div class="step ${done?'done':''} ${now?'now':''}"${late}><span class="when">${when}</span><b>${done?'✓ ':now?'▶ ':''}${s[0]}</b><small>${s[3]}</small>${actual&&actual!==s[1].slice(0,5)?`<small class="faint">예정 ${esc(s[1])}</small>`:''}</div>`;}).join('');
  const trig=(a.stamps||{}).plan_trigger?` · 오늘 장전 판단: ${a.stamps.plan_trigger}`:'';
  const retry=(a.retry&&a.retry.attempts>1?` · ${a.retry.attempts}회 시도`:'')+trig;
  if(a.sessions)$('db-status').innerHTML+=`<span class="badge">장 ${esc(a.sessions.open)}~${esc(a.sessions.close)}</span>`;
  $('db-next').textContent=(a.phase==='holiday'?'오늘은 휴장일입니다. 다음 거래일 스캔 시각에 다시 시작합니다.':a.status!=='running'?'운용이 정지되어 있어 진행하지 않습니다.':
    idx<=0?`다음: ${cfg.scan_at} 장전 스캔`:a.phase==='planned'?`다음: ${cfg.entry_at} 매수`:a.phase==='monitoring'?`다음: ${cfg.eod_review_at} 마감 판단 · 그 사이 급등·새 기사가 있으면 AI에게 묻습니다`:a.phase==='eod_reviewed'?`다음: ${cfg.force_close_at} 정리`:a.phase==='closed'?'오늘 일정이 끝났습니다. 내일 '+cfg.scan_at+'에 다시 시작합니다.':'')+retry;

  // 간밤 미국 (장전 판단에 넘긴 숫자 그대로)
  const ov=(a.ai_inputs||{}).overnight_us;
  $('db-overnight-panel').hidden=!ov||!ov.rows||!ov.rows.length;
  if(ov&&ov.rows&&ov.rows.length){
    const ewy=ov.rows.find(r=>r.symbol==='EWY'),sox=ov.rows.find(r=>r.symbol==='SOXX');
    const heavy=(ewy&&ewy.change_pct<=-2)||(sox&&sox.change_pct<=-3);
    const up=ewy&&ewy.change_pct>=2;
    $('db-overnight').innerHTML=`<div class="kpis" style="grid-template-columns:repeat(${Math.min(ov.rows.length,6)},1fr);margin-bottom:10px">`
      +ov.rows.map(r=>`<div class="kpi" title="${esc(r.why||'')}"><span>${esc(r.label)}</span><strong class="${r.change_pct>0?'up':r.change_pct<0?'down':''}">${r.change_pct>0?'+':''}${r.change_pct.toFixed(2)}%</strong><small>3일 ${r.change_3d_pct>0?'+':''}${r.change_3d_pct.toFixed(2)}%</small></div>`).join('')+'</div>'
      +`<p class="small ${heavy?'up':''}">${ov.as_of} 미국 종가 기준 · ${heavy?'<b>간밤 한국·반도체가 크게 밀렸습니다. 갭하락 출발 가능성이 높습니다.</b>':up?'<b>간밤 한국 관련 자산이 크게 올랐습니다. 갭상승 가능성이 높습니다.</b>':'큰 변동은 없었습니다.'}</p>`
      +`<p class="small muted">${esc(ov.note||'')}</p>`;
  }

  // 차트
  drawEquity(a);drawDays(a);

  // 보유
  const pos=Object.values(a.positions);
  $('db-positions').innerHTML=pos.length?`<div class="pos-cards">${pos.map(p=>{const q=a.quotes[p.symbol];const cur=q?q.bid:p.entry_price;const pnl=(cur-p.entry_price)*p.qty,pct=(cur/p.entry_price-1)*100;
    const span=p.tp_price-p.sl_price||1;const pos=v=>Math.min(100,Math.max(0,(v-p.sl_price)/span*100));const at=pos(cur),en=pos(p.entry_price);
    return `<div class="pos-card"><div class="row"><span class="name">${esc(p.name||p.symbol)}</span>${p.hold_overnight?'<span class="tag ok">익일 보유</span>':''}</div><div class="pnl ${pnl>0?'up':pnl<0?'down':''}">${signed(pnl)} <small style="font-size:13px">(${pct>0?'+':''}${pct.toFixed(2)}%)</small></div><div class="small muted">${p.qty}주 · 진입 ${fmt(p.entry_price,0)} → 현재 ${fmt(cur,0)}</div><div class="track" title="손절가 ~ 목표가 사이에서 현재가가 어디쯤인지"><div class="fill" style="left:${Math.min(en,at).toFixed(1)}%;width:${Math.abs(at-en).toFixed(1)}%;background:${pnl>=0?'var(--up)':'var(--down)'}"></div><div class="entry" style="left:${en.toFixed(1)}%"></div><div class="cur" style="left:${at.toFixed(1)}%"></div></div><div class="marks"><span class="down">손절 ${fmt(p.sl_price,0)} (-${p.sl_pct}%)</span><span class="up">목표 ${fmt(p.tp_price,0)} (+${p.tp_pct}%)</span></div></div>`;}).join('')}</div>`
    :`<p class="muted">${a.phase==='closed'?'오늘 정리가 끝났습니다.':a.plan&&a.plan.picks.length&&['planned'].includes(a.phase)?'매수 대기 중입니다.':'보유 종목이 없습니다.'}</p>`;

  // 계획
  const plan=a.plan;
  const stanceTag={no_trade:'<span class="tag down">오늘 오전 매수 없음</span>',cautious:'<span class="tag policy">신중</span>'};
  $('db-plan').innerHTML=plan?themeRanking(plan)+(stanceTag[plan.market_stance]||'')+(plan.market_view?`<p class="small"><b>시장 관점</b> ${esc(plan.market_view)}</p>`:'')
    +(plan.picks.length?plan.picks.map(p=>`<div class="feed-item"><strong>${esc(p.name||p.symbol)} <span class="muted">${esc(p.symbol)}</span> <span class="tag">비중 ${p.weight_pct}%</span><span class="tag up">목표 +${p.take_profit_pct}%</span><span class="tag down">손절 -${p.stop_loss_pct}%</span></strong><p>${esc(p.thesis)}</p><p class="small muted">반대 근거 · ${esc(p.counter_case)}</p></div>`).join('')
      :`<p class="muted">${plan.market_stance==='no_trade'?'오늘 오전은 사지 않습니다 — '+esc(plan.no_trade_reason||plan.summary||'')+'<br>마감 무렵 다시 판단합니다(오늘 크게 눌린 종목은 내일 반등을 보고 살 수 있습니다).':'오늘은 살 종목이 없다고 판단했습니다. '+esc(plan.summary||'')}</p>`)
    :`<p class="muted">${a.phase==='planning'?'AI가 판단하는 중입니다 (1~2분).':'아직 장전 판단이 없습니다.'}</p>`;

  // 기록 · AI 입력
  $('db-log').innerHTML=(a.log||[]).slice().reverse().slice(0,12).map(l=>`<div class="feed-item"><small>${mdhm(l.at)}</small> ${esc(l.message)}</div>`).join('')||'<p class="muted">기록 없음</p>';
  const rv=(a.tuning||{}).last_review;
  const lessons=(a.tuning||{}).lessons||[];
  $('db-review').innerHTML=(a.ai&&a.ai.pending?'<p class="small accent">AI가 판단 중입니다…</p>':'')+(rv?`<p class="small"><span class="tag ok">${esc(rv.day)} 복기 · ${mdhm(rv.at)}</span> ${esc(rv.summary)}</p>`
      +(rv.diagnosis?`<p class="small muted">${esc(rv.diagnosis)}</p>`:'')
      +(rv.applied&&rv.applied.length?'<p class="small"><b>바꾼 설정</b></p>'+rv.applied.map(x=>`<div class="feed-item"><strong>${esc(x.key)} ${esc(x.from)} → ${esc(x.to)}</strong><p class="small muted">${esc(x.reason)}</p></div>`).join('')
        :'<p class="small muted">설정은 그대로 두었습니다.</p>')
    :'<p class="muted">아직 복기가 없습니다. 장 마감 정리 뒤에 AI가 오늘을 되짚고 내일 숫자를 조정합니다.</p>')
    +(lessons.length?'<p class="small" style="margin-top:10px"><b>내일 적용할 교훈</b></p><ul class="small" style="margin:4px 0 0;padding-left:18px">'+lessons.map(l=>`<li>${esc(l)}</li>`).join('')+'</ul>':'');
  const inp=a.ai_inputs;
  $('db-inputs').innerHTML=inp?`<p class="small"><span class="tag ok">${mdhm(inp.at)}</span> 후보 <b>${inp.candidates}종</b> · 종목 기사 <b>${inp.candidate_news}건</b>(본문) · 국내 시장 제목 <b>${inp.kr_market_news.length}건</b> · 미국 거시 <b>${inp.us_evidence.length}건</b>${inp.news_freshest_at?` · 가장 최근 기사 ${mdhm(inp.news_freshest_at)}`:''}</p>`+(inp.kr_market_news.slice(0,4).map(n=>`<div class="feed-item"><small>${esc(n.source)} · ${mdhm(n.published_at)}</small><strong>${external(n.url,n.title)}</strong></div>`).join(''))
    :'<p class="muted">아직 오늘 장전 판단이 없습니다.</p>';
}

// ── 차트 (외부 라이브러리 없이 캔버스) ───────────────────────────────────
function canvasCtx(id){const canvas=$(id),width=canvas.clientWidth,height=canvas.clientHeight,dpr=devicePixelRatio||1;canvas.width=width*dpr;canvas.height=height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);ctx.font='11px sans-serif';return {ctx,width,height};}
function drawEquity(a){
  const {ctx,width,height}=canvasCtx('db-equity');
  const pts=(a.equity_curve||[]);const init=a.config.initial_cash;
  if(pts.length<2){$('db-equity-note').textContent='운용을 시작하면 5분마다 점이 찍힙니다. 아직 자료가 부족합니다.';ctx.fillStyle='#98a4b5';ctx.fillText('자료 없음',10,20);return;}
  const left=8,right=70,top=12,bottom=height-26;
  const vals=pts.map(p=>p.equity);let lo=Math.min(init,...vals),hi=Math.max(init,...vals);const pad=(hi-lo)*.1||init*.01;lo-=pad;hi+=pad;
  const x=i=>left+i/(pts.length-1)*(width-left-right),y=v=>top+(hi-v)/(hi-lo)*(bottom-top);
  ctx.strokeStyle='#30435a';ctx.fillStyle='#a0b5ce';
  for(let i=0;i<4;i++){const v=lo+(hi-lo)*i/3;ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(width-right,y(v));ctx.stroke();ctx.fillText((v/1e4).toFixed(0)+'만',width-right+6,y(v)+4);}
  // 시작 자금선
  ctx.setLineDash([4,4]);ctx.strokeStyle='#98a4b5';ctx.beginPath();ctx.moveTo(left,y(init));ctx.lineTo(width-right,y(init));ctx.stroke();ctx.setLineDash([]);
  const last=vals[vals.length-1];const color=last>=init?'#e5484d':'#2b6cdf';
  ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(x(i),y(p.equity)):ctx.moveTo(x(i),y(p.equity)));ctx.strokeStyle=color;ctx.lineWidth=2;ctx.stroke();
  ctx.lineTo(x(pts.length-1),y(init));ctx.lineTo(x(0),y(init));ctx.closePath();ctx.fillStyle=color+'18';ctx.fill();ctx.lineWidth=1;
  // 날짜 눈금: 날이 바뀌는 지점
  ctx.fillStyle='#a0b5ce';let lastDay='';pts.forEach((p,i)=>{const day=p.at.slice(5,10);if(day!==lastDay){lastDay=day;if(i===0||x(i)-left>34)ctx.fillText(day,x(i),height-8);}});
  $('db-equity-note').textContent=`${mdhm(pts[0].at)} ~ ${mdhm(pts[pts.length-1].at)} · ${pts.length}점 · 점선은 시작 자금`;
}
function drawDays(a){
  const {ctx,width,height}=canvasCtx('db-days');
  const days=Object.entries(a.days||{}).sort().slice(-20);
  if(!days.length){$('db-days-note').textContent='하루가 마감 정리되면 그날 실현손익이 막대로 쌓입니다.';ctx.fillStyle='#98a4b5';ctx.fillText('자료 없음',10,20);return;}
  const left=8,right=70,top=12,bottom=height-26;const vals=days.map(([,d])=>d.realized_pnl);
  const mx=Math.max(1,...vals.map(Math.abs));const y=v=>top+(mx-v)/(2*mx)*(bottom-top);const bw=(width-left-right)/days.length;
  ctx.strokeStyle='#30435a';ctx.fillStyle='#a0b5ce';[mx,0,-mx].forEach(v=>{ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(width-right,y(v));ctx.stroke();ctx.fillText((v/1e4).toFixed(0)+'만',width-right+6,y(v)+4);});
  days.forEach(([day,d],i)=>{const v=d.realized_pnl;ctx.fillStyle=v>=0?'#e5484d':'#2b6cdf';const x=left+i*bw+bw*.2;ctx.fillRect(x,Math.min(y(0),y(v)),bw*.6,Math.max(1,Math.abs(y(v)-y(0))));ctx.fillStyle='#a0b5ce';if(days.length<=10||i%2===0)ctx.fillText(day.slice(5),x-2,height-8);});
  const total=vals.reduce((s,v)=>s+v,0),w=days.filter(([,d])=>d.realized_pnl>0).length;
  $('db-days-note').textContent=`최근 ${days.length}일 · 합계 ${signed(total)} · 수익일 ${w} / 손실일 ${days.length-w}`;
}

$('db-mode').querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{dbMode=b.dataset.mode;try{localStorage.setItem('sq-db-mode',dbMode);}catch(e){}loadDashboard().catch(e=>notify(e.message));}));
action('db-refresh',loadDashboard);
action('db-review-now',async()=>{
  let a=dbData&&dbData.account;
  if(!a){await loadDashboard();a=dbData&&dbData.account;}
  if(!a)throw Error('단타 계좌가 없습니다. 먼저 계좌를 만드세요.');
  $('db-review').innerHTML='<p class="muted">AI가 다시 복기하는 중입니다… 1~2분 걸립니다.</p>';
  await api(`/daytrade/accounts/${a.id}/control`,'POST',{action:'review_now'});
  notify('마감 복기를 시작했습니다. 끝나면 이 화면에 결과가 바뀝니다.');
  setTimeout(()=>loadDashboard().catch(()=>{}),20000);});
action('db-goto-create',()=>{if(typeof setDtMode==='function'){dtMode=dbData?dbData.mode:'paper';try{localStorage.setItem('sq-dt-mode',dtMode);}catch(e){}$('dt-mode').querySelectorAll('button').forEach(x=>x.classList.toggle('active',x.dataset.mode===dtMode));}tab('daytrade');});
action('db-goto-settings',()=>tab('settings'));
window.addEventListener('resize',()=>{if(dbData&&dbData.account&&!$('dashboard').hidden){drawEquity(dbData.account);drawDays(dbData.account);}});
