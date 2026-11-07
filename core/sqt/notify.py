"""텔레그램 알림. 무엇을 샀고 팔았는지, 무슨 뉴스를 보고 그렇게 판단했는지 사람에게 보낸다.

설정 (.env)
    TELEGRAM_BOT_TOKEN   @BotFather 가 준 토큰
    TELEGRAM_CHAT_ID     받을 채팅의 id (개인이면 본인 id, 그룹이면 음수)
    둘 중 하나라도 비어 있으면 조용히 아무것도 보내지 않는다. 알림이 매매를 멈추면 안 된다.

원칙
    - 전송은 별도 스레드에서. 텔레그램이 느려도 감시 루프가 기다리지 않는다.
    - 실패해도 예외를 밖으로 던지지 않는다. 로그만 남긴다.
    - 시각은 전부 KST 로 적는다. DB 에는 UTC 로 저장하지만 사람이 읽는 건 KST 다.
    - 토큰이 로그·오류 메시지에 섞이지 않게 한다.
"""
from __future__ import annotations

import html
import json
import logging
import os
import queue
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger('bqt.notify')
KST = timezone(timedelta(hours=9))
API = 'https://api.telegram.org/bot{token}/{method}'

_queue: queue.Queue = queue.Queue(maxsize=200)
_worker_started = False
_worker_lock = threading.Lock()


def configured() -> bool:
    return bool(os.getenv('TELEGRAM_BOT_TOKEN', '').strip() and os.getenv('TELEGRAM_CHAT_ID', '').strip())


def kst(value: Any = None) -> str:
    """ISO 문자열이나 datetime 을 'MM-DD HH:MM' KST 로."""
    if value is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except ValueError:
            return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime('%m-%d %H:%M')


def esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ''), quote=False)


def won(value: float) -> str:
    return f'{value:+,.0f}원' if value < 0 or value > 0 else '0원'


# 운영 점검 화면용 전송 결과. 토큰·본문은 담지 않는다.
_stats = {'last_ok_at': None, 'last_error': '', 'last_error_at': None, 'sent': 0, 'failed': 0}


def _record(ok: bool, error: str = '') -> None:
    now = datetime.now(timezone.utc).isoformat()
    if ok:
        _stats.update(last_ok_at=now, sent=_stats['sent'] + 1)
    else:
        _stats.update(last_error=error[:160], last_error_at=now, failed=_stats['failed'] + 1)


def stats() -> dict:
    return dict(_stats, configured=configured(), level=level())


def _post(method: str, payload: dict) -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(API.format(token=token, method=method), data=body,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
            if not data.get('ok'):
                log.warning('텔레그램 거절: %s', str(data.get('description', ''))[:120])
                _record(False, '텔레그램 거절: ' + str(data.get('description', ''))[:120])
            else:
                _record(True)
            return bool(data.get('ok'))
    except urllib.error.HTTPError as exc:
        # 본문에 토큰이 들어 있지 않지만, 혹시 몰라 상태코드만 남긴다.
        log.warning('텔레그램 HTTP %s', exc.code)
        _record(False, f'HTTP {exc.code} · 401/404 는 토큰, 400/403 은 chat id·봇 차단 확인')
    except Exception as exc:  # noqa: BLE001
        log.warning('텔레그램 전송 실패: %s', type(exc).__name__)
        _record(False, f'연결 실패 ({type(exc).__name__}) · NAS 인터넷 연결 확인')
    return False


def _worker() -> None:
    while True:
        text = _queue.get()
        try:
            # 텔레그램 메시지 상한 4096자. 넘치면 잘라서 여러 번.
            for i in range(0, len(text), 3800):
                _post('sendMessage', {'chat_id': os.getenv('TELEGRAM_CHAT_ID', '').strip(),
                                      'text': text[i:i + 3800], 'parse_mode': 'HTML',
                                      'disable_web_page_preview': True})
        finally:
            _queue.task_done()


def send(text: str) -> bool:
    """큐에 넣고 바로 돌아온다. 설정이 없으면 False."""
    global _worker_started
    if not configured() or not text:
        return False
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True, name='telegram-notify').start()
            _worker_started = True
    try:
        _queue.put_nowait(text)
        return True
    except queue.Full:
        log.warning('텔레그램 큐가 가득 찼습니다. 메시지를 버립니다.')
        return False


# ── 알림 범위 ───────────────────────────────────────────────────────────────
# trades : 매수·매도(익절·손절) 체결, 하루 결산, 돈과 직결된 경고만 (기본)
# all    : 위 + 장전 계획·장중 판단·마감 판단·복기·금융 일정 (예전 방식)
# 판단 근거와 상세 기록은 웹 화면에 항상 남는다.
LEVELS = ('trades', 'all')
_level_override: str | None = None


def set_level(value: str | None) -> None:
    global _level_override
    _level_override = value if value in LEVELS else None


def level() -> str:
    if _level_override:
        return _level_override
    value = os.getenv('TELEGRAM_LEVEL', 'trades').strip().lower()
    return value if value in LEVELS else 'trades'


def allowed(kind: str) -> bool:
    """kind: trade(체결·결산) / alert(주문 결과 불명확 등 돈과 직결) / detail(판단·계획·일정)."""
    return level() == 'all' or kind in ('trade', 'alert')


def post(text: str, kind: str = 'detail') -> bool:
    """알림 범위를 확인하고 보낸다. 범위 밖이면 보내지 않고 False."""
    if not allowed(kind):
        return False
    return send(text)


_once: dict[str, float] = {}


def post_once(key: str, text: str, kind: str = 'alert') -> bool:
    """같은 key 는 한 번만 보낸다(키에 날짜를 넣어 하루 한 번). 운영 장애가 조용히 묻히지 않게 하되 반복하지 않는다."""
    if key in _once:
        return False
    if len(_once) > 500:
        for old in sorted(_once, key=_once.get)[:250]:
            _once.pop(old, None)
    _once[key] = datetime.now(timezone.utc).timestamp()
    return post(text, kind)


def send_now(text: str) -> bool:
    """동기 전송. 설정 확인 버튼처럼 결과를 바로 알아야 할 때만."""
    if not configured():
        return False
    return _post('sendMessage', {'chat_id': os.getenv('TELEGRAM_CHAT_ID', '').strip(),
                                 'text': text[:3800], 'parse_mode': 'HTML',
                                 'disable_web_page_preview': True})


# ── 단타 서비스가 쓰는 메시지 틀 ─────────────────────────────────────────────
from .notify_templates import (plan_message, buy_message, sell_message, order_message, event_message, eod_message, review_message, summary_message, summary_kind, financial_message, error_message)
