# Synology에서 AI 주식 투자 운용

## 최신 릴리스 3.22.2 · 2026-11-06

[전 종목 탈락 수정](UX-OVERHAUL-2026-11-02.md) (3.22.2 절). 달력 라이브러리가 2026-06-03, 2026-07-17 휴장을 몰라
portfolio 계좌의 모든 종목이 일봉 검사에서 탈락해 회의·매수가 멈췄던 문제를 고쳤다. 달력이 모르는 휴장일은 KODEX 200 일봉으로
찾고, 스캔한 종목은 규칙을 통과한 어느 기간이든 후보가 된다(사용자 결정). 운영 점검에 매수 후보 항목을 추가했다.

Windows 393개, amd64 Linux 392개 통과. 외부 통신 차단 컨테이너 3단계 검증 통과.
공개 일봉 10종 비교: 수정 전 10종 탈락, 수정 후 10종 자료 검사 통과.
사용자 승인 후 Docker Hub `bluewind201/stock-quant:3.22.2` / `latest` 게시 및 pull 확인 완료.
두 태그의 amd64/arm64 이미지 digest:
`sha256:ba9476cb358f9bf9a80c1cb5950037abd6d86ce8ec75e82f54f1c22dfce1b53e`.
받은 amd64 이미지로 컨테이너 검증과 시크릿 파일 검사를 다시 통과했다. arm64는 빌드·매니페스트만 확인했다.
실제 7역할 회의, 주문, Telegram 발송, NAS 업데이트는 수행하지 않았다.

```sh
docker compose pull stock
docker compose up -d stock
```

## 이전 릴리스 3.22.1 · 2026-11-04

[조용한 멈춤 방지](UX-OVERHAUL-2026-11-02.md) (3.22.1 절). 3.22.0에서 회의·텔레그램이 이틀간 없었다는 보고에 따라
홈 맨 위 운영 점검, AI 판단·스캔 실패와 "회의 미개최"의 하루 한 번 경고, 거래 없는 날에도 매일 결산을 추가했다.
Windows 382개, amd64 Linux 381개 통과. 외부 통신 차단 컨테이너 3단계 검증(인증 이동·재생성 후 유지·전용 볼륨)과
점검 API 확인, PC·모바일 브라우저 검증 통과.
사용자 승인 후 Docker Hub `bluewind201/stock-quant:3.22.1` / `latest` 게시 및 pull 확인 완료.
두 태그의 amd64/arm64 이미지 digest:
`sha256:94b417eb2eeeabe05cab0fb38233d93f3a102f51fb4948ae2a78749b540d82b3`.
받은 amd64 이미지로 컨테이너 3단계 검증과 시크릿 파일 검사를 다시 통과했다. arm64는 빌드·매니페스트만 확인했다.
실제 NAS 원인·실제 AI 호출·Telegram 발송·NAS 업데이트는 확인하지 않았다.

```sh
docker compose pull stock
docker compose up -d stock
```

적용 후 홈 맨 위 "운영 점검"의 빨간 항목부터 해결한다(대부분 연결·설정 → AI 로그인).

## 이전 릴리스 3.22.0 · 2026-11-02

[홈·AI 회의실·뉴스 센터·인증 보존·짧은 알림](UX-OVERHAUL-2026-11-02.md).
쉬운 홈 화면과 그래프, 담당자가 움직이며 회의하는 2D 회의실, 분류된 뉴스 센터(영어 제목 한국어 번역),
웹 화면 AI 로그인과 인증 보존, 매매 보고 위주의 텔레그램 알림을 추가했다.

Windows 376개, amd64 Linux 375개 통과(각각 Linux 전용 3개 / Windows 전용 4개 제외).
PC·모바일 브라우저 검증과 외부 통신 차단 컨테이너의 재생성 후 인증 유지 검증 통과.
사용자 승인 후 Docker Hub `bluewind201/stock-quant:3.22.0` / `latest` 게시 및 pull 확인 완료.
두 태그의 amd64/arm64 이미지 digest:
`sha256:8f73d50ee36cd3c5afdd04ec7eefef2199464eea71b01f6e489aec5ff4387509`.
받은 amd64 이미지로 외부 통신 차단 컨테이너 검증(인증 폴더 이동, 컨테이너 재생성 후 인증·토큰·알림 범위 유지,
전용 볼륨 비변경, 실제 Claude CLI 로그인 주소 생성)과 시크릿 파일 검사를 다시 통과했다.
arm64는 빌드·매니페스트만 확인했다. 실제 구독 AI 호출·번역·주문·Telegram 발송·NAS 업데이트는 수행하지 않았다.
고정 태그를 쓰면 `.env`의 `STOCK_IMAGE=bluewind201/stock-quant:3.22.0`으로 변경한다.

NAS 적용 (프로젝트 폴더):

```sh
docker compose pull stock
docker compose up -d stock
```

- Compose에 `TELEGRAM_LEVEL`, `PUBLIC_URL` 줄이 새로 있다. 예전 Compose를 그대로 써도 기본값(매매 보고만)으로 동작한다.
- 인증 폴더가 볼륨이 아니던 경우 첫 실행에서 한 번 더 로그인해야 한다(설정 → AI 로그인). 이후 업데이트에는 유지된다.
- 텔레그램 범위는 설정 화면에서 바꾼다. 브라우저는 강력 새로고침.

## 이전 릴리스 3.21.0 · 2026-11-01

