'use strict';
const horizonNames={short:'단기',medium:'중기',long:'장기'};
const horizonCaps={
  growth:{short:30,medium:35,long:25},
  all_weather:{short:15,medium:35,long:40},
  balanced:{short:20,medium:30,long:30},
  short:{short:80,medium:0,long:0},
  medium:{short:0,medium:80,long:0},
  long:{short:0,medium:0,long:80}
};
const horizonDescriptions={
  short:'수일~3주 · 눌림 반등 / 20일 돌파 / 우량 과매도 회복 · 1차 분할익절(+4.5%) + 본절잠금',
  medium:'수주~3개월 · 3~6개월 주도 모멘텀 · 1차 분할익절(+10%) + 50일 추세 추종',
  long:'6개월 이상 · 12개월 구조적 우상향 코어 · 200일 추세 보유 + +20% 도달 시 수익잠금'
};
const defaultUsCore=['NVDA','MSFT','AAPL','GOOGL','AMZN','META','AVGO','QQQ','SPY'];
let pfFilter='all';

strategyNotes.portfolio='국내(KRX)와 해외 미국(NASDAQ·NYSE) 주식을 단기·중기·장기 3중 시간축으로 24시간 스캔하고, 본절잠금·분할익절·추적손절로 손실은 짧게 끊고 추세 수익은 극대화합니다.';

function pfIsUs(sym,cur){return cur==='USD'||/^[A-Z]/.test(String(sym||''));}
function pfFlag(sym,cur){return pfIsUs(sym,cur)?'🇺🇸':'🇰🇷';}
function pfPrice(v,sym,cur){
  if(!Number.isFinite(v))return '—';
  return pfIsUs(sym,cur)?'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}):won(v);
}
function pfFx(s){const r=Number(s?.fx_usd_krw||dbData?.global_market?.usd_krw||1385);return Number.isFinite(r)&&r>0?r:1385;}
function pfQuoteBid(s,p){
  const q=s?.quotes?.[p.symbol];
  return q&&Number.isFinite(q.bid)&&q.bid>0?q.bid:q&&Number.isFinite(q.price)&&q.price>0?q.price:p.entry_price;
}
function pfHoldingKrw(s,p){
  const cur=pfQuoteBid(s,p),mult=pfIsUs(p.symbol,p.currency)?pfFx(s):1;
  return (cur||0)*(p.qty||0)*mult;
}
function pfPnlKrw(s,p){
  const cur=pfQuoteBid(s,p),mult=pfIsUs(p.symbol,p.currency)?pfFx(s):1;
  return ((cur||p.entry_price)-p.entry_price)*(p.qty||0)*mult;
}

function applyGlobalRecommendedPreset(){
  $('dt-strategy').value='portfolio';
  $('portfolio-market-kr').checked=true;
  $('portfolio-market-us').checked=true;
  $('portfolio-style').value='growth';
  $('portfolio-risk').value='1.2';
  $('portfolio-core').value='069500,229200,005930,000660';
  $('portfolio-us-core').value=defaultUsCore.join(',');
  $('portfolio-be-lock').checked=true;
  $('portfolio-partial-tp').checked=true;
  if(!$('dt-cash').disabled&&Number($('dt-maxpos').value)<10){
    $('dt-maxpos').value='10';
    $('dt-pospct').value='10';
  }else if(Number($('dt-maxpos').value)*Number($('dt-pospct').value)<=100&&Number($('dt-pospct').value)>10){
    $('dt-pospct').value='10';
    $('dt-maxpos').value='10';
  }
  portfolioForm();
}

