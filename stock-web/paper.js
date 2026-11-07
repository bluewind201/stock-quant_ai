'use strict';
let paperState=null,paperId=null,paperLoading=false,paperFormHiddenFor=null;
let tradeMode='paper';
const tradingPath=()=>'/'+tradeMode;
const bucketNames={scalp:'단타',swing:'중기',long:'장기',cash:'현금'};
const phaseNames={preopen:'장전',opening:'개장 초기',morning:'오전',midday:'점심',afternoon:'오후',closing:'마감',postclose:'장후',legacy:'이전 기록'};
async function loadPaper(){
    if(paperLoading||!authenticated)return;paperLoading=true;
    try{const mode=tradeMode;const data=await api(tradingPath()+'/status');if(mode!==tradeMode)return;const select=$('paper-account');
        const liveStatus=tradeMode==='live'?data:await api('/live/status');
        const active=liveStatus.accounts.filter(a=>a.status==='running').length;
        $('trade-mode-badge').textContent=active?`실거래 ${active}계좌 운용 중`:(tradeMode==='live'?'실거래 화면':'AI 판단 · 가상 주문');
        $('trade-mode-notice').textContent=tradeMode==='live'?(data.enabled?'실거래 사용 설정이 켜져 있습니다. 시작한 계좌는 실제 자금으로 주문합니다. 재시작 후에는 직접 시작해야 합니다.':'실거래 비활성화: 모의 검증 후 NAS의 ENABLE_LIVE_TRADING과 TOSS_ACCOUNT_SEQ를 설정하세요.'):'모의 운용입니다. 원화·달러 계좌를 각각 하나씩 운용할 수 있으며 실제 주문을 전송하지 않습니다.';
        if(active)$('trade-mode-notice').textContent=`실제 자금으로 ${active}개 계좌가 운용 중입니다. `+$('trade-mode-notice').textContent;
        select.innerHTML='<option value="">계좌 선택</option>'+data.accounts.sort((a,b)=>b.created_at.localeCompare(a.created_at)).map(a=>`<option value="${esc(a.id)}">${esc(a.currency)} · ${time(a.created_at)} · ${a.status==='running'?'운용 중':'정지'}</option>`).join('');
        if(!paperId&&data.accounts.length)paperId=data.accounts[0].id;select.value=paperId||'';
        if(paperId&&paperFormHiddenFor!==paperId){$('paper-form').hidden=true;paperFormHiddenFor=paperId;}
        if(paperId){paperState=await api(tradingPath()+'/accounts/'+paperId);if(mode===tradeMode)renderPaper();}
        else{$('paper-view').hidden=true;$('session-policy').hidden=true;}
    }finally{paperLoading=false;}
}
function renderPaper(){
    const s=paperState,m=s.metrics,c=s.config,ai=s.ai;$('paper-view').hidden=false;$('paper-export').disabled=false;
    const live=tradeMode==='live';
    $('paper-title').textContent=`${c.currency} ${live?'실거래':'가상'} 포트폴리오 · ${c.watchlist.length}종목`;
    $('paper-state').textContent=s.status==='running'?(live?'실제 자금 · 실거래 운용 중':'실제 시세 · 모의 운용 중'):'전체 운용 정지';
    $('paper-start').textContent=live?'실거래 시작':'AI 모의 운용 시작';
    $('paper-liquidate').textContent=live?'실제 포지션 전량 청산':'가상 포지션 전량 청산';
    $('paper-start').disabled=s.status==='running';$('paper-stop').disabled=s.status!=='running';
    $('paper-review').disabled=s.status!=='running'||ai.pending;
    $('paper-pause').textContent=s.entries_enabled?'신규 매수 일시정지':'신규 매수 재개';
    const alerts=[s.halt_reason,s.liquidate?'전량 청산 요청 상태':'',...Object.values(s.errors),ai.error].filter(Boolean);
    $('paper-alert').textContent=alerts.join(' · ');$('paper-alert').className=alerts.length?'notice':'';
    $('paper-clock').textContent=`마지막 시세 확인 ${time(s.last_poll_at)} · 위험 검사 ${time(s.last_risk_at)} · ${live?'토스 확인 체결 반영':'가상 주문만 실행'}`;
    const window=s.market_session;$('session-policy').hidden=!window;
    if(window)$('session-policy').textContent=`${window.label} · ${window.timezone} · ${window.focus}`;
    $('live-mode-status').textContent=m.live_note;
    const groups=m.performance?.by_entry_phase_bucket||{};
    $('phase-performance').innerHTML=Object.entries(groups).map(([key,g])=>{const [phase,bucket]=key.split('/');return `<div class="indicator"><span>${esc(phaseNames[phase]||phase)} / ${bucketNames[bucket]||esc(bucket)} · ${g.sell_fills}회</span><strong>${fmt(g.net_pnl)} ${c.currency} · 평균 ${fmt(g.mean_net_pnl)}</strong></div>`;}).join('')||'<p class="muted">매도 체결이 쌓이면 진입 시간대와 단타·중기·장기별 순손익을 비교합니다.</p>';
    $('live-order-panel').hidden=!live;
    if(live)$('live-order-list').innerHTML=(s.orders||[]).slice().reverse().map(o=>`<div class="feed-item"><strong>${esc(o.symbol)} · ${o.side==='buy'?'매수':'매도'} ${o.qty}주 · ${esc(o.status)}</strong><p>확인 체결 ${o.filled_qty}주 · ${esc(o.error||o.reason)}</p><small>프로그램 주문 ID ${esc(o.id)}</small></div>`).join('')||'<p>실제 주문이 없습니다.</p>';
    for(const [id,value] of [['paper-equity',fmt(m.equity)+' '+c.currency],['paper-return',fmt(m.return_pct)+'%'],['paper-dd',fmt(m.max_drawdown_pct)+'%'],['paper-balance',fmt(m.cash)],['paper-benchmark',m.benchmark_return_pct===null?'시세 대기':fmt(m.benchmark_return_pct)+'%']])$(id).textContent=value;
    $('paper-allocation').innerHTML=Object.entries(s.allocation).map(([k,v])=>`<div class="indicator"><span>${bucketNames[k]}</span><strong>${fmt(v)}%</strong></div>`).join('');
    $('paper-summary').textContent=ai.summary;
    $('paper-ai-state').textContent=`${ai.pending?'AI가 판단 중입니다. 가격 감시는 계속합니다.':'판단 대기'} · 오늘 ${ai.calls_today}/${c.max_ai_calls_per_day}회 · 마지막 완료 ${time(ai.last_completed_at)}`;
    $('paper-progress').innerHTML=`<div class="indicator"><span>관찰 거래일</span><strong>${m.observed_days}일</strong></div><div class="indicator"><span>매도 체결 / 수익 체결 비율</span><strong>${m.sell_fills}회 / ${m.win_rate===null?'—':fmt(m.win_rate)+'%'}</strong></div><div class="indicator"><span>실현 / 평가 손익</span><strong>${fmt(m.realized_pnl)} / ${fmt(m.unrealized_pnl)}</strong></div><div class="indicator"><span>오늘 수익률</span><strong>${fmt(m.day_return_pct)}%</strong></div>`;
    $('paper-quotes').innerHTML=c.watchlist.map(i=>{const q=s.quotes[i.symbol];return `<tr><td>${esc(i.symbol)}</td><td>${q?fmt(q.price):'—'}</td><td>${q?fmt(q.bid)+' / '+fmt(q.ask):'—'}</td><td>${q?fmt(q.spread_bps)+' bp':'—'}</td><td>${q?time(q.timestamp):'시세 대기'} · ${s.quote_status[i.symbol]?'정규장·유효 시세':'휴장 또는 시세 확인 필요'}</td></tr>`;}).join('');
    $('paper-positions').innerHTML=Object.values(s.positions).map(p=>`<tr><td>${esc(p.symbol)} · ${bucketNames[p.bucket]}</td><td>${p.qty}</td><td>${fmt(p.entry_price)}</td><td>${fmt(p.stop)} / ${fmt(p.target)}</td><td>${fmt(p.trailing_pct)}% · ${time(p.entered_at)}</td></tr>`).join('')||'<tr><td colspan="5">보유 포지션이 없습니다.</td></tr>';
    $('paper-decisions').innerHTML=s.decisions.slice().reverse().map(d=>`<div class="feed-item"><small>${time(d.at)} · ${esc(d.trigger)} · ${d.status==='done'?'검증 완료':'판단 폐기'}</small><strong>${esc(d.summary)}</strong>${d.results.map(r=>`<p>${esc(r.symbol)} · ${bucketNames[r.bucket]} · ${esc(r.action)} / ${r.filled}주 체결${r.order_intent_id?' · 실제 주문 확인 대기':''}<br>${esc(r.blocked.length?r.blocked.join(' · '):r.reason)}</p>${r.reasoning?.thesis?`<p>논거: ${esc(r.reasoning.thesis)}<br>반대 근거: ${esc(r.reasoning.counter_case)}<br>무효화 조건: ${esc(r.reasoning.invalidation)}</p>`:''}`).join('')}</div>`).join('')||'<p class="muted">AI 운용을 시작하면 판단 근거가 쌓입니다.</p>';
    $('paper-trades').innerHTML=s.trades.slice().reverse().map(t=>`<tr><td>${time(t.at)}</td><td>${esc(t.symbol)} · ${bucketNames[t.bucket]}</td><td>${t.side==='buy'?'매수':'매도'} / ${t.qty}</td><td>${fmt(t.price)} / ${fmt(t.fee)}</td><td>${t.pnl===null?'—':fmt(t.pnl)} · ${esc(t.reason)}</td></tr>`).join('')||'<tr><td colspan="5">체결된 가상 주문이 없습니다.</td></tr>';
    const selected=$('paper-chart-symbol').value;$('paper-chart-symbol').innerHTML=c.watchlist.map(i=>`<option value="${esc(i.symbol)}">${esc(i.symbol)}</option>`).join('');if(c.watchlist.some(i=>i.symbol===selected))$('paper-chart-symbol').value=selected;drawPaperChart();
}
function drawPaperChart(){
    if(!paperState||$('paper').hidden)return;
    const feat=paperState.features[$('paper-chart-symbol').value]||{},frame=$('paper-chart-frame').value;
    const rows=feat[frame+'_bars']||[],tech=feat[frame],canvas=$('paper-chart'),width=canvas.clientWidth,height=300,dpr=window.devicePixelRatio||1;
    canvas.width=width*dpr;canvas.height=height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);
    $('paper-chart-note').textContent=rows.length?`${rows.length}개 완료 봉 · ${frame==='minute'?'1분봉':'일봉'} · 진행 중인 봉은 제외`:'완료 봉 데이터를 기다리는 중입니다.';
    $('paper-technicals').innerHTML=tech?Object.entries({RSI:tech.rsi14,MACD:tech.macd,'20기간 평균':tech.sma20,'60기간 평균':tech.sma60,ATR:tech.atr14,'거래량 배수':tech.volume_ratio}).map(([k,v])=>`<div class="indicator"><span>${k}</span><strong>${fmt(v)}</strong></div>`).join(''):'<p class="muted">최소 61개 완료 봉이 필요합니다.</p>';
    if(feat.intraday?.bars)$('paper-technicals').innerHTML+=`<div class="indicator"><span>VWAP (${feat.intraday.vwap_scope==='session'?'당일 전체':'수집 구간'})</span><strong>${fmt(feat.intraday.vwap)}</strong></div><div class="indicator"><span>개장 15분 고점</span><strong>${feat.intraday.opening_range_high?fmt(feat.intraday.opening_range_high):'표본 미확인'}</strong></div>`;
    if(!rows.length||width<1)return;const lo=Math.min(...rows.map(b=>b.low))*.998,hi=Math.max(...rows.map(b=>b.high))*1.002,range=hi-lo||1,dx=(width-65)/rows.length,y=p=>15+(hi-p)/range*(height-45);
    ctx.font='10px sans-serif';ctx.fillStyle='#66758a';for(let n=0;n<5;n++){const v=lo+range*n/4;ctx.fillText(fmt(v),width-60,y(v));ctx.strokeStyle='#e3e8ef';ctx.beginPath();ctx.moveTo(5,y(v));ctx.lineTo(width-65,y(v));ctx.stroke();}
    rows.forEach((b,i)=>{const x=5+(i+.5)*dx;ctx.fillStyle=ctx.strokeStyle=b.close>=b.open?'#e5484d':'#2b6cdf';ctx.beginPath();ctx.moveTo(x,y(b.high));ctx.lineTo(x,y(b.low));ctx.stroke();ctx.fillRect(x-dx*.3,Math.min(y(b.open),y(b.close)),Math.max(1,dx*.6),Math.max(1,Math.abs(y(b.open)-y(b.close))));});
}
action('paper-ai-check',async()=>{const s=await api('/paper/ai-check','POST',{});$('paper-connection').textContent=s.message;});
action('paper-form',async()=>{const watchlist=$('paper-watchlist').value.split(/[\n,]+/).map(s=>s.trim()).filter(Boolean).map(s=>{const parts=s.toUpperCase().split(':');if(parts.length!==2)throw Error('NASDAQ:AAPL 또는 KRX:005930 형식으로 입력하세요.');return{exchange:parts[0].trim(),symbol:parts[1].trim()};});
    paperState=await api(tradingPath()+'/accounts','POST',{currency:$('paper-currency').value,initial_cash:Number($('paper-cash').value),watchlist,max_position_pct:Number($('paper-max-position').value),max_invested_pct:Number($('paper-max-invested').value),max_scalp_pct:Number($('paper-scalp-cap').value),daily_loss_pct:Number($('paper-day-loss').value),max_drawdown_pct:Number($('paper-max-dd').value),max_ai_calls_per_day:Number($('paper-ai-limit').value),fee_bps:Number($('paper-fee').value),sell_tax_bps:Number($('paper-tax').value),require_news:$('paper-require-news').checked,...(tradeMode==='live'?{paper_account_id:$('live-paper-source').value}:{})});
    paperId=paperState.id;$('paper-form').hidden=true;await loadPaper();notify(tradeMode==='live'?'실거래 계좌를 준비했습니다. 설정과 금액 확인 후 직접 시작하세요.':'가상 계좌를 만들었습니다. AI 모의 운용 시작을 누르세요.');},'submit');
