"""운영 점검. '회의가 왜 안 열렸나, 알림이 왜 안 왔나'를 사람이 읽는 말로 정리한다.

계좌 화면 자료(DayTradeService.view)와 연결 상태만 읽는다. 주문·AI 호출은 하지 않는다.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NO_MEETING_HINTS = ('후보 없음', '진입 창', '생략', '스캔 실패', '판단 실패', '판단 포기')


def _t(value):
    try:
        t = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _hm(value) -> str:
    t = _t(value)
    return t.astimezone(KST).strftime('%H:%M') if t else ''


def _today(state, now) -> list[dict]:
    day = now.astimezone(KST).date().isoformat()
    return [r for r in state.get('log') or [] if (_t(r.get('at')) or now).astimezone(KST).date().isoformat() == day]


def meeting(state: dict | None, now: datetime) -> dict:
    """오늘 장전 회의(장전 판단)의 상태와 이유."""
    if not state:
        return {'status': 'bad', 'detail': '운용 계좌가 없어 회의가 열리지 않습니다. 투자 운용에서 계좌를 만드세요.'}
    hh = now.astimezone(KST).strftime('%H:%M')
    errors = state.get('errors') or {}
    ai = state.get('ai') or {}
    day = now.astimezone(KST).date().isoformat()
    history = [h for h in ai.get('history') or []
               if '장전' in str(h.get('kind', '')) and (_t(h.get('at')) or now).astimezone(KST).date().isoformat() == day]
    plan = state.get('plan')
    scan_at = (state.get('config') or {}).get('scan_at', '07:30')
    if state.get('phase') == 'holiday':
        return {'status': 'ok', 'detail': '오늘은 휴장일이라 회의가 없습니다.'}
    if plan and state.get('day') == day:
        picks = len(plan.get('picks') or [])
        when = _hm((state.get('stamps') or {}).get('plan')) or (_hm(history[-1]['at']) if history else '')
        return {'status': 'ok', 'detail': f'{when} 장전 회의 완료 · 매수 계획 {picks}건'.strip()}
    if ai.get('pending'):
        return {'status': 'running', 'detail': f'{_hm(ai.get("started_at"))} 시작한 장전 회의가 진행 중입니다.'}
    if errors.get('scan_giveup'):
        return {'status': 'bad', 'detail': errors['scan_giveup']}
    if history and not history[-1].get('ok'):
        return {'status': 'bad', 'detail': f'{_hm(history[-1]["at"])} 장전 회의 실패 · {history[-1].get("error") or "원인 미기록"}'}
    if errors.get('scan'):
        return {'status': 'bad', 'detail': errors['scan']}
    if state.get('status') != 'running':
        return {'status': 'bad', 'detail': '운용이 정지되어 있어 회의가 열리지 않습니다. 투자 운용에서 "운용 시작"을 누르세요.'}
    if hh < max('07:30', scan_at):
        return {'status': 'ok', 'detail': f'오늘 {max("07:30", scan_at)}에 장전 회의가 열립니다.'}
    for row in reversed(_today(state, now)):
        if any(h in row.get('message', '') for h in NO_MEETING_HINTS):
            return {'status': 'warn', 'detail': f'{_hm(row["at"])} {row["message"]}'}
    return {'status': 'warn', 'detail': '예정 시각이 지났지만 장전 회의 기록이 없습니다. 아래 활동 기록을 확인하세요.'}


def checks(account: dict | None, *, ai_status: dict, toss_configured: bool, session: bool,
           telegram: dict, storage: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    rows = []

    def add(id, label, status, detail, action=''):
        rows.append({'id': id, 'label': label, 'status': status, 'detail': detail, 'action': action})

    if not account:
        add('account', '운용 계좌', 'bad', '계좌가 없습니다. 투자 운용에서 모의 계좌를 만드세요.', 'daytrade')
    else:
        cfg = account.get('config') or {}
        running = account.get('status') == 'running'
        add('account', '운용 상태', 'ok' if running else 'bad',
            ('모의' if account.get('mode') != 'live' else '실거래') + (' 운용 중' if running else ' 운용 정지 · "운용 시작"을 눌러야 회의와 매매가 진행됩니다'),
            '' if running else 'daytrade')
        if cfg.get('strategy') != 'portfolio':
            add('strategy', '운용 방식', 'warn', '기존 당일 전략 계좌라 AI 회의실(역할별 회의)이 열리지 않습니다. '
                '정지 후 "단기·중기·장기 투자" 방식으로 바꾸면 회의가 열립니다.', 'daytrade')
        elif cfg.get('ai_office') is False:
            add('strategy', '운용 방식', 'warn', 'AI 회의실이 꺼져 있어 단일 판단만 합니다. 계좌 설정에서 켤 수 있습니다.', 'daytrade')
        else:
            markets = cfg.get('markets') or ['KR']
            m_label = '국내+미국' if {'KR', 'US'} <= set(markets) else ('미국 전용' if markets == ['US'] else '국내 전용')
            add('strategy', '운용 방식', 'ok', f'단기·중기·장기 투자 ({m_label}) · AI 회의실 사용')
        poll = _t(account.get('last_poll_at'))
        if running and session and account.get('phase') != 'holiday':
            stale = not poll or (now - poll).total_seconds() > 180
            add('worker', '감시 프로그램', 'bad' if stale else 'ok',
                f'마지막 확인 {_hm(account.get("last_poll_at")) or "기록 없음"}' + (' · 3분 넘게 멈춰 있습니다. 컨테이너를 재시작하세요.' if stale else ' · 정상 동작'))
        m = meeting(account, now)
        add('meeting', '오늘 장전 회의', m['status'], m['detail'], 'office' if m['status'] in ('ok', 'running') else '')
        reviewed = account.get('portfolio_checks') or []
        if cfg.get('strategy') == 'portfolio' and reviewed:
            passed = sum(1 for r in reviewed if r.get('eligible'))
            reason, count = Counter(str(r.get('reason') or '사유 없음') for r in reviewed).most_common(1)[0]
            if passed:
                add('candidates', '매수 후보', 'ok', f'마지막 스캔 {len(reviewed)}종 검토 · 후보 {passed}종')
            elif count >= len(reviewed) * .8:
                # 전 종목이 같은 이유로 떨어지면 시장 판단이 아니라 자료·프로그램 문제일 가능성이 크다.
                add('candidates', '매수 후보', 'bad', f'{len(reviewed)}종 중 {count}종이 같은 이유로 탈락 · {reason}')
            else:
                add('candidates', '매수 후보', 'warn', f'{len(reviewed)}종 검토 · 후보 0종 · 가장 많은 사유: {reason}')
    authed = bool(ai_status.get('installed') and ai_status.get('authenticated'))
    provider = {'codex': 'ChatGPT', 'claude': 'Claude'}.get(ai_status.get('provider'), ai_status.get('provider') or 'AI')
    add('ai', 'AI 로그인', 'ok' if authed else 'bad',
        f'{provider} ' + ('연결됨' if authed else '로그인 필요 · ' + str(ai_status.get('message') or '')[:160]),
        '' if authed else 'settings')
    weak = [k for k, v in (storage or {}).items() if not v.get('persistent')]
    if weak:
        add('auth_storage', '로그인 보관', 'warn', '업데이트하면 로그인이 사라질 수 있습니다: ' +
            ', '.join({'codex': 'ChatGPT', 'claude': 'Claude'}.get(k, k) for k in weak), 'settings')
    scan_error = (account or {}).get('errors', {}).get('scan') or (account or {}).get('errors', {}).get('quotes')
    add('toss', '토스 시세', 'bad' if not toss_configured or scan_error else 'ok',
        '토스 키가 없습니다 (.env)' if not toss_configured else scan_error or '키 설정됨 · 최근 조회 오류 없음',
        'settings' if not toss_configured or scan_error else '')
    if not telegram.get('configured'):
        add('telegram', '텔레그램', 'warn', '봇 토큰·chat id 가 없어 알림을 보내지 않습니다', 'settings')
    else:
        err, ok_at = telegram.get('last_error'), telegram.get('last_ok_at')
        failed_last = bool(err and (not ok_at or (telegram.get('last_error_at') or '') > ok_at))
        add('telegram', '텔레그램', 'bad' if failed_last else 'ok',
            (f'마지막 전송 실패 {_hm(telegram.get("last_error_at"))} · {err}' if failed_last else
             f'마지막 전송 {_hm(ok_at)}' if ok_at else '이번 실행 이후 보낸 알림 없음') +
            f' · 범위: {"매매 보고 + 경고 + 결산" if telegram.get("level") == "trades" else "상세"}',
            'settings' if failed_last else '')
    order = {'bad': 0, 'warn': 1, 'running': 2, 'ok': 3}
    worst = min((r['status'] for r in rows), key=order.get) if rows else 'ok'
    recent = [{'at': r.get('at'), 'message': r.get('message', '')} for r in ((account or {}).get('log') or [])[-15:]][::-1]
    history = [{'at': h.get('at'), 'kind': h.get('kind'), 'ok': h.get('ok'), 'error': h.get('error', ''),
                'seconds': h.get('seconds')} for h in ((account or {}).get('ai') or {}).get('history', [])[-6:]][::-1]
    return {'generated_at': now.isoformat(), 'overall': worst, 'checks': rows, 'recent_log': recent,
            'ai_history': history}