function portfolioForm(c,isDefault=false){
  if(c){
    for(const h of Object.keys(horizonNames)){
      $('portfolio-watch-'+h).value=(c.portfolio_watchlists?.[h]||[]).join(',');
      if($('portfolio-us-watch-'+h))$('portfolio-us-watch-'+h).value=(c.us_watchlists?.[h]||[]).join(',');
    }
    const mkts=isDefault?['KR','US']:(c.markets&&c.markets.length?c.markets:['KR']);
    if($('portfolio-market-kr'))$('portfolio-market-kr').checked=mkts.includes('KR');
    if($('portfolio-market-us'))$('portfolio-market-us').checked=mkts.includes('US');
    $('portfolio-style').value=isDefault?'growth':(c.portfolio_style||'balanced');
    $('portfolio-core').value=(isDefault?['069500','229200','005930','000660']:(c.portfolio_core||['069500','229200'])).join(',');
    if($('portfolio-us-core'))$('portfolio-us-core').value=(c.us_core&&c.us_core.length?c.us_core:defaultUsCore).join(',');
    $('portfolio-risk').value=isDefault?1.2:(c.portfolio_risk_pct??.5);
    if($('portfolio-be-lock'))$('portfolio-be-lock').checked=c.breakeven_lock!==false;
    if($('portfolio-partial-tp'))$('portfolio-partial-tp').checked=c.partial_tp!==false;
    if(isDefault){
      $('dt-strategy').value='portfolio';
      $('dt-maxpos').value='10';
      $('dt-pospct').value='10';
    }
  }
  const active=$('dt-strategy').value==='portfolio';
  $('portfolio-fields').hidden=!active;$('legacy-entry-fields').hidden=active;$('legacy-schedule-fields').hidden=active;
  if(active)$('dt-strategy-note').textContent=strategyNotes.portfolio+' 기존 당일 전략 계좌는 운용 정지·보유 없음·미체결 없음 상태에서 전환하세요.';
}

const baseFillDefaults=fillDtDefaults,baseFillAccount=fillFromAccount,baseDtConfig=dtConfig;
fillDtDefaults=function(c){baseFillDefaults(c);portfolioForm(c,true);};
fillFromAccount=function(c){baseFillAccount(c);portfolioForm(c,false);};
dtConfig=function(){
  const c=baseDtConfig();
  const mkts=[];
  if(!$('portfolio-market-kr')||$('portfolio-market-kr').checked)mkts.push('KR');
  if($('portfolio-market-us')&&$('portfolio-market-us').checked)mkts.push('US');
  if(!mkts.length)throw Error('운용 시장을 최소 1개(국내 또는 미국) 선택하세요.');
  c.markets=mkts;
  c.portfolio_style=$('portfolio-style').value;
  c.portfolio_core=$('portfolio-core').value.split(/[,\s]+/).map(x=>x.trim()).filter(Boolean);
  c.us_core=($('portfolio-us-core')?.value||'').split(/[,\s]+/).map(x=>x.trim().toUpperCase()).filter(Boolean);
  c.portfolio_risk_pct=Number($('portfolio-risk').value);
  c.breakeven_lock=$('portfolio-be-lock')?$('portfolio-be-lock').checked:true;
  c.partial_tp=$('portfolio-partial-tp')?$('portfolio-partial-tp').checked:true;
  c.portfolio_watchlists=Object.fromEntries(Object.keys(horizonNames).map(h=>[h,$('portfolio-watch-'+h).value.split(/[,\s]+/).map(x=>x.trim()).filter(Boolean)]));
  c.us_watchlists=Object.fromEntries(Object.keys(horizonNames).map(h=>[h,($('portfolio-us-watch-'+h)?.value||'').split(/[,\s]+/).map(x=>x.trim().toUpperCase()).filter(Boolean)]));
  return c;
};
$('dt-strategy').addEventListener('change',()=>portfolioForm());
$('portfolio-preset-global')?.addEventListener('click',()=>{applyGlobalRecommendedPreset();notify('국내+미국 · 단/중/장기 수익 극대화 추천 설정을 입력했습니다.');});

function horizonCards(s){
  const style=s.config.portfolio_style||'balanced';
  const caps=horizonCaps[style]||horizonCaps.balanced,equity=s.equity||s.cash,used={short:0,medium:0,long:0};
  for(const p of Object.values(s.positions||{}))used[p.horizon||'short']+=pfHoldingKrw(s,p);
  const cashFloor=Math.max(10,100-(caps.short+caps.medium+caps.long));
  const mkts=s.config.markets||['KR'];
  return `<div class="horizon-grid">`+Object.keys(caps).map(h=>{
    const pct=equity>0?used[h]/equity*100:0;
    const b=s.reporting?.buckets?.[h]||{};
    const realized=b.realized||0,unreal=b.unrealized||0;
    return `<div class="horizon-card"><div class="row"><span class="eyebrow">${horizonNames[h]} · 상한 ${caps[h]}%</span><span class="badge ${realized+unreal>0?'ok':realized+unreal<0?'bad':''}">손익 ${signed(realized+unreal)}</span></div><h3>${won(used[h])}</h3><p>${horizonDescriptions[h]}</p><div class="allocation-track"><span style="width:${Math.min(100,caps[h]>0?pct/caps[h]*100:0)}%"></span></div><small>전체 자산 대비 ${pct.toFixed(1)}% / 한도 ${caps[h]}% · 실현 ${signed(realized)}${b.win_rate!=null?` · 승률 ${b.win_rate.toFixed(0)}%`:''}</small></div>`;
  }).join('')+`</div><p class="small muted">운용 시장: <b>${mkts.includes('KR')&&mkts.includes('US')?'🇰🇷 국내 + 🇺🇸 미국 24시간 통합':mkts.includes('US')?'🇺🇸 미국 전용':'🇰🇷 국내 전용'}</b> · 환율 기준 ${fmt(pfFx(s),1)}원/$ · 현금 예비 최소 ${cashFloor}% · 본절잠금 ${s.config.breakeven_lock!==false?'켜짐':'꺼짐'} · 분할익절 ${s.config.partial_tp!==false?'켜짐':'꺼짐'}</p>`;
}

