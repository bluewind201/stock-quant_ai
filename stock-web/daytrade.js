'use strict';
// 당일 단타 탭. 선택은 하나뿐이다: 모의 운용이냐 실거래냐. 종목은 매일 AI 가 고른다.
// 모의 = 가상 체결로 규칙이 잘 맞는지 본다. 실거래 = 토스 실계좌에 실제 주문이 나간다.
let dtStatus=null,dtState=null,dtMode='paper',dtLoading=false,dtEditing=false;
const dtPhase={idle:'대기',holiday:'휴장',scanned:'스캔 완료',planning:'AI 판단 중',planned:'매수 대기',entering:'매수 중',monitoring:'장중 감시',eod_reviewed:'마감 판단 완료',closed:'정리 완료'};
const orderStatus={DRAFT:'준비',SUBMITTING:'전송 중',OPEN:'미체결',CANCELING:'취소 중',UNKNOWN:'결과 불명확',DONE:'완료',BLOCKED:'보류'};
const isLive=()=>dtMode==='live';
const strategyNotes={ai:'뉴스·테마·차트를 AI가 종합 판단합니다.',orb:'09:30~11:00 시초 15분 고가 신규 돌파 + 완료 5분봉 거래량 1.5배를 확인합니다.',retest:'시초 고가 돌파 → 지지 재시험 → 직전 봉 고가 재돌파와 거래량 회복을 기다립니다.',bull_flag:'강한 상승 뒤 두 개 조정봉의 거래량 수축과 고가 재돌파를 확인합니다.'};
function showStrategyNote(){ const basket=$('dt-strategy').value==='top5_morning';$('dt-basket-fields').hidden=!basket; $('dt-strategy-note').textContent=(strategyNotes[$('dt-strategy').value]||'')+(basket?' 보유가 없는 계좌에서 운용 정지 후 선택하세요.':' 전략 변경은 운용 정지 후 가능합니다. 기존 보유의 진입 기준·손절가는 유지합니다.'); }
$('dt-strategy').addEventListener('change',showStrategyNote);
strategyNotes.in_play='AI가 조건부 승인한 종목을 자동 감시하고 첫 5분 고가 돌파 시 위험 예산 안에서 매수합니다. 활동 기록에서 승인·자료·패턴 보류 사유를 확인하세요.';
strategyNotes.top5_morning='당일 후보 상위 5종목을 동일 비중으로 오전 매수하고 익절·손절 또는 오전 정리 시각에 매도합니다. 별도 돌파 신호를 기다리지 않습니다.';
function fillBasket(c){for(const [id,key,fallback] of [['dt-basket-entry','basket_entry_at','09:05'],['dt-basket-exit','basket_exit_at','11:50'],['dt-basket-window','basket_entry_minutes',10],['dt-basket-tp','basket_tp_pct',4],['dt-basket-sl','basket_sl_pct',2]])$(id).value=c[key]??fallback;}

async function loadDaytrade(){
  // 설정을 고치는 중에는 자동 새로고침을 쉰다. 입력하던 값이 덮이면 안 된다.
  if(dtLoading||dtEditing||!authenticated)return;dtLoading=true;
  try{
    const [status,brief]=await Promise.all([api('/daytrade/status'),api('/financial-brief')]);dtStatus=status;
    $('dt-financial').innerHTML=(brief.events||[]).map(e=>`<div class="feed-item">${mdhm(e.scheduled_at)} · ${esc(e.title)} <a href="${esc(e.url)}" target="_blank" rel="noopener">출처</a></div>`).join('')||'수집된 확정 일정 없음';
    $('dt-financial').innerHTML+=(brief.attention||[]).slice(0,4).map(e=>`<div class="feed-item">관심 이슈(일정 미확인): ${esc(e.title)}</div>`).join('')+`<p>한국시간 · 미국장은 내일 정오까지 · 확인 ${mdhm(brief.as_of)}${(brief.missing_sources||[]).length?' · 일부 공급원 미확인':''}</p>`;
    if(!$('dt-form-filled').value){fillDtDefaults(dtStatus.defaults);$('dt-form-filled').value='1';}
    dtState=dtStatus[dtMode];
    renderDtMode();
    if(dtState)renderDaytrade();else renderDtEmpty();
  }finally{dtLoading=false;}
}

function setDtMode(mode){dtMode=mode;try{localStorage.setItem('sq-dt-mode',mode);}catch(e){}$('dt-mode').querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));loadDaytrade().catch(e=>notify(e.message));}
try{dtMode=localStorage.getItem('sq-dt-mode')||'paper';}catch(e){}
$('dt-mode').querySelectorAll('button').forEach(b=>{b.classList.toggle('active',b.dataset.mode===dtMode);b.addEventListener('click',()=>setDtMode(b.dataset.mode));});

