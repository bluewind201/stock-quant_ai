"""테마 수집 파서. 네트워크 없이 저장된 응답으로만 검증한다.

바깥 API 에 의존하는 모듈이라, 여기서 지키려는 것은 세 가지다.
  1) 응답의 문자열 숫자를 정확히 숫자로 바꾸는가
  2) 거래정지·관리종목을 표시해서 내보내는가 (관심 종목에 넣으면 주문이 안 나간다)
  3) 응답 형태가 바뀌거나 요청이 실패했을 때 예외를 밖으로 던지지 않는가

2026-09-10 에 출처가 HTML 표에서 JSON API 로 바뀌면서 파싱이 통째로 깨진 적이 있다.
그때 매매 판단까지 멈추지는 않았는데, 3) 을 지켜둔 덕분이었다.
"""
import sqt.themes as themes

LIST_RESPONSE = [
    {'no': '598', 'type': 'theme', 'name': '콩/대두', 'totalCnt': '5',
     'riseCnt': '5', 'fallCnt': '0', 'steadyCnt': '0',
     'changeRate': '14.45', 'recent3daysChangeRate': '-0.18',
     'leadingItem': '1,007540,샘표|1,248170,샘표식품'},
    {'no': '177', 'type': 'theme', 'name': 'LPG(액화석유가스)', 'totalCnt': '9',
     'riseCnt': '6', 'fallCnt': '3', 'steadyCnt': '0',
     'changeRate': '3.35', 'recent3daysChangeRate': '1.33',
     'leadingItem': ''},
]

STOCKS_RESPONSE = [
    {'itemname': '미래생명자원', 'itemcode': '218150', 'tradeStopYn': 'N',
     'manageStatusGb': '0', 'tradableStatus': 'tradable'},
    {'itemname': '샘표식품', 'itemcode': '248170', 'tradeStopYn': 'N',
     'manageStatusGb': '0', 'tradableStatus': 'tradable'},
    # 같은 종목이 다시 나와도 한 번만 담는다.
    {'itemname': '샘표식품', 'itemcode': '248170', 'tradeStopYn': 'N',
     'manageStatusGb': '0', 'tradableStatus': 'tradable'},
    {'itemname': '거래정지종목', 'itemcode': '999990', 'tradeStopYn': 'Y',
     'manageStatusGb': '1', 'tradableStatus': 'suspended'},
]


def test_list_parses_numbers_counts_and_leaders():
    rows = themes.parse_list(LIST_RESPONSE)
    assert len(rows) == 2

    first = rows[0]
    assert first['no'] == 598 and isinstance(first['no'], int)
    assert first['name'] == '콩/대두'
    assert first['change_pct'] == 14.45
    assert first['change_3d_avg_pct'] == -0.18
    assert (first['up'], first['flat'], first['down']) == (5, 0, 0)
    assert first['members'] == 5
    assert [x['symbol'] for x in first['leaders']] == ['007540', '248170']
    assert first['leaders'][0]['name'] == '샘표'

    # 하루 급등이 3일 기준으로는 마이너스인 경우. 이 구분이 사라지면 안 된다.
    assert rows[0]['change_pct'] > 0 > rows[0]['change_3d_avg_pct']
    # 주도주가 비어 있어도 깨지지 않는다.
    assert rows[1]['leaders'] == []


def test_members_dedupe_and_flag_untradable():
    rows = themes.parse_members(STOCKS_RESPONSE)
    assert [r['symbol'] for r in rows] == ['218150', '248170', '999990']
    assert rows[0]['name'] == '미래생명자원'
    assert rows[0]['tradable'] is True and rows[0]['managed'] is False
    # 거래정지·관리종목은 버리지 않고 표시한다. 화면에서 걸러낼 수 있어야 한다.
    assert rows[2]['tradable'] is False and rows[2]['managed'] is True


def test_unexpected_shape_returns_empty_without_raising():
    assert themes.parse_list({'message': 'Not Found'}) == []
    assert themes.parse_list([{'name': '필드 누락'}]) == []
    assert themes.parse_members({'error': 'boom'}) == []
    assert themes.parse_members([{'itemcode': '짧음'}]) == []


def test_request_failure_is_reported_not_raised(monkeypatch):
    def boom(path, params, timeout=15.0):
        raise OSError('network down')

    monkeypatch.setattr(themes, '_get', boom)
    themes._cache.clear()

    top = themes.top_themes(limit=5)
    assert top['themes'] == []
    assert '실패' in top['error']

    detail = themes.members(598)
    assert detail['members'] == [] and '실패' in detail['error']
    # 설명문은 없어도 그만이다. 빈 문자열로 조용히 넘어간다.
    assert themes.description(598) == ''
    themes._cache.clear()


def test_overview_attaches_members_and_description(monkeypatch):
    def fake(path, params, timeout=15.0):
        if path.endswith('/stocklist'):
            return STOCKS_RESPONSE
        if path.endswith('/info'):
            return {'name': '콩/대두', 'categoryInfo': '식용유·장류 원료 종목군.'}
        return LIST_RESPONSE

    monkeypatch.setattr(themes, '_get', fake)
    themes._cache.clear()

    data = themes.overview(limit=1)
    assert data['error'] == ''
    theme = data['themes'][0]
    assert [s['symbol'] for s in theme['stocks']] == ['218150', '248170', '999990']
    assert theme['description'] == '식용유·장류 원료 종목군.'
    # 등락률이 매수 신호가 아니라는 경고를 응답에서 떼지 말 것
    assert '매수 신호가 아니라' in data['note']
    themes._cache.clear()