function renderHoldingRichCard(s,p){
  const cur=pfQuoteBid(s,p),us=pfIsUs(p.symbol,p.currency);
  const pct=p.entry_price>0?(cur/p.entry_price-1)*100:0;
  const pnlKrw=pfPnlKrw(s,p),valKrw=pfHoldingKrw(s,p);
  const peak=Math.max(p.peak_price||cur,cur,p.entry_price);
  const low=Math.min(p.sl_price||p.entry_price*.92,p.entry_price*.97);
  const high=Math.max(peak*1.02,p.entry_price*1.06);
  const posPct=high>low?Math.max(0,Math.min(100,(cur-low)/(high-low)*100)):50;
  const entryPct=high>low?Math.max(0,Math.min(100,(p.entry_price-low)/(high-low)*100)):35;
  return `<div class="holding-card rich-holding">
    <div class="row"><div><b>${pfFlag(p.symbol,p.currency)} ${esc(p.name||p.symbol)}</b> <small class="muted">${esc(p.symbol)}</small></div>
      <div class="holding-badges"><span class="badge accent">${horizonNames[p.horizon||'short']}</span>${p.breakeven_locked?'<span class="badge ok">🛡 본절·수익 잠금</span>':''}${p.partial_tp_done?'<span class="badge ok">✨ 1차 분할익절 완료</span>':''}</div>
    </div>
    <div class="holding-metrics">
      <div><span>평가금액</span><b>${won(valKrw)}</b><small>${p.qty}주 × ${pfPrice(cur,p.symbol,p.currency)}</small></div>
      <div><span>평가손익</span><b class="${pct>=0?'up':'down'}">${pct>=0?'+':''}${pct.toFixed(2)}%</b><small class="${pnlKrw>=0?'up':'down'}">${signed(pnlKrw)}</small></div>
      <div><span>진입가 → 보호가</span><b>${pfPrice(p.entry_price,p.symbol,p.currency)} → ${pfPrice(p.sl_price,p.symbol,p.currency)}</b><small>최고가 ${pfPrice(peak,p.symbol,p.currency)}</small></div>
    </div>
    <div class="stop-track" title="왼쪽: 보호가격(손절/본절) · 세로선: 매수가 · 원: 현재가">
      <span class="stop-fill ${pct>=0?'gain':'loss'}" style="width:${posPct.toFixed(1)}%"></span>
      <i class="stop-entry" style="left:${entryPct.toFixed(1)}%"></i>
      <b class="stop-dot" style="left:${posPct.toFixed(1)}%"></b>
    </div>
    <p class="small muted" style="margin:6px 0 0">${esc(p.thesis||'추세 및 모멘텀 조건 진입')} · 하락 시 보호 가격(${pfPrice(p.sl_price,p.symbol,p.currency)})에서 자동 방어</p>
  </div>`;
}

