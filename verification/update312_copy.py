from pathlib import Path
p=Path(__file__).resolve().parents[1]/'stock-web/index.html'
text=p.read_text(encoding='utf-8')
changes={
    '지금은 가상자금으로만 운용합니다.':'선택한 계좌 모드에 따라 모의 또는 실거래로 운용합니다.',
    '장전 판단의 가장 중요한 입력':'기준일을 확인하는 시장 배경 자료',
    'AI 판단 · 하루 한 번':'AI 판단 · 장전 계획과 장중 재평가',
    'AI(Claude)는 후보 안에서만 고릅니다.':'선택한 AI 모델은 후보 안에서만 고릅니다.',
    '계획대로 사고, 목표가에 닿으면 팔고(익절), 손절가에 닿으면 팝니다(손절).':'진입 가격 상한과 비용 대비 위험을 확인한 뒤 사고, 목표가·손절가에 따라 청산합니다.',
    '이 사이 AI는 부르지 않습니다 — 급등이나 새 기사가 있을 때만 다시 묻습니다.':'급등·새 기사 외에도 09:15와 12:00에 완료 분봉과 거래량으로 다시 판단합니다.',
    '지금은 <b>가상 체결</b>이라 실제 주문은 나가지 않습니다.':'<b>실거래</b> 계좌는 실제 주문을 전송합니다. 현재 모드는 화면 상단에서 확인하세요.',
}
for before,after in changes.items():
    assert before in text, before
    text=text.replace(before,after)
p.write_text(text,encoding='utf-8')