[AI 오피스 구현·인수인계](AI-OFFICE-2026-11-01.md).
자료 수집 → 차트/수급·심리/뉴스·거시/기업·공시 4개 분석 → 찬반 리서치 → 매매 제안 → 위험 검토.
선택한 구독 AI를 역할별로 7회 순차 호출하며 portfolio 전략의 장전·장중 계획에 기본 적용한다.
기존 계좌의 전략·보유 기간은 유지하며 설정에서 AI 오피스를 끄면 기존 단일 판단을 사용한다.
트레이딩 데스크의 AI 오피스에서 실제 진행 중 역할만 2D 동작으로 표시하고 보고서·출처·부족 자료를 확인한다.
뉴스 수집 범위를 보유·후보 종목, 기업 관련 기사, 미국 정책·연준·경제 지표와 테마로 확대했다.
DART 원문 재무·SNS 원자료·컨센서스는 미연동이며 구독 사용량과 판단 시간이 증가한다.

Windows 전체 360개, amd64 Linux 356개 통과(Windows 전용 4개 제외).
전체 페이지 PC/모바일 브라우저 테스트와 외부 통신 차단 컨테이너 실행 검증 통과.
arm64는 빌드 검증만 했다. 실제 AI 호출·주문·Telegram 발송·NAS 재시작은 수행하지 않았다.
Docker Hub `bluewind201/stock-quant:3.21.0` / `latest` 게시 및 pull 확인 완료.
두 태그의 amd64/arm64 이미지 digest:
`sha256:6b08fee0076c13e85e4007e412c076b6347d817d90b458295b1752f88f1cfd7d`.
고정 태그를 쓰면 `.env`의 `STOCK_IMAGE=bluewind201/stock-quant:3.21.0`으로 변경한다.

```sh
docker compose pull stock
docker compose up -d stock
```

브라우저를 강력 새로고침하고 AI 오피스 탭을 연다. 다음 portfolio 계획 실행부터 역할별 기록이 표시된다.
기존 인증 볼륨을 유지한다. NAS 업데이트는 사용자가 직접 수행한다.


## 이전 릴리스 3.20.0 · 2026-11-01

[트레이딩 데스크 전면 개편](TRADING-DESK-2026-11-01.md).
어두운 유리 패널, 왼쪽 메뉴/모바일 하단 메뉴, 차트·성과/기간별 포트폴리오/매매·기록/분석·복기로 구성했다.
모든 화면 상단에 GPT·Claude 상태와 선택한 AI, 확인 시각을 표시한다.
인증 확인·토큰 설정·조회 실패를 구분하며 모델 호출 성공과 인증 상태는 별도다.
사용자는 기존 AI 연결이 정상임을 확인했다. 이번 변경은 화면 중심이며 매매 전략·보유는 자동 변경하지 않는다.

실제 페이지 스크립트를 사용하는 PC/모바일 통합 테스트에서 로그인, 네 작업 공간, 키보드 전환,
운용/뉴스/설정 페이지, 인증 조회 실패·복구, 가로 넘침, 동작 줄이기, 주문 요청 없음 검증 통과.
외부 통신을 차단한 amd64 컨테이너에서 로그인·계좌 설정·새 화면 파일·모델 목록 검사 통과.
arm64는 빌드 검증만 했다. 실제 AI 호출·주문·NAS 재시작은 하지 않았다.
Docker Hub `bluewind201/stock-quant:3.20.0` / `latest` 게시 및 pull 확인 완료.
두 태그의 amd64/arm64 이미지 digest:
`sha256:701d19b1ca200f6a1e6f9f31cb439fd455adbf0875e6783a71724f390ed35e48`.
고정 태그를 쓰면 `.env`의 `STOCK_IMAGE=bluewind201/stock-quant:3.20.0`으로 변경한다.

```sh
docker compose pull stock
docker compose up -d stock
```

이전 화면이 보이면 브라우저를 강력 새로고침한다. 인증 볼륨을 지우거나 새로 로그인할 필요는 없다.

## 이전 릴리스 3.19.2 · 2026-10-31

[Docker AI 로그인 명령어와 수정 내역](AI-AUTH-2026-10-31.md).
Claude의 저장된 구독 로그인 인식, GPT 로그인/실행의 file 인증 저장소 일치,
인증 확인 후 캐시 갱신, 두 AI 각각의 상태 확인 버튼을 추가했다.
NAS에서 발생한 실제 오류 문구는 아직 미확인이다. 사용자 계정의 브라우저 승인은 직접 진행해야 한다.

Windows 전체 353개 통과 후 캐시 무효화 변경 관련 26개 재검증, 최종 amd64 Docker 전체 349개 통과(4개 Windows 전용 제외).
전체 페이지 로그인·PC·모바일 테스트와 격리 컨테이너 실행 검사 통과. arm64는 빌드 검증만 했다.
실제 사용자 인증/모델 호출/주문/NAS 재시작은 수행하지 않았다.
Docker Hub `bluewind201/stock-quant:3.19.2` / `latest` 게시 및 pull 확인 완료.
두 태그의 amd64/arm64 이미지 digest:
`sha256:a9060f2f8e5b24719e7b42b7bb865789593590f3d0b0ec12e3c2aea47b8f628e`.
고정 태그를 쓰면 `.env`의 `STOCK_IMAGE=bluewind201/stock-quant:3.19.2`로 변경한다.

```sh
docker compose pull stock
docker compose up -d stock
```

## 이전 릴리스 3.19.1 · 2026-10-31