function decisionSummary(s){
  const checks=s.portfolio_checks||[],entries=s.learning?.entry_checks||{},picks=s.plan?.picks||[];
  const candsBySym=Object.fromEntries(((s.scan?.candidates)||[]).map(c=>[c.symbol,c]));
  const count=checks.filter(c=>c.eligible?.length).length;
  const krChecks=checks.filter(c=>!pfIsUs(c.symbol,c.currency)).length,usChecks=checks.filter(c=>pfIsUs(c.symbol,c.currency)).length;
  let text=s.status!=='running'?'운용 정지 상태입니다. 시작 버튼을 눌러야 판단과 감시가 진행됩니다.':s.ai?.pending?'AI 투자팀 7역할이 후보와 보유 근거를 검토하고 있습니다.':!s.scan?'아직 후보 검토 전입니다. "지금 스캔 · AI 판단"을 누르면 즉시 후보를 탐색합니다.':`스캔한 ${checks.length}종목(🇰🇷 ${krChecks} · 🇺🇸 ${usChecks}) 중 ${count}종목이 퀀트 조건을 통과했고, AI가 ${picks.length}종목을 매수 후보로 선정했습니다.`;
  const filtered=checks.filter(c=>{
    const us=pfIsUs(c.symbol,c.currency),picked=picks.some(p=>p.symbol===c.symbol);
    if(pfFilter==='kr')return !us;
    if(pfFilter==='us')return us;
    if(pfFilter==='picked')return picked;
    if(pfFilter==='eligible')return (c.eligible||[]).length>0;
    return true;
  });
  const filterBar=`<div class="pf-filter-bar" role="tablist" aria-label="후보 필터">`+[
    ['all',`전체 ${checks.length}`],['picked',`AI 매수후보 ${picks.length}`],['eligible',`조건 통과 ${count}`],['kr',`🇰🇷 국내 ${krChecks}`],['us',`🇺🇸 미국 ${usChecks}`]
  ].map(([k,lbl])=>`<button type="button" class="small ${pfFilter===k?'active':''}" data-pf-filter="${k}">${lbl}</button>`).join('')+`</div>`;
  return periodBoard(s)+`<p class="decision-lead">${esc(text)}</p>`+(s.plan?.summary?`<p class="muted">AI 종합 의견: ${esc(s.plan.summary)}</p>`:'')+
    (checks.length?filterBar+`<div class="decision-list">`+filtered.slice(0,64).map(c=>{
      const p=picks.find(x=>x.symbol===c.symbol),e=entries[c.symbol],cand=candsBySym[c.symbol],pf=cand?.portfolio;
      const metrics=pf?`현재 ${pfPrice(pf.price,c.symbol)} · RSI ${pf.rsi14??'—'} · 20일 ${pf.ret20d_pct!=null?(pf.ret20d_pct>=0?'+':'')+pf.ret20d_pct+'%':'—'} · 변동성 ${pf.atr_pct??'—'}%`:'';
      return `<div class="${p?'picked-card':''}">
        <div class="row"><b>${pfFlag(c.symbol,c.currency)} ${esc(c.name||c.symbol)} <small class="muted">${esc(c.symbol)}</small></b>
        <span class="badge ${p?'ok':c.eligible?.length?'accent':''}">${p?'AI 매수 후보':c.eligible?.length?'조건 통과':'대기'}</span></div>
        ${metrics?`<small class="pf-metric-line">${esc(metrics)}</small>`:''}
        <small>${esc(e?.reason||p?.entry_status||c.reason)}${c.eligible?.length?' · 가능 기간: '+c.eligible.map(h=>horizonNames[h]).join('/'):''}</small>
      </div>`;
    }).join('')+`</div>`:'<p class="muted">아직 스캔 전입니다. 상단의 "지금 스캔 · AI 판단"을 누르면 국내·미국 후보 진단 결과가 표시됩니다.</p>');
}

function periodBoard(s){
  const style=s.config.portfolio_style||'balanced',caps=horizonCaps[style]||horizonCaps.balanced;
  return '<div class="horizon-grid">'+Object.keys(horizonNames).map(h=>{
    const picks=(s.plan?.picks||[]).filter(p=>p.horizon===h),held=Object.values(s.positions||{}).filter(p=>(p.horizon||'short')===h);
    const candidates=(s.scan?.candidates||[]).filter(c=>c.eligible_horizons?.includes(h));
    const limit=s.portfolio_buckets?.[h]?.slot_limit??Math.ceil(s.config.max_positions*(caps[h]||0)/80);
    return `<section class="horizon-card"><h3>${horizonNames[h]} 전략 슬롯</h3><p>조건 통과 <b>${candidates.length}</b> · AI 선정 <b>${picks.length}</b> · 보유 <b>${held.length} / ${limit}</b></p><p class="small muted">${esc(s.plan?.horizon_reviews?.[h]||'기간별 AI 검토 대기')}</p><b>AI 매수 계획</b><p>${picks.map(p=>`${pfFlag(p.symbol)} ${esc(p.name||p.symbol)}`).join(', ')||'없음'}</p><b>현재 보유</b><p>${held.map(p=>`${pfFlag(p.symbol,p.currency)} ${esc(p.name||p.symbol)}`).join(', ')||'없음'}</p><details><summary>조건 통과 후보 ${candidates.length}종목 보기</summary>${candidates.map(c=>`<p class="small">${pfFlag(c.symbol,c.market==='US'?'USD':'KRW')} <b>${esc(c.name||c.symbol)}</b> · ${esc(c.portfolio?.signals?.[h]?.reason||'')}</p>`).join('')||'<p class="small muted">현재 조건을 통과한 종목이 없습니다.</p>'}</details></section>`;
  }).join('')+'</div>';
}

