from pathlib import Path
root=Path(__file__).resolve().parents[1]
p=root/'stock-web/index.html'
s=p.read_text(encoding='utf-8')
s=s.replace('Stock Quant · AI 당일 단타','Stock Quant · AI 투자 관리').replace('AI 당일 단타','AI 투자 관리')
s=s.replace('<script src="/static/daytrade.js" defer></script>','<script src="/static/daytrade.js" defer></script><script src="/static/portfolio.js" defer></script>')
s=s.replace('장전에 프로그램이 후보를 거르고, AI가 고르고, 익절·손절선이 장중을 지킵니다. 선택한 계좌 모드에 따라 모의 또는 실거래로 운용합니다.','단기·중기·장기로 나누어 투자하고, AI가 근거를 검토합니다. 내 자산과 매매 이유를 한눈에 확인하세요.')
s=s.replace('data-tab="dashboard">대시보드','data-tab="dashboard">내 자산').replace('data-tab="daytrade">당일 단타','data-tab="daytrade">투자 운용')
s=s.replace('<button data-tab="paper">관심종목 운용</button>','')
s=s.replace('단타 계좌의 오늘 상태와 자산 흐름입니다.','지금 투자한 금액, 남은 현금, 다음 판단을 확인하세요.')
s=s.replace('<div id="db-view" hidden>','<article id="db-portfolio" class="panel" hidden></article>\n<div id="db-view" hidden>')
start=s.index('<details id="db-guide"');end=s.index('</details>',start)+len('</details>')
s=s[:start]+'''<details id="db-guide" class="panel"><summary>처음 사용하는 분을 위한 안내</summary><div class="guide">
<div><b>1. 운용 방식 선택</b>실제 시세로 가상 자금을 운용하거나, 연결한 실계좌로 운용합니다. 과거 시세를 재생하는 훈련은 제공하지 않습니다.</div>
<div><b>2. 투자 기간 선택</b>단기는 수일~수주, 중기는 수주~수개월, 장기는 6개월 이상을 봅니다. 균형형은 세 기간에 나누어 투자합니다.</div>
<div><b>3. AI 판단과 가격 확인</b>완료된 일봉과 뉴스로 후보를 검토하고 가격·자금 조건이 맞을 때 매수합니다. 매수하지 못한 이유도 표시합니다.</div>
<div><b>4. 보유와 매도</b>당일 마감으로 팔지 않습니다. 보호 가격이나 추세가 깨지거나 투자 근거가 훼손되면 정리합니다. 장기 방식은 가격 추세 전략이며 기업 가치평가를 대신하지 않습니다.</div>
</div></details>'''+s[end:]
s=s.replace('<div class="eyebrow">DAY TRADE</div><h1>당일 단타</h1><p>고를 것은 하나입니다 — <b>모의로 검증할지, 실제 돈으로 할지</b>. 종목은 매일 AI가 고릅니다.</p>',
'''<div class="eyebrow">MY INVESTMENT</div><h1>투자 운용</h1><p>투자 기간과 금액을 정하세요. AI가 종목과 근거를 검토하고 프로그램이 가격 조건을 확인합니다.</p>''')
s=s.replace('<h3>운용 전략</h3><label>신규 진입 방식<select id="dt-strategy">','<h3>운용 방식</h3><label>방식 선택<select id="dt-strategy"><option value="portfolio">단기·중기·장기 투자 (새 기본 방식)</option><optgroup label="기존 계좌 호환 · 당일 전략">')
s=s.replace('AI 승인·당일 주도주 ORB (추천)','AI 승인·당일 주도주 ORB (기존)')
s=s.replace('<option value="bull_flag">상승 깃발형</option></select>','<option value="bull_flag">상승 깃발형</option></optgroup></select>')
insert=s.index('<div id="dt-basket-fields"')
s=s[:insert]+'''<div id="portfolio-fields"><div class="form-grid">
<label>투자 기간<select id="portfolio-style"><option value="balanced">균형형 · 단기 20% / 중기 30% / 장기 30%</option><option value="short">단기 집중 · 수일~수주</option><option value="medium">중기 집중 · 수주~수개월</option><option value="long">장기 집중 · 6개월 이상</option></select></label>
<label>기본 관심종목 (국내 6자리 코드, 쉼표로 구분)<input id="portfolio-core" value="069500,229200"></label></div>
<p class="small muted">기본 관심종목과 거래대금·테마 후보를 함께 검토합니다. 각 비율은 상한이며 현금은 최소 20%를 남깁니다. 하루가 끝나도 보유를 유지합니다.</p>
<details><summary>한 거래의 손실 예산</summary><label>계좌 대비 예상 손절 위험 (%)<input id="portfolio-risk" type="number" value="0.5" min="0.1" max="1" step="0.1"></label><p>변동성과 비용에 따라 매수 수량을 줄입니다. 급락·갭에서는 실제 손실이 이 예산을 넘을 수 있습니다.</p></details></div>
<div id="legacy-entry-fields">'''+s[insert:]
s=s.replace('<h3>기본</h3>','</div><h3>투자 금액과 분산</h3>',1)
s=s.replace('하루 최대 종목 수','동시에 보유할 최대 종목 수')
s=s.replace('<h3>목표가 · 손절가 범위','<div id="legacy-schedule-fields"><h3>목표가 · 손절가 범위',1)
s=s.replace('<p id="dt-session-note" class="small muted"></p>','<p id="dt-session-note" class="small muted"></p></div>',1)
s=s.replace('단타 전용 계좌여야 하며','프로그램 전용 계좌여야 하며')
s=s.replace('<article class="panel"><h2>장전 스캔 후보','<details class="panel"><summary>후보의 상세 자료 (고급)</summary><h2>장전 스캔 후보')
s=s.replace('<div id="dt-candidates" class="table-wrap"></div></article>','<div id="dt-candidates" class="table-wrap"></div></details>')
s=s.replace('<div class="grid"><article class="panel"><h2>오늘의 계획','<article class="panel"><h2>왜 매수했거나 기다리고 있나요?</h2><div id="portfolio-decisions"></div></article><div class="grid"><article class="panel"><h2>오늘의 계획',1)
s=s.replace('장 마감 뒤 AI가 오늘을 되짚고 내일 숫자를 바꿉니다','판단과 결과를 기록하고 근거 있는 개선안을 평가합니다')
s=s.replace('모의 성과만으로 실거래를 자동 시작하지 않습니다.','실거래는 별도로 연결하고 시작해야 합니다.')
s=s.replace('<article class="panel"><h2>운용 검증 진행</h2>','<article class="panel" hidden><h2>이전 운용 기록</h2>')
s=s.replace('당일 단타와 별개의 계좌이며','기존 관심종목 전용 계좌이며')
s=s.replace('당일 단타는 이 목록을 쓰지 않습니다 — 매일 자동으로 스캔합니다.','투자 운용의 기본 관심종목은 해당 계좌 설정에서 입력합니다.')
s=s.replace('<details class="panel"><summary>토스증권 Open API 발급 순서</summary>', '<details class="panel"><summary>기존 관심종목 계좌 관리 (고급)</summary><p>이전에 만든 관심종목 계좌의 보유와 주문을 관리합니다.</p><button data-goto="paper">기존 계좌 열기</button></details><details class="panel"><summary>토스증권 Open API 발급 순서</summary>')
s=s.replace('</main><footer>Stock Quant · 가상 체결 · 급락과 갭 손실은 실제 계좌에서 더 큽니다</footer>', '</main><footer>Stock Quant · 투자 판단과 체결은 구분됩니다 · 현재 운용 모드를 확인하세요</footer>')
p.write_text(s,encoding='utf-8')