[유리 대시보드·기간별 AI 선정·로그인 개선](GLASS-DASHBOARD-2026-10-31.md).
인증 후 과거 훈련 자료를 읽지 않고, AI 연결 확인은 화면 응답을 막지 않도록 분리했다.
세 기간의 AI 선정 종목·매수 대기·보유와 자산 비중 그래프를 표시한다.
세 기간 동시 운용은 투자 전략 `portfolio`, 기간 설정 `balanced`(균형형)를 사용한다.
기존 운용 계좌의 전략·보유·설정은 자동 변경하지 않았다.

Windows 345개, amd64 Linux 341개 테스트 통과(Windows 전용 4개 제외).
실제 전체 페이지 스크립트로 로그인·PC·모바일·차트를 검증했으며 외부 통신 차단 컨테이너 검사도 통과했다.
arm64는 빌드 검증만 했다. NAS 로그인 속도는 직접 측정하지 않았다.
Docker Hub `bluewind201/stock-quant:3.19.1` / `latest` 게시 및 pull 확인 완료.
두 태그의 amd64/arm64 이미지 digest:
`sha256:d4b91c7ab9b7dfaaba01b5c8b55442fa8c794687393d2b43c84b0fc9ee672158`.

NAS는 직접 변경하지 않았다. 고정 태그를 쓰면 `.env`에서
`STOCK_IMAGE=bluewind201/stock-quant:3.19.1`으로 변경하고 프로젝트 폴더에서 실행한다.

```sh
docker compose pull stock
docker compose up -d stock
```

## 이전 릴리스 3.19.0 · 2026-10-31

[대시보드·당일 매도 검증·모델 선택 변경 내역](RESEARCH-DASHBOARD-2026-10-29.md).
단기·중기·장기별 보유와 실현 손익, 캔들·이동평균, 매수/반대 근거, 낙폭과 매도 이유를 표시한다.
Telegram 매수·매도·마감 알림에도 기간별 보유와 손익을 반영했다.
`portfolio`에서 진입 이전 완료 일봉을 근거로 새 포지션을 즉시 재매도하던 경로를 수정했다.
당일 AI 매도는 진입 후 발표된 해당 종목의 뉴스 근거 ID가 필요하다. 보호 손절은 계속 적용한다.
기존 AI/ORB/TOP5 전략은 자동 전환되지 않으며 화면에 기존 전략 경고를 표시한다.

GPT 6.1 Sol/Terra, GPT 6 Astra/Luna와 Claude Opus/Sonnet/Haiku 5.5, Fable 5.1을 선택할 수 있다.
기존 모델 선택은 유지한다. Fable은 별도 사용 크레딧 비용 확인이 필요하다. 계정별 모델 권한은 별도다.

Windows 344개, amd64 Linux 340개 테스트 통과(Windows 전용 4개 제외).
PC·모바일 화면 및 외부 통신을 차단한 컨테이너에서 로그인·계좌 설정·모델 목록·차트 자산을 검증했다.
Codex CLI 0.159.0의 설치와 실행 옵션도 확인했다. arm64는 빌드만 검증했다.
실제 AI 구독 호출·실계좌 주문·Telegram 발송은 하지 않았다. 수익 개선은 아직 검증하지 않았다.
Docker Hub `bluewind201/stock-quant:3.19.0`과 `latest` 게시 및 재다운로드 확인 완료.
두 태그는 linux/amd64·linux/arm64를 포함하며 동일 digest다:
`sha256:8c0f4f2870001a5d2f6f288e37af24e71126ee7c2763785adae517c5efc42989`.

NAS 운용 컨테이너는 직접 변경하지 않았다. 고정 태그를 사용하면 프로젝트의 `.env`에서
`STOCK_IMAGE=bluewind201/stock-quant:3.19.0`으로 변경한 뒤 프로젝트 폴더에서 실행한다.

```sh
docker compose pull stock
docker compose up -d stock
```

## 이전 릴리스 3.18.0 · 2026-10-26

[기간별 후보·투자 분리와 전문가 참고 자료](INVESTMENT-PERIODS-2026-10-26.md).
단기·중기·장기를 독립 탐색하고 신규 후보를 최대 5개씩 따로 배정한다. 후보의 중복 배정을 막고
기간별 종목 수·자금 한도를 적용한다. 화면에서 세 기간의 매수 계획·보유·보류 이유를 따로 본다.
단기 돌파 및 중기 3개월 모멘텀 조건을 추가했다. 테마/뉴스 일부 조회 실패가 전체 스캔을 막지 않게 했다.

기존 `portfolio` 계좌는 다음 스캔부터 새 후보 정책을 사용하며 보유의 기간은 바뀌지 않는다.
다른 전략은 자동 전환하지 않는다. 단기·중기·장기 투자 전략과 균형형 선택 여부를 확인한다.
관심종목은 운용 정지 후 설정에서 기간별로 입력한다. 실거래 시작이나 NAS 배포는 수행하지 않는다.

고정 버전 사용 시 `STOCK_IMAGE=bluewind201/stock-quant:3.18.0`으로 바꾼다.

Windows 333개, amd64 Linux 329개 테스트 통과(Windows 전용 4개 제외).
세 기간의 분리 체결·매도, 후보 중복 방지·슬롯·자료 장애·보유 기간 보존을 확인했다.
PC·모바일 화면과 외부 통신을 차단한 컨테이너의 기간별 설정 저장·실행 검증도 통과했다.
실제 구독 AI·실계좌 주문·Telegram 전송은 실행하지 않았다. arm64는 빌드만 검증했다.
Docker Hub `bluewind201/stock-quant:3.18.0` / `latest` 게시 및 3.18.0 재다운로드 완료.
두 태그의 linux/amd64·linux/arm64 포함과 동일 digest 확인:
`sha256:c8bac161b5eaee9f2f50cb88e85f89fb638ba038be1e73905300014f0110aa35`.
수익률 개선은 아직 검증하지 않았다.