document.addEventListener('click',e=>{
  const b=e.target.closest('[data-pf-filter]');
  if(!b)return;
  pfFilter=b.dataset.pfFilter;
  if(dtState&&$('portfolio-decisions'))$('portfolio-decisions').innerHTML=decisionSummary(dtState);
});

const baseRenderDaytrade=renderDaytrade;
renderDaytrade=function(){baseRenderDaytrade();const s=dtState;if(!s)return;
  const active=s.config.strategy==='portfolio';
  $('portfolio-decisions').innerHTML=active?decisionSummary(s):'<p>기존 당일 전략을 운용 중입니다. 보유가 없는 상태에서 운용을 정지하고 설정을 바꿔 국내+미국 단기·중기·장기 투자 방식을 선택하세요.</p>';
  if(!active)return;
  $('dt-preview').hidden=true;
  const hasUs=(s.config.markets||['KR']).includes('US'),hasKr=(s.config.markets||['KR']).includes('KR');
  $('dt-schedule').textContent=(hasKr?'🇰🇷 국내: 07:30 장전 · 09:05~15:20 매수/감시 · 10:15/12:00/14:30 재검토':'')+(hasKr&&hasUs?'  |  ':'')+(hasUs?'🇺🇸 미국: 21:30(서머타임)/22:30 장전 · 정규장 매수/감시 · 장중 재검토':'')+' · 당일 강제 청산 없음';
  $('dt-config').innerHTML=horizonCards(s);
  $('dt-plan').innerHTML=`<p>${esc(s.plan?.summary||'아직 판단 전입니다.')}</p>`+(s.plan?.picks||[]).map(p=>`<div class="feed-item"><b>${pfFlag(p.symbol)} ${esc(p.name||p.symbol)} <small class="muted">${esc(p.symbol)}</small> · ${horizonNames[p.horizon||'short']}</b><p>${esc(p.thesis)}</p><small>목표 비중 ${p.weight_pct}% · 최대 진입가 ${pfPrice(p.max_entry_price,p.symbol)} · 초기 손절 -${p.stop_loss_pct||''}% · ${esc(p.entry_status||'가격 확인 대기')}</small></div>`).join('');
  $('dt-positions').innerHTML=Object.values(s.positions||{}).map(p=>renderHoldingRichCard(s,p)).join('')||'<p class="muted">아직 보유 종목이 없습니다. 상단에서 "지금 스캔 · AI 판단"을 실행하거나 대기 사유를 확인하세요.</p>';
};