function renderDtMode(){
  const ls=dtStatus.live_status;
  $('dt-mode-note').className='notice '+(isLive()?'bad':'info');
  $('dt-mode-note').innerHTML=isLive()
    ?`<b>실거래</b> — 토스 실계좌에 실제 주문이 나갑니다. 투입 자금 상한 ${won(ls.capital_cap_krw)} · 주문 1건 상한 ${won(ls.order_cap_krw)} (NAS .env 의 LIVE_CAPITAL_KRW / LIVE_MAX_ORDER_KRW). 익절·손절은 프로그램이 시세를 보고 매도 주문을 냅니다 — NAS 가 꺼지면 이 감시도 멈춥니다.<br>주문이 나갈 토스 계좌: ${ls.account_seq?`<b>TOSS_ACCOUNT_SEQ=${esc(ls.account_seq)}</b> (설정 · 연결 화면에서 어느 계좌인지 확인)`:'<b>미지정</b>'}${ls.available?'':' <br><b>지금은 꺼져 있습니다</b> — NAS .env 에 ENABLE_LIVE_TRADING=true 와 TOSS_ACCOUNT_SEQ 를 넣고 컨테이너를 다시 만드세요.'}`
    :`<b>모의 운용</b> — 실제 시세로 같은 규칙을 돌리되 체결은 가상입니다. 며칠 돌려 승률·손익이 납득되면 실거래로 넘어가세요.`;
  const lv=dtStatus.live;$('trade-mode-badge').textContent=lv&&lv.status==='running'?'실거래 운용 중':'가상 주문';$('trade-mode-badge').className='badge '+(lv&&lv.status==='running'?'bad':'');
  const other=dtStatus[isLive()?'paper':'live'];
  $('dt-mode-other').textContent=other?`${isLive()?'모의':'실거래'} 계좌: ${other.status==='running'?'운용 중':'정지'} · 보유 ${Object.keys(other.positions).length}`:'';
}

function renderDtEmpty(){
  $('dt-view').hidden=true;$('dt-empty').hidden=false;
  const ls=dtStatus.live_status;
  $('dt-form-title').textContent=isLive()?'실거래 계좌 만들기':'모의 계좌 만들기';
  $('dt-cash-label').textContent=isLive()?'투입 자금 (원) · 실제 돈':'가상자금 (원)';
  $('dt-live-fields').hidden=!isLive();
  $('dt-ack').required=isLive();
  const btn=$('dt-form').querySelector('button.primary');
  btn.textContent=isLive()?'실거래 계좌 만들기':'모의 계좌 만들기';
  btn.disabled=isLive()&&!ls.available;
  if(isLive()&&$('dt-cash').value&&Number($('dt-cash').value)>ls.capital_cap_krw)$('dt-cash').value=String(ls.capital_cap_krw);
}

function fillDtDefaults(d){
  $('dt-learning').checked=d.self_improve!==false;
  fillBasket(d);
  $('dt-strategy').value=d.strategy||'ai';
  $('dt-risk').value=d.risk_per_trade_pct??.25;$('dt-approval').value=d.approval_minutes??20;$('dt-price-reviews').value=d.max_price_reviews_per_day??16;
  showStrategyNote();
  const map={'dt-cash':'initial_cash','dt-maxpos':'max_positions','dt-pospct':'position_pct',
    'dt-tpmin':'take_profit_min_pct','dt-tpmax':'take_profit_max_pct','dt-slmin':'stop_loss_min_pct','dt-slmax':'stop_loss_max_pct',
    'dt-scan-at':'scan_at','dt-entry-at':'entry_at','dt-eod-at':'eod_review_at','dt-close-at':'force_close_at',
    'dt-surge':'surge_trigger_pct','dt-aicalls':'max_ai_calls_per_day','dt-minamt':'min_trading_amount_krw','dt-maxprev':'max_prev_change_pct'};
  for(const [id,key] of Object.entries(map))if($(id)&&d[key]!==undefined)$(id).value=id==='dt-minamt'?d[key]/1e8:d[key];
  $('dt-news-trigger').checked=!!d.news_trigger;$('dt-require-flow').checked=!!d.require_flow;$('dt-require-news').checked=!!d.require_news;
  dtSession=d.entry_session||'regular';
  $('dt-close-entry').checked=d.close_entry!==false;$('dt-review').checked=d.daily_review!==false;$('dt-autotune').checked=d.auto_tune!==false;
  if($('dt-closemax'))$('dt-closemax').value=d.close_entry_max!==undefined?d.close_entry_max:2;
}

