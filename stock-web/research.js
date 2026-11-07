'use strict';
// Original UI; concepts referenced from TradingAgents, FinRL and OpenBB (see release notes).
let researchSymbol='';
function selectionBoard(s){
  const picks=s.plan?.picks||[],held=Object.values(s.positions||{}).filter(p=>p.qty>0);
  const caps=horizonCaps[s.config.portfolio_style||'balanced'];
  const equity=s.equity||0,values=Object.keys(horizonNames).map(h=>held.filter(p=>p.horizon===h).reduce((v,p)=>v+p.qty*(s.quotes?.[p.symbol]?.bid||p.entry_price),0));
  const total=values.reduce((a,b)=>a+b,0)+Math.max(0,s.cash||0),colors=['#38bdf8','#a78bfa','#2dd4bf','#94a3b8'];
  let offset=0;
  const slices=[...values,Math.max(0,s.cash||0)].map((v,i)=>{const n=total?v/total*100:0,el=`<circle cx="65" cy="65" r="48" fill="none" stroke="${colors[i]}" stroke-width="13" pathLength="100" stroke-dasharray="${n} ${100-n}" stroke-dashoffset="${-offset}" transform="rotate(-90 65 65)"/>`;offset+=n;return el;}).join('');
  return `<section class="allocation-overview"><svg viewBox="0 0 130 130" role="img" aria-label="단기 중기 장기 현금 평가 비중"><circle cx="65" cy="65" r="48" fill="none" stroke="#dbe7ed" stroke-width="13"/>${slices}<text x="65" y="62" text-anchor="middle">평가자산</text><text x="65" y="79" text-anchor="middle">${total?'100%':'기록 없음'}</text></svg><div><span class="eyebrow">AI INVESTMENT DESK</span><h2>세 가지 기간, 하나의 AI 투자 판단</h2><p>${s.config.portfolio_style==='balanced'?'단기 · 중기 · 장기를 모두 검토합니다.':'현재는 '+esc(horizonNames[s.config.portfolio_style]||'기존 전략')+' 집중 설정입니다. 세 기간을 함께 운용하려면 균형형을 선택하세요.'}</p><div class="allocation-legend">${[...Object.values(horizonNames),'현금'].map((h,i)=>`<span><i style="background:${colors[i]}"></i>${h} ${total?(([...values,Math.max(0,s.cash||0)][i]/total)*100).toFixed(1):'0'}%</span>`).join('')}</div><small>AI가 종목·기간·매수 근거를 선택하고 가격·위험·자금 조건을 통과하면 주문합니다.</small></div></section><div class="selection-grid">`+Object.entries(horizonNames).map(([h,label])=>{
    const selected=picks.filter(p=>p.horizon===h),owned=held.filter(p=>p.horizon===h);
    return `<section class="selection-card"><div class="row"><h3>${label} 투자</h3><span class="badge">${caps?.[h]?'AI 판단':'비활성'}</span></div><p class="small muted">${horizonDescriptions[h]}</p><h4>AI 선정 · ${selected.length}종목</h4>${selected.map(p=>`<div class="selected-stock"><b>${esc(p.name||p.symbol)}</b><small>${esc(p.symbol)} · ${s.positions?.[p.symbol]?.qty>0?'보유 중':esc(p.entry_status||'매수 대기')}</small><p>${esc(p.thesis||'선정 근거 확인 중')}</p></div>`).join('')||'<p class="muted">선정 종목 없음</p>'}<h4>실제 보유 · ${owned.length}종목</h4><p>${owned.map(p=>`${esc(p.name||p.symbol)} <b>${p.qty}주</b>`).join('<br>')||'보유 없음'}</p><small>${esc(s.plan?.horizon_reviews?.[h]||'AI 검토 전')}</small></section>`;
  }).join('')+'</div>';
}
const exitLabels={stop:'손절·보호 가격',trend:'완료 일봉 추세',ai:'AI 근거 재검토',expiry:'기간 종료',day_end:'기존 당일 정리',target:'기존 익절',manual:'사용자 청산',other:'기타'};
function researchLine(points,key,label,color='#0d9488'){
  const rows=points.filter(p=>Number.isFinite(p[key]));
  if(rows.length<2)return '<p class="muted">그래프에 필요한 기록이 아직 부족합니다.</p>';
  const vals=rows.map(p=>p[key]),lo=Math.min(...vals),hi=Math.max(...vals),span=hi-lo||1;
  const xy=rows.map((p,i)=>`${20+i/(rows.length-1)*600},${100-(p[key]-lo)/span*75}`).join(' ');
  return `<svg class="research-spark" viewBox="0 0 640 125" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title><path d="M20 100H620" stroke="#cbd5e1"/><polyline points="${xy}" fill="none" stroke="${color}" stroke-width="2.5"/><text x="20" y="16">${hi.toFixed(2)}%</text><text x="20" y="119">${lo.toFixed(2)}%</text><text x="420" y="119">보관된 자산 기록 구간</text></svg>`;
}
function candleChart(rows){
  if(rows.length<2)return '<p class="muted">다음 후보 스캔에서 완료 일봉 차트를 수집합니다.</p>';
  const lo=Math.min(...rows.map(p=>p.low),...rows.flatMap(p=>[p.sma20,p.sma50,p.sma200].filter(Number.isFinite)));
  const hi=Math.max(...rows.map(p=>p.high),...rows.flatMap(p=>[p.sma20,p.sma50,p.sma200].filter(Number.isFinite))),span=hi-lo||1;
  const y=v=>185-(v-lo)/span*155,x=i=>42+i/(rows.length-1)*580,bw=Math.max(2,400/rows.length);
  let shapes=rows.map((r,i)=>{const color=r.close>=r.open?'#e5484d':'#2563eb';return `<g><title>${esc(r.date)} · 시가 ${r.open} / 고가 ${r.high} / 저가 ${r.low} / 종가 ${r.close}</title><path d="M${x(i)} ${y(r.high)}V${y(r.low)}" stroke="${color}"/><rect x="${x(i)-bw/2}" y="${Math.min(y(r.open),y(r.close))}" width="${bw}" height="${Math.max(1,Math.abs(y(r.open)-y(r.close)))}" fill="${color}"/></g>`;}).join('');
  for(const [key,color] of [['sma20','#f59e0b'],['sma50','#7c3aed'],['sma200','#0d9488']]){
    const pts=rows.map((r,i)=>Number.isFinite(r[key])?`${x(i)},${y(r[key])}`:null).filter(Boolean);
    if(pts.length>1)shapes+=`<polyline points="${pts.join(' ')}" fill="none" stroke="${color}" stroke-width="1.6"/>`;
  }
  return `<svg class="research-candles" viewBox="0 0 660 220" role="img" aria-label="완료 일봉과 20·50·200일 이동평균"><text x="8" y="17">${Math.round(hi).toLocaleString()}원</text>${shapes}<text x="8" y="199">${Math.round(lo).toLocaleString()}원</text><text x="42" y="218">${esc(rows[0].date)}</text><text x="540" y="218">${esc(rows.at(-1).date)}</text></svg><p class="chart-note">완료 일봉 · 주황 20일 / 보라 50일 / 초록 200일 · 막대에 마우스를 올리면 가격 표시</p>`;
}
function renderResearch(s){
  $('db-portfolio').hidden=s.config.strategy==='portfolio';
  const report=s.reporting||{},buckets=report.buckets||{},multi=s.config.strategy==='portfolio';
  $('research-mode').innerHTML=`<div><span class="eyebrow">PORTFOLIO RESEARCH · 3.21.0</span><h2>${multi?'AI 포트폴리오 스튜디오':'기존 당일 전략 운용 중'}</h2><p>${multi?'단기·중기·장기 보유를 구분합니다. 마감 시간만으로 청산하지 않습니다.':'현재 전략에는 당일 정리 규칙이 남아 있습니다. 기간별 전략으로 자동 전환된 상태가 아닙니다.'}</p></div><span class="badge">${s.mode==='live'?'실거래':'모의 운용'}</span>`;
  $('research-selection').innerHTML=selectionBoard(s);
  $('research-periods').innerHTML=Object.entries(buckets).filter(([h,b])=>h!=='legacy'||b.holdings.length||b.today_sells).map(([h,b])=>`<section class="research-period"><span class="eyebrow">${esc(b.label)}</span><strong>${won(b.today_realized)}</strong><small>오늘 실현손익 · 누적 ${won(b.realized)}</small><p>${b.holdings.map(p=>`${esc(p.name)} <b>${p.qty}주</b>`).join('<br>')||'보유 없음'}</p><span class="small">오늘 매수 ${b.today_buys} / 매도 ${b.today_sells}건</span></section>`).join('')||'<p>기간별 기록을 준비하고 있습니다.</p>';
  $('research-risk').innerHTML=`<div class="research-metrics"><div><small>종료된 보유의 수익 비율</small><strong>${report.win_rate==null?'자료 없음':report.win_rate.toFixed(1)+'%'}</strong><small>완료 ${report.closed_count||0}건 · 부분 매도 중인 보유 제외</small></div><div><small>보관구간 최대 낙폭</small><strong>${report.max_drawdown_pct==null?'자료 없음':report.max_drawdown_pct.toFixed(2)+'%'}</strong><small>기간 중 고점 대비 · 미래 손실 예측 아님</small></div><div><small>보유 평가손익 · 미확정</small><strong>${signed(s.unrealized||0)}</strong><small>실현손익과 구분 · 시세 갱신 상태 확인</small></div></div>`+researchLine(report.drawdown||[],'pct','고점 대비 자산 낙폭','#dc6b4a');
  const candidates=s.scan?.candidates||[];
  if(!candidates.some(c=>c.symbol===researchSymbol))researchSymbol=candidates[0]?.symbol||'';
  $('research-symbol').innerHTML=candidates.map(c=>`<option value="${esc(c.symbol)}" ${c.symbol===researchSymbol?'selected':''}>${esc(c.name||c.symbol)} · ${c.eligible_horizons?.map(h=>horizonNames[h]).join('/')||'조건 대기'}</option>`).join('')||'<option>후보 없음</option>';
  renderEvidence(s);
  $('research-exits').innerHTML=(report.exits||[]).slice(0,12).map(t=>`<div class="research-exit"><div><b>${esc(t.name)}</b> <span class="badge ${t.same_day?'warn':''}">${t.same_day?'매수 당일 매도':'매도'}</span> <span class="small">${esc(horizonNames[t.horizon]||'기존 당일/미분류')} · ${mdhm(t.at)}</span></div><strong>${signed(t.pnl)}</strong><p><b>${esc(exitLabels[t.kind]||'기타')}</b> · ${esc(t.reason)}</p></div>`).join('')||'<p class="muted">매도 기록이 없습니다. 매도 시 규칙과 이유를 이곳에 표시합니다.</p>';
  if(s.portfolio_exit_deferred?.length)$('research-exits').innerHTML+='<p class="notice">'+s.portfolio_exit_deferred.map(e=>esc(e.symbol+' · '+e.reason)).join('<br>')+'</p>';
}
function renderEvidence(s){
  const c=(s.scan?.candidates||[]).find(c=>c.symbol===researchSymbol),p=(s.plan?.picks||[]).find(p=>p.symbol===researchSymbol)||s.positions?.[researchSymbol];
  if(!c){$('research-evidence').innerHTML='<p class="muted">후보가 수집되면 차트·근거·반대 근거를 확인할 수 있습니다.</p>';return;}
  const f=c.portfolio||{};
  $('research-evidence').innerHTML=candleChart(f.chart||[])+`<div class="research-evidence-grid"><div><h3>매수·보유 근거</h3><p>${esc(p?.thesis||'AI가 선택한 투자 근거가 아직 없습니다.')}</p></div><div><h3>반대 근거</h3><p>${esc(p?.counter_case||'반대 근거 미제공 · 위험이 없다는 뜻은 아닙니다.')}</p></div><div><h3>매도·무효화 기준</h3><p>${esc(p?.invalidation||'기간별 추세와 보호 가격을 확인합니다.')}</p></div><div><h3>자료 상태</h3><p>일봉 ${esc(f.last_date||'미수집')} · ${f.bars||0}개<br>뉴스 ${(c.news||[]).length}개 · 경고 조회 ${c.warnings_checked?'확인':'미확인'}<br>재무제표·이익 추정치 미수집</p></div></div><div class="research-rules">${Object.entries(f.signals||{}).map(([h,v])=>`<p><span class="badge ${v.eligible?'ok':''}">${horizonNames[h]} ${v.eligible?'가격 조건 통과':'대기'}</span> ${esc(v.reason)} · 초기 손절 ${v.stop_pct}%</p>`).join('')}</div><details><summary>관련 뉴스와 출처</summary>${(c.news||[]).map(n=>`<p>${esc(n.title)}<br><small>${esc(n.source||'출처 미확인')} · ${esc(n.published_at||'시각 미확인')}</small></p>`).join('')||'<p>수집된 뉴스 없음</p>'}</details>`;
}
const researchWorkspace=document.createElement('article');researchWorkspace.className='research-workspace';researchWorkspace.innerHTML=`<div id="research-mode" class="research-hero"></div><div id="research-selection"></div><div id="research-periods" class="research-periods"></div><div class="research-layout"><section class="panel research-main"><div class="row"><h2>종목 분석 · 판단 근거</h2><select id="research-symbol" aria-label="분석할 종목"></select></div><div id="research-evidence"></div></section><section class="panel"><h2>성과와 위험</h2><div id="research-risk"></div></section></div><section class="panel"><h2>왜 팔았나요? <span class="help">최근 매도 · 당일 청산과 보호 매도 구분</span></h2><div id="research-exits"></div></section><details class="panel"><summary>참고한 공개 프로젝트</summary><p><a href="https://github.com/TauricResearch/TradingAgents" target="_blank" rel="noopener">TradingAgents</a>: 분석·반대 근거·위험 판단 분리 · <a href="https://github.com/AI4Finance-Foundation/FinRL" target="_blank" rel="noopener">FinRL</a>: 성과 평가 관점 · <a href="https://github.com/openbq-org/OpenBB" target="_blank" rel="noopener">OpenBB</a>: 데이터와 분석 화면 구성</p><p>자체 구현한 화면입니다. 해당 프로젝트의 모델 훈련·수익률을 재현한 것은 아니며, 다중 AI를 별도로 병렬 호출하지 않습니다.</p></details>`;
$('db-view').insertBefore(researchWorkspace,$('db-kpis').nextSibling);
$('research-symbol').addEventListener('change',e=>{researchSymbol=e.target.value;if(dbData?.account)renderEvidence(dbData.account);});
const baseResearchDashboard=renderDashboard;
renderDashboard=function(){baseResearchDashboard();if(dbData?.account)renderResearch(dbData.account);};