const baseRenderDashboard=renderDashboard;
renderDashboard=function(){baseRenderDashboard();const s=dbData?.account;$('db-portfolio').hidden=false;
  if(!s){$('db-portfolio').innerHTML='<div class="eyebrow">START HERE</div><h2>글로벌 AI 투자를 시작하는 세 단계</h2><p>① 시세·AI 연결 확인 → ② 국내+미국 및 단·중·장기 비중 선택 → ③ 운용 시작</p><button data-goto="daytrade" class="primary">투자 운용 설정하기</button>';return;}
  if(s.config.strategy!=='portfolio'){$('db-portfolio').innerHTML='<h2>현재는 기존 당일 전략입니다</h2><p>국내+미국 단기·중기·장기 방식은 홈 배너 또는 투자 운용 설정에서 원클릭으로 전환할 수 있습니다.</p><button data-goto="daytrade" class="primary">투자 운용 열기</button>';return;}
  $('db-portfolio').innerHTML='<div class="eyebrow">GLOBAL PORTFOLIO</div><h2>내 자산 배분 · 국내 🇰🇷 &amp; 미국 🇺🇸</h2>'+horizonCards(s);
  const sells=(s.trades||[]).filter(t=>t.side==='sell'&&new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t.at))===s.day);
  const realized=sells.reduce((v,t)=>v+(t.pnl||0),0),eq=s.equity||s.cash;
  const mkts=s.reporting?.markets||{};
  $('db-kpis').innerHTML=[
    ['총 평가자산',won(eq),`현금 ${won(s.cash)} · 환율 ${fmt(pfFx(s),0)}원/$`],
    ['오늘 실현 + 평가손익',signed(realized+(s.unrealized||0)),`오늘 매도 ${sells.length}건(${signed(realized)}) · 평가 ${signed(s.unrealized||0)}`],
    ['누적 수익률',pctc((eq/s.config.initial_cash-1)*100),`누적 실현 ${signed(s.realized_pnl||0)}`],
    ['국내 🇰🇷 · 미국 🇺🇸 보유',`${Object.keys(s.positions).length} / ${s.config.max_positions}종목`,`국내 ${mkts.KR?.holdings||0}종 · 미국 ${mkts.US?.holdings||0}종`]
  ].map(([title,value,note])=>`<div class="kpi"><span>${title}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
  $('db-positions').innerHTML=Object.values(s.positions).map(p=>renderHoldingRichCard(s,p)).join('')||'<p class="muted">보유 종목이 없습니다. 투자 운용에서 후보와 대기 이유를 확인하세요.</p>';
  $('db-positions').previousElementSibling.querySelector('.help').textContent='본절잠금 · 1차 분할익절 · 추세 추적 손절 자동 작동';
  $('db-plan').innerHTML=`<p>${esc(s.plan?.summary||'아직 판단 전입니다.')}</p>`+(s.plan?.picks||[]).map(p=>`<p><b>${pfFlag(p.symbol)} ${esc(p.name||p.symbol)} · ${horizonNames[p.horizon||'short']}</b> (${pfPrice(p.max_entry_price,p.symbol)} 이하) — ${esc(p.entry_status||'매수 후보')}</p>`).join('');
  if(!s.tuning?.last_review)$('db-review').innerHTML='<p class="muted">마감 뒤 판단과 체결 결과를 복기합니다. 근거가 충분한 교훈만 평가 적용합니다.</p>';
  $('db-sub').textContent=`${s.status==='running'?'국내·미국 24시간 스캔 및 손실방어 엔진이 작동 중입니다.':'운용이 정지되어 있습니다.'} 마지막 확인 ${hm(s.last_poll_at)}`;
  $('db-timeline').innerHTML=[
    ['07:30 KST','🇰🇷 국내 스캔·AI 회의'],
    ['09:05~15:20','🇰🇷 국내 매수·본절/익절 감시'],
    ['10:15 / 14:30','🇰🇷 장중 AI 재검토'],
    ['21:30/22:30','🇺🇸 미국 장전 스캔·AI 회의'],
    ['22:35~05:00','🇺🇸 미국 매수·실시간 감시']
  ].map(([time,label])=>`<div class="step"><span class="when">${time}</span><b>${label}</b></div>`).join('');
  $('db-next').textContent='장 마감 강제 청산 없이 보유를 이어가며, 본절잠금·분할익절·추세 이탈 기준으로 리스크를 통제합니다.';
};

const methods=document.createElement('details');methods.className='panel';methods.innerHTML=`<summary>단기 · 중기 · 장기 멀티팩터 &amp; 손실방어 원리</summary><div class="horizon-grid">
<div class="horizon-card"><h3>단기 · 스윙 반등 &amp; 돌파</h3><p>20일선 지지 반등, 20일 고점 돌파(거래량 동반), 우량 과매도 회복 구간만 선별하며 RSI 과열(82+) 급등 추격매수는 차단합니다. +2.5% 도달 시 본절잠금, +4.5% 도달 시 50% 분할익절로 승률을 높입니다.</p></div>
<div class="horizon-card"><h3>중기 · 주도 모멘텀 추종</h3><p>3~6개월 위험조정 수익률(Sharpe 대용치) 상위 주도주의 눌림과 추세 지속을 포착합니다. +4.5% 본절잠금, +10% 1차 분할익절(40%) 후 잔량은 50일선 추세를 끝까지 추종합니다.</p></div>
<div class="horizon-card"><h3>장기 · 글로벌 복리 코어</h3><p>국내 대표 ETF·대형주와 미국 빅테크·지수 ETF의 200일선 구조적 상승 추세에 투자합니다. 일시적 노이즈에 털리지 않도록 1.5% 이탈 완충폭을 두고 +20% 수익 도달 시 +10% 수익을 영구 잠급니다.</p></div></div>
<p class="small muted">주의: 과거 연구 및 기술적 추세 규칙이 미래 수익을 보장하지 않으며, 모든 주문은 계좌 위험 예산과 가격 조건 내에서만 실행됩니다.</p>`;
$('daytrade').appendChild(methods);
portfolioForm();