```sh
docker compose pull stock
docker compose up -d stock
```

## 이전 릴리스 3.17.1 · 2026-10-26

[구독 AI 한도 초과 자동 전환](AI-FAILOVER-2026-10-26.md)을 추가했다.
주 AI가 한도 초과면 연결된 다른 제공업체로 한 번 전환한다. ChatGPT ↔ Claude 두 연결을
미리 완료해야 하며 주 AI 선택은 보존된다. 한도가 찬 제공업체는 15분 뒤 다시 확인한다.
두 AI 모두 사용할 수 없으면 해당 판단을 적용하지 않는다. 시간 초과·인증·형식 오류는 자동 전환하지 않는다.

Windows 324개, amd64 Linux 320개 테스트 통과(Windows 전용 4개 제외).
양방향 한도 전환·재시작 대기 유지·주 AI 복귀·양쪽 실패·오류 구분을 가짜 제공업체로 검증했다.
PC·모바일 화면 및 외부 통신 차단 컨테이너의 로그인·설정 저장·화면 제공도 통과했다.
실제 구독 AI 호출·주문·Telegram 발송·NAS 운용 시작은 하지 않았다. arm64는 빌드만 검증했다.
Docker Hub `bluewind201/stock-quant:3.17.1` / `latest` 게시 및 3.17.1 재다운로드 완료.
두 태그의 amd64·arm64와 동일 digest 확인:
`sha256:40632628ea0e458a6b9ff26962312f8132e16eb8181ecf6df1092795bf806a18`.

이미지를 고정했다면 `STOCK_IMAGE=bluewind201/stock-quant:3.17.1`로 변경한다.

```sh
docker compose pull stock
docker compose up -d stock
```

## 이전 릴리스 3.17.0 · 2026-10-25

[단기·중기·장기 전환과 초보자 화면](PORTFOLIO-2026-10-21.md).
새 계좌 기본은 `portfolio`, 균형형 단기20%·중기30%·장기30% 상한과 현금 최소20%다.
기간 집중형도 선택할 수 있다. 완료 일봉·AI 검토·위험 예산·추적 손절을 사용하며 당일 강제 청산은 없다.
과거 재생 훈련의 자동 워커와 검증 진행 화면을 제거하고 실제 시세 모의 운용은 유지한다.

기존 계좌는 자동 전환하지 않는다. 업데이트 후 **투자 운용 → 운용 정지 → 이 계좌의 설정 →
설정 바꾸기 → 단기·중기·장기 투자 → 기간 선택 → 저장 → 운용 시작** 순서다.
전환 시 보유·미체결·진행 중 AI 판단이 없어야 한다. 기존 보유를 업데이트만으로 청산하지 않는다.
기존 관심종목 전용 계좌는 설정의 고급 관리에서 접근한다.

`STOCK_IMAGE`를 고정한 경우 `bluewind201/stock-quant:3.17.0`으로 바꾸고 NAS에서 실행한다.

```sh
docker compose pull stock
docker compose up -d stock
```

Windows 테스트 306개, amd64 Linux 테스트 302개 통과(Windows 전용 4개 제외).
PC·모바일 화면, 기간 설정 저장, 투자 비중·대기 사유 표시를 검증했다.
외부 통신을 차단한 컨테이너에서 로그인·설정 저장·화면 제공·비루트 실행·인증 파일 제외를 확인했다.
arm64 이미지는 빌드했으며 실제 arm64 실행은 검증하지 않았다. 실제 NAS 최근 2주 로그는 미확보다.
2026-10-25 Docker Hub `bluewind201/stock-quant:3.17.0` / `latest` 게시 및 3.17.0 재다운로드 완료.
두 태그의 linux/amd64·linux/arm64 포함과 동일 digest를 확인했다:
`sha256:12d0d7ebbb541942751832694c3283ea02cf796cffd6100f4fe1c33f065c0345`.
NAS 배포와 운용 시작은 수행하지 않았다.
새 전략의 수익성은 아직 검증하지 않았다. 실제 주문·구독 AI 호출·외부 Telegram 전송은 테스트에서 하지 않는다.

## 이전 릴리스 3.16.0 · 2026-10-07

[AI 개선 기록·짧은 알림·금융 일정·판단 지연 개선](AI-IMPROVEMENT-2026-10-07.md)을 추가했다.
`self_improve`는 기본 켜짐이다. 판단과 체결 근거에 기반한 교훈을 다음 거래일부터 하나씩
평가하며 표본 부족은 제안으로 보관한다. 모델을 재학습하거나 주문 코드를 자동 편집하지 않는다.
숫자 자동 조정 `auto_tune`은 별도 기능으로 기본 꺼짐이고 시나리오2에서는 비활성화한다.
텔레그램의 판단·주문 접수·체결을 구분하고, 오늘/미국장 공식 일정과 관심 이슈를 짧게 보낸다.
자동 브리핑은 `NEWS_AUTO_REFRESH=true`와 텔레그램 설정이 있어야 동작한다.

