'use strict';
const horizonNames={short:'단기',medium:'중기',long:'장기'};
const horizonCaps={balanced:{short:20,medium:30,long:30},short:{short:80,medium:0,long:0},medium:{short:0,medium:80,long:0},long:{short:0,medium:0,long:80}};
const horizonDescriptions={short:'수일~수주 · 상승 추세의 눌림과 반등',medium:'수주~수개월 · 지속적으로 강한 종목',long:'6개월 이상 · 긴 상승 추세 유지'};
strategyNotes.portfolio='기간별 일봉 조건과 AI 판단으로 투자합니다. 당일 강제 청산 없이 추세·보호 가격으로 보유를 관리합니다.';

function portfolioForm(c){
  if(c)for(const h of Object.keys(horizonNames))$('portfolio-watch-'+h).value=(c.portfolio_watchlists?.[h]||[]).join(',');
  if(c){$('portfolio-style').value=c.portfolio_style||'balanced';$('portfolio-core').value=(c.portfolio_core||['069500','229200']).join(',');$('portfolio-risk').value=c.portfolio_risk_pct??.5;}
  const active=$('dt-strategy').value==='portfolio';
  $('portfolio-fields').hidden=!active;$('legacy-entry-fields').hidden=active;$('legacy-schedule-fields').hidden=active;
  if(active)$('dt-strategy-note').textContent=strategyNotes.portfolio+' 기존 계좌는 운용 정지·보유 없음·미체결 없음 상태에서 전환하세요.';
}
const baseFillDefaults=fillDtDefaults,baseFillAccount=fillFromAccount,baseDtConfig=dtConfig;
fillDtDefaults=function(c){baseFillDefaults(c);portfolioForm(c);};
fillFromAccount=function(c){baseFillAccount(c);portfolioForm(c);};
dtConfig=function(){const c=baseDtConfig();c.portfolio_style=$('portfolio-style').value;c.portfolio_core=$('portfolio-core').value.split(/[,\s]+/).filter(Boolean);c.portfolio_risk_pct=Number($('portfolio-risk').value);c.portfolio_watchlists=Object.fromEntries(Object.keys(horizonNames).map(h=>[h,$('portfolio-watch-'+h).value.split(/[,\s]+/).filter(Boolean)]));return c;};
$('dt-strategy').addEventListener('change',()=>portfolioForm());

