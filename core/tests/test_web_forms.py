"""화면 폼이 걸려 넘어지는 자리를 정적으로 막는다.

실제로 겪은 일 (2026-09-14): 단타 계좌 설정 폼의 핸들러가 'submit' 이 아니라 'click' 에 걸려 있어서,
폼 안 아무 데나 (고급 섹션을 여는 <summary> 까지) 누르면 저장이 실행됐다. 게다가 action() 이
preventDefault() 를 부르는 바람에 <details> 가 열리지도 않았다. 브라우저 없이 잡을 수 있는 종류의 실수다.

지키려는 성질
  1) <form> 에 붙는 핸들러는 반드시 'submit' 으로 건다 (click 이면 폼 전체가 버튼이 된다)
  2) 폼 안의 보조 버튼(프리셋·취소)은 type="button" 이어야 한다 (기본값이 submit 이라 폼이 날아간다)
  3) JS 가 쓰는 element id 는 HTML 에 실재한다 (오타로 조용히 죽는 코드를 막는다)
"""
import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / 'stock-web'
SCRIPTS = ('app.js', 'dashboard.js', 'daytrade.js', 'paper.js', 'office.js', 'home.js', 'charts.js')


def sources():
    return {name: (WEB / name).read_text(encoding='utf-8') for name in SCRIPTS}


def test_form_handlers_are_bound_to_submit_not_click():
    problems = []
    for name, source in sources().items():
        for match in re.finditer(r"action\('([A-Za-z0-9-]*form)'", source):
            following = source.find("action('", match.end())
            segment = source[match.end(): following if following != -1 else len(source)]
            # 바인딩은 반드시 action('...', fn, 'submit') 형태여야 한다.
            # ,'submit') 로 정확히 찾는다 - 본문 안의 =='submit' 같은 비교는 세지 않는다.
            if ",'submit')" not in segment.replace(' ', ''):
                problems.append(f'{name}: {match.group(1)} 핸들러가 submit 이 아니라 click 에 걸려 있습니다 '
                                '(폼 안 아무 데나 눌러도 실행됩니다)')
    assert not problems, problems


def test_helper_buttons_inside_forms_do_not_submit():
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    problems = []
    for form in re.finditer(r'<form\b.*?</form>', html, re.S):
        body = form.group(0)
        form_id = (re.search(r'id="([^"]+)"', body) or [None, '?'])[1]
        buttons = re.findall(r'<button\b[^>]*>', body)
        submits = [b for b in buttons if 'type="button"' not in b]
        # 폼 하나에 진짜 제출 버튼은 하나뿐이어야 한다
        if len(submits) != 1:
            problems.append(f'{form_id}: 제출 버튼이 {len(submits)}개 - {submits}')
    assert not problems, problems


def test_every_element_id_used_by_js_exists_in_the_page():
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    present = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
    # 렌더링 중에 만들어지는 id 는 JS 문자열 안에 있으므로 함께 인정한다
    for source in sources().values():
        present |= set(re.findall(r'id="([A-Za-z0-9_-]+)"', source))
    missing = set()
    for name, source in sources().items():
        for used in re.findall(r"\$\('([A-Za-z0-9_-]+)'\)", source):
            if used not in present:
                missing.add(f'{name}: {used}')
    assert not missing, missing