action('paper-account',async()=>{paperId=$('paper-account').value;if(paperId){$('paper-form').hidden=true;await loadPaper();}},'change');
action('paper-new',()=>{$('paper-form').hidden=!$('paper-form').hidden;});
$('paper-currency').addEventListener('change',()=>{const kr=$('paper-currency').value==='KRW';$('paper-cash').value=kr?'10000000':'10000';$('paper-watchlist').value=kr?'KRX:005930, KRX:000660':'NASDAQ:AAPL, NASDAQ:MSFT, NASDAQ:NVDA';});
for(const [id,command] of [['paper-start','start'],['paper-stop','stop'],['paper-review','review'],['paper-liquidate','liquidate'],['paper-pause','pause_entries']])action(id,async()=>{if(!paperId)return;const actionName=id==='paper-pause'&&!paperState.entries_enabled?'resume_entries':command;paperState=await api(tradingPath()+'/accounts/'+paperId+'/control','POST',{action:actionName,...(tradeMode==='live'?{acknowledge_live:$('live-confirm').value}:{})});renderPaper();notify('계좌 운용 상태를 변경했습니다.');});
action('paper-export',async()=>download(tradeMode==='live'?'live-trading-record.json':'ai-paper-validation.json',await api(tradingPath()+'/accounts/'+paperId+'/export')));
action('trade-mode',async()=>{
    tradeMode=$('trade-mode').value;paperId=null;paperState=null;$('paper-view').hidden=true;$('paper-form').hidden=false;$('paper-export').disabled=true;$('live-setup').hidden=tradeMode!=='live';
    document.querySelector('#paper-form button').textContent=tradeMode==='live'?'실거래 계좌 준비':'가상 계좌 만들기';
    if(tradeMode==='live'){
        const data=await api('/paper/status');$('live-paper-source').innerHTML='<option value="">검증한 계좌 선택</option>'+data.accounts.map(a=>`<option value="${esc(a.id)}">${esc(a.currency)} · ${time(a.created_at)}</option>`).join('');
        $('paper-cash').value=$('paper-currency').value==='KRW'?'1000000':'1000';
    }
    await loadPaper();
},'change');
action('live-paper-source',async()=>{
    const id=$('live-paper-source').value;if(!id)return;
    const [report,source]=await Promise.all([api('/live/validation/'+id),api('/paper/accounts/'+id)]);
    $('live-validation').textContent=(report.eligible_for_manual_setup?'최소 모의 관찰 조건 충족. 수익성 증명은 아닙니다.':report.reasons.join(' · '));
    const c=source.config;$('paper-currency').value=c.currency;$('paper-watchlist').value=c.watchlist.map(i=>i.exchange+':'+i.symbol).join(', ');
    $('paper-cash').value=c.currency==='KRW'?'1000000':'1000';
    for(const [id,key] of [['paper-max-position','max_position_pct'],['paper-max-invested','max_invested_pct'],['paper-scalp-cap','max_scalp_pct'],['paper-day-loss','daily_loss_pct'],['paper-max-dd','max_drawdown_pct'],['paper-fee','fee_bps'],['paper-tax','sell_tax_bps']])$(id).value=c[key];
    $('paper-require-news').checked=true;
},'change');
action('live-cancel-orders',async()=>{if(!paperId)return;paperState=await api('/live/accounts/'+paperId+'/control','POST',{action:'cancel_orders'});renderPaper();});
action('live-bind-form',async()=>{await api('/live/orders/'+encodeURIComponent($('live-intent-id').value)+'/bind','POST',{broker_order_id:$('live-broker-id').value});await loadPaper();notify('실제 주문 조회 결과를 연결했습니다.');},'submit');
$('live-broker-accounts').textContent='토스 계좌 식별자 조회';
action('live-broker-accounts',async()=>{const data=await api('/live/broker-accounts');$('live-broker-result').textContent=data.accounts.map(a=>`${a.masked_number} · ${a.account_type} · TOSS_ACCOUNT_SEQ=${a.account_seq}`).join(' / ')||'사용 가능한 계좌가 없습니다.';});
$('paper-chart-symbol').addEventListener('change',drawPaperChart);$('paper-chart-frame').addEventListener('change',drawPaperChart);window.addEventListener('resize',drawPaperChart);
setInterval(()=>{if(authenticated&&!$('paper').hidden)loadPaper().catch(e=>notify(e.message));},4000);