function horizonCards(s){
  const caps=horizonCaps[s.config.portfolio_style||'balanced'],equity=s.equity||s.cash,used={short:0,medium:0,long:0};
  for(const p of Object.values(s.positions||{})){const q=(s.quotes||{})[p.symbol];used[p.horizon||'short']+=p.qty*(q?.bid||p.entry_price);}
  return `<div class="horizon-grid">`+Object.keys(caps).map(h=>`<div class="horizon-card"><span class="eyebrow">${horizonNames[h]}</span><h3>${won(used[h])}</h3><p>${horizonDescriptions[h]}</p><div class="allocation-track"><span style="width:${Math.min(100,equity>0?used[h]/equity*100:0)}%"></span></div><small>전체 자산의 최대 ${caps[h]}% · 현재 ${equity>0?(used[h]/equity*100).toFixed(1):'0'}%</small></div>`).join('')+`</div><p class="small muted">현금 최소 20% · 비율은 목표 매수량이 아닌 상한입니다. 조건이 맞지 않으면 현금을 유지합니다.</p>`;
}
function decisionSummary(s){
  const checks=s.portfolio_checks||[],entries=s.learning?.entry_checks||{},picks=s.plan?.picks||[];
  const count=checks.filter(c=>c.eligible?.length).length;
  let text=s.status!=='running'?'운용 정지 상태입니다. 시작 버튼을 눌러야 판단과 감시가 진행됩니다.':s.ai?.pending?'AI가 후보와 보유 근거를 검토하고 있습니다.':!s.scan?'아직 후보 검토 전입니다. 다음 거래일 07:30부터 확인합니다.':`확인한 ${checks.length}종목 중 ${count}종목이 기간별 가격 조건에 맞고, AI가 ${picks.length}종목을 매수 후보로 선택했습니다.`;
  return periodBoard(s)+`<p class="decision-lead">${esc(text)}</p>`+(s.plan?.summary?`<p>AI 의견: ${esc(s.plan.summary)}</p>`:'')+
    (checks.length?`<div class="decision-list">`+checks.slice(0,48).map(c=>{const p=picks.find(p=>p.symbol===c.symbol),e=entries[c.symbol];return `<div><b>${esc(c.name||c.symbol)}</b><span class="badge ${p?'ok':''}">${p?'매수 후보':c.eligible.length?'AI 검토 대상':'가격 조건 대기'}</span><small>${esc(e?.reason||p?.entry_status||c.reason)}${c.eligible.length?' · '+c.eligible.map(h=>horizonNames[h]).join('/') :''}</small></div>`;}).join('')+`</div>`:'<p class="muted">자료 오류·추세 조건·AI 보류·주문 제한을 이곳에서 확인할 수 있습니다.</p>');
}
function periodBoard(s){
  return '<div class="horizon-grid">'+Object.keys(horizonNames).map(h=>{
    const picks=(s.plan?.picks||[]).filter(p=>p.horizon===h),held=Object.values(s.positions||{}).filter(p=>(p.horizon||'short')===h);
    const candidates=(s.scan?.candidates||[]).filter(c=>c.eligible_horizons?.includes(h));
    const limit=s.portfolio_buckets?.[h]?.slot_limit??Math.ceil(s.config.max_positions*horizonCaps[s.config.portfolio_style||'balanced'][h]/80);
    return `<section class="horizon-card"><h3>${horizonNames[h]} 종목</h3><p>검토 가능 ${candidates.length} · 매수 계획 ${picks.length} · 보유 ${held.length} / ${limit}</p><p>${esc(s.plan?.horizon_reviews?.[h]||'기간별 AI 검토 전')}</p><b>매수 계획</b><p>${picks.map(p=>esc(p.name||p.symbol)).join(', ')||'없음'}</p><b>보유</b><p>${held.map(p=>esc(p.name||p.symbol)).join(', ')||'없음'}</p><details><summary>조건 통과 후보 ${candidates.length}개</summary>${candidates.map(c=>`<p>${esc(c.name||c.symbol)} · ${esc(c.portfolio?.signals?.[h]?.reason||'')}</p>`).join('')||'<p>관심종목·자료 상태·기간별 추세 조건을 확인하세요.</p>'}</details></section>`;
  }).join('')+'</div>';
}
const baseRenderDaytrade=renderDaytrade;
renderDaytrade=function(){baseRenderDaytrade();const s=dtState;if(!s)return;
  const active=s.config.strategy==='portfolio';
  $('portfolio-decisions').innerHTML=active?decisionSummary(s):'<p>기존 당일 전략을 운용 중입니다. 보유가 없는 상태에서 운용을 정지하고, 아래 설정을 바꿔 새 투자 방식을 선택하세요.</p>';
  if(!active)return;
  $('dt-preview').hidden=true;
  $('dt-schedule').textContent='07:30 장전 검토 · 12:00 / 14:30 재검토 · 09:05~15:10 신규 매수 · 15:30 평가 기록 · 당일 강제 청산 없음 (KST)';
  $('dt-config').innerHTML=horizonCards(s);
  $('dt-plan').innerHTML=`<p>${esc(s.plan?.summary||'아직 판단 전입니다.')}</p>`+(s.plan?.picks||[]).map(p=>`<div class="feed-item"><b>${esc(p.name||p.symbol)} · ${horizonNames[p.horizon||'short']}</b><p>${esc(p.thesis)}</p><small>최대 비중 ${p.weight_pct}% · 최대 매수가 ${won(p.max_entry_price)} · ${esc(p.entry_status||'가격 확인 대기')}</small></div>`).join('');
  $('dt-positions').innerHTML=Object.values(s.positions||{}).map(p=>`<div class="holding-card"><div><b>${esc(p.name||p.symbol)}</b> <span class="badge">${horizonNames[p.horizon||'short']}</span></div><p>${p.qty}주 · 매수가 ${won(p.entry_price)} · 현재가 ${s.quotes?.[p.symbol]?won(s.quotes[p.symbol].bid):'확인 필요'}</p><strong>보호 가격 ${won(p.sl_price)}</strong><p class="small muted">${esc(p.thesis||'진입 당시 투자 근거 확인')}<br>추세가 유지되면 보유 · 하락 시 보호 가격과 기간별 기준으로 정리</p></div>`).join('')||'<p class="muted">아직 보유 종목이 없습니다. 위에서 대기 이유를 확인하세요.</p>';
};
const baseRenderDashboard=renderDashboard;
renderDashboard=function(){baseRenderDashboard();const s=dbData?.account;$('db-portfolio').hidden=false;
  if(!s){$('db-portfolio').innerHTML='<div class="eyebrow">START HERE</div><h2>투자를 시작하는 세 단계</h2><p>① 시세·AI 연결 확인 → ② 투자 기간과 금액 선택 → ③ 운용 시작</p><button data-goto="daytrade" class="primary">투자 운용 설정하기</button>';return;}
  if(s.config.strategy!=='portfolio'){$('db-portfolio').innerHTML='<h2>현재는 기존 당일 전략입니다</h2><p>단기·중기·장기 방식은 투자 운용의 설정에서 선택할 수 있습니다. 기존 보유나 주문은 자동 전환하지 않습니다.</p><button data-goto="daytrade">투자 운용 열기</button>';return;}
  $('db-portfolio').innerHTML='<div class="eyebrow">MY PORTFOLIO</div><h2>내 돈은 어디에 투자되어 있나요?</h2>'+horizonCards(s);
  const sells=(s.trades||[]).filter(t=>t.side==='sell'&&new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(t.at))===s.day);
  const realized=sells.reduce((v,t)=>v+(t.pnl||0),0),eq=s.equity||s.cash;
  $('db-kpis').innerHTML=[['평가자산',won(eq),'현금 '+won(s.cash)],['오늘 매도 손익',signed(realized),`확인된 매도 ${sells.length}건 · 보유 평가손익 제외`],['누적 수익률',pctc((eq/s.config.initial_cash-1)*100),'시작 자금 대비 자산 변화'],['보유 종목',String(Object.keys(s.positions).length),'최대 '+s.config.max_positions+'종목']].map(([title,value,note])=>`<div class="kpi"><span>${title}</span><strong>${value}</strong><small>${note}</small></div>`).join('');
  $('db-positions').innerHTML=Object.values(s.positions).map(p=>`<div class="holding-card"><b>${esc(p.name||p.symbol)} · ${horizonNames[p.horizon||'short']}</b><p>${p.qty}주 · 매수가 ${won(p.entry_price)}</p><strong>보호 가격 ${won(p.sl_price)}</strong></div>`).join('')||'<p class="muted">보유 종목이 없습니다. 투자 운용에서 후보와 대기 이유를 확인하세요.</p>';
  $('db-positions').previousElementSibling.querySelector('.help').textContent='보호 가격 이탈 시 매도 · 상승 추세는 보유';
  $('db-plan').innerHTML=`<p>${esc(s.plan?.summary||'아직 판단 전입니다.')}</p>`+(s.plan?.picks||[]).map(p=>`<p><b>${esc(p.name||p.symbol)} · ${horizonNames[p.horizon||'short']}</b> — ${esc(p.entry_status||'매수 후보')}</p>`).join('');
  if(!s.tuning?.last_review)$('db-review').innerHTML='<p class="muted">마감 뒤 판단과 체결 결과를 복기합니다. 근거가 충분한 교훈만 평가 적용합니다.</p>';
  $('db-sub').textContent=`${s.status==='running'?'기간별 투자와 보호 가격을 감시하고 있습니다.':'운용이 정지되어 있습니다.'} 마지막 확인 ${hm(s.last_poll_at)}`;
  $('db-timeline').innerHTML=[['07:30','후보·보유 검토'],['09:05~15:10','가격 조건에 맞으면 매수'],['12:00 / 14:30','AI 재검토'],['15:30','평가 기록 · 보유 유지']].map(([time,label])=>`<div class="step"><span class="when">${time}</span><b>${label}</b></div>`).join('');
  $('db-next').textContent='장 마감은 청산 시각이 아닙니다. 추세 이탈·보호 가격·투자 근거 훼손을 확인해 매도합니다.';
};
const methods=document.createElement('details');methods.className='panel';methods.innerHTML=`<summary>어떤 투자 방법을 참고했나요?</summary><div class="horizon-grid">
<div class="horizon-card"><h3>단기 · 추세 안의 눌림</h3><p>Fidelity의 이동평균 추세 해석을 참고해 20일 평균 부근 반등 또는 20일 고점 돌파를 검토합니다. 최대 20일을 계획하고 손절·추적 보호를 적용합니다.</p></div>
<div class="horizon-card"><h3>중기 · 모멘텀</h3><p>AQR의 모멘텀 원리를 참고해 3개월 또는 최근 한 달을 제외한 6개월 수익률과 20·50일 추세를 봅니다. 최대 120일을 계획하며 매일 다른 종목으로 갈아타지 않습니다.</p></div>
<div class="horizon-card"><h3>장기 · 추세 보유</h3><p>기본·장기 관심종목과 유동성 후보에서 최근 한 달을 제외한 12개월 수익률과 200일 추세를 봅니다. Vanguard의 분산 원칙을 참고하되 자산군 전체 분산을 구현한 것은 아닙니다. 상승 추세는 보유하되 보호 가격을 따라 올립니다. 재무 기반 가치투자 방식은 아닙니다.</p></div></div>
<p>참고 자료: <a href="https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/sma" target="_blank" rel="noopener">Fidelity 추세 지표</a> · <a href="https://funds.aqr.com/Insights/Strategies/Momentum-Factor" target="_blank" rel="noopener">AQR 모멘텀</a> · <a href="https://investor.vanguard.com/investor-resources-education/portfolio-management/diversifying-your-portfolio" target="_blank" rel="noopener">Vanguard 분산</a> · <a href="https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf" target="_blank" rel="noopener">AQR 추세 연구</a> · <a href="https://www.quantconnect.com/research/15338/asset-class-momentum/" target="_blank" rel="noopener">QuantConnect 모멘텀 예제</a> · <a href="https://m.samsungfund.com/upload/invest/2MH100-A.pdf" target="_blank" rel="noopener">삼성자산운용 모멘텀 투자 설명서</a></p><p class="small muted">연구의 원리를 국내 주식에 맞게 변형한 자체 규칙입니다. 원본 전략 복제나 검증된 수익률을 의미하지 않습니다. 전체 시장이 아닌 수집된 후보 안에서 판단합니다.</p>`;
$('daytrade').appendChild(methods);
portfolioForm();