function fillFromAccount(c){
  $('dt-learning').checked=c.self_improve!==false;
  fillBasket(c);
  $('dt-strategy').value=c.strategy||'ai';
  $('dt-risk').value=c.risk_per_trade_pct??.25;$('dt-approval').value=c.approval_minutes??20;$('dt-price-reviews').value=c.max_price_reviews_per_day??16;
  showStrategyNote();
  const map={'dt-cash':'initial_cash','dt-maxpos':'max_positions','dt-pospct':'position_pct',
    'dt-tpmin':'take_profit_min_pct','dt-tpmax':'take_profit_max_pct','dt-slmin':'stop_loss_min_pct','dt-slmax':'stop_loss_max_pct',
    'dt-scan-at':'scan_at','dt-entry-at':'entry_at','dt-eod-at':'eod_review_at','dt-close-at':'force_close_at',
    'dt-surge':'surge_trigger_pct','dt-aicalls':'max_ai_calls_per_day','dt-maxprev':'max_prev_change_pct','dt-closemax':'close_entry_max'};
  for(const [id,key] of Object.entries(map))if($(id)&&c[key]!==undefined)$(id).value=c[key];
  $('dt-minamt').value=Math.round((c.min_trading_amount_krw||0)/1e8);
  $('dt-news-trigger').checked=!!c.news_trigger;$('dt-require-flow').checked=!!c.require_flow;$('dt-require-news').checked=!!c.require_news;
  $('dt-close-entry').checked=!!c.close_entry;$('dt-review').checked=!!c.daily_review;$('dt-autotune').checked=!!c.auto_tune;
  dtSession=c.entry_session||'regular';
  $('dt-session-note').textContent=`지금: ${c.entry_session==='premarket'?'NXT 프리마켓':'정규장'} 진입 · ${c.entry_at} 매수 → ${c.eod_review_at} 마감 판단 → ${c.force_close_at} 정리`;
}

function startEdit(){
  dtEditing=true;fillFromAccount(dtState.config);
  $('dt-empty').hidden=false;
  $('dt-form-title').textContent=(dtState.mode==='live'?'실거래':'모의')+' 계좌 설정 바꾸기';
  $('dt-cash').disabled=true;$('dt-cash-label').textContent=(dtState.mode==='live'?'투입 자금':'가상자금')+' (바꿀 수 없음 · 새 계좌를 만드세요)';
  $('dt-live-fields').hidden=true;$('dt-ack').required=false;
  const btn=$('dt-form').querySelector('button.primary');btn.textContent='설정 저장';btn.disabled=false;
  $('dt-edit-cancel').hidden=false;
  $('dt-form').scrollIntoView({behavior:'smooth',block:'start'});
}

function stopEdit(){
  dtEditing=false;$('dt-cash').disabled=false;$('dt-edit-cancel').hidden=true;
  // renderDtEmpty() 는 "계좌가 없을 때" 용이라 폼을 다시 연다. 계좌가 있으면 그냥 닫는다.
  if(dtState){$('dt-empty').hidden=true;renderDaytrade();}
  else renderDtEmpty();
}

const PRESETS={
  regular:{entry_at:'09:00',entry_session:'regular',eod_review_at:'15:00',force_close_at:'15:20',
           note:'정규장만 씁니다. 09:00 매수 → 15:00 마감 판단 → 15:20 정리(종가 단일가 구간).'},
  premarket:{entry_at:'08:00',entry_session:'premarket',eod_review_at:'15:00',force_close_at:'15:20',
           note:'NXT 프리마켓 08:00부터 삽니다. 토스 앱에서 계좌의 "투자자지시 거래소"를 통합(SOR)으로 바꿔야 주문이 나갑니다.'},
  after:{entry_at:'09:00',entry_session:'regular',eod_review_at:'19:30',force_close_at:'19:50',
           note:'NXT 애프터마켓(15:30~20:00)까지 들고 갑니다. 정규장 뒤에는 거래가 얇어 지정가가 잘 안 채워질 수 있습니다.'}};
function applyPreset(name){
  const p=PRESETS[name];if(!p)return;
  $('dt-entry-at').value=p.entry_at;$('dt-eod-at').value=p.eod_review_at;$('dt-close-at').value=p.force_close_at;
  dtSession=p.entry_session;$('dt-session-note').textContent=p.note;
}
let dtSession='regular';
document.querySelectorAll('#dt-form [data-preset]').forEach(b=>b.addEventListener('click',()=>applyPreset(b.dataset.preset)));