Windows 294개, amd64 Linux 290개 테스트 통과(Windows 전용 4개 제외).
브라우저에서 개선 제안·금융 일정·시나리오2 설정을 확인했고, 외부 통신을 차단한 컨테이너에서
로그인·개선 기본값·금융 일정 API·설정 저장·비루트 실행·인증 파일 제외를 확인했다.
amd64·arm64 이미지 빌드 완료. arm64 실제 실행은 미검증이다.
Docker Hub `bluewind201/stock-quant:3.16.0` / `latest` 게시 및 3.16.0 재다운로드 확인 완료.
두 태그 digest: `sha256:c41dbad504f6f91e45e56e00d67d6ee7bc903bab2339ea2edf376723763b16b9`.
두 태그 모두 linux/amd64·linux/arm64 포함을 확인했다.
실제 주문·구독 AI 호출·외부 Telegram 전송·NAS 운용 시작은 수행하지 않았다.
실제 수익률과 NAS에서의 AI 지연 개선율은 아직 검증하지 않았다.

기존 Compose와 데이터 볼륨을 사용한다. `STOCK_IMAGE`를 고정해 사용한다면
`bluewind201/stock-quant:3.16.0`으로 변경한 뒤 NAS 프로젝트 폴더에서 실행한다.

```sh
docker compose pull stock
docker compose up -d stock
```

이미지 업데이트만으로 기존 계좌의 전략·모델·실거래 여부를 바꾸지 않는다.
대시보드에서 `AI 개선 기록`과 `오늘 금융 일정 · 주요 이슈`를 확인한다.

## 이전 릴리스 3.15.0 · 2026-10-06

[시나리오2 · TOP5 오전 바스켓](SCENARIO2-TOP5-2026-10-06.md)을 추가했다.
기본값: 07:30 후보 선정 → 09:05~09:15 TOP5 동일 비중 매수 → +4% 익절/-2% 손절 또는 11:50 정리.
종목당 15%(총 75%)이며 최대 20%까지 설정한다. AI 관망 의견이어도 선택한 바스켓 매수는 시도한다.
오후 매수·익일 보유·자동 튜닝을 끄고 진입 창 만료 시 미체결 매수를 취소한다.

Windows 278개, amd64 Linux 274개 테스트 통과(Windows 전용 4개 제외).
브라우저 전략 선택·설정 payload·순위/손익 표시와 컨테이너 로그인·설정 저장·비루트 실행·
인증 파일 제외를 확인했다. amd64·arm64 이미지 빌드를 완료했으며 arm64 실행은 검증하지 않았다.
Docker Hub `bluewind201/stock-quant:3.15.0` / `latest` 게시 및 3.15.0 재다운로드 확인 완료.
두 태그의 digest: `sha256:8ca3565c22ff1c871d0086c5f7632857db1da36c598e9b24f702e5802bf71540`.
linux/amd64·linux/arm64 포함. 실제 주문·구독 AI 호출·NAS 운용 시작은 수행하지 않았다.

기존 Compose와 볼륨을 사용한다. `.env`에서 `STOCK_IMAGE`가 고정되어 있으면
`bluewind201/stock-quant:3.15.0`으로 변경한 뒤 NAS 프로젝트 폴더에서 실행한다.

```sh
docker compose pull stock
docker compose up -d stock
```

앱에서 보유·미체결·진행 중 AI 판단이 없는 계좌를 **정지 → 설정 바꾸기 → 시나리오 2 · TOP5 오전 바스켓 → 저장 → 운용 시작**.
이미지 업데이트만으로 전략을 바꾸지 않는다. 청산되지 않는 예외 상황과 종목별 실제 체결 결과를
확인할 것. TOP5의 수익 개선 효과는 아직 검증하지 않았다.

## 이전 릴리스 3.14.0 · 2026-10-06

Docker Hub `bluewind201/stock-quant:3.14.0` / `latest` 게시 및 재다운로드 확인 완료.
두 태그 digest: `sha256:583447299b2a7edaff9e717daa79caa0f31423af82bd9c30388b575a958ed2b3`.
linux/amd64·linux/arm64 포함. Windows 262개, amd64 Linux 258개 테스트 통과(Windows 전용 4개 제외).
브라우저에서 전략 선택·설정 저장 입력을 확인했고, 컨테이너 로그인·새 전략 계좌 저장·비루트 실행·
인증 파일 제외를 확인했다. arm64는 빌드 확인이며 실제 NAS 실행은 사용자가 진행한다.

[추천 방식 구현·Claude 인수인계](IN-PLAY-IMPLEMENTATION-2026-10-06.md): 백그라운드 주도주 후보 갱신,
관측 시초 거래량 비교, AI 조건부 승인, 승인 안에서 자동 가격 신호 실행, 위험 예산 수량 제한을 추가했다.
이번 배포에서 실제 주문·사용자 구독 AI 호출·실거래 전환은 수행하지 않았다.

기존 Compose·DB·인증 볼륨을 사용한다. `.env`의 `STOCK_IMAGE`가 이전 버전으로 고정되어 있으면
`bluewind201/stock-quant:3.14.0`으로 변경한 뒤 NAS 프로젝트 폴더에서 실행한다.

```sh
docker compose pull stock
docker compose up -d stock
```

앱에서 **단타 → 운용 정지 → 설정 바꾸기 → AI 승인·당일 주도주 ORB (추천) → 저장 → 운용 시작**.
이전 계좌의 전략 선택은 유지하므로 이미지 업데이트만으로 새 전략이 켜지지는 않는다.
기본 손실 예산 0.25%, 승인 20분, 별도 승인 검토 하루 16회이며 화면에서 변경할 수 있다.
동시간 거래량 이력은 실제 관측으로 쌓이며 부족한 동안에는 당일 봉 거래량 확장으로 대체했다고 명시한다.
익일 보유 예외는 기존 EOD 정책으로 유지한다. 수익 개선 효과는 아직 검증하지 않았다.

