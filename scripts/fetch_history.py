"""Download ~4 years of daily bars for the backtest universe and cache them on disk.

    python scripts/fetch_history.py            # 국내 25 + 미국 25
    python scripts/fetch_history.py --only KRX

Toss caps `/api/v1/candles` at 1000 daily bars, which is almost exactly four
years of sessions. That cap - not our choice - sets the backtest window.

The cache is plain JSON per symbol so a rerun costs nothing and the backtest
stays reproducible after the API moves on. Delete the file to refetch.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'core'))

OUT = ROOT / 'backtest' / 'history'

# 시가총액 상위 위주. 4년 내내 거래된 종목만 넣었다.
KRX = [
    ('005930', '삼성전자'), ('000660', 'SK하이닉스'), ('373220', 'LG에너지솔루션'),
    ('207940', '삼성바이오로직스'), ('005380', '현대차'), ('000270', '기아'),
    ('068270', '셀트리온'), ('105560', 'KB금융'), ('055550', '신한지주'),
    ('005490', 'POSCO홀딩스'), ('035420', 'NAVER'), ('006400', '삼성SDI'),
    ('035720', '카카오'), ('051910', 'LG화학'), ('086790', '하나금융지주'),
    ('028260', '삼성물산'), ('034020', '두산에너빌리티'), ('259960', '크래프톤'),
    ('096770', 'SK이노베이션'), ('032830', '삼성생명'), ('012450', '한화에어로스페이스'),
    ('066570', 'LG전자'), ('010130', '고려아연'), ('011200', 'HMM'), ('015760', '한국전력'),
]

US = [
    ('AAPL', 'NASDAQ'), ('MSFT', 'NASDAQ'), ('NVDA', 'NASDAQ'), ('GOOGL', 'NASDAQ'),
    ('AMZN', 'NASDAQ'), ('META', 'NASDAQ'), ('TSLA', 'NASDAQ'), ('AVGO', 'NASDAQ'),
    ('AMD', 'NASDAQ'), ('NFLX', 'NASDAQ'), ('COST', 'NASDAQ'), ('ADBE', 'NASDAQ'),
    ('INTC', 'NASDAQ'), ('QCOM', 'NASDAQ'), ('TXN', 'NASDAQ'), ('AMAT', 'NASDAQ'),
    ('MU', 'NASDAQ'), ('PANW', 'NASDAQ'), ('INTU', 'NASDAQ'), ('CSCO', 'NASDAQ'),
    ('JPM', 'NYSE'), ('V', 'NYSE'), ('UNH', 'NYSE'), ('XOM', 'NYSE'), ('WMT', 'NASDAQ'),
]


def load_env() -> None:
    """.env 를 읽어 환경변수로 넣는다. 값을 감싼 따옴표는 벗긴다."""
    path = ROOT / '.env'
    if not path.exists():
        path = ROOT / '.env.stock'      # 이름을 바꾸기 전 설치본
    if not path.exists():
        sys.exit('.env 가 없습니다. 토스 키가 있어야 시세를 받습니다.')
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        os.environ.setdefault(key.strip(), value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', choices=['KRX', 'US'], help='한쪽 시장만 받는다')
    parser.add_argument('--from-themes', type=int, metavar='N', dest='from_themes',
                        help='상위 N개 테마의 구성 종목 전체를 받는다 (국내만)')
    parser.add_argument('--count', type=int, default=1000, help='일봉 개수 (최대 1000)')
    parser.add_argument('--refresh', action='store_true', help='캐시를 무시하고 다시 받는다')
    args = parser.parse_args()

    load_env()
    from sqt.models import Instrument
    from sqt.toss import Toss

    client = Toss()
    if not client.configured:
        sys.exit('토스 키가 설정되지 않았습니다 (TOSS_CLIENT_ID / TOSS_CLIENT_SECRET).')

    targets: list[tuple[str, str, str]] = []
    if args.from_themes:
        # 테마 구성은 '오늘' 기준이다. 과거에 그대로 적용하면 사후편향이 생기므로,
        # 백테스트에서는 대조군도 반드시 같은 종목 풀에서 뽑아 편향을 상쇄해야 한다.
        from sqt import themes
        data = themes.overview(limit=args.from_themes)
        if data['error']:
            sys.exit(f'테마 수집 실패: {data["error"]}')
        seen: dict[str, str] = {}
        membership: dict[str, list[str]] = {}
        for theme in data['themes']:
            for stock in theme.get('stocks', []):
                seen.setdefault(stock['symbol'], stock['name'])
                membership.setdefault(stock['symbol'], []).append(theme['name'])
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT.parent / 'themes.json').write_text(json.dumps(
            {'fetched_at': datetime.now().isoformat(timespec='seconds'),
             'themes': [{k: v for k, v in th.items() if k != 'stocks'} |
                        {'symbols': [s['symbol'] for s in th.get('stocks', [])]}
                        for th in data['themes']],
             'membership': membership, 'names': seen}, ensure_ascii=False, indent=2),
            encoding='utf-8')
        print(f'상위 {args.from_themes}개 테마 · 고유 종목 {len(seen)}개 '
              f'(구성표: backtest/themes.json)')
        targets += [(code, 'KRX', name) for code, name in seen.items()]
    else:
        if args.only != 'US':
            targets += [(code, 'KRX', name) for code, name in KRX]
        if args.only != 'KRX':
            targets += [(code, exchange, code) for code, exchange in US]

    OUT.mkdir(parents=True, exist_ok=True)
    ok = skipped = failed = 0

    for code, exchange, name in targets:
        path = OUT / f'{exchange}_{code}.json'
        if path.exists() and not args.refresh:
            bars = json.loads(path.read_text(encoding='utf-8'))['bars']
            print(f'  = {code:8s} {len(bars):5d}봉 (캐시)')
            skipped += 1
            continue
        try:
            bars = client.history(Instrument(symbol=code, exchange=exchange), count=args.count)
        except Exception as exc:  # noqa: BLE001 - 한 종목 실패로 전체를 멈추지 않는다
            print(f'  ! {code:8s} 실패: {type(exc).__name__}: {str(exc)[:90]}')
            failed += 1
            continue
        path.write_text(json.dumps(
            {'symbol': code, 'exchange': exchange, 'name': name, 'bars': bars},
            ensure_ascii=False), encoding='utf-8')
        print(f'  + {code:8s} {len(bars):5d}봉  {bars[0]["date"]} ~ {bars[-1]["date"]}')
        ok += 1
        time.sleep(0.2)  # 연속 호출 사이 여유. 공식 한도를 넘길 이유가 없다.

    print(f'\n신규 {ok} / 캐시 {skipped} / 실패 {failed}   저장 위치: {OUT.relative_to(ROOT)}')
    return 1 if failed and not ok and not skipped else 0


if __name__ == '__main__':
    sys.exit(main())
