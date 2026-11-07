"""GPT 관심 종목 추천. 네트워크와 실제 GPT 없이 검증한다.

여기서 지키려는 핵심 성질은 하나다.
**GPT 는 넘겨준 후보 목록 밖의 종목을 관심 종목에 넣을 수 없다.**
목록 밖 코드가 하나라도 섞이면 그 종목만 빼는 게 아니라 추천 전체를 폐기한다.
종목 코드를 지어내고 그럴듯한 근거를 붙이는 것이 LLM 의 가장 흔한 실패라서,
이 경계는 GPT 의 판단력과 무관하게 코드가 지켜야 한다.
"""
import pytest
from fastapi.testclient import TestClient

from sqt import themes
from sqt.api import create_app
from sqt.paper_service import PaperService

THEME_DATA = {
    'error': '', 'total': 2, 'source': 'test', 'note': '테스트',
    'themes': [
        {'no': 1, 'name': '전선', 'change_pct': 7.29, 'change_3d_avg_pct': 1.62,
         'up': 8, 'flat': 0, 'down': 0, 'members': 2, 'leaders': [],
         'stocks': [{'symbol': '000500', 'name': '가온전선'},
                    {'symbol': '006260', 'name': 'LS'}]},
        {'no': 2, 'name': '정유', 'change_pct': 4.46, 'change_3d_avg_pct': 1.99,
         'up': 2, 'flat': 0, 'down': 1, 'members': 2, 'leaders': [],
         'stocks': [{'symbol': '096770', 'name': 'SK이노베이션'},
                    {'symbol': '010950', 'name': 'S-Oil'}]},
    ],
}


def plan(symbols):
    return {'summary': '테스트 추천', 'us_context': '연준 자료와의 연결은 근거가 부족합니다.',
            'picks': [{'symbol': s, 'theme': '전선',
                       'thesis': '완료 일봉 기준 60일선 위에서 거래량이 늘었습니다.',
                       'counter_case': '3일 평균이 낮아 하루 급등일 가능성이 있습니다.'}
                      for s in symbols]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('ADMIN_PASSWORD', 'test-stock-password')
    monkeypatch.setattr(themes, 'overview', lambda limit=10, with_members=True: THEME_DATA)
    app = create_app(tmp_path / 'test.sqlite3', background=False)
    with TestClient(app) as c:
        assert c.post('/api/login', json={'password': 'test-stock-password'}).status_code == 200
        yield c


def use_ai(client, monkeypatch, result):
    """앱이 실제로 쓰는 SubscriptionAI 인스턴스를 가짜로 바꾼다."""
    service = client.app.state.paper
    monkeypatch.setattr(service.ai, 'recommend', lambda snapshot: result)
    monkeypatch.setattr(service.ai, 'status',
                        lambda: {'installed': True, 'authenticated': True, 'api_key_used': False})


def test_invented_symbol_discards_the_whole_recommendation(client, monkeypatch):
    # 실재하지 않는 코드 하나가 섞이면, 나머지 정상 종목까지 함께 버린다.
    use_ai(client, monkeypatch, plan(['000500', '999999']))
    response = client.post('/api/themes/recommend', json={})
    assert response.status_code == 422
    assert '999999' in response.json()['detail']


def test_symbol_outside_candidates_is_rejected_even_if_real(client, monkeypatch):
    # 실재하는 코드라도 후보 목록에 없으면 거부한다. GPT 가 유니버스를 넓힐 수 없다.
    use_ai(client, monkeypatch, plan(['005930']))
    assert client.post('/api/themes/recommend', json={}).status_code == 422


def test_valid_picks_get_names_and_watchlist(client, monkeypatch):
    use_ai(client, monkeypatch, plan(['000500', '096770']))
    body = client.post('/api/themes/recommend', json={}).json()
    assert [p['symbol'] for p in body['picks']] == ['000500', '096770']
    assert [p['name'] for p in body['picks']] == ['가온전선', 'SK이노베이션']
    assert body['watchlist'] == [{'exchange': 'KRX', 'symbol': '000500'},
                                 {'exchange': 'KRX', 'symbol': '096770'}]
    assert body['candidate_count'] == 4
    # 후보가 어느 테마에 속하는지 되돌려줘야 화면에서 분산 정도를 보여줄 수 있다.
    assert body['picks'][1]['themes'] == ['정유']


def test_recommendation_never_places_an_order(client, monkeypatch):
    use_ai(client, monkeypatch, plan(['000500']))
    body = client.post('/api/themes/recommend', json={}).json()
    assert '주문은' in body['note']
    # 추천만으로 계좌나 포지션이 생기지 않는다.
    assert client.get('/api/overview').json()['live_orders'] is False


def test_theme_context_is_limited_to_the_watchlist(monkeypatch):
    monkeypatch.setattr(themes, 'overview', lambda limit=10, with_members=True: THEME_DATA)
    service = PaperService.__new__(PaperService)

    rows = service.watchlist_themes({'currency': 'KRW', 'watchlist': [{'symbol': '006260'}]})
    assert [r['name'] for r in rows] == ['전선']
    assert rows[0]['watchlist_members'] == ['006260']
    assert '000500' in rows[0]['other_members']

    # 달러 계좌에 국내 테마를 넣지 않는다.
    assert service.watchlist_themes({'currency': 'USD', 'watchlist': [{'symbol': '006260'}]}) == []


def test_theme_source_failure_does_not_break_decisions(monkeypatch):
    def broken(limit=10, with_members=True):
        raise OSError('naver down')

    monkeypatch.setattr(themes, 'overview', broken)
    service = PaperService.__new__(PaperService)
    assert service.watchlist_themes({'currency': 'KRW', 'watchlist': [{'symbol': '006260'}]}) == []