## 이전 릴리스 3.13.0 · 2026-09-30

Docker Hub `bluewind201/stock-quant:3.13.0` / `latest` 게시 및 재다운로드 확인 완료.
두 태그 digest: `sha256:99c7885eb06988d790563191614bcd56ef9dbe149ab0964765ecceb5785e7763`.
linux/amd64·linux/arm64 포함. Windows 252개, amd64 Linux 248개 테스트 통과(Windows 전용 4개 제외).
브라우저 스크립트·전략 선택, 컨테이너 기동·비루트 사용자·인증 파일 제외도 확인했다.
arm64는 빌드 확인이며 실제 NAS 실행·수익률은 검증하지 않았다.

[9/30 실패 분석과 변경 상세](FAILURE-REVIEW-2026-09-30.md): 계획/실제 체결 성과 구분,
봉별 VWAP, 모든 보유 종목 차트 전달, 지수 대체 수집, 오래된 급등 기준 초기화,
Claude 대용량 입력 전달 개선, 선택 가능한 단타 전략 3개를 추가했다.
기존 계좌는 `AI 종합 판단`을 유지한다. 정지 → 설정 변경 → 운용 전략에서 새 방식을 선택한다.
기존 Compose·DB·인증 볼륨은 유지하며 실거래 전환을 자동 실행하지 않는다.

NAS 프로젝트 폴더에서 실행한다. `.env`의 `STOCK_IMAGE`가 예전 버전에 고정되어 있으면
`bluewind201/stock-quant:3.13.0`으로 변경한다.

```sh
docker compose pull stock
docker compose up -d stock
```

ChatGPT 구독 로그인은 NAS 프로젝트 폴더에서:

```sh
docker compose exec stock codex -c 'cli_auth_credentials_store="file"' -c 'forced_login_method="chatgpt"' login --device-auth
docker compose exec stock codex -c 'cli_auth_credentials_store="file"' login status
```

컨테이너 내부 터미널에서는 앞의 `docker compose exec stock`을 뺀다.
표시된 URL과 코드를 PC 브라우저에서 사용한다. 필요한 경우 ChatGPT 보안 설정의 device-code login을 허용한다.
Codex 인증은 Compose의 전용 볼륨에 저장된다. 실제 모델 권한·구독 한도는 NAS 연결 확인에서 점검한다.

## 이전 릴리스 3.12.0 · 2026-09-28

Docker Hub `bluewind201/stock-quant:3.12.0` / `latest` 게시 및 재다운로드 확인 완료.
두 태그 digest: `sha256:b3f303d12214fcbcd001208f1ea32669f1b4ba4c010876e2360f8440169f1826`.
amd64·arm64 이미지가 포함돼 있습니다. amd64 Linux 238개 통과, Windows 전용 4개 제외.
Windows 242개 통과, 브라우저 스크립트/모델 선택, 컨테이너 기동, 인증 파일·거래 DB 제외 확인.
arm64는 빌드만 확인했으며 실제 NAS 실행은 사용자가 진행합니다.

[변경 상세·Claude 인수인계](EXECUTION-REVIEW-2026-09-28.md): 숫자로 된 매수 상한,
완료 분봉·거래량·시초가, 뉴스 관련성/중복 처리, 09:15·12:00 재평가,
미국장 날짜 처리, 국내 지수·금리·환율 자료, Sol/Opus 5.5 선택을 추가했습니다.

3.11.0 Compose를 사용 중이면 그대로 사용할 수 있습니다. `.env`에 `STOCK_IMAGE`가 과거 버전으로
고정돼 있으면 `bluewind201/stock-quant:3.12.0`으로 변경하세요. 기존 볼륨과 인증은 유지합니다.

```sh
docker compose pull stock
docker compose up -d stock
```

설정 → AI 판단 모델에서 GPT-6 Sol 또는 Claude Opus 5.5를 선택합니다. 기존 Astra 선택은 유지됩니다.
인증 확인은 모델 사용 권한·구독 한도까지 보장하지 않으며 실제 NAS 호출 결과를 확인해야 합니다.
이번 배포에서 실제 주문이나 실거래 전환은 실행하지 않았습니다.

기존 계좌는 첫 실행 시 5% 목표 하한을 1%로 낮추고 복기 자동 튜닝을 끕니다.
다른 값으로 설정한 목표 하한과 기존 보유의 익절·손절 가격은 유지합니다.
마감/익일 보유 판단은 계속하며 과거 훈련 재생 worker는 기본 꺼짐입니다.

## 이전 릴리스 3.11.0 · 2026-09-21

Docker Hub `bluewind201/stock-quant:3.11.0` / `latest`에 Claude와 ChatGPT GPT-6 Astra 전환,
마감 판단 재시도 및 익일 보유 지침 개선을 게시했습니다.
두 태그 digest: `sha256:ac7d3d546c6a232464969c9e8b47c638dbe62a4db53bd2756cbc89b1053af685`.
amd64는 Linux 테스트 225개 통과, Windows 전용 4개 제외 및 실행 확인 완료. arm64는 빌드만 확인했습니다.

**이번 업데이트는 새 docker-compose.yml도 필요합니다.** Codex 전용 인증 볼륨이 추가됐습니다.
기존 NAS 프로젝트 폴더의 Compose 파일을 갱신하고 `.env`와 기존 볼륨을 유지하세요.
이미지 고정 설정이 있다면 `STOCK_IMAGE=bluewind201/stock-quant:3.11.0`으로 갱신합니다.