function dtConfig(){
  const cfg={mode:dtMode,currency:'KRW',initial_cash:Number($('dt-cash').value),max_positions:Number($('dt-maxpos').value),position_pct:Number($('dt-pospct').value),
    take_profit_min_pct:Number($('dt-tpmin').value),take_profit_max_pct:Number($('dt-tpmax').value),take_profit_default_pct:Number($('dt-tpmin').value)<=10&&10<=Number($('dt-tpmax').value)?10:Number($('dt-tpmin').value),
    stop_loss_min_pct:Number($('dt-slmin').value),stop_loss_max_pct:Number($('dt-slmax').value),stop_loss_default_pct:Number($('dt-slmin').value)<=3&&3<=Number($('dt-slmax').value)?3:Number($('dt-slmin').value),
    scan_at:$('dt-scan-at').value,entry_at:$('dt-entry-at').value,eod_review_at:$('dt-eod-at').value,force_close_at:$('dt-close-at').value,entry_session:dtSession,
    news_trigger:$('dt-news-trigger').checked,surge_trigger_pct:Number($('dt-surge').value),max_ai_calls_per_day:Number($('dt-aicalls').value),
    min_trading_amount_krw:Number($('dt-minamt').value)*1e8,max_prev_change_pct:Number($('dt-maxprev').value),
    require_flow:$('dt-require-flow').checked,require_news:$('dt-require-news').checked,
    close_entry:$('dt-close-entry').checked,close_entry_max:Number($('dt-closemax').value),
    daily_review:$('dt-review').checked,auto_tune:$('dt-autotune').checked};
  cfg.strategy=$('dt-strategy').value;
  cfg.self_improve=$('dt-learning').checked;
  cfg.basket_entry_at=$('dt-basket-entry').value;cfg.basket_exit_at=$('dt-basket-exit').value;cfg.basket_entry_minutes=Number($('dt-basket-window').value);cfg.basket_tp_pct=Number($('dt-basket-tp').value);cfg.basket_sl_pct=Number($('dt-basket-sl').value);
  cfg.risk_per_trade_pct=Number($('dt-risk').value);cfg.approval_minutes=Number($('dt-approval').value);cfg.max_price_reviews_per_day=Number($('dt-price-reviews').value);
  if(isLive())cfg.acknowledge_live=$('dt-ack').value.trim();
  return cfg;
}

function renderDaytrade(){
  const s=dtState;if(!s)return;$('dt-view').hidden=false;if(!dtEditing)$('dt-empty').hidden=true;
  const live=s.mode==='live';
  const c=s.config,eq=s.equity||s.cash,pnl=eq-c.initial_cash;
  $('dt-title').textContent=`${live?'실거래':'모의'} · ${s.day||'—'} · ${dtPhase[s.phase]||s.phase}`;
  $('dt-state').textContent=s.status==='running'?(live?'실거래 운용 중':'모의 운용 중'):'정지';$('dt-state').className='badge '+(s.status==='running'?(live?'bad':'ok'):'');
  $('dt-start').textContent=s.status==='running'?'운용 정지':(live?'실거래 시작':'운용 시작');
  $('dt-start').className=s.status==='running'?'':(live?'danger':'primary');
  $('dt-stats').innerHTML=[[live?'평가자산 (실제)':'평가자산',won(eq)],['현금',won(s.cash)],['실현손익',`<span class="${s.realized_pnl>0?'up':s.realized_pnl<0?'down':''}">${signed(s.realized_pnl)}</span>`],
    ['평가손익',`<span class="${s.unrealized>0?'up':s.unrealized<0?'down':''}">${signed(s.unrealized)}</span>`],['누적수익률',pctc(pnl/c.initial_cash*100)],
    ['보유',`${Object.keys(s.positions).length} / ${c.max_positions}`],['AI 판단',`예약 ${s.ai.anchor_calls_today||0} · 이벤트 ${s.ai.calls_today}/${c.max_ai_calls_per_day}`]].map(([a,b])=>`<div class="indicator"><span class="small muted">${a}</span><strong class="small">${b}</strong></div>`).join('');
  const alerts=[s.ai.error,...Object.values(s.errors||{})].filter(Boolean);
  $('dt-alert').textContent=alerts.join(' · ');
  const ss=s.sessions;
  $('dt-schedule').innerHTML=[['스캔·AI 판단',c.scan_at],['매수',`${c.entry_at} ~ +${c.entry_window_minutes}분 (${c.entry_session==='premarket'?'NXT 프리마켓':'정규장'})`],['마감 판단',c.eod_review_at],['강제 정리',c.force_close_at]].map(([a,b])=>`<span class="muted small">${a}</span> <strong class="small">${b}</strong>`).join(' &nbsp;·&nbsp; ')
    +(ss?`<br><span class="muted small">오늘 거래 가능</span> <strong class="small">${esc(ss.open)} ~ ${esc(ss.close)}</strong> <span class="muted small">(정규장 ${esc(ss.regular_open)}~${esc(ss.regular_close)}${ss.source==='toss'?' · 토스 캘린더':' · 설정값'})</span>`:'');
  $('dt-new').hidden=s.status==='running'||Object.keys(s.positions).length>0;

  $('dt-config').innerHTML=[['모드',live?'실거래 (실제 주문)':'모의 (가상 체결)'],[live?'투입 자금':'가상자금',won(c.initial_cash)],['최대 종목 / 비중',`${c.max_positions}종 · ${c.position_pct}%`],['익절 범위',`${c.take_profit_min_pct}~${c.take_profit_max_pct}%`],['손절 범위',`${c.stop_loss_min_pct}~${c.stop_loss_max_pct}%`],
    ['전략',({top5_morning:'시나리오2 · TOP5 오전',in_play:'AI 승인·주도주 ORB',ai:'AI 종합',orb:'시초 범위 돌파',retest:'눌림 재돌파',bull_flag:'상승 깃발형'})[c.strategy||'ai']],
    ['거래대금 하한',`${Math.round((c.min_trading_amount_krw||0)/1e8)}억`],['전일 상승 상한',`${c.max_prev_change_pct}%`],['급등 트리거',`${c.surge_trigger_pct}%`],['AI 호출 상한',`${c.max_ai_calls_per_day}회`],
    ['뉴스 트리거',c.news_trigger?'켬':'끔'],['수급 필수',c.require_flow?'켬':'끔'],['기사 필수',c.require_news?'켬':'끔'],
    ['마감 매수',c.close_entry?`켬 · 최대 ${c.close_entry_max}종`:'끔'],['마감 복기',c.daily_review?(c.auto_tune?'켬 · 자동 조정':'켬 · 제안만'):'끔'],
    ['장전 재시도',`${c.retry_minutes}분 간격 · 최대 ${c.max_scan_attempts}회`],
    live?['지정가 여유 / 미체결 취소',`${c.live_limit_buffer_bps}bp / ${c.live_fill_timeout_seconds}초`]:['비용 가정',`수수료 ${c.fee_bps}bp · 세금 ${c.sell_tax_bps}bp · 슬리피지 ${c.slippage_bps}bp`]].map(([a,b])=>`<div class="indicator"><span class="muted">${a}</span><strong>${b}</strong></div>`).join('');

  const plan=s.plan;
  $('dt-plan').innerHTML=plan?themeRanking(plan)+`<p class="small"><strong>시장 관점</strong> ${esc(plan.market_view||'—')}</p><p class="small muted">${esc(plan.summary)}</p>`+(plan.picks.length?plan.picks.map(p=>`<div class="feed-item"><strong>${esc(p.name||p.symbol)} <span class="muted">${esc(p.symbol)}</span> · 비중 ${p.weight_pct}% · 목표 +${p.take_profit_pct}% / 손절 -${p.stop_loss_pct}%</strong><p>${esc(p.thesis)}</p><p class="small muted">반대 근거 · ${esc(p.counter_case)}<br>무효화 · ${esc(p.invalidation)}</p></div>`).join(''):'<p class="muted">오늘은 진입 종목이 없습니다.</p>'):'<p class="muted">아직 장전 판단이 없습니다.</p>';

  const pos=Object.values(s.positions);
  $('dt-positions').innerHTML=pos.length?`<table><thead><tr><th>종목</th><th>수량</th><th>진입가</th><th>현재가</th><th>평가손익</th><th>목표가</th><th>손절가</th><th>상태</th></tr></thead><tbody>`+pos.map(p=>{const q=s.quotes[p.symbol];const cur=q?q.bid:null;const upl=cur?(cur-p.entry_price)*p.qty:0;const uplp=cur?(cur/p.entry_price-1)*100:0;
    return `<tr><td>${esc(p.name||p.symbol)}<br><small class="muted">${esc(p.symbol)} · ${p.source==='event'?'이벤트':'계획'}</small></td><td>${p.qty}</td><td>${fmt(p.entry_price,0)}</td><td>${cur?fmt(cur,0):'—'}</td><td>${pctc(uplp)}<br><small>${signed(upl)}</small></td><td class="up">${fmt(p.tp_price,0)}</td><td class="down">${fmt(p.sl_price,0)}</td><td>${p.closing?'<span class="tag warn">매도 주문 중</span>':''}${p.hold_overnight?'<span class="tag ok">익일 보유</span>':''}</td></tr>`;}).join('')+'</tbody></table>':'<p class="muted">보유 종목 없음</p>';

  const cands=(s.scan&&s.scan.candidates)||[];
  $('dt-candidates').innerHTML=cands.length?`<p class="small muted">풀 ${s.scan.pool_size}종 → 조회 ${s.scan.enriched}종 → 후보 ${cands.length}종 · ${hm(s.scan.scanned_at)} 기준</p><table><thead><tr><th>종목</th><th>점수</th><th>전일</th><th>거래대금</th><th>외국인</th><th>기관</th><th>뉴스</th><th>테마</th></tr></thead><tbody>`+cands.map(x=>`<tr><td>${esc(x.name||x.symbol)}<br><small class="muted">${esc(x.symbol)} · ${(x.origins||[]).join('+')}</small></td><td>${x.score}</td><td>${pctc(x.prev_change_pct)}</td><td>${x.trading_amount?Math.round(x.trading_amount/1e8)+'억':'—'}</td><td>${fmt(x.flow_foreigner_1d,0)}</td><td>${fmt(x.flow_institution_1d,0)}</td><td>${x.news_count??(x.news||[]).length}</td><td class="small">${esc((x.themes||[]).slice(0,2).map(t=>t.name).join(', '))}</td></tr>`).join('')+'</tbody></table>':'<p class="muted">스캔 결과 없음</p>';

  const trades=(s.trades||[]).slice().reverse().slice(0,30);
  const priceFmt=(v,sym,cur)=>(cur==='USD'||/^[A-Z]/.test(String(sym||'')))?(Number.isFinite(v)?'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):'—'):fmt(v,0)+'원';
  $('dt-trades').innerHTML=trades.length?`<table><thead><tr><th>시각</th><th>종목</th><th>구분</th><th>수량</th><th>가격</th><th>손익 (원화)</th><th>사유</th></tr></thead><tbody>`+trades.map(t=>`<tr><td>${mdhm(t.at)}</td><td>${/^[A-Z]/.test(String(t.symbol||''))?'🇺🇸 ':'🇰🇷 '}${esc(t.name||t.symbol)}<br><small class="muted">${esc(t.symbol)}${t.horizon?' · '+esc(({short:'단기',medium:'중기',long:'장기'})[t.horizon]||t.horizon):''}</small></td><td>${t.side==='buy'?'<span class="up">매수</span>':t.partial?'<span class="ok">분할익절</span>':'<span class="down">매도</span>'}</td><td>${t.qty}</td><td>${priceFmt(t.price,t.symbol,t.currency)}</td><td>${t.pnl!==undefined?`<span class="${t.pnl>=0?'up':'down'}">${signed(t.pnl)} (${t.pnl_pct>=0?'+':''}${(t.pnl_pct||0).toFixed(2)}%)</span>`:'—'}</td><td class="small">${esc({plan:'계획 매수',event:'이벤트 매수'}[t.reason]||t.reason||'')}</td></tr>`).join('')+'</tbody></table>':'<p class="muted">체결 없음</p>';

  // 실제 주문 장부 (실거래만)
  $('dt-orders-panel').hidden=!live;
  if(live){
    const rows=(s.orders||[]).slice().reverse();
    $('dt-orders').innerHTML=rows.length?`<table><thead><tr><th>시각</th><th>종목</th><th>구분</th><th>수량</th><th>지정가</th><th>체결</th><th>상태</th><th>비고</th></tr></thead><tbody>`+rows.map(o=>`<tr><td>${mdhm(o.created_at)}</td><td>${esc(o.name||o.symbol)}<br><small class="muted">${esc(o.symbol)}${o.broker_order_id?' · '+esc(o.broker_order_id):''}</small></td><td>${o.side==='buy'?'<span class="up">매수</span>':'<span class="down">매도</span>'}</td><td>${o.qty}</td><td>${fmt(o.limit_price,0)}</td><td>${o.filled_qty}/${o.qty}${o.filled_qty?'<br><small>@'+fmt(o.filled_amount/o.filled_qty,0)+'</small>':''}</td><td><span class="tag ${o.status==='DONE'?'ok':o.status==='UNKNOWN'?'up':o.status==='BLOCKED'?'':'warn'}">${orderStatus[o.status]||o.status}</span>${o.broker_status?'<br><small class="muted">'+esc(o.broker_status)+'</small>':''}</td><td class="small">${esc(o.error||o.reason||'')}${o.status==='UNKNOWN'&&!o.broker_order_id?`<div class="row" style="margin-top:6px;justify-content:flex-start"><input class="small" style="width:150px" placeholder="토스 주문번호" data-bind-input="${esc(o.id)}"><button class="small" data-bind="${esc(o.id)}">연결</button><button class="small quiet" data-discard="${esc(o.id)}">주문 없음 확인 · 폐기</button></div>`:''}</td></tr>`).join('')+'</tbody></table>':'<p class="muted">아직 주문이 없습니다.</p>';
  }

  const rv=(s.tuning||{}).last_review;
  $('dt-review-out').innerHTML=rv?`<p class="small"><span class="tag ok">${esc(rv.day)} · ${mdhm(rv.at)}</span> ${esc(rv.summary)}</p><p class="small muted">${esc(rv.diagnosis||'')}</p>`
      +((rv.lessons||[]).length?'<p class="small"><b>교훈</b></p><ul class="small" style="margin:4px 0;padding-left:18px">'+rv.lessons.map(l=>`<li>${esc(l)}</li>`).join('')+'</ul>':'')
      +((rv.applied||[]).length?'<p class="small"><b>반영한 조정</b></p>'+rv.applied.map(x=>`<div class="feed-item"><strong>${esc(x.key)} ${esc(x.from)} → ${esc(x.to)}</strong><p class="small muted">${esc(x.reason)}</p></div>`).join(''):'<p class="small muted">반영한 설정 변경 없음</p>')
      +((rv.skipped||[]).length?'<p class="small muted">제외: '+rv.skipped.map(x=>esc(x.key)+'('+esc(x.reason)+')').join(', ')+'</p>':'')
    :'<p class="muted">아직 복기가 없습니다. 장 마감 정리 뒤 자동으로 실행됩니다.</p>';
  $('dt-log').innerHTML=(s.log||[]).slice().reverse().slice(0,40).map(l=>`<div class="feed-item"><small>${mdhm(l.at)}</small> ${esc(l.message)}</div>`).join('')||'<p class="muted">기록 없음</p>';
  if(c.strategy==='in_play'){
    const run=s.in_play||{},counts=run.counts||{},approvals=run.approvals||{};
    const diagnostics=`<div class="notice info"><b>주도주 감시</b> · 후보 ${counts.candidates||0} · 신호 검사 통과 ${counts.signals||0}회 · 매수 실행 ${counts.submitted||0}회 · 승인 검토 ${run.reviews||0}/${c.max_price_reviews_per_day||16}<br>자료 갱신 ${mdhm(run.data_at)} · ${(run.data_errors||[]).map(esc).join(' · ')}</div>`;
    const rows=Object.entries(run.checks||{}).map(([symbol,check])=>`<div class="feed-item"><b>${esc(symbol)}</b> ${esc(check.reason)}<br><small>${mdhm(check.at)} · 승인 ${approvals[symbol]?mdhm(approvals[symbol].expires_at)+'까지':'없음'}</small></div>`).join('');
    $('dt-log').innerHTML=diagnostics+rows+$('dt-log').innerHTML;
  }
  $('dt-ai').innerHTML=(s.ai.history||[]).slice().reverse().slice(0,10).map(h=>`<div class="feed-item"><small>${mdhm(h.at)} · ${esc(h.kind)} · ${h.ok?'성공':'실패'}${h.seconds!==undefined?` · ${h.seconds}초`:''}${h.cost_usd?` · $${h.cost_usd.toFixed(3)}`:''}</small><p>${esc(h.ok?h.summary:h.error)}</p></div>`).join('')||'<p class="muted">AI 판단 기록 없음</p>';
  const memory=s.learning||{},labels={ready:'다음날 평가 대기',needs_evidence:'근거 표본 부족',rejected:'근거 검증 실패',trial:'성과 평가 중',retained:'평가 후 유지',rolled_back:'악화로 되돌림',interrupted:'설정 변경으로 평가 중단'};
  $('dt-learning-out').innerHTML=`<p>자동 개선 ${c.self_improve!==false?'켜짐':'꺼짐'} · 버전 ${esc(memory.current_version||'기록 대기')}</p>`+(memory.proposals||[]).slice(-6).reverse().map(p=>`<div class="feed-item"><b>${esc(labels[p.status]||p.status)}</b> · ${esc(p.lesson)}<br><small>근거 ${(p.evidence_ids||[]).length}건 · ${esc(p.reason||'')}</small></div>`).join('');
  if(c.strategy==='top5_morning'){
    const basket=s.basket||{},picks=(s.plan||{}).picks||[];
    const rows=picks.map(p=>{const trades=(s.trades||[]).filter(t=>t.symbol===p.symbol&&t.at>=basket.selected_at),bought=trades.some(t=>t.side==='buy'),pnl=trades.filter(t=>t.side==='sell').reduce((a,t)=>a+(t.pnl||0),0);return `<div class="feed-item"><b>${p.basket_rank}. ${esc(p.name||p.symbol)} (${esc(p.symbol)})</b> · ${p.weight_pct}% · ${esc(bought?'매수 체결':p._done?'주문 준비/접수':p.entry_status||'진입 대기')} · 실현 ${won(pnl)}</div>`;}).join('');
    $('dt-log').innerHTML=`<div class="notice info"><b>TOP5 오전 바스켓</b> · ${esc(basket.rank_source==='ai_rank'?'AI 종목 순위':basket.rank_source==='scanner_order'?'스캐너 후보 순서':'선정 대기')} · ${picks.length}/5종목 · ${esc(c.entry_at)} 매수 / ${esc(c.force_close_at)} 정리 · 익절 ${c.basket_tp_pct}% / 손절 ${c.basket_sl_pct}%</div>`+rows+$('dt-log').innerHTML;
  }
}

async function control(action,extra={}){
  if(!dtState)throw Error('계좌가 없습니다.');
  await api(`/daytrade/accounts/${dtState.id}/control`,'POST',{action,...extra});
  await loadDaytrade();
}
action('dt-form',async(e)=>{
  // 일부 브라우저는 <summary> 를 제출 버튼처럼 다룬다. 실제 제출 버튼이 아니면 무시한다.
  if(e&&e.submitter&&e.submitter.type!=='submit')return;
  if(dtEditing){const cfg=dtConfig();delete cfg.acknowledge_live;delete cfg.mode;delete cfg.currency;delete cfg.initial_cash;
    await api(`/daytrade/accounts/${dtState.id}/config`,'POST',cfg);notify('설정을 저장했습니다. 다음 판단부터 적용됩니다.');
    dtEditing=false;$('dt-cash').disabled=false;$('dt-edit-cancel').hidden=true;$('dt-empty').hidden=true;await loadDaytrade();return;}
  const cfg=dtConfig();if(isLive()&&cfg.acknowledge_live!=='실거래')throw Error('확인란에 "실거래"라고 입력하세요.');
  await api('/daytrade/accounts','POST',cfg);$('dt-ack').value='';notify(isLive()?'실거래 계좌를 만들었습니다. 시작하려면 "실거래 시작"을 누르고 문구를 입력하세요.':'모의 계좌를 만들었습니다. 운용 시작을 누르세요.');await loadDaytrade();},'submit');
action('dt-edit',()=>startEdit());
action('dt-edit-cancel',()=>stopEdit());
action('dt-start',async()=>{
  if(dtState.status==='running')return control('stop');
  if(dtState.mode==='live'){const typed=prompt('실제 돈으로 주문이 나갑니다.\n계속하려면 "실거래 시작"이라고 입력하세요.');if(typed===null)return;if(typed.trim()!=='실거래 시작')throw Error('문구가 다릅니다. 시작하지 않았습니다.');return control('start',{acknowledge_live:'실거래 시작'});}
  return control('start');
});
action('dt-scan-now',async()=>{await control('scan_now');notify('다음 주기에 스캔과 AI 판단을 실행합니다. (이벤트 한도와 무관)');});
action('dt-review-now',async()=>{
  $('dt-review-out').innerHTML='<p class="muted">AI가 다시 복기하는 중입니다… 1~2분 걸립니다.</p>';
  await control('review_now');notify('마감 복기를 시작했습니다. 끝나면 이 화면에 결과가 바뀝니다.');
  setTimeout(()=>loadDaytrade().catch(()=>{}),20000);});
action('dt-close-all',async()=>{if(!confirm(dtState&&dtState.mode==='live'?'실계좌 보유 종목을 전부 매도 주문합니다. 계속할까요?':'보유 종목을 전부 청산합니다.'))return;await control('close_all');});
action('dt-new',()=>{dtEditing=false;$('dt-cash').disabled=false;$('dt-edit-cancel').hidden=true;$('dt-view').hidden=true;renderDtEmpty();notify('새 계좌를 만들면 지금 계좌는 보관됩니다 (기록은 남습니다).');});
action('dt-preview',async()=>{$('dt-preview-out').innerHTML='<p class="muted">스캔 중… 약 40초. 계좌 상태는 바뀌지 않습니다.</p>';const r=await api('/daytrade/scan?limit=12');
  $('dt-preview-out').innerHTML=`<div class="row" style="margin-top:10px"><p class="small muted" style="margin:0">미리보기 · 풀 ${r.pool_size}종 → 후보 ${r.candidates.length}종 · AI 호출 없음</p><button id="dt-preview-close" type="button" class="small quiet">닫기</button></div>`+r.candidates.map(x=>`<div class="feed-item"><strong>${esc(x.name||x.symbol)} <span class="muted">${esc(x.symbol)}</span> · 점수 ${x.score}</strong><p class="small">전일 ${x.prev_change_pct>=0?'+':''}${(x.prev_change_pct||0).toFixed(1)}% · 거래대금 ${x.trading_amount_krw?Math.round(x.trading_amount_krw/1e8)+'억':'—'} · 외국인 ${fmt(x.flow_foreigner_1d,0)} · 기관 ${fmt(x.flow_institution_1d,0)} · ${(x.themes||[]).map(t=>t.name).join(', ')||'테마 없음'}</p>${(x.news||[]).slice(0,2).map(n=>`<p class="small muted">📰 ${esc(n.title)}</p>`).join('')}</div>`).join('');
  $('dt-preview-close')?.addEventListener('click',()=>{$('dt-preview-out').innerHTML='';});});
$('dt-orders').addEventListener('click',async e=>{
  const b=e.target.closest('[data-bind],[data-discard]');if(!b||!dtState)return;
  try{
    if(b.dataset.bind){const v=$('dt-orders').querySelector(`[data-bind-input="${b.dataset.bind}"]`).value.trim();if(!v)throw Error('토스 앱 주문 내역의 주문번호를 입력하세요.');await api(`/daytrade/accounts/${dtState.id}/orders/${b.dataset.bind}/bind`,'POST',{broker_order_id:v});notify('주문을 연결했습니다.');}
    else{if(!confirm('토스 앱에서 이 주문이 실제로 없는 것을 확인했습니까? 폐기하면 장부는 바뀌지 않습니다.'))return;await api(`/daytrade/accounts/${dtState.id}/orders/${b.dataset.discard}/discard`,'POST',{});notify('폐기했습니다.');}
    await loadDaytrade();
  }catch(err){notify(err.message);}
});