```sh
docker compose pull stock
docker compose up -d stock
docker compose exec stock codex -c 'cli_auth_credentials_store="file"' login --device-auth
```

표시된 공식 페이지에서 본인 ChatGPT 계정으로 로그인한 뒤 웹 설정의 AI 판단 제공자에서 Astra를 선택합니다.
Claude를 계속 쓰면 새 로그인은 필요 없습니다. 선택은 모든 계좌의 판단기에 공통 적용됩니다.
연결 성공은 사용자 계정의 모델 권한/구독 한도에 따라 달라집니다.
[상세 연결 및 검토 문서](DUAL-AI-2026-09-21.md)를 참고하세요. 실제 NAS 업데이트·구독 로그인은 아직 수행하지 않았습니다.

## 이전 릴리스 3.10.0

NAS에서는 소스를 빌드하지 않고 Docker Hub의 `bluewind201/stock-quant:latest`를 내려받습니다. 고정 버전은 `bluewind201/stock-quant:3.10.0`입니다. Linux amd64·arm64 이미지를 제공하며 Docker가 NAS CPU에 맞게 선택합니다. ARM 32비트는 지원하지 않습니다.

2026-09-17에 [Docker Hub 공개 저장소](https://hub.docker.com/r/bluewind201/stock-quant)에 `3.10.0`과 `latest`를 게시하고 다운로드를 확인했습니다. amd64는 컨테이너 테스트 217개 통과(Windows 전용 4개 제외), HTTP health 및 Claude 실행 확인을 완료했습니다. arm64는 빌드·매니페스트 확인만 수행했으며 실제 실행은 검증하지 않았습니다. 실제 NAS 업데이트는 사용자가 진행합니다.

두 태그의 멀티플랫폼 digest: `sha256:5e81e61d9e06dd7919e7fd6cf588efa8d73f501e5b2a521e7ff9b6907a3ffad7`.

## 3.10.0 업데이트

07:30 KST 이전 장전 판단 우회 차단, 테마 TOP 5 선정/표시, 차트 입력, 최신 시세 체결 검증,
매수·청산 재시도, 익일 반등용 마감 근거 강화를 포함합니다.
[수익성 개선 설계](PROFIT-RESEARCH.md)의 새 전략/수치 예시는 아직 활성화된 주문 로직이 아닙니다.

기존 NAS 프로젝트 폴더에서 실행합니다. `.env`에 `STOCK_IMAGE`가 구버전으로 고정돼 있다면
`bluewind201/stock-quant:3.10.0`으로 바꾼 뒤 진행합니다. 설정하지 않았다면 `latest`를 사용합니다.

```sh
docker compose pull stock
docker compose up -d stock
docker compose ps
```

기존 프로젝트 이름과 볼륨을 유지합니다. 데이터를 지우는 `down -v`는 실행하지 않습니다.
업데이트 후 계좌 상태와 장전 검토 시각을 확인합니다. Docker Hub 게시가 NAS 자동 업데이트를 의미하지는 않습니다.

## 설치 — Compose 1개 + .env 1개

NAS에 새 프로젝트 폴더를 만들고 **파일 두 개**만 넣습니다. 소스 코드나 Python 설치는 필요 없습니다.

1. 저장소의 `docker-compose.yml` 을 그대로 복사합니다. **수정하지 않습니다.**
2. `.env.example` 을 같은 폴더에 **`.env`** 라는 이름으로 복사하고 값을 채웁니다.

```sh
ADMIN_PASSWORD=화면로그인비밀번호
SESSION_SECRET=            # python -c "import secrets; print(secrets.token_hex(32))"
STOCK_BIND=0.0.0.0:8430    # NAS 는 반드시 0.0.0.0. 127.0.0.1 이면 LAN 에서 안 열립니다
TOSS_CLIENT_ID=
TOSS_CLIENT_SECRET=
```

`ADMIN_PASSWORD` 는 이 프로그램 화면의 로그인 비밀번호이며 토스 비밀번호가 아닙니다.

3. 토스 WTS 의 Open API 허용 IP 에 **NAS 가 쓰는 외부 공인 IP** 를 등록합니다. 시세 수집에
   토스 키가 필요합니다. AI 판단에는 Anthropic API 키를 쓰지 않고 구독 토큰만 씁니다.
4. Container Manager 의 **프로젝트 → 생성** 에서 그 폴더를 지정해 시작합니다.
   SSH 를 쓴다면 프로젝트 폴더에서:

```sh
docker compose pull
docker compose up -d
```

`http://NAS_IP:8430` 에 접속해 `ADMIN_PASSWORD` 로 로그인합니다.

### 파일 이름이 반드시 `.env` 여야 하는 이유

Docker Compose 는 프로젝트 폴더의 **`.env` 만** 자동으로 읽습니다. `.env.stock` 같은 다른
이름은 `--env-file` 로 직접 지정해야 하는데, **Container Manager 에는 그 옵션을 줄 방법이
없습니다.** 이름이 다르면 값이 전혀 전달되지 않고, 화면 로그인이 이렇게 실패합니다.

```
서버 ADMIN_PASSWORD를 설정하고 다시 실행하세요.   (HTTP 503)
```

`.env` 를 고쳤으면 컨테이너를 **다시 만들어야** 반영됩니다. 환경변수는 컨테이너를 만들 때
고정되므로 재시작만으로는 바뀌지 않습니다.

```sh
docker compose up -d --force-recreate
docker compose exec -T stock python -c "import os;print('설정됨' if os.getenv('ADMIN_PASSWORD') else '비어있음')"
```

값이 제대로 들어갔는지는 로그인 전에 이걸로 확인할 수 있습니다.

```sh
curl -s http://127.0.0.1:8430/api/health
# {"ok":true,...,"password_configured":true}  <- true 여야 로그인이 됩니다
```

## Claude 구독 연결 — 최초 한 번, NAS 접속 없이

판단은 **Claude 구독**으로 합니다. Anthropic API 키가 아니라 Pro/Max 구독 토큰이며,
API 과금이 발생하지 않습니다. NAS 안에서 로그인할 필요가 없습니다.

**PC에서** Claude Code CLI로 한 번만 실행합니다.

```sh
claude setup-token
```

브라우저가 열리고 승인하면 `sk-ant-oat...` 로 시작하는 **1년짜리 토큰**이 터미널에 출력됩니다.
이 값을 NAS 프로젝트 폴더의 `.env` 에 넣습니다.

```sh
CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...
```

그리고 컨테이너를 다시 만듭니다. 환경변수는 컨테이너 생성 시 고정되므로 재시작만으로는 반영되지 않습니다.

```sh
docker compose up -d --force-recreate
```

화면에서 **AI 연결 확인**을 누르면 설치·토큰 상태를 볼 수 있습니다. 이 확인은 모델을
호출하지 않으므로 사용량을 쓰지 않습니다. 확인 후 가상 계좌를 만들고 **AI 모의 운용 시작**을
누릅니다. 시세·뉴스·차트 조건을 충족해야 새 판단과 가상 매수가 일어납니다. 휴장 중에는
다음 정규장 시세를 기다립니다.

토큰이 없거나 형식이 틀리면 판단을 시작하지 않고 그 사유를 화면에 표시합니다.
**API 키로 자동 대체하지 않습니다.** `ANTHROPIC_API_KEY` 는 자식 프로세스로 전달하지
않는데, `-p` 모드에서 그 값이 있으면 구독 대신 API 과금으로 넘어가기 때문입니다.

### 사용량을 아끼려면

기본값은 `sonnet` 모델에 사고량 `low` 이고, 도구 사용을 차단하고 한 번의 응답만 받습니다.
판단 품질을 올리고 싶으면 `.env` 에서 바꿉니다. 사용량은 그만큼 늘어납니다.

```sh
SQT_AI_MODEL=opus        # 비우면 sonnet
SQT_AI_EFFORT=medium     # 비우면 low
SQT_AI_DAILY_BUDGET=64   # 하루 판단 호출 횟수 상한
```

공식 인증 안내: https://code.claude.com/docs/en/authentication

## 업데이트와 데이터

```sh
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail 100 stock
```

가상 자금·포지션·체결·판단 기록은 `stock-data` Docker named volume에 저장합니다. 컨테이너 재생성이나 이미지 교체 후에도 유지합니다. **`docker compose down -v` 또는 볼륨 삭제는 거래 기록을 지우므로 업데이트에 사용하지 않습니다.** AI 인증은 `.env` 의 토큰이라 볼륨이 필요 없습니다. Windows 테스트 DB는 NAS로 자동 복사하지 않습니다.

NAS 전용 Compose의 `PAPER_RESUME_ON_RESTART: "true"`는 통화별로 직전에 실행 중이던 가상 계좌 한 개씩 다시 감시합니다. 정지한 계좌는 시작하지 않습니다. 이전 AI 응답은 폐기하고 새 시세를 확인합니다. 재시작 후 수동 시작을 원하면 `"false"`로 바꾸세요. 전원이 꺼져 있던 동안의 가격 변동을 소급 체결하지 않습니다.

포트 8430은 NAS의 LAN 접속용입니다. 이미 사용 중이면 `"8431:8080"`처럼 왼쪽 숫자를 바꿉니다. HTTPS 역방향 프록시를 구성한 경우 `COOKIE_SECURE: "true"`로 바꿀 수 있습니다. 데이터 경로를 일반 공유 폴더의 bind mount로 변경하면 UID/GID `10001:10001` 쓰기 권한이 필요하므로 기본 named volume 사용을 권장합니다.

## 문제 확인

- 화면 로그인이 503이면 Compose의 `ADMIN_PASSWORD`가 비어 있는지 확인하고 컨테이너를 재생성합니다.
- 토스 인증·403 오류는 키와 NAS 공인 IP 허용을 확인합니다. 실행 위치가 PC에서 NAS로 바뀌면 공인 IP가 다를 수 있습니다.
- GPT 인증 오류는 위 `login status`로 확인하고 장치 코드 로그인을 다시 진행합니다. 구독 사용량을 초과하면 새 AI 판단을 중단하며 API 과금으로 전환하지 않습니다.
- 시세 지연·휴장·잔량 부족에서는 즉시 가상 체결하지 않습니다. 신규 매수 일시정지는 손절 감시를 유지하지만 전체 운용 정지는 감시도 멈춥니다.

기본 설정은 **모의 운용**입니다. 2.1.0의 실거래 주문 경로는 `ENABLE_LIVE_TRADING=false`일 때 차단됩니다. 실거래는 검증 후 [LIVE-TRADING.md](LIVE-TRADING.md)를 따라 계좌·자금 한도를 설정하고 직접 시작하세요. `PAPER_RESUME_ON_RESTART`는 통화별 모의 계좌에만 적용되며 실거래는 재시작 후 항상 직접 시작해야 합니다. 기존 Compose에 새 실거래 변수가 없어도 기본값은 비활성화입니다. 실제 수행한 검증은 [STOCK-VALIDATION.md](STOCK-VALIDATION.md)에 기록합니다.
