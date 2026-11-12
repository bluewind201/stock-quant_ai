"""당일 단타 서비스. 하루를 시점으로 나누고, 판단할 거리가 있을 때만 AI 를 부른다.

하루 흐름 (모두 KST, 설정으로 바꾼다)
    scan_at       07:30  프로그램 스캔 - 테마·랭킹·수급·뉴스로 후보를 좁힌다. AI 없음.
    (바로 이어서)         ★ AI #1 장전 판단 - 후보 중 무엇을 얼마에, 익절·손절 폭
    entry_at      09:00  진입 창. 계획된 종목을 사고 익절·손절 괄호를 건다.
    장중                  감시만. 괄호가 알아서 청산한다. 이벤트가 있을 때만
                          ★ AI #2 뉴스   보유·후보 종목에 새 기사
                          ★ AI #3 급등   후보가 surge_trigger_pct 이상 움직임
    eod_review_at 15:00  ★ AI #4 마감 판단 - 남은 보유분 "내일 갈 것 같은가"
    force_close_at 15:20  hold_overnight 판정 외 전부 시장가 정리
    그 뒤                 휴식. AI 호출 0.

기존 PaperService 와 다른 점
    저쪽은 "15분마다 지금 살까?" 를 묻는다. 여기는 시점이 정해져 있고, 시점 사이에는
    프로그램이 괄호(익절·손절)만 지킨다. 한 번도 안 사던 문제의 원인이 이 차이였다.

이 파일이 하지 않는 것
    실계좌 주문. 지금은 가상 체결만 한다. 실계좌는 토스 OCO 조건주문으로 괄호를 걸어야
    하는데, 그 경로는 검증 뒤에 붙인다. 가상 체결은 호가에 슬리피지를 더해 채운다.

솔직하게
    급등·테마 추격은 두 번 백테스트했고 둘 다 무작위와 구분되지 않았다. 사용자가
    위험을 알고 선택한 방식이라 그대로 만들되, 모든 조건을 숫자로 노출한다.
"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import krnews, notify, overnight, scanner, news_quality, trade_setups
from . import portfolio
from .in_play import InPlayFeed
from . import learning, financial_brief
import time
from .daytrade_live import LiveExecution
from .calendar import calendar
from .models import Instrument, stamp, utcnow
from .paper_models import DayTradeConfig
from .subscription_ai import AIError, SubscriptionAI
from .toss_orders import TossOrders

log = logging.getLogger('bqt.daytrade')
KST = timezone(timedelta(hours=9))

# 마감 복기에서 AI 가 바꿀 수 있는 항목. (하한, 상한, 하루 최대 변동폭)
# 하루에 조금씩만 움직이게 한다 - 하루 실적은 표본이 아니라 잡음에 가깝다.
TUNABLE: dict[str, tuple[float, float, float]] = {
    'take_profit_min_pct': (1.0, 30.0, 2.0),
    'take_profit_max_pct': (1.0, 50.0, 3.0),
    'stop_loss_min_pct': (0.5, 10.0, 1.0),
    'stop_loss_max_pct': (0.5, 20.0, 1.5),
    'position_pct': (1.0, 30.0, 3.0),
    'max_positions': (1.0, 12.0, 1.0),
    'max_prev_change_pct': (5.0, 30.0, 5.0),
    'surge_trigger_pct': (1.0, 20.0, 2.0),
    'min_trading_amount_krw': (1e9, 5e10, 0.0),   # 변동폭은 현재값의 50% 로 따로 계산
}
TUNABLE_BOOL = ('require_flow', 'require_news', 'close_entry', 'news_trigger')

# 만든 뒤에는 바꿀 수 없는 것. 이걸 바꾸는 건 사실상 다른 계좌다.
FIXED_KEYS = ('mode', 'currency', 'initial_cash')


def _hhmm(now_utc: datetime) -> str:
    return now_utc.astimezone(KST).strftime('%H:%M')


def _day(now_utc: datetime) -> str:
    return now_utc.astimezone(KST).date().isoformat()


def _after(now_utc: datetime, hhmm: str) -> bool:
    return _hhmm(now_utc) >= hhmm


def _between(now_utc: datetime, start: str, end: str) -> bool:
    """start <= 지금 < end (KST HH:MM). 상한이 없으면 밤 11시에도 '마감 판단' 이 참이 된다."""
    return start <= _hhmm(now_utc) < end


def _minutes_since(now_utc: datetime, hhmm: str) -> float:
    local = now_utc.astimezone(KST)
    h, m = map(int, hhmm.split(':'))
    target = local.replace(hour=h, minute=m, second=0, microsecond=0)
    return (local - target).total_seconds() / 60.0


class DayTradeService:
    account_kind = 'daytrade_accounts'

    def __init__(self, store, broker, ai=None, live=None):
        self.store, self.broker, self.ai = store, broker, ai or SubscriptionAI()
        # 실거래 실행기. mode == 'live' 계좌에서만 쓴다. 모의 계좌는 아래 가상 체결을 쓴다.
        self.live = live or LiveExecution(store, TossOrders(broker))
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.pulse_lock = threading.Lock()
        self.in_play_feed = InPlayFeed(store, broker)

    # ── 생성·조회·제어 ───────────────────────────────────────────────────────
    def create(self, config: dict) -> dict:
        cfg = DayTradeConfig.model_validate(config).model_dump(mode='json')
        account_seq = ''
        if cfg['mode'] == 'live':
            account_seq = self.live.check_before_create(cfg['initial_cash'])
        with self.lock:
            # 모드당 계좌 하나. 이전 것은 정지·빈 상태여야 하고, 기록은 남긴 채 보관(archived) 한다.
            for old in self.store.all(self.account_kind):
                if old.get('archived') or old.get('mode', 'paper') != cfg['mode']:
                    continue
                if old['status'] == 'running':
                    raise ValueError('먼저 기존 계좌의 운용을 정지하세요.')
                if old['positions']:
                    raise ValueError('기존 계좌에 보유 종목이 있습니다. 전량 청산한 뒤 새 계좌를 만드세요.')
                if cfg['mode'] == 'live' and self.live.pending(old['id']):
                    raise ValueError('기존 실거래 계좌에 확인 중인 주문이 있습니다.')
                old['archived'] = True
                old['archived_at'] = utcnow().isoformat()
                self.store.put(self.account_kind, old['id'], old)
        state = {
            'id': uuid.uuid4().hex, 'mode': cfg['mode'], 'status': 'stopped', 'archived': False,
            'execution_v312': True,
            'account_seq': account_seq,
            'created_at': utcnow().isoformat(), 'config': cfg,
            'cash': cfg['initial_cash'], 'positions': {}, 'trades': [],
            'day': '', 'phase': 'idle', 'scan': None, 'plan': None, 'quotes': {},
            'ai': {'calls_day': '', 'calls_today': 0, 'pending': False, 'error': '',
                   'history': []},
            'events': {'seen_news': [], 'surge_ref': {}, 'surged': []},
            'log': [], 'errors': {}, 'realized_pnl': 0.0, 'last_poll_at': None,
            # 대시보드용. 자산 곡선(5분 간격), 일별 마감 요약, AI 가 마지막으로 검토한 자료
            'equity_curve': [], 'days': {}, 'ai_inputs': None,
            # 실제로 각 단계를 언제 했는지(예정 시각이 아니라). 재시도 기록. 복기에서 얻은 교훈·조정
            'stamps': {}, 'retry': {'attempts': 0, 'last': None}, 'sessions': None,
            'tuning': {'lessons': [], 'history': [], 'last_review': None, 'review_day': '', 'review_tries': 0},
        }
        self.store.put(self.account_kind, state['id'], state)
        return state

    @staticmethod
    def _upgrade(state: dict) -> dict:
        """이전 버전에서 만든 계좌에 새 필드를 채운다. 3.4 이전 계좌는 전부 모의였다."""
        state.setdefault('equity_curve', [])
        state.setdefault('days', {})
        state.setdefault('ai_inputs', None)
        state.setdefault('archived', False)
        state.setdefault('stamps', {})
        state.setdefault('retry', {'attempts': 0, 'last': None})
        state.setdefault('sessions', None)
        state.setdefault('tuning', {'lessons': [], 'history': [], 'last_review': None,
                                    'review_day': '', 'review_tries': 0, 'pending_snapshot': None})
        state['ai'].setdefault('anchor_calls_today', 0)
        state['ai'].setdefault('started_at', None)
        if state.get('mode') not in ('paper', 'live'):
            state['mode'] = 'paper'
        state['config'].setdefault('mode', state['mode'])
        state['config'].setdefault('strategy', 'ai')
        state['config'].setdefault('self_improve',True)
        state['config'].setdefault('portfolio_style','balanced')
        state['config'].setdefault('markets',['KR'])
        state['config'].setdefault('portfolio_core',['069500','229200'])
        state['config'].setdefault('us_core',['NVDA','MSFT','AAPL','GOOGL','AMZN','META','AVGO','QQQ','SPY'])
        state['config'].setdefault('portfolio_risk_pct',.5)
        state['config'].setdefault('portfolio_watchlists',{})
        state['config'].setdefault('us_watchlists',{})
        state['config'].setdefault('breakeven_lock',True)
        state['config'].setdefault('partial_tp',True)
        learning.ensure(state)
        state['config'].setdefault('risk_per_trade_pct', .25)
        state['config'].setdefault('approval_minutes', 20)
        state['config'].setdefault('max_price_reviews_per_day', 16)
        for key,value in (('basket_entry_at','09:05'),('basket_exit_at','11:50'),
                          ('basket_entry_minutes',10),('basket_tp_pct',4),('basket_sl_pct',2)):
            state['config'].setdefault(key,value)
        state['config'].setdefault('live_limit_buffer_bps', 30)
        state['config'].setdefault('live_fill_timeout_seconds', 180)
        if not state.get('execution_v312'):
            # Remove the old blanket +5% target floor, without changing open brackets.
            if state['config'].get('take_profit_min_pct') == 5:
                state['config']['take_profit_min_pct'] = 1
            state['config']['auto_tune'] = False
            state['execution_v312'] = True
        # Legacy midnight settings must not consume the next morning's plan.
        if state['config'].get('scan_at', '07:30') < '07:30':
            state['config']['scan_at'] = '07:30'
            state['errors']['schedule_migration'] = '장전 검토 시각을 한국시간 07:30으로 보정했습니다.'
        for key, value in (('close_entry', True), ('close_entry_max', 2), ('daily_review', True),
                           ('auto_tune', True), ('retry_minutes', 3), ('max_scan_attempts', 5),
                           ('session_open', '08:00'), ('session_close', '20:00')):
            state['config'].setdefault(key, value)
        return state

    def is_live(self, state: dict) -> bool:
        return state.get('mode') == 'live'

    def view(self, id: str) -> dict:
        state = self._upgrade(self.store.get(self.account_kind, id))
        state['equity'] = self.equity(state)
        state['unrealized'] = sum(self._unrealized(state, p) for p in state['positions'].values())
        from .reporting import summary
        state['reporting']=summary(state)
        if state.get('office_run_id'):
            try:
                office=self.store.get('office_runs',state['office_run_id'])
                if office.get('status')=='running' and (utcnow()-stamp(office['updated_at'])).total_seconds()>900:
                    office=dict(office,status='interrupted')
                state['office']=office
            except KeyError:pass
        if state['config'].get('strategy')=='portfolio':state['portfolio_buckets']=portfolio.bucket_status(self,state)
        now_v = utcnow()
        state['fx_usd_krw'] = portfolio.fx_rate(self, state, now_v)
        state['market_sessions'] = {
            'kr_open': portfolio.kr_tradable_window(now_v),
            'us_open': portfolio.us_session_open(now_v),
            'us_preopen': portfolio.us_preopen_or_open(now_v),
        }
        state.pop('account_seq', None)
        if self.is_live(state):
            state['orders'] = self.live.journal(id)
            state['pending_orders'] = self.live.pending(id)
        return state

    def current(self, mode: str) -> dict | None:
        """모드별로 보관되지 않은 계좌 하나. 없으면 None."""
        rows = [r for r in self.store.all(self.account_kind)
                if not r.get('archived') and r.get('mode', 'paper') == mode]
        rows.sort(key=lambda r: r.get('created_at', ''), reverse=True)
        return self.view(rows[0]['id']) if rows else None

    def update_config(self, id: str, payload: dict) -> dict:
        """운용 중에도 조건을 바꾼다. 자금·모드·통화는 못 바꾼다 - 그건 새 계좌다.

        이미 보유한 종목의 익절·손절가는 진입할 때 정해진 값을 그대로 쓴다. 범위를 바꿔도
        들고 있는 종목의 괄호는 움직이지 않는다(중간에 손절선이 뛰면 더 위험하다).
        """
        with self.lock:
            state = self._upgrade(self.store.get(self.account_kind, id))
            if state.get('archived'):
                raise ValueError('보관된 계좌의 설정은 바꿀 수 없습니다.')
            current = dict(state['config'])
            draft = dict(current)
            draft.update({k: v for k, v in payload.items() if k in current and k not in FIXED_KEYS})
            for key in FIXED_KEYS:
                draft[key] = current[key]
            # 범위를 바꾸면 기본값이 범위를 벗어날 수 있다. 사람이 신경 쓸 값이 아니라 맞춰 준다.
            draft['take_profit_default_pct'] = min(max(draft['take_profit_default_pct'],
                                                       draft['take_profit_min_pct']), draft['take_profit_max_pct'])
            draft['stop_loss_default_pct'] = min(max(draft['stop_loss_default_pct'],
                                                     draft['stop_loss_min_pct']), draft['stop_loss_max_pct'])
            cfg = DayTradeConfig.model_validate(draft).model_dump(mode='json')
            if (current.get('strategy')=='top5_morning' and state.get('basket',{}).get('locked')
                    and state['day']==_day(utcnow())):
                protected=('strategy','position_pct','basket_entry_at','basket_exit_at',
                           'basket_entry_minutes','basket_tp_pct','basket_sl_pct')
                if any(cfg[k]!=current[k] for k in protected):
                    raise ValueError('시나리오2 진입 후에는 당일 바스켓 설정을 변경할 수 없습니다. 다음 거래일 진입 전에 변경하세요.')
            if cfg['strategy'] != current['strategy']:
                if 'portfolio' in (cfg['strategy'],current['strategy']) and state['positions']:
                    raise ValueError('투자 기간 전환은 보유가 없는 계좌에서 선택하세요. 기존 보유의 청산 기준을 임의로 바꾸지 않습니다.')
                if cfg['strategy']=='top5_morning' and state['positions']:
                    raise ValueError('시나리오2는 보유가 없는 계좌에서 선택하세요. 기존 보유를 먼저 정리해야 TOP5 성과를 구분할 수 있습니다.')
                if state['status']=='running' or state['ai'].get('pending') or self.live.pending(state['id']):
                    raise ValueError('전략 변경은 운용 정지·AI 판단 및 미체결 주문 완료 후 가능합니다.')
                state['plan']=None
                state.pop('in_play',None)
                state.pop('basket',None)
                state['phase']='scanned' if state.get('scan') else 'idle'
                if 'portfolio' in (cfg['strategy'],current['strategy']):
                    state['scan']=None
                    state['phase']='idle'
                    state['retry']={'attempts':0,'last':None}
                    state.pop('portfolio_exits',None)
                    memory=learning.ensure(state)
                    memory['changes'].append({'status':'interrupted','reason':'투자 기간 체계 전환',
                        'previous_lessons':list(memory['active_lessons']),'at':utcnow().isoformat()})
                    memory['active_lessons']=[]
                    memory['trial']=None
                    for proposal in memory['proposals']:
                        if proposal['status'] in ('ready','trial'):proposal['status']='interrupted'
            changed = {k: (current.get(k), cfg[k]) for k in cfg if current.get(k) != cfg[k]}
            if changed:
                state['config'] = cfg
                self._log(state, '설정 변경: ' + ', '.join(f'{k} {a} → {b}' for k, (a, b) in list(changed.items())[:8])
                                 + (' 외' if len(changed) > 8 else ''))
                self.store.put(self.account_kind, id, state)
            return self.view(id)

    def bind_order(self, id: str, intent_id: str, broker_order_id: str) -> dict:
        with self.lock:
            state = self._upgrade(self.store.get(self.account_kind, id))
            result = self.live.bind(state, intent_id, broker_order_id, utcnow(), self._log)
            self.store.put(self.account_kind, id, state)
            return result

    def discard_order(self, id: str, intent_id: str) -> dict:
        with self.lock:
            state = self._upgrade(self.store.get(self.account_kind, id))
            result = self.live.discard_unknown(state, intent_id, self._log)
            self.store.put(self.account_kind, id, state)
            return result

    def control(self, id: str, action: str) -> dict:
        with self.lock:
            state = self._upgrade(self.store.get(self.account_kind, id))
            if state.get('archived') and action == 'start':
                raise ValueError('보관된 계좌는 다시 시작할 수 없습니다. 새 계좌를 만드세요.')
            if action == 'start':
                if self.is_live(state):
                    if not self.live.enabled:
                        raise ValueError('실거래가 꺼져 있습니다. NAS .env 의 ENABLE_LIVE_TRADING / TOSS_ACCOUNT_SEQ 를 확인하세요.')
                    if any(r['status'] == 'running' for r in self.store.all('live_accounts')):
                        raise ValueError('관심종목 실거래가 운용 중입니다. 같은 계좌로 두 방식을 동시에 돌리지 않습니다.')
                    if any(o['status'] == 'UNKNOWN' for o in self.live.pending(id)):
                        raise ValueError('결과를 모르는 주문이 있습니다. 먼저 토스 앱에서 확인하고 연결하거나 폐기하세요.')
                    self.live.reconcile(state)
                state['status'] = 'running'
                self._log(state, '운용 시작' + (' (실거래 · 실제 주문이 나갑니다)' if self.is_live(state) else ''))
            elif action == 'stop':
                state['status'] = 'stopped'
                if state.get('in_play'):state['in_play']['approvals']={}
                if self.is_live(state):
                    n = self.live.cancel_all(state, 'buy')
                    self._log(state, '운용 정지. 보유분은 그대로 두고' + (f' 미체결 매수 {n}건은 취소합니다' if n else ' 미체결 매수는 없습니다'))
                else:
                    self._log(state, '운용 정지 (보유분은 그대로 둡니다)')
            elif action == 'scan_now':
                if state.get('basket',{}).get('locked') and state['day']==_day(utcnow()):
                    raise ValueError('시나리오2 진입 후에는 당일 TOP5를 다시 선정할 수 없습니다.')
                state['phase'] = 'idle'
                state['scan'] = None
                state['plan'] = None
                state['_force_scan'] = utcnow().isoformat()
                state['retry'] = {'attempts': 0, 'last': None}
                self._log(state, '즉시 스캔·판단 요청 (수동)')
            elif action == 'review_now':
                # tick 에 맡기면 휴장일·정지 상태에서 영영 실행되지 않는다. 여기서 바로 띄운다.
                self._review_now(state, utcnow())
            elif action == 'close_all':
                self._close_all(state, utcnow(), '수동 전량 청산')
            else:
                raise ValueError('알 수 없는 제어 명령입니다.')
            self.store.put(self.account_kind, id, state)
            return state

    def _log(self, state: dict, message: str) -> None:
        code=message[:6]
        if len(code)==6 and code.isdigit() and '매수' in message:
            learning.ensure(state).setdefault('entry_checks',{})[code]={'at':utcnow().isoformat(),'reason':message[:240]}
        state['log'].append({'at': utcnow().isoformat(), 'message': message})
        state['log'] = state['log'][-200:]

    @staticmethod
    def _recent_picks(state: dict, days: int = 3) -> list[dict]:
        """최근 며칠 무엇을 골랐고 결과가 어땠나. 같은 테마를 관성으로 반복하는 걸 막는 재료다."""
        rows = []
        for day, entry in sorted((state.get('days') or {}).items())[-days:]:
            trades = [t for t in state.get('trades', []) if t.get('at') and _day(stamp(t['at'])) == day]
            realized = {}
            for trade in trades:
                if trade.get('side') == 'sell':
                    symbol = trade['symbol']
                    realized[symbol] = realized.get(symbol, 0) + trade.get('pnl', 0)
            rows.append({'day': day, 'stance': entry.get('stance', ''),
                         'picks': entry.get('picks', []), 'themes': entry.get('themes', []),
                         'picks_are_plans_not_fills': True,
                         'executed_trades': trades[-30:], 'realized_by_symbol': realized,
                         'account_realized_pnl': entry.get('realized_pnl', 0),
                         'wins': entry.get('wins', 0), 'losses': entry.get('losses', 0)})
        return rows

    # ── 장 시간 ──────────────────────────────────────────────────────────────
    def _sessions(self, state: dict, day: str) -> dict:
        """오늘 거래 가능 시간. 하루 한 번 토스 장 캘린더에서 받고, 실패하면 설정값을 쓴다.

        프리마켓(NXT) 08:00~09:00 · 정규장 09:00~15:30 · 애프터마켓(NXT) 15:30~20:00.
        이 값이 없으면 '15:00 이후' 같은 조건이 밤 11시에도 참이 되어 종가로 주문이 나간다(실제로 겪었다).
        """
        cfg = state['config']
        cached = state.get('sessions') or {}
        if cached.get('day') == day:
            return cached
        sessions = {'day': day, 'source': 'default', 'open': cfg['session_open'],
                    'regular_open': '09:00', 'regular_close': '15:30', 'close': cfg['session_close']}
        getter = getattr(self.broker, 'market_calendar_kr', None)
        if getter:
            try:
                data = getter() or {}
                today = data.get('today') or {}
                integrated = today.get('integrated') or {}
                regular = integrated.get('regularMarket') or {}
                if today.get('date') == day and regular.get('startTime'):
                    pre = integrated.get('preMarket') or {}
                    after = integrated.get('afterMarket') or {}
                    hm = lambda iso: str(iso)[11:16]          # noqa: E731 - '...T08:00:00+09:00' → '08:00'
                    sessions = {
                        'day': day, 'source': 'toss',
                        'open': hm(pre.get('startTime') or regular['startTime']),
                        'regular_open': hm(regular['startTime']),
                        'regular_close': hm(regular.get('endTime') or cfg['session_close']),
                        'close': hm(after.get('endTime') or regular.get('endTime') or cfg['session_close']),
                    }
                    self._log(state, f'오늘 장: 프리 {sessions["open"]}~{sessions["regular_open"]} · '
                                     f'정규 {sessions["regular_open"]}~{sessions["regular_close"]} · '
                                     f'애프터 ~{sessions["close"]} (토스 캘린더)')
            except Exception as exc:  # noqa: BLE001 - 캘린더가 죽어도 매매가 멈추면 안 된다
                log.warning('장 캘린더 조회 실패: %s', exc)
        state['sessions'] = sessions
        return sessions

    def _tradable(self, state: dict, now: datetime) -> bool:
        s = self._sessions(state, _day(now))
        return _between(now, s['open'], s['close'])

    # ── 대시보드 기록 ─────────────────────────────────────────────────────────
    def _record_equity(self, state: dict, now: datetime, min_gap_seconds: float = 300.0) -> None:
        """자산 곡선. 5분에 한 점. 하루 약 100점, 3000점이면 한 달 남짓."""
        curve = state.setdefault('equity_curve', [])
        if curve:
            try:
                last = datetime.fromisoformat(curve[-1]['at'])
            except ValueError:
                last = None
            if last and (now - last).total_seconds() < min_gap_seconds:
                return
        curve.append({'at': now.isoformat(), 'equity': round(self.equity(state)),
                      'cash': round(state['cash'])})
        del curve[:-3000]

    def _record_day(self, state: dict, day: str) -> None:
        """하루 마감 요약. 대시보드의 일별 손익 막대."""
        sells = [t for t in state['trades'] if t['side'] == 'sell' and _day(stamp(t['at']))==day]
        buys = [t for t in state['trades'] if t['side'] == 'buy' and _day(stamp(t['at']))==day]
        pnl = sum(t.get('pnl', 0.0) for t in sells)
        plan = state.get('plan') or {}
        cands = {c['symbol']: c for c in (state.get('scan') or {}).get('candidates', [])}
        picks, themes = [], []
        for p in plan.get('picks', []):
            names = [t['name'] for t in cands.get(p['symbol'], {}).get('themes', [])[:2]]
            picks.append({'symbol': p['symbol'], 'name': p.get('name', ''), 'themes': names})
            themes.extend(names)
        state.setdefault('days', {})[day] = {
            'stance': plan.get('market_stance', ''), 'picks': picks,
            'themes': sorted(set(themes)),
            'realized_pnl': round(pnl), 'sells': len(sells), 'buys': len(buys),
            'wins': sum(1 for t in sells if t.get('pnl', 0) > 0),
            'losses': sum(1 for t in sells if t.get('pnl', 0) <= 0),
            'equity': round(self.equity(state)),
            'ai_calls': state['ai'].get('calls_today', 0) + state['ai'].get('anchor_calls_today', 0),
            'kept': [p.get('name') or p['symbol'] for p in state['positions'].values() if p.get('hold_overnight')],
            'basket':dict(state.get('basket',{})),
            'realized_by_symbol':{p['symbol']:round(sum(t.get('pnl',0) for t in sells if t['symbol']==p['symbol'])) for p in picks},
        }
        # 너무 오래된 날은 버린다 (약 1년)
        for old in sorted(state['days'])[:-260]:
            state['days'].pop(old, None)

    # ── 회계 ─────────────────────────────────────────────────────────────────
    def _unrealized(self, state: dict, pos: dict) -> float:
        q = state['quotes'].get(pos['symbol'])
        if not q:
            return 0.0
        if pos.get('currency', 'KRW') == 'USD':
            fx = portfolio.fx_rate(self, state)
            return pos['qty'] * q['bid'] * fx - pos['cost']
        return (q['bid'] - pos['entry_price']) * pos['qty']

    def equity(self, state: dict) -> float:
        value = state['cash']
        fx = None
        for pos in state['positions'].values():
            q = state['quotes'].get(pos['symbol'])
            unit = q['bid'] if q else pos['entry_price']
            if pos.get('currency', 'KRW') == 'USD':
                if fx is None:
                    fx = portfolio.fx_rate(self, state)
                value += unit * pos['qty'] * fx
            else:
                value += unit * pos['qty']
        return value

    def _fill_buy(self, state: dict, symbol: str, name: str, krw: float, quote: dict,
                  tp_pct: float, sl_pct: float, meta: dict, now: datetime) -> bool:
        cfg=state['config']
        is_us = portfolio.is_us_symbol(symbol)
        if cfg.get('strategy')=='top5_morning':
            if (meta.get('source')!='plan' or _minutes_since(now,cfg['entry_at'])>=cfg['entry_window_minutes']
                or _hhmm(now)<cfg['entry_at']):
                self._log(state,f'{symbol} 시나리오2 매수 차단: 확정 TOP5 오전 진입 창 밖')
                return False
        pending=self.live.pending(state['id'],'buy') if self.is_live(state) else []
        occupied=set(state['positions']) | {o['symbol'] for o in pending}
        if symbol in occupied or len(occupied)>=cfg['max_positions']:
            self._log(state,f'{symbol} 매수 보류: 보유·미체결 포함 종목 한도')
            return False
        if cfg.get('strategy') == 'portfolio' and is_us:
            if not portfolio.us_entry_window(now):
                self._log(state, f'{symbol} 매수 차단: 미국 정규장 진입 가능 시간 밖')
                return False
        elif not _between(now,cfg['entry_at'],cfg['force_close_at']) or not self._tradable(state,now):
            self._log(state, f'{symbol} 매수 차단: 주문 가능 시간 밖')
            return False
        if not self._fresh_quote(quote, max(now, utcnow())):
            self._log(state, f'{symbol} 매수 보류: 유효한 최신 시세·호가 필요')
            return False
        strategy=cfg.get('strategy','ai')
        # The user's separate, explicitly enabled overnight exception uses the EOD policy.
        if (strategy=='in_play' and meta.get('source')=='close' and meta.get('hold_overnight')
            and _between(now,cfg['eod_review_at'],cfg['force_close_at'])):
            strategy='ai'
        if strategy not in ('ai','top5_morning','portfolio'):
            evidence=meta.pop('_chart',None) if strategy=='in_play' else None
            if strategy=='in_play':
                approval=(state.get('in_play',{}).get('approvals',{}).get(symbol) or {})
                if (meta.get('source')!='in_play' or approval.get('used') or not approval.get('expires_at')
                    or stamp(approval['expires_at'])<=max(now,utcnow())):
                    return False
            evidence=evidence or scanner.chart_context(self.broker,symbol,now)
            self._refresh_quotes(state,[symbol])
            quote=state['quotes'].get(symbol)
            if not self._fresh_quote(quote,max(now,utcnow())):return False
            gate=trade_setups.evaluate(strategy,evidence,quote,now)
            if not gate['allowed']:
                self._log(state,f'{symbol} 전략 진입 대기: {gate["reason"]}')
                return False
            meta=dict(meta,strategy_stop=gate['stop'])
            if strategy=='in_play':meta['approval_expires_at']=approval['expires_at']
        meta=dict(meta,strategy=strategy,plan_decided_at=(state.get('plan') or {}).get('decided_at'))
        meta=dict(meta,policy_version=learning.policy(state),
                  decision_id=meta.get('decision_id') or ((state.get('plan') or {}).get('decision_id') if meta.get('source')=='plan'
                  else learning.ensure(state).get('last_decision_id')))
        if strategy=='top5_morning':
            h,m=map(int,cfg['entry_at'].split(':'))
            deadline=now.astimezone(KST).replace(hour=h,minute=m,second=0,microsecond=0)+timedelta(minutes=cfg['entry_window_minutes'])
            meta['approval_expires_at']=deadline.isoformat()
        if strategy=='portfolio':
            h=meta.get('horizon','short')
            if is_us:
                if not portfolio.us_entry_window(now):
                    return False
            elif not '09:05'<=_hhmm(now)<'15:10':
                return False
            if portfolio.bucket_status(self,state)[h]['open_slots']<=0:
                self._log(state,f'{symbol} 매수 보류: {portfolio.LABELS[h]} 보유·미체결 종목 수 한도')
                return False
            local_tz = portfolio.ET if is_us else KST
            today_local = now.astimezone(local_tz).date().isoformat()
            if any(t['symbol']==symbol and stamp(t['at']).astimezone(local_tz).date().isoformat()==today_local for t in state['trades']):
                self._log(state,f'{symbol} 매수 보류: 같은 날 재진입 제한')
                return False
            krw=portfolio.budget(self,state,h,krw,quote,sl_pct,now)
            if krw<=0:
                self._log(state,f'{symbol} 매수 보류: 투자 기간·전체 투자 한도 또는 현금 부족')
                return False
            exp_dt = (now.astimezone(portfolio.ET).replace(hour=15,minute=50,second=0,microsecond=0)
                      if is_us else now.astimezone(KST).replace(hour=15,minute=10,second=0,microsecond=0))
            meta.update(hold_overnight=True,approval_expires_at=exp_dt.isoformat())
        # Enforce price constraints against the executable price, not narrative text.
        ceiling = meta.get('max_entry_price') or 0
        cfg = state['config']
        sell_tax_bps = 0.0 if is_us else cfg['sell_tax_bps']
        costs_pct = (2*cfg['fee_bps'] + sell_tax_bps + 2*cfg['slippage_bps']
                     + max(0, quote.get('spread_bps', 0))) / 100
        effective_sl=min(sl_pct,max(0,(1-meta['strategy_stop']/quote['ask'])*100)) if meta.get('strategy_stop') else sl_pct
        if strategy!='top5_morning' and tp_pct - costs_pct < effective_sl + costs_pct:
            self._log(state, f'{symbol} 매수 보류: 비용 차감 기대 이익이 손절 위험보다 작음')
            return False
        if strategy=='in_play':
            expected=quote['ask']*(1+max(cfg['slippage_bps'],cfg['live_limit_buffer_bps'] if self.is_live(state) else 0)/10000)
            stop=max(meta['strategy_stop'],expected*(1-sl_pct/100))
            loss_per_share=expected-stop+expected*costs_pct/100
            risk_budget=self.equity(state)*cfg['risk_per_trade_pct']/100
            qty=int(risk_budget/loss_per_share) if loss_per_share>0 else 0
            if qty<1:return False
            krw=min(krw,qty*expected*(1+cfg['fee_bps']/10000))
            meta=dict(meta,risk_budget_krw=risk_budget,max_qty=qty)
        candidate = next((c for c in (state.get('scan') or {}).get('candidates', [])
                          if c['symbol'] == symbol), {})
        daily = ((candidate.get('technical') or {}).get('daily') or {})
        resistance = daily.get('resistance20')
        if strategy not in ('top5_morning','portfolio') and meta.get('source') == 'plan' and resistance and daily.get('price', resistance) < resistance:
            ceiling = min(ceiling or float('inf'), resistance / (1 + tp_pct/100))
        meta = dict(meta, max_entry_price=ceiling)
        if ceiling and quote['ask'] * (1 + state['config']['slippage_bps']/10000) > ceiling:
            unit_lbl = f'${ceiling:,.2f}' if is_us else f'{ceiling:,.0f}원'
            self._log(state, f'{symbol} 매수 보류: 예상 체결가가 계획·저항 기준 상한 {unit_lbl} 초과')
            return False
        if self.is_live(state):
            if is_us:
                self._log(state, f'{symbol} 매수 보류: 해외주식 주문은 현재 모의운용 전용입니다')
                return False
            return self.live.queue_buy(state, symbol, name, krw, quote, tp_pct, sl_pct, meta, now, self._log)
        cfg = state['config']
        fx = portfolio.fx_rate(self, state, now) if is_us else 1.0
        ask = quote['ask'] * (1 + cfg['slippage_bps'] / 10000)
        unit_krw = ask * fx * (1 + cfg['fee_bps'] / 10000)
        qty = int(krw // unit_krw)
        if meta.get('max_qty') is not None:qty=min(qty,int(meta['max_qty']))
        if qty < 1:
            self._log(state, f'{symbol} 매수 불가: 예산 {krw:,.0f}원으로 1주도 못 삼')
            return False
        cost = qty * unit_krw
        if cost > state['cash']:
            self._log(state, f'{symbol} 매수 불가: 현금 부족')
            return False
        state['cash'] -= cost
        inst = portfolio.instrument_for(symbol, state.get('symbol_meta'))
        pos_extra = {'currency': 'USD', 'market': 'US', 'exchange': inst.exchange, 'entry_fx': round(fx, 2)} if is_us else {'currency': 'KRW', 'market': 'KR', 'exchange': 'KRX'}
        state['positions'][symbol] = {
            'symbol': symbol, 'name': name, 'qty': qty, 'initial_qty': qty, 'entry_price': ask,
            'entry_at': now.isoformat(), 'cost': cost,
            'tp_pct': tp_pct, 'sl_pct': sl_pct,
            'tp_price': ask * (1 + tp_pct / 100), 'sl_price': ask * (1 - sl_pct / 100),
            'hold_overnight': False, **pos_extra, **meta,
        }
        state['trades'].append({'symbol': symbol, 'side': 'buy', 'qty': qty, 'price': ask,
                                'currency': 'USD' if is_us else 'KRW', 'market': 'US' if is_us else 'KR',
                                'fx_rate': round(fx, 2) if is_us else 1.0,
                                'name':name,
                                'horizon':meta.get('horizon','short'),
                                'basket_rank':meta.get('basket_rank'),
                                'policy_version':meta['policy_version'],'decision_id':meta.get('decision_id'),
                                'strategy':strategy,'plan_decided_at':meta['plan_decided_at'],
                                'at': now.isoformat(), 'reason': meta.get('source', 'plan')})
        price_lbl = f'${ask:,.2f} (환율 {fx:,.0f}원)' if is_us else f'{ask:,.0f}'
        self._log(state, f'{name}({symbol}) {qty}주 매수 @{price_lbl} · 익절 +{tp_pct}% 손절 -{sl_pct}%')
        notify.post(notify.buy_message(state['positions'][symbol],state=state),'trade')
        return True

    def _fill_partial_sell(self, state: dict, symbol: str, sell_qty: int, price: float, reason: str, now: datetime) -> bool:
        if self.is_live(state):
            return False
        quote = state['quotes'].get(symbol)
        if not self._fresh_quote(quote, max(now, utcnow())):
            return False
        pos = state['positions'].get(symbol)
        if not pos or sell_qty < 1 or sell_qty >= pos['qty']:
            return False
        cfg = state['config']
        is_us = pos.get('currency', 'KRW') == 'USD'
        fx = portfolio.fx_rate(self, state, now) if is_us else 1.0
        sell_tax = 0.0 if is_us else cfg['sell_tax_bps']
        net_price = quote['bid'] * (1 - cfg['slippage_bps'] / 10000)
        proceeds = sell_qty * net_price * fx * (1 - (cfg['fee_bps'] + sell_tax) / 10000)
        cost_sold = pos['cost'] * (sell_qty / pos['qty'])
        pnl = proceeds - cost_sold
        pos['qty'] -= sell_qty
        pos['cost'] -= cost_sold
        state['cash'] += proceeds
        state['realized_pnl'] += pnl
        state['trades'].append({
            'symbol': symbol, 'side': 'sell', 'partial': True, 'qty': sell_qty,
            'currency': 'USD' if is_us else 'KRW', 'market': 'US' if is_us else 'KR',
            'fx_rate': round(fx, 2) if is_us else 1.0,
            'name': pos.get('name') or symbol, 'horizon': pos.get('horizon', 'short'),
            'price': net_price, 'at': now.isoformat(), 'pnl': pnl,
            'strategy': pos.get('strategy', 'ai'), 'basket_rank': pos.get('basket_rank'),
            'policy_version': pos.get('policy_version'), 'decision_id': pos.get('decision_id'),
            'entry_at': pos.get('entry_at'), 'cost_basis': cost_sold,
            'pnl_pct': pnl / cost_sold * 100 if cost_sold else 0.0, 'reason': reason,
        })
        price_lbl = f'${net_price:,.2f}' if is_us else f'{net_price:,.0f}'
        self._log(state, f'{pos["name"]}({symbol}) {sell_qty}주 분할익절 @{price_lbl} · {pnl:+,.0f}원 '
                         f'({pnl / cost_sold * 100:+.2f}%) · 잔여 {pos["qty"]}주 추세유지')
        notify.post(notify.sell_message(state['trades'][-1], pos['name'], state=state), 'trade')
        return True

    def _fill_sell(self, state: dict, symbol: str, price: float, reason: str, now: datetime) -> None:
        if self.is_live(state):
            self.live.queue_sell(state, symbol, state['quotes'].get(symbol), reason, now, self._log)
            return
        quote = state['quotes'].get(symbol)
        if not self._fresh_quote(quote, max(now, utcnow())):
            self._log(state, f'{symbol} 모의 매도 보류: 유효한 최신 시세·호가 필요')
            return
        price = quote['bid']
        pos = state['positions'].pop(symbol, None)
        if not pos:
            return
        cfg = state['config']
        is_us = pos.get('currency', 'KRW') == 'USD'
        fx = portfolio.fx_rate(self, state, now) if is_us else 1.0
        sell_tax = 0.0 if is_us else cfg['sell_tax_bps']
        net_price = price * (1 - cfg['slippage_bps'] / 10000)
        proceeds = pos['qty'] * net_price * fx * (1 - (cfg['fee_bps'] + sell_tax) / 10000)
        pnl = proceeds - pos['cost']
        state['cash'] += proceeds
        state['realized_pnl'] += pnl
        state['trades'].append({'symbol': symbol, 'side': 'sell', 'qty': pos['qty'],
                                'currency': 'USD' if is_us else 'KRW', 'market': 'US' if is_us else 'KR',
                                'fx_rate': round(fx, 2) if is_us else 1.0,
                                'name':pos.get('name') or symbol,
                                'horizon':pos.get('horizon','short'),
                                'price': net_price, 'at': now.isoformat(), 'pnl': pnl,
                                'strategy':pos.get('strategy','ai'),
                                'basket_rank':pos.get('basket_rank'),
                                'policy_version':pos.get('policy_version'),'decision_id':pos.get('decision_id'),
                                'entry_at':pos.get('entry_at'),'cost_basis':pos['cost'],
                                'pnl_pct': pnl / pos['cost'] * 100, 'reason': reason})
        price_lbl = f'${net_price:,.2f}' if is_us else f'{net_price:,.0f}'
        self._log(state, f'{pos["name"]}({symbol}) 청산 @{price_lbl} · {pnl:+,.0f}원 '
                         f'({pnl / pos["cost"] * 100:+.2f}%) · {reason}')
        notify.post(notify.sell_message(state['trades'][-1], pos['name'],state=state),'trade')

    def _close_all(self, state: dict, now: datetime, reason: str, keep_overnight: bool = False) -> None:
        for symbol in list(state['positions']):
            pos = state['positions'][symbol]
            if keep_overnight and pos.get('hold_overnight'):
                continue
            q = state['quotes'].get(symbol)
            price = q['bid'] if q else pos['entry_price']
            self._fill_sell(state, symbol, price, reason, now)

    # ── AI 예산 ──────────────────────────────────────────────────────────────
    def _ai_busy(self) -> bool:
        """판단 잠금 사용 중. 뉴스 번역처럼 짧은 보조 작업이 쥔 잠금은 판단이 기다렸다 받으므로 바쁨으로 보지 않는다."""
        return self.ai.lock.locked() and not getattr(self.ai, 'background_task', False)

    def _ai_allowed(self, state: dict, now: datetime, anchor: bool = False) -> bool:
        """anchor = 하루 정해진 시점의 판단(장전·마감·복기). 이건 횟수 한도에 걸리지 않는다.

        한도는 장중 이벤트(뉴스·급등)가 사용량을 갉아먹는 걸 막으려고 둔 것이지,
        하루의 뼈대가 되는 판단을 거르라고 둔 것이 아니다.
        """
        ai = state['ai']
        day = _day(now)
        if ai['calls_day'] != day:
            ai['calls_day'] = day
            ai['calls_today'] = 0
            ai['anchor_calls_today'] = 0
        # 판단 중에 컨테이너가 죽으면 pending 이 영원히 True 로 남는다. 15분이면 끝났다고 본다.
        if ai['pending'] and ai.get('started_at'):
            try:
                if (now - stamp(ai['started_at'])).total_seconds() > 900:
                    ai['pending'] = False
                    ai['error'] = '이전 판단이 끝나지 않아 초기화했습니다(재시작 추정).'
            except ValueError:
                ai['pending'] = False
        if ai['pending'] or self._ai_busy():
            return False
        if anchor:
            return True
        if ai['calls_today'] >= state['config']['max_ai_calls_per_day']:
            ai['error'] = '하루 이벤트 판단 한도 도달. 예약 판단과 괄호 감시는 계속합니다.'
            return False
        return True

    def _ai_call(self, state: dict, kind: str, fn, snapshot: dict, on_done,
                 fail_phase: str | None = None, counts: bool = True) -> None:
        """AI 는 백그라운드에서. 워커가 판단을 기다리며 감시를 멈추면 안 된다.

        fail_phase: 실패했을 때 되돌릴 단계. 이게 없으면 판단이 한 번 실패한 뒤
                    'planning' 에 갇혀 그날 내내 아무 일도 일어나지 않는다(실제로 그랬다).
        counts:     False 면 하루 이벤트 한도에 치지 않는다(예약 판단).
        """
        snapshot['selected_strategy']=trade_setups.PROFILES[state['config'].get('strategy','ai')]
        snapshot['financial_calendar']=financial_brief.build(self.store,utcnow())
        run=learning.ensure(state)
        child=getattr(self.ai,'providers',{}).get(getattr(self.ai,'provider','')) or self.ai
        run['ai_identity']={'provider':getattr(self.ai,'provider','claude'),'model':getattr(child,'model',None)}
        snapshot['policy_version']=learning.policy(state)
        decision_id=learning.record(self.store,state,kind,snapshot,utcnow())
        state['ai']['pending'] = True
        state['ai']['started_at'] = utcnow().isoformat()
        if counts:
            state['ai']['calls_today'] += 1
        else:
            state['ai']['anchor_calls_today'] = state['ai'].get('anchor_calls_today', 0) + 1
        state['ai']['error'] = ''
        account_id = state['id']
        office_enabled=bool(snapshot.get('portfolio_policy') and state['config'].get('ai_office',True)
                            and kind=='장전 판단' and hasattr(self.ai,'plan_office'))
        if office_enabled:
            state['office_run_id']=decision_id
            self.store.put('office_runs',decision_id,{'id':decision_id,'account_id':account_id,'status':'running',
                'started_at':utcnow().isoformat(),'updated_at':utcnow().isoformat(),'stages':{},'application':'판단 중'})

        def office_progress(role,status,output):
            with self.lock:
                record=self.store.get('office_runs',decision_id)
                latest=self.store.get(self.account_kind,account_id)
                now=utcnow()
                if status=='running' and (latest['status']!='running' or snapshot.get('day')!=_day(now)
                                          or snapshot['policy_version']!=learning.policy(latest)):
                    record.update(status='cancelled',updated_at=now.isoformat(),application='정지·거래일·설정 변경으로 미적용')
                    self.store.put('office_runs',decision_id,record)
                    raise AIError('AI 오피스 중단: 운용 상태 또는 판단 기준이 변경되었습니다.')
                record['updated_at']=now.isoformat()
                stage=record['stages'].setdefault(role,{})
                stage.update(status=status,updated_at=now.isoformat())
                if status=='running':stage['started_at']=now.isoformat()
                if output is not None:stage['output']=output
                if role=='collect' and status=='done':
                    latest['office_exit_evidence']=output.get('exit_evidence',[])
                    self.store.put(self.account_kind,account_id,latest)
                if status=='failed':record['status']='failed'
                if role=='office' and status=='done':record['status']='done'
                self.store.put('office_runs',decision_id,record)

        def run():
            started=time.monotonic()
            result, error = None, ''
            try:
                result = self.ai.plan_office(snapshot,office_progress) if office_enabled else fn(snapshot)
            except AIError as exc:
                error = str(exc)
            except Exception as exc:  # noqa: BLE001
                error = f'{kind} 처리 오류 ({type(exc).__name__})'
                log.error('%s failed: %s',kind,type(exc).__name__)
            with self.lock:
                latest = self.store.get(self.account_kind, account_id)
                latest['ai']['pending'] = False
                latest['ai']['error'] = error
                elapsed=time.monotonic()-started
                learning.result(self.store,decision_id,result,error,self.ai.last_usage,elapsed,utcnow())
                latest['ai']['history'].append({'at': utcnow().isoformat(), 'kind': kind,
                                                'ok': not error, 'error': error,
                                                'summary': (result or {}).get('summary', '')[:400],
                                                'cost_usd': self.ai.last_usage.get('cost_usd')})
                latest['ai']['history'][-1]['provider'] = self.ai.last_usage.get('provider', 'claude')
                latest['ai']['history'][-1]['model'] = self.ai.last_usage.get('model')
                latest['ai']['history'][-1]['office_calls'] = self.ai.last_usage.get('office_calls',0) if office_enabled else 0
                latest['ai']['history'][-1]['seconds']=round(elapsed,2)
                latest['ai']['history'][-1]['decision_id']=decision_id
                latest['ai']['history'][-1]['policy_version']=snapshot.get('policy_version') or learning.policy(latest)
                latest['ai']['history'] = latest['ai']['history'][-60:]
                if result is not None:
                    if kind!='마감 복기' and (latest['status']!='running' or snapshot.get('day',_day(utcnow()))!=_day(utcnow())
                                             or snapshot['policy_version']!=learning.policy(latest)):
                        self._log(latest,f'{kind} 결과 폐기: 정지·거래일·전략 버전 변경')
                        latest['ai']['history'][-1].update(ok=False,error='정지·거래일·전략 버전 변경으로 결과 미적용')
                        if office_enabled:
                            office=self.store.get('office_runs',decision_id)
                            office.update(status='cancelled',application='결과 미적용')
                            self.store.put('office_runs',decision_id,office)
                        self.store.put(self.account_kind,account_id,latest)
                        return
                    try:
                        on_done(latest, result, utcnow())
                        if '장전' in kind and latest.get('plan'):latest['plan']['decision_id']=decision_id
                    except Exception as exc:  # noqa: BLE001
                        latest['ai']['error'] = f'{kind} 결과 적용 오류: {exc}'
                        log.exception('결과 적용 실패')
                else:
                    if fail_phase:
                        latest['phase'] = fail_phase
                    self._log(latest, f'{kind} 실패: {error}'
                                      + (' · 잠시 뒤 다시 시도합니다' if fail_phase else ''))
                    # 판단 실패는 회의·매매가 멈췄다는 뜻이라 하루 한 번 경고로 보낸다.
                    notify.post_once(f'ai-fail:{_day(utcnow())}:{account_id}:{kind}',
                                     notify.error_message(f'AI {kind} 실패', error + ' · 웹 홈의 운영 점검에서 원인을 확인하세요'))
                if office_enabled:
                    office=self.store.get('office_runs',decision_id)
                    office['application']='실패 · 주문에 미적용' if latest['ai']['error'] else '검증 후 계획 반영 · 체결과 별도'
                    if latest['ai']['error']:office['status']='failed'
                    self.store.put('office_runs',decision_id,office)
                self.store.put(self.account_kind, account_id, latest)

        threading.Thread(target=run, daemon=True, name=f'daytrade-{kind}').start()

    # ── 스케줄 ───────────────────────────────────────────────────────────────
    def start_worker(self) -> None:
        def worker():
            while not self.stop_event.wait(5):
                for state in self.store.all(self.account_kind):
                    if state['status'] != 'running':
                        if state.get('mode') == 'live' and self.live.pending(state['id']):
                            try:
                                self.live_pulse(state['id'])
                            except Exception:  # noqa: BLE001
                                log.exception('실거래 주문 확인 오류')
                        continue
                    try:
                        self.tick(state['id'])
                    except Exception as exc:  # noqa: BLE001
                        log.exception('단타 tick 오류')
                        with self.lock:
                            latest = self.store.get(self.account_kind, state['id'])
                            at = utcnow().astimezone(KST).strftime('%m-%d %H:%M')
                            latest['errors']['worker'] = f'{at} 감시 루프 오류 ({type(exc).__name__}). 다음 주기에 다시 시도합니다.'
                            self.store.put(self.account_kind, state['id'], latest)
                    else:
                        if (state.get('errors') or {}).get('worker'):  # 정상으로 돌아오면 지난 오류를 남기지 않는다
                            with self.lock:
                                latest = self.store.get(self.account_kind, state['id'])
                                latest['errors'].pop('worker', None)
                                self.store.put(self.account_kind, state['id'], latest)
        threading.Thread(target=worker, daemon=True, name='daytrade-worker').start()

    def tick(self, id: str) -> None:
        if not self.pulse_lock.acquire(blocking=False):
            return
        try:
            now = utcnow()
            with self.lock:
                state = self._upgrade(self.store.get(self.account_kind, id))
                cfg = state['config']
                day = _day(now)
                if self.is_live(state):
                    self.live.pulse(state, now, self._log)

                # 새 거래일 - 어제 것 정리. 미국 정규장이 자정 너머 진행 중일 때는 미국장 종료 후 넘긴다.
                us_active_now = (cfg.get('strategy') == 'portfolio'
                                 and 'US' in (cfg.get('markets') or ['KR'])
                                 and portfolio.us_preopen_or_open(now))
                if state['day'] != day and not us_active_now:
                    state['day'] = day
                    state['phase'] = 'idle'
                    state['scan'] = None
                    state['plan'] = None
                    state.pop('basket',None)
                    state.pop('portfolio_exits',None)
                    state.pop('portfolio_held_data',None)
                    learning.advance(state,now)
                    learning.ensure(state)['observations']={}
                    learning.ensure(state)['entry_checks']={}
                    state['events'] = {'seen_news': [], 'surge_ref': {}, 'surged': [], 'portfolio_reviews': []}
                    state['stamps'] = {}
                    state['retry'] = {'attempts': 0, 'last': None}
                    state['sessions'] = None
                    for pos in state['positions'].values():
                        pos['hold_overnight'] = cfg.get('strategy')=='portfolio'
                    self._log(state, f'{day} 거래일 시작')

                if not calendar('KRX').is_session(day) or day in {'2026-06-03', '2026-07-17'}:
                    if cfg.get('strategy') == 'portfolio' and 'US' in (cfg.get('markets') or ['KR']) and portfolio.us_preopen_or_open(now):
                        portfolio.tick(self, state, now)
                        self.store.put(self.account_kind, id, state)
                        return
                    state['phase'] = 'holiday'
                    self.store.put(self.account_kind, id, state)
                    return

                hhmm = _hhmm(now)
                if cfg.get('strategy')=='portfolio':
                    portfolio.tick(self,state,now)
                    self.store.put(self.account_kind,id,state)
                    return
                phase = state['phase']
                if (cfg.get('strategy')=='top5_morning' and self.is_live(state)
                        and _minutes_since(now,cfg['entry_at'])>=cfg['entry_window_minutes']):
                    self.live.cancel_all(state,'buy')
                sessions = self._sessions(state, day)
                tradable = _between(now, sessions['open'], sessions['close'])

                # 1) 장전 스캔 → AI 계획. 실패하면 재시도한다. (조회만 하므로 장 시간과 무관)
                if self._needs_plan(state, now):
                    self._try_plan(state, now)
                    phase = state['phase']

                # 2) 진입 창 (entering: 시세가 없거나 실거래 주문이 아직 안 채워진 종목이 남은 상태)
                if phase in ('planned', 'entering') and _after(now, cfg['entry_at']):
                    if (_minutes_since(now, cfg['entry_at']) > cfg['entry_window_minutes'] or not tradable
                            or cfg.get('strategy')=='top5_morning' and _minutes_since(now,cfg['entry_at'])>=cfg['entry_window_minutes']):
                        state['phase'] = 'monitoring'
                        if cfg.get('strategy')=='top5_morning':
                            for p in (state.get('plan') or {}).get('picks',[]):
                                if not p.get('_done'):p['entry_status']='미매수: 오전 진입 창 종료'
                        self._log(state, '진입 창이 지나 남은 계획은 취소' if tradable else '장 시간이 아니어서 남은 계획은 취소')
                    else:
                        self._do_entry(state, now)
                    phase = state['phase']

                # 3) 감시 (진입 후 ~ 마감 정리 전). 장이 닫히면 시세가 멈추므로 감시도 쉰다.
                if tradable and (state['positions'] or phase in ('entering', 'monitoring', 'eod_reviewed')):
                    self._do_monitor(state, now)

                # 4) 마감 판단 → 5) 강제 청산
                # 보유가 없어도 부른다: 오늘 눌린 종목을 내일 반등 목적으로 살지 묻기 위해서다.
                # 반드시 [마감 판단 ~ 강제 정리) 안에서만. 상한이 없으면 밤 11시에 종가로 사들인다.
                if phase not in ('eod_reviewed', 'closed') and tradable \
                        and _between(now, cfg['eod_review_at'], cfg['force_close_at']) \
                        and (state['positions'] or cfg['close_entry']):
                    self._do_eod_review(state, now)
                if _after(now, cfg['force_close_at']) and phase != 'closed':
                    basket_mode=cfg.get('strategy')=='top5_morning'
                    if basket_mode:
                        if self.is_live(state):self.live.cancel_all(state,'buy')
                        for pos in state['positions'].values():pos['hold_overnight']=False
                    closable = [p for p in state['positions'].values() if not p.get('hold_overnight')]
                    if tradable:
                        self._close_all(state, now, '시나리오2 오전장 정리' if basket_mode else '마감 전 정리', keep_overnight=not basket_mode)
                        closable = [p for p in state['positions'].values() if not p.get('hold_overnight')]
                    elif closable:
                        # 장이 닫힌 뒤다. 지금 파는 건 불가능하고, 가상 체결이라도 종가로 파는 건 거짓이다.
                        names = ', '.join(p.get('name') or p['symbol'] for p in closable)
                        self._log(state, f'장 종료 후여서 정리하지 못한 보유가 있습니다: {names} · 다음 거래일에 처리합니다')
                        notify.post(notify.error_message('마감 정리 실패', f'장 종료 후 미정리: {names}'),'alert')
                        closable = []
                    if not closable and (not self.is_live(state) or
                            not self.live.pending(id, None if basket_mode else 'sell')):
                        state['phase'] = 'closed'
                        state['stamps']['closed'] = now.isoformat()
                        kept = [p['name'] for p in state['positions'].values()]
                        self._log(state, '장 마감 정리 완료' + (f' · 익일 보유: {", ".join(kept)}' if kept else ''))
                        self._record_day(state, day)
                        # 거래일이 넘어가면 계획·스캔이 지워진다. 나중에 사람이 다시 복기할 수 있게 재료를 남긴다.
                        state['tuning']['pending_snapshot'] = self._review_snapshot(state, day, now)
                        notify.post(notify.summary_message(state),notify.summary_kind(state))

                # 6) 마감 복기 - 오늘 왜 됐는지/안 됐는지 보고 내일 숫자를 조정한다
                if cfg['daily_review']:
                    self._maybe_review(state, now, day)

                if self.is_live(state):
                    # 이 주기에 준비된 주문(DRAFT)은 바로 보낸다. 5초를 더 기다릴 이유가 없다.
                    self.live.pulse(state, now, self._log)
                self._record_equity(state, now)
                state['last_poll_at'] = now.isoformat()
                self.store.put(self.account_kind, id, state)
        finally:
            self.pulse_lock.release()

    def live_pulse(self, id: str) -> None:
        """정지된 실거래 계좌의 남은 주문(취소·매도)만 확인한다. 새 판단·매수는 하지 않는다."""
        if not self.pulse_lock.acquire(blocking=False):
            return
        try:
            with self.lock:
                state = self._upgrade(self.store.get(self.account_kind, id))
                self.live.pulse(state, utcnow(), self._log)
                self.store.put(self.account_kind, id, state)
        finally:
            self.pulse_lock.release()

    # ── 장전 스캔·판단: 될 때까지 다시 시도한다 ───────────────────────────────
    def _needs_plan(self, state: dict, now: datetime) -> bool:
        cfg = state['config']
        us_open_mode = (cfg.get('strategy') == 'portfolio'
                        and 'US' in (cfg.get('markets') or ['KR'])
                        and portfolio.us_preopen_or_open(now))
        if cfg.get('strategy')=='top5_morning' and (state.get('basket',{}).get('locked') or
                _minutes_since(now,cfg['entry_at'])>=cfg['entry_window_minutes']):
            state.pop('_force_scan',None)
            return False
        # Includes legacy/manual requests restored after a midnight restart (unless US market session is active).
        if _hhmm(now)>=cfg['force_close_at'] and not us_open_mode:
            state.pop('_force_scan',None)
            return False
        if _hhmm(now) < max('07:30', cfg['scan_at']) and not us_open_mode:
            if state.pop('_force_scan', None):
                self._log(state, f'장전 판단은 한국시간 {cfg["scan_at"]} 예약 시각에 실행합니다')
            return False
        forced = state.get('_force_scan')
        if forced:
            try:
                fresh = 0 <= (now - stamp(str(forced))).total_seconds() <= 1800
            except (ValueError, TypeError):
                fresh = True            # 옛 형식(True). 한 번은 받아 준다
            if fresh:
                return True
            state.pop('_force_scan', None)
            self._log(state, '즉시 스캔 요청이 30분을 넘겨 취소되었습니다 (예약 시각에 다시 합니다)')
            return False
        if (not _after(now, cfg['scan_at']) or _hhmm(now) >= cfg['force_close_at']):
            return False
        phase = state['phase']
        # idle/holiday: 아직 안 했다. scanned: 스캔은 됐는데 판단이 안 붙었다.
        # planning: 판단을 띄웠는데 실패해서 돌아왔다(_ai_call 의 fail_phase).
        if phase not in ('idle', 'holiday', 'scanned', 'planning'):
            return False
        if phase == 'planning' and state['ai']['pending']:
            return False
        retry = state.setdefault('retry', {'attempts': 0, 'last': None})
        if retry['last']:
            try:
                if (now - stamp(retry['last'])).total_seconds() < cfg['retry_minutes'] * 60:
                    return False
            except ValueError:
                pass
        if retry['attempts'] >= cfg['max_scan_attempts']:
            if not state['errors'].get('scan_giveup'):
                state['errors']['scan_giveup'] = (
                    f'장전 스캔·판단을 {retry["attempts"]}번 시도했지만 실패했습니다. '
                    '오늘은 신규 진입하지 않습니다. "지금 스캔·AI 판단"으로 다시 시도할 수 있습니다.')
                self._log(state, state['errors']['scan_giveup'])
                notify.post_once(f'scan-giveup:{state["day"]}:{state["id"]}',
                                 notify.error_message('장전 판단 포기', state['errors']['scan_giveup']))
            return False
        return True

    def _try_plan(self, state: dict, now: datetime) -> None:
        """스캔 → (진입 창 안이면) AI 장전 판단. 실패는 다음 주기에 다시."""
        cfg = state['config']
        manual = bool(state.pop('_force_scan', False))
        retry = state.setdefault('retry', {'attempts': 0, 'last': None})
        retry['attempts'] += 1
        retry['last'] = now.isoformat()
        trigger = '수동 요청' if manual else f'예약 {cfg["scan_at"]}'
        state['stamps']['plan_trigger'] = trigger
        if retry['attempts'] == 1:
            self._log(state, f'장전 스캔·판단 시작 ({trigger})')
        else:
            self._log(state, f'장전 스캔·판단 재시도 {retry["attempts"]}회차 ({trigger})')

        if not state.get('scan') or state['phase'] in ('idle', 'holiday'):
            if not self._do_scan(state, now):
                return
        if not (state.get('scan') or {}).get('candidates') and not (cfg.get('strategy')=='portfolio' and state['positions']):
            state['phase'] = 'monitoring'
            return

        late = _minutes_since(now, cfg['entry_at']) > cfg['entry_window_minutes']
        if late and not manual:
            state['phase'] = 'monitoring'
            self._log(state, '진입 창이 지나 장전 판단은 생략합니다. 마감 판단에서 다시 봅니다.')
            return
        if not self._ai_allowed(state, now, anchor=True):
            return          # 다음 주기에 다시 (횟수 한도에는 걸리지 않는다)
        self._plan_call(state, now)

    # ── 단계별 동작 ──────────────────────────────────────────────────────────
    def _do_scan(self, state: dict, now: datetime) -> bool:
        cfg = state['config']
        started=time.monotonic()
        try:
            scan = portfolio.scan(self,state,now) if cfg.get('strategy')=='portfolio' else scanner.premarket_scan(self.broker, cfg)
            state['ai']['scan_seconds']=round(time.monotonic()-started,2)
        except Exception as exc:  # noqa: BLE001
            state['errors']['scan'] = f'스캔 실패: {exc}'
            self._log(state, f'장전 스캔 실패: {exc} · 잠시 뒤 다시 시도합니다')
            notify.post_once(f'scan-fail:{state["day"]}:{state["id"]}',
                             notify.error_message('장전 스캔 실패', f'{exc} · 토스 키·NAS 공인 IP 허용을 확인하세요'))
            return False
        state['errors'].pop('scan', None)
        state['scan'] = {k: v for k, v in scan.items() if k != 'config'}
        state['phase'] = 'scanned'
        state['stamps']['scan'] = now.isoformat()
        self._log(state, f'장전 스캔: 풀 {scan["pool_size"]}종 → 후보 {len(scan["candidates"])}종')
        if not scan['candidates']:
            state['phase'] = 'monitoring'
            self._log(state, '후보 없음. 오늘은 신규 진입하지 않습니다.')
        return True

    def _plan_call(self, state: dict, now: datetime) -> None:
        """스캔 결과 + 뉴스 + 지난 복기의 교훈을 넘겨 장전 판단을 요청한다."""
        cfg = state['config']
        scan = state['scan']
        us = []
        try:
            us = sorted(self.store.all('evidence'), key=lambda n: n['available_at'], reverse=True)[:30]
        except Exception:  # noqa: BLE001
            pass
        # 국내 시장 전체 뉴스 (제목만). 종목별 기사는 후보 안에 본문까지 들어 있다.
        kr_market = krnews.market_news(hours=18)[:15]
        # 간밤 미국. 제목이 아니라 숫자다 - EWY(한국 ETF)·반도체 등락률이 오늘 갭의 실제 근거다.
        try:
            us_overnight = overnight.brief(self.broker)
        except Exception as exc:  # noqa: BLE001 - 간밤 지표 때문에 장전 판단 전체가 죽으면 안 된다
            log.warning('간밤 지표 실패: %s', exc)
            us_overnight = {'as_of': '', 'rows': [], 'headline': '', 'error': str(exc)[:120], 'note': ''}
        open_slots = max(0, cfg['max_positions'] - len(state['positions']))
        lessons = learning.ensure(state)['active_lessons'] if cfg.get('self_improve',True) else []
        recent = self._recent_picks(state)
        snapshot = {
            'at': now.isoformat(), 'day': state['day'],
            'market_context': self.broker.market_context() if hasattr(self.broker, 'market_context') else {'errors':['unsupported']},
            'max_positions': open_slots, 'position_pct': cfg['position_pct'],
            'take_profit_min_pct': cfg['take_profit_min_pct'], 'take_profit_max_pct': cfg['take_profit_max_pct'],
            'stop_loss_min_pct': cfg['stop_loss_min_pct'], 'stop_loss_max_pct': cfg['stop_loss_max_pct'],
            'entry_session': cfg['entry_session'], 'entry_at': cfg['entry_at'],
            'force_close_at': cfg['force_close_at'],
            'basket_policy':{k:cfg[k] for k in ('basket_entry_at','basket_exit_at','basket_entry_minutes',
                                               'basket_tp_pct','basket_sl_pct')} if cfg.get('strategy')=='top5_morning' else None,
            'cash': state['cash'], 'equity': self.equity(state),
            'held': list(state['positions']),
            'candidates': [self._candidate_view(c) for c in scan['candidates']],
            'theme_universe': self._theme_universe(scan['candidates']),
            'theme_target_count': 5,
            'timezone': 'Asia/Seoul', 'scheduled_at_kst': cfg['scan_at'],
            'us_evidence': [{'title': n['title'], 'source': n['source'], 'published_at': n['published_at'],
                             'assessment': n.get('assessment', ''),'url':n.get('url','')} for n in us],
            'kr_market_news': [{'title': n['title'], 'source': n['source'], 'published_at': n['published_at'],'url':n.get('url','')}
                               for n in kr_market],
            'overnight_us': {k: v for k, v in us_overnight.items() if k in ('as_of', 'rows', 'note')},
            'recent_picks': recent,
            'lessons': lessons,
            'note': scan['note'],
        }
        if cfg.get('strategy')=='portfolio':
            caps = portfolio.style_caps(cfg)
            snapshot['portfolio_policy']={'style':cfg['portfolio_style'],'caps':caps,
                'markets':cfg.get('markets',['KR']),'usd_krw':portfolio.fx_rate(self,state,now),
                'buckets':portfolio.bucket_status(self,state),
                'exit_evidence':portfolio.exit_evidence(state),
                'horizons':portfolio.LABELS,'cash_floor_pct':max(10,100-sum(caps.values())),'risk_per_trade_pct':cfg['portfolio_risk_pct'],
                'held':list(state['positions'].values()),'held_daily':{s:{k:v for k,v in f.items() if k!='chart'} for s,f in state.get('portfolio_held_data',{}).items()},
                'no_day_end_liquidation':True,'fundamentals':'미수집: 밸류·재무 건전성 판단 근거로 사용할 수 없음'}
        # "AI 가 무엇을 보고 판단했나" 를 화면에 보여주기 위해 남긴다. 후보별 기사는 scan 안에 있다.
        state['ai_inputs'] = {
            'at': now.isoformat(), 'kind': '장전 판단',
            'candidates': len(scan['candidates']),
            'candidate_news': sum(len(c.get('news', [])) for c in scan['candidates']),
            'kr_market_news': [{'title': n['title'], 'source': n['source'], 'published_at': n['published_at'],
                                'url': n.get('url', '')} for n in kr_market],
            'us_evidence': [{'title': n['title'], 'source': n['source'], 'published_at': n['published_at'],
                             'url': n.get('url', '')} for n in us],
            'overnight_us': us_overnight,
            'recent_picks': recent,
            'news_freshest_at': max([n['published_at'] for n in kr_market if n.get('published_at')]
                                    + [n['published_at'] for n in us if n.get('published_at')] + ['']),
        }
        # 콜백이 phase 를 planned/monitoring 으로 바꾼다. 그 뒤에 덮어쓰면 안 되므로 먼저 둔다.
        state['phase'] = 'planning'
        # 실패하면 'scanned' 로 돌아온다 -> 스캔을 다시 돌리지 않고 판단만 재시도한다.
        self._ai_call(state, '장전 판단', self.ai.plan_day, snapshot, self._apply_plan,
                      fail_phase='scanned', counts=False)

    @staticmethod
    def _theme_universe(candidates: list[dict]) -> list[dict]:
        groups = {}
        for c in candidates:
            for t in c.get('themes', []):
                group = groups.setdefault(t['name'], {'name': t['name'], 'symbols': [],
                    'change_pct': t.get('change_pct'), 'change_3d_avg_pct': t.get('change_3d_avg_pct'),
                    'description': t.get('description', '')})
                if c['symbol'] not in group['symbols']:
                    group['symbols'].append(c['symbol'])
        return list(groups.values())

    @staticmethod
    def _candidate_view(c: dict) -> dict:
        daily={k:v for k,v in (c.get('portfolio') or {}).items() if k!='chart'}
        technical=c.get('technical')
        if c.get('portfolio') and technical:technical=dict(technical,daily=daily)
        return {
            'symbol': c['symbol'], 'name': c.get('name', ''),
            'market': c.get('market', 'US' if portfolio.is_us_symbol(c['symbol']) else 'KR'),
            'currency': c.get('currency', 'USD' if portfolio.is_us_symbol(c['symbol']) else 'KRW'),
            'exchange': c.get('exchange', 'NASDAQ' if portfolio.is_us_symbol(c['symbol']) else 'KRX'),
            'score': c.get('score'),
            'origins': c.get('origins', []), 'prev_change_pct': c.get('prev_change_pct'),
            'last_price': c.get('last_price'), 'trading_amount_krw': c.get('trading_amount'),
            'flow_foreigner_1d': c.get('flow_foreigner_1d'), 'flow_institution_1d': c.get('flow_institution_1d'),
            'flow_foreigner_3d': c.get('flow_foreigner_3d'), 'flow_institution_3d': c.get('flow_institution_3d'),
            'themes': c.get('themes', []),
            'technical': technical,
            'portfolio': daily or None, 'eligible_horizons':c.get('eligible_horizons',[]),
            'data_notes':c.get('data_notes',[]),
            'in_play': c.get('in_play'),
            'news': [{'title': n['title'], 'body': n.get('body','')[:400], 'source': n.get('source',''),
                      'published_at': n.get('published_at','')} for n in c.get('news', [])[:4]],
        }

    def _apply_plan(self, state: dict, plan: dict, now: datetime) -> None:
        cfg = state['config']
        us_open_mode = (cfg.get('strategy') == 'portfolio'
                        and 'US' in (cfg.get('markets') or ['KR'])
                        and portfolio.us_preopen_or_open(now))
        if state['day'] != _day(now) or (_hhmm(now) < max('07:30', cfg['scan_at']) and not us_open_mode):
            state['phase'] = 'idle'
            self._log(state, '장전 판단 결과 폐기: 거래일 또는 한국시간 예약 구간이 달라졌습니다')
            return
        if cfg.get('strategy')=='top5_morning':
            self._apply_top5_plan(state,plan,now)
            return
        if cfg.get('strategy')=='portfolio':
            portfolio.apply_plan(self,state,plan,now)
            return
        state['stamps']['plan'] = now.isoformat()
        state['errors'].pop('scan_giveup', None)
        allowed = {c['symbol']: c for c in (state.get('scan') or {}).get('candidates', [])}
        universe = {t['name']: set(t['symbols']) for t in self._theme_universe(list(allowed.values()))}
        ranked = plan.get('top_themes', [])
        if universe and not ranked and plan.get('market_stance') != 'no_trade':
            self._log(state, '장전 판단 재검토 필요: 매수 계획에 테마 순위가 없습니다')
            state['phase'] = 'scanned'
            return
        if (len(ranked) > 5 or len({t['name'] for t in ranked}) != len(ranked)
                or any(t['name'] not in universe or not t['symbols']
                       or not set(t['symbols']) <= universe[t['name']] for t in ranked)):
            self._log(state, '장전 판단 폐기: 테마 순위 또는 구성종목이 실제 후보와 다릅니다')
            state['phase'] = 'scanned'
            return
        selected = {s for t in ranked for s in t['symbols']}
        stance = plan.get('market_stance', 'normal')
        if stance == 'no_trade':
            state['plan'] = {'summary': plan['summary'], 'market_view': plan.get('market_view', ''),
                             'market_stance': stance, 'no_trade_reason': plan.get('no_trade_reason', ''),
                             'picks': [], 'top_themes': ranked, 'decided_at': now.isoformat()}
            state['phase'] = 'monitoring'
            self._log(state, '장전 판단: 오늘 오전은 매수하지 않습니다 · '
                             + (plan.get('no_trade_reason') or plan['summary'])[:120])
            notify.post(notify.plan_message(state, state['plan'], allowed))
            return
        unknown = [p['symbol'] for p in plan['picks'] if p['symbol'] not in allowed]
        if unknown:
            self._log(state, f'장전 판단 폐기: 후보 밖 종목 {", ".join(unknown[:3])}')
            state['phase'] = 'monitoring'
            return
        picks = []
        for p in plan['picks'][:max(0, cfg['max_positions'] - len(state['positions']))]:
            if ranked and p['symbol'] not in selected:
                self._log(state, f'{p["symbol"]} 제외: 선정한 상위 테마 종목이 아닙니다')
                continue
            tp_ok = cfg['take_profit_min_pct'] <= p['take_profit_pct'] <= cfg['take_profit_max_pct']
            sl_ok = cfg['stop_loss_min_pct'] <= p['stop_loss_pct'] <= cfg['stop_loss_max_pct']
            if not (tp_ok and sl_ok):
                self._log(state, f'{p["symbol"]} 제외: 익절 {p["take_profit_pct"]}% / 손절 {p["stop_loss_pct"]}% 가 범위 밖')
                continue
            p = dict(p)
            p['weight_pct'] = min(p['weight_pct'], cfg['position_pct'])
            p['name'] = allowed[p['symbol']].get('name', '')
            picks.append(p)
        state['plan'] = {'summary': plan['summary'], 'market_view': plan.get('market_view', ''),
                         'market_stance': stance, 'no_trade_reason': plan.get('no_trade_reason', ''),
                         'picks': picks, 'top_themes': ranked, 'decided_at': now.isoformat()}
        state['phase'] = 'planned' if picks else 'monitoring'
        if cfg.get('strategy')=='in_play':
            self._approve_in_play(state,picks,now)
            state['phase']='monitoring'
        self._log(state, f'장전 판단: {len(picks)}종 선정' + (' · ' + ', '.join(p['name'] or p['symbol'] for p in picks) if picks else ' · 오늘은 진입 없음'))
        notify.post(notify.plan_message(state, state['plan'], allowed))

    def _apply_top5_plan(self, state: dict, plan: dict, now: datetime) -> None:
        """A separate, fixed morning cohort; AI ranks, the user's basket policy trades."""
        cfg=state['config']
        if state.get('basket',{}).get('locked'):
            self._log(state,'시나리오2: 진입 시작 후 TOP5 변경 불가')
            return
        if _minutes_since(now,cfg['entry_at'])>=cfg['entry_window_minutes']:
            state['phase']='monitoring'
            self._log(state,'시나리오2: 오전 진입 창 이후 판단 결과 폐기')
            return
        candidates=(state.get('scan') or {}).get('candidates',[])
        allowed={c['symbol']:c for c in candidates}
        symbols=plan.get('ranked_symbols') or []
        source='ai_rank' if symbols else 'scanner_order'
        if not symbols:symbols=list(allowed)[:5]
        if (len(symbols)!=min(5,len(allowed)) or len(set(symbols))!=len(symbols)
                or any(s not in allowed for s in symbols)):
            state['phase']='scanned'
            self._log(state,'시나리오2 판단 재검토: TOP5 순위가 후보·종목 수와 불일치')
            return
        original={p['symbol']:p for p in plan.get('picks',[])}
        picks=[]
        for rank,symbol in enumerate(symbols,1):
            p=original.get(symbol,{})
            picks.append({'symbol':symbol,'name':allowed[symbol].get('name',symbol),
                'basket_rank':rank,'weight_pct':cfg['position_pct'],
                'take_profit_pct':cfg['basket_tp_pct'],'stop_loss_pct':cfg['basket_sl_pct'],
                'thesis':p.get('thesis') or '당일 후보 TOP5 오전 동일 비중 바스켓',
                'counter_case':p.get('counter_case') or '순위는 상승을 보장하지 않음',
                'invalidation':f"손절 -{cfg['basket_sl_pct']}% 또는 {cfg['basket_exit_at']} 오전 청산",
                'max_entry_price':0})
        state['basket']={'day':state['day'],'rank_source':source,'symbols':symbols,
                         'locked':False,'selected_at':now.isoformat()}
        state['plan']={'summary':plan.get('summary','')+' · 시나리오2는 관망 의견과 별도로 TOP5를 매수합니다.',
            'market_view':plan.get('market_view',''),'market_stance':'normal',
            'ai_market_stance':plan.get('market_stance','normal'),'no_trade_reason':plan.get('no_trade_reason',''),
            'top_themes':plan.get('top_themes',[]),'ranked_symbols':symbols,
            'picks':picks,'decided_at':now.isoformat()}
        state['stamps']['plan']=now.isoformat()
        state['phase']='planned' if picks else 'monitoring'
        self._log(state,f'시나리오2 TOP5 확정 ({source}): '+', '.join(symbols)+
                  (f' · 후보 부족: {len(symbols)}/5' if len(symbols)<5 else ''))
        notify.post(notify.plan_message(state,state['plan'],allowed))

    @staticmethod
    def _fresh_quote(quote: dict | None, now: datetime) -> bool:
        if not quote:
            return False
        try:
            bid, ask = float(quote['bid']), float(quote['ask'])
            return (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask
                    and all(0 <= (now - stamp(quote[key])).total_seconds() <= 30
                            for key in ('timestamp', 'book_timestamp')))
        except (KeyError, TypeError, ValueError):
            return False

    def _refresh_quotes(self, state: dict, symbols: list[str]) -> None:
        if not symbols:
            return
        try:
            meta = state.get('symbol_meta') or {}
            instruments = [portfolio.instrument_for(s, meta) for s in symbols]
            quotes = self.broker.quotes(instruments)
            state['quotes'].update(quotes)
            state['errors'].pop('quotes', None)
        except Exception as exc:  # noqa: BLE001
            state['errors']['quotes'] = f'시세 조회 실패: {exc}'

    def _do_entry(self, state: dict, now: datetime, market_filter: str | None = None) -> None:
        cfg = state['config']
        if cfg.get('strategy')=='in_play':
            state['phase']='monitoring'
            return
        plan = state.get('plan') or {}
        pending = [p for p in plan.get('picks', []) if p['symbol'] not in state['positions']
                   and not p.get('_done')
                   and (market_filter is None or portfolio.symbol_market(p['symbol']) == market_filter)]
        if not pending:
            if all(p.get('_done') or p['symbol'] in state['positions'] for p in plan.get('picks', [])):
                state['phase'] = 'monitoring'
            return
        self._refresh_quotes(state, [p['symbol'] for p in pending])
        equity = self.equity(state)
        if cfg.get('strategy')=='top5_morning':
            basket=state['basket']
            basket['locked']=True
            equity=basket.setdefault('budget_equity',equity)
        for p in pending:
            q = state['quotes'].get(p['symbol'])
            if not q or q.get('ask', 0) <= 0:
                p['entry_status']='시세 없음: 진입 창 안에서 재시도'
                continue        # 시세 오면 다음 주기에
            krw = equity * p['weight_pct'] / 100
            ok = self._fill_buy(state, p['symbol'], p['name'], krw, q, p['take_profit_pct'], p['stop_loss_pct'],
                                {'source': 'plan', 'thesis': p['thesis'], 'counter_case': p['counter_case'],
                                 'invalidation': p['invalidation'],
                                 'basket_rank':p.get('basket_rank'),
                                 'horizon':p.get('horizon','short'),
                                 'max_entry_price': p.get('max_entry_price', 0)}, now)
            # Rejected attempts remain eligible inside the entry window. A queued
            # live intent is successful here and must never be submitted twice.
            p['_done'] = bool(ok)
            p['entry_status']='주문 접수/가상 체결' if ok else '매수 보류: 활동 기록 확인'
            if ok:
                state['stamps'].setdefault('entry', now.isoformat())
            if not ok:
                continue
        if all(p.get('_done') for p in plan.get('picks', [])) and not (self.is_live(state) and self.live.pending(state['id'], 'buy')):
            state['phase'] = 'monitoring'
        else:
            state['phase'] = 'entering'

    def _do_monitor(self, state: dict, now: datetime) -> None:
        cfg = state['config']
        held = list(state['positions'])
        watched = [c['symbol'] for c in (state.get('scan') or {}).get('candidates', [])][:15]
        self._refresh_quotes(state, sorted(set(held + watched)))
        learning.observe(state,now)

        # 괄호: 손절을 먼저 본다(보수적). 같은 주기에 둘 다 닿으면 손절.
        for symbol in held:
            pos = state['positions'].get(symbol)
            q = state['quotes'].get(symbol)
            if not pos or not q or pos.get('closing'):
                continue
            if q['bid'] <= pos['sl_price']:
                self._fill_sell(state, symbol, min(q['bid'], pos['sl_price']), f'손절 -{pos["sl_pct"]}%', now)
            elif pos.get('strategy_stop') and q['bid']<=pos['strategy_stop']:
                self._fill_sell(state,symbol,q['bid'],'선택 전략의 가격 구조 무효화',now)
            elif q['bid'] >= pos['tp_price']:
                self._fill_sell(state, symbol, max(q['bid'], pos['tp_price']), f'익절 +{pos["tp_pct"]}%', now)

        # 이벤트 트리거는 진입~마감 판단 사이에서만, AI 예산이 남았을 때만.
        if cfg.get('strategy')=='top5_morning':return
        if not _between(now, cfg['entry_at'], cfg['eod_review_at']) or not self._tradable(state, now):
            return
        # Two bounded price-led reviews, independent of the news event budget.
        slot = 'opening' if '09:15' <= _hhmm(now) < '09:30' else ('midday' if '12:00' <= _hhmm(now) < '12:15' else None)
        reviewed = state['events'].setdefault('price_reviews', [])
        if slot and slot not in reviewed and self._ai_allowed(state, now, anchor=True):
            symbols = list(dict.fromkeys(list(state['positions']) + watched[:5]))
            if symbols:
                reviewed.append(slot)
                basket = [{'symbol': s, 'quote': state['quotes'].get(s),
                           'technical': scanner.chart_context(self.broker, s, now),
                           'candidate': next((c for c in (state.get('scan') or {}).get('candidates', []) if c['symbol']==s), None)}
                          for s in symbols]
                self._fire_event(state, now, slot, symbols[0], {'basket': basket}, counts=False)
                return
        if cfg.get('strategy')=='in_play':
            self._monitor_in_play(state,now)
            if cfg['news_trigger'] and self._ai_allowed(state,now):
                current=[c['symbol'] for c in (state.get('scan') or {}).get('candidates',[])][:8]
                self._check_news(state,now,list(state['positions'])+current)
            return
        if not self._ai_allowed(state, now):
            return
        if cfg.get('strategy','ai')!='ai' and self._check_setup(state,now,watched):
            return
        self._check_surge(state, now, watched)
        if state['ai']['pending']:
            return
        if cfg['news_trigger']:
            self._check_news(state, now, list(state['positions']) + watched[:8])

    def _check_setup(self,state,now,watched):
        events=state['events']; last=events.get('setup_checked_at')
        if last and (now-stamp(last)).total_seconds()<60:return False
        events['setup_checked_at']=now.isoformat()
        cursor=events.get('setup_cursor',0)%max(1,len(watched))
        ordered=watched[cursor:]+watched[:cursor]; events['setup_cursor']=cursor+3
        checks=state.setdefault('strategy_checks',{})
        for symbol in ordered[:3]:
            if symbol in state['positions']:continue
            quote=state['quotes'].get(symbol)
            if not self._fresh_quote(quote,now):continue
            chart=scanner.chart_context(self.broker,symbol,now)
            gate=trade_setups.evaluate(state['config']['strategy'],chart,quote,now)
            checks[symbol]={'at':now.isoformat(),**gate}
            bars=((chart.get('intraday') or {}).get('frames',{}).get('5m',{}).get('recent_bars') or [])
            key=symbol+':'+str(bars[-1]['timestamp'] if bars else None)
            seen=events.setdefault('setup_seen',[])
            if gate['allowed'] and key not in seen:
                events['setup_seen']=(seen+[key])[-100:]
                self._fire_event(state,now,'setup',symbol,{'strategy':state['config']['strategy'],'evidence':gate})
                return True
        return False

    def _in_play_state(self,state,now):
        runtime=state.setdefault('in_play',{})
        if runtime.get('day')!=_day(now):
            runtime.clear()
            runtime.update(day=_day(now),approvals={},checks={},counts={},reviews=0)
        return runtime

    def _approve_in_play(self,state,decisions,now,issued_at=None):
        runtime=self._in_play_state(state,now)
        issued_at=issued_at or now
        if _day(issued_at)!=_day(now) or state['status']!='running':return
        expires=issued_at+timedelta(minutes=state['config']['approval_minutes'])
        if expires<=now:return
        candidates={c['symbol'] for c in (state.get('scan') or {}).get('candidates',[])}
        for d in decisions:
            symbol=d['symbol']
            if d.get('action','buy')!='buy':
                runtime['approvals'].pop(symbol,None)
                continue
            if symbol not in candidates or symbol in state['positions']:continue
            cfg=state['config']
            if (not d.get('max_entry_price') or
                not cfg['take_profit_min_pct']<=d.get('take_profit_pct',0)<=cfg['take_profit_max_pct'] or
                not cfg['stop_loss_min_pct']<=d.get('stop_loss_pct',0)<=cfg['stop_loss_max_pct']):
                runtime['checks'][symbol]={'at':now.isoformat(),'reason':'AI 승인 가격·익절·손절 조건 부족'}
                continue
            runtime['approvals'][symbol]={**d,'issued_at':issued_at.isoformat(),
                'expires_at':expires.isoformat(),'used':False,
                'decision_id':learning.ensure(state).get('last_decision_id')}

    def _monitor_in_play(self,state,now):
        runtime=self._in_play_state(state,now)
        cfg=state['config']
        candidates=(state.get('scan') or {}).get('candidates',[])
        result=self.in_play_feed.poll(state['id'],now,cfg,candidates)
        if result:
            if result.get('error'):
                state['errors']['in_play']=result['error']
            elif result.get('day')==_day(now):
                state['errors'].pop('in_play',None)
                runtime['data_at']=result['at'];runtime['data_errors']=result.get('errors',[])
                runtime['market_context']=result.get('market_context',{'errors':['unavailable']})
                state.setdefault('scan',None)
                if state['scan'] is None:state['scan']={'candidates':[],'note':'장중 주도주 수집'}
                state['scan']['candidates']=result['candidates']
                candidates=result['candidates']
                runtime['counts']['candidates']=len(candidates)
        if not _between(now,cfg['entry_at'],min('15:10',cfg['eod_review_at'])):return
        for c in candidates:
            symbol=c['symbol'];approval=runtime['approvals'].get(symbol)
            if symbol in state['positions']:continue
            if not approval or approval.get('used') or stamp(approval['expires_at'])<=now:
                runtime['checks'][symbol]={'at':now.isoformat(),'reason':'AI 조건부 승인 대기·만료'}
                continue
            chart={**(c.get('technical') or {}),'in_play':c.get('in_play',{})}
            quote=state['quotes'].get(symbol)
            if not self._fresh_quote(quote,now):quote=c.get('quote')
            if not self._fresh_quote(quote,now):
                runtime['checks'][symbol]={'at':now.isoformat(),'reason':'최신 시세·호가 대기'}
                continue
            gate=trade_setups.evaluate('in_play',chart,quote,now)
            runtime['checks'][symbol]={'at':now.isoformat(),**gate}
            if not gate['allowed']:continue
            runtime['counts']['signals']=runtime['counts'].get('signals',0)+1
            ok=self._fill_buy(state,symbol,c.get('name',symbol),self.equity(state)*cfg['position_pct']/100,
                quote,approval['take_profit_pct'],approval['stop_loss_pct'],
                {'source':'in_play','thesis':approval.get('reason',approval.get('thesis','AI 조건부 승인')),
                 'max_entry_price':approval['max_entry_price'],'_chart':chart,'decision_id':approval.get('decision_id')},now)
            if ok:
                approval['used']=True
                runtime['counts']['submitted']=runtime['counts'].get('submitted',0)+1
                runtime['checks'][symbol]['reason']='신호 확인·매수 실행'
            else:
                runtime['checks'][symbol]['reason']='실행 검사 보류: 로그에서 비용·가격·한도 확인'
        # Separate bounded approval budget. No dependency on the news event quota.
        last=runtime.get('review_at')
        if (state['ai']['pending'] or self._ai_busy() or not candidates or
            runtime['reviews']>=cfg['max_price_reviews_per_day'] or
            last and (now-stamp(last)).total_seconds()<cfg['approval_minutes']*60):return
        runtime['review_at']=now.isoformat();runtime['reviews']+=1
        basket=[{'symbol':c['symbol'],'candidate':self._candidate_view(c),
                 'quote':c.get('quote'),'technical':c.get('technical')} for c in candidates]
        snapshot={'at':now.isoformat(),'trigger':'conditional_approval','symbols':[c['symbol'] for c in candidates],
                  'positions':list(state['positions'].values()),'cash':state['cash'],
                  'open_slots':max(0,cfg['max_positions']-len(state['positions'])),
                  'take_profit_min_pct':cfg['take_profit_min_pct'],'take_profit_max_pct':cfg['take_profit_max_pct'],
                  'stop_loss_min_pct':cfg['stop_loss_min_pct'],'stop_loss_max_pct':cfg['stop_loss_max_pct'],
                  'approval_minutes':cfg['approval_minutes'],'payload':{'basket':basket},
                  'market_context':runtime.get('market_context',{'errors':['unavailable']}),
                  'held_evidence':{s:{'quote':state['quotes'].get(s),
                     'technical':next((c.get('technical') for c in candidates if c['symbol']==s),None)} for s in state['positions']},
                  'execution_mode':'buy means conditional permission, not immediate order; positive max_entry_price required'}
        issued=now
        approved_symbols=set(snapshot['symbols']) | set(state['positions'])
        def done(latest,plan,when):
            if latest['config'].get('strategy')=='in_play' and _day(issued)==_day(when):
                scoped={**plan,'decisions':[d for d in plan['decisions'] if d['symbol'] in approved_symbols]}
                self._apply_event(latest,scoped,when,issued_at=issued)
        self._ai_call(state,'주도주 조건부 승인',self.ai.judge_event,snapshot,done,counts=False)

    def _check_surge(self, state: dict, now: datetime, watched: list[str]) -> None:
        cfg = state['config']
        ref = state['events']['surge_ref']
        for symbol in watched:
            q = state['quotes'].get(symbol)
            if not self._fresh_quote(q,now) or symbol in state['positions'] or symbol in state['events']['surged']:
                continue
            dated=state['events'].setdefault('surge_ref_day',{})
            if dated.get(symbol)!=_day(now):
                ref.pop(symbol,None);dated[symbol]=_day(now)
            base = ref.setdefault(symbol, q['price'])
            move = (q['price'] / base - 1) * 100 if base else 0
            if move >= cfg['surge_trigger_pct'] and len(state['positions']) < cfg['max_positions']:
                state['events']['surged'].append(symbol)
                self._fire_event(state, now, 'surge', symbol, {'move_pct': round(move, 2), 'from': base, 'now': q['price']})
                return

    def _check_news(self, state: dict, now: datetime, symbols: list[str]) -> None:
        seen = set(state['events']['seen_news'])
        last = state['events'].get('news_checked_at')
        if last and (now - stamp(last)).total_seconds() < 600:
            return
        state['events']['news_checked_at'] = now.isoformat()
        candidates = {c['symbol']:c for c in (state.get('scan') or {}).get('candidates', [])}
        symbols = list(dict.fromkeys(symbols))
        cursor = state['events'].get('news_cursor', 0) % max(1, len(symbols))
        symbols = symbols[cursor:] + symbols[:cursor]
        titles = state['events'].setdefault('news_titles', [])
        for index, symbol in enumerate(symbols[:10]):
            fresh = [n for n in krnews.stock_news(symbol, 3, ttl=300) if n['id'] not in seen
                     and n['published_at'] and 0 <= (now - stamp(n['published_at'])).total_seconds() < 3 * 3600]
            for n in fresh:
                state['events']['seen_news'].append(n['id'])
            state['events']['seen_news'] = state['events']['seen_news'][-500:]
            name = candidates.get(symbol, state['positions'].get(symbol, {})).get('name', '')
            fresh = [n for n in fresh if news_quality.relevant(n, name, symbol)
                     and not news_quality.duplicate(n, titles)]
            if fresh:
                titles.extend(n['title'] for n in fresh)
                state['events']['news_titles'] = titles[-200:]
                state['events']['news_cursor'] = cursor + index + 1
                self._fire_event(state, now, 'news', symbol, {'articles': fresh[:2]})
                return

    def _fire_event(self, state: dict, now: datetime, kind: str, symbol: str, payload: dict, counts=True) -> None:
        cfg = state['config']
        cand = next((c for c in (state.get('scan') or {}).get('candidates', []) if c['symbol'] == symbol), None)
        pos = state['positions'].get(symbol)
        charts=scanner.chart_batch(self.broker,[symbol]+list(state['positions']),now)
        chart=charts[symbol]
        held_evidence={s:{'quote':state['quotes'].get(s),
                          'technical':charts[s]}
                       for s in state['positions']}
        snapshot = {
            'at': now.isoformat(), 'trigger': kind, 'symbol': symbol, 'payload': payload,
            'market_context': self.broker.market_context() if hasattr(self.broker, 'market_context') else {'errors':['unsupported']},
            'open_slots': max(0, cfg['max_positions'] - len(state['positions'])),
            'regular_session': cfg['entry_at'] <= _hhmm(now) < cfg['eod_review_at'],
            'position_pct': cfg['position_pct'],
            'take_profit_min_pct': cfg['take_profit_min_pct'], 'take_profit_max_pct': cfg['take_profit_max_pct'],
            'stop_loss_min_pct': cfg['stop_loss_min_pct'], 'stop_loss_max_pct': cfg['stop_loss_max_pct'],
            'symbols': sorted(set(list(state['positions']) + [symbol] + [r['symbol'] for r in payload.get('basket', [])])),
            'positions': list(state['positions'].values()),
            'position': {k: v for k, v in pos.items()} | {'unrealized': self._unrealized(state, pos)} if pos else None,
            'candidate': self._candidate_view(cand) if cand else None,
            'quote': state['quotes'].get(symbol),
            'technical': chart,
            'held_evidence':held_evidence,
        }
        self._log(state, f'이벤트 감지({kind}): {symbol}')
        name = (cand or {}).get('name') or (pos or {}).get('name') or ''
        def done(latest, plan, when, _kind=kind, _symbol=symbol, _name=name, _payload=payload):
            self._apply_event(latest, plan, when, issued_at=now)
            notify.post(notify.event_message(_kind, _symbol, _name, _payload, plan))
        self._ai_call(state, f'이벤트 판단({kind})', self.ai.judge_event, snapshot, done, counts=counts)

    def _apply_event(self, state: dict, plan: dict, now: datetime, issued_at=None) -> None:
        cfg = state['config']
        if not self._tradable(state, now):
            self._log(state, '이벤트 판단 결과 적용 취소: 장 시간이 아닙니다')
            return
        cands = {c['symbol']: c for c in (state.get('scan') or {}).get('candidates', [])}
        if cfg.get('strategy')=='in_play':
            self._approve_in_play(state,plan['decisions'],now,issued_at)
        self._refresh_quotes(state, sorted({d['symbol'] for d in plan['decisions']
                                            if d['symbol'] in cands or d['symbol'] in state['positions']}))
        for d in plan['decisions']:
            symbol = d['symbol']
            if d['action'] == 'close' and symbol in state['positions']:
                q = state['quotes'].get(symbol)
                self._fill_sell(state, symbol, q['bid'] if q else state['positions'][symbol]['entry_price'],
                                f'AI 청산: {d["reason"][:60]}', now)
            elif d['action'] == 'buy':
                if cfg.get('strategy')=='in_play':continue
                if not _between(now, cfg['entry_at'], cfg['eod_review_at']):
                    self._log(state, f'{symbol} 이벤트 매수 취소: 신규 진입 시간 종료')
                    continue
                if symbol in state['positions'] or symbol not in cands:
                    continue
                if len(state['positions']) >= cfg['max_positions']:
                    continue
                tp, sl = d['take_profit_pct'], d['stop_loss_pct']
                if not (cfg['take_profit_min_pct'] <= tp <= cfg['take_profit_max_pct']
                        and cfg['stop_loss_min_pct'] <= sl <= cfg['stop_loss_max_pct']):
                    self._log(state, f'{symbol} 이벤트 매수 제외: 익절/손절 범위 밖')
                    continue
                q = state['quotes'].get(symbol)
                if not q:
                    continue
                self._fill_buy(state, symbol, cands[symbol].get('name', ''), self.equity(state) * cfg['position_pct'] / 100,
                               q, tp, sl, {'source': 'event', 'thesis': d['reason'],
                                          'max_entry_price': d.get('max_entry_price', 0)}, now)

    def _today_change_pct(self, state: dict, cand: dict) -> float | None:
        """오늘 얼마나 눌렸나. 기준은 스캔이 받아 둔 전일 종가."""
        quote = state['quotes'].get(cand['symbol'])
        base = cand.get('last_price')
        if not quote or not base:
            return None
        return round((quote['bid'] / base - 1) * 100, 2)

    def _do_eod_review(self, state: dict, now: datetime) -> None:
        cfg = state['config']
        retry = state.setdefault('eod_retry', {'day': state['day'], 'attempts': 0, 'last': None})
        if retry['day'] != state['day']:
            retry.update(day=state['day'], attempts=0, last=None)
        if retry['attempts'] >= 3 or (retry['last'] and (now - stamp(retry['last'])).total_seconds() < 60):
            return
        # 판단 직전에 시세를 새로 받는다. 오늘 얼마나 눌렸는지가 마감 매수의 근거이므로
        # 장중에 감시가 한 번도 안 돌았더라도(아무것도 안 산 날) 여기서는 반드시 최신이어야 한다.
        watch = list(state['positions'])
        if cfg['close_entry']:
            watch += [c['symbol'] for c in (state.get('scan') or {}).get('candidates', [])[:15]]
        self._refresh_quotes(state, sorted(set(watch)))
        if not self._ai_allowed(state, now, anchor=True):
            self._log(state, '마감 판단 대기: 이전 판단 완료 후 정리 시각 전까지 다시 시도합니다.')
            return
        us_events = []
        try:
            us_events = sorted(self.store.all('events'), key=lambda e: e['scheduled_at'])[-10:]
        except Exception:  # noqa: BLE001
            pass
        # 마감 무렵 신규 매수용 후보: 오늘 얼마나 눌렸는지를 붙여 넘긴다.
        open_slots = 0
        candidates = []
        if cfg['close_entry']:
            open_slots = max(0, min(cfg['close_entry_max'], cfg['max_positions'] - len(state['positions'])))
            if open_slots:
                for c in (state.get('scan') or {}).get('candidates', [])[:15]:
                    if c['symbol'] in state['positions']:
                        continue
                    view = self._candidate_view(c)
                    view['today_change_pct'] = self._today_change_pct(state, c)
                    view['current'] = (state['quotes'].get(c['symbol']) or {}).get('bid')
                    candidates.append(view)
        rebound_evidence = {}
        for symbol in sorted(set(state['positions']) | {c['symbol'] for c in candidates}):
            try:
                articles = [n for n in krnews.stock_news(symbol, 5, ttl=300)
                            if n.get('published_at')
                            and 0 <= (now - stamp(n['published_at'])).total_seconds() <= 86400]
                news_error = ''
            except Exception:
                articles, news_error = [], 'news_unavailable'
            rebound_evidence[symbol] = {
                'news': articles, 'news_error': news_error,
                'technical': scanner.chart_context(self.broker, symbol, now),
                'note': '기사 없음은 가치 훼손 없음의 증거가 아닙니다.',
            }
        snapshot = {
            'at': now.isoformat(), 'force_close_at': cfg['force_close_at'],
            'market_context': self.broker.market_context() if hasattr(self.broker,'market_context') else {'errors':['unsupported']},
            'rebound_evidence': rebound_evidence,
            'market_stance_this_morning': (state.get('plan') or {}).get('market_stance', ''),
            'positions': [dict(p) | {'current': (state['quotes'].get(p['symbol']) or {}).get('bid'),
                                     'unrealized': self._unrealized(state, p),
                                     'unrealized_pct': self._unrealized(state, p) / p['cost'] * 100 if p['cost'] else 0}
                          for p in state['positions'].values()],
            'open_slots': open_slots, 'cash': state['cash'], 'equity': self.equity(state),
            'position_pct': cfg['position_pct'],
            'take_profit_min_pct': cfg['take_profit_min_pct'], 'take_profit_max_pct': cfg['take_profit_max_pct'],
            'stop_loss_min_pct': cfg['stop_loss_min_pct'], 'stop_loss_max_pct': cfg['stop_loss_max_pct'],
            'candidates': candidates,
            'lessons': (state.get('tuning') or {}).get('lessons', []),
            'us_events': [{'title': e['title'], 'scheduled_at': e['scheduled_at'], 'impact': e['impact']} for e in us_events],
        }
        retry['attempts'] += 1
        retry['last'] = now.isoformat()
        state['phase'] = 'eod_reviewed'
        state['stamps']['eod'] = now.isoformat()
        self._ai_call(state, '마감 판단', self.ai.judge_eod, snapshot, self._apply_eod,
                      fail_phase='monitoring', counts=False)

    def _apply_eod(self, state: dict, plan: dict, now: datetime) -> None:
        cfg = state['config']
        if state['day'] != _day(now):
            self._log(state, '마감 판단 결과 폐기: 거래일 변경')
            return
        keep = {d['symbol'] for d in plan['decisions'] if d['action'] == 'hold_overnight'}
        for symbol, pos in state['positions'].items():
            pos['hold_overnight'] = symbol in keep
        reasons = {d['symbol']: d['reason'] for d in plan['decisions']}
        self._log(state, '마감 판단: ' + (', '.join(f'{state["positions"][s]["name"]} 익일 보유' for s in keep if s in state['positions']) or '전부 정리'))
        for s in keep:
            if s in state['positions']:
                self._log(state, f'  {s}: {reasons.get(s, "")[:80]}')

        # 마감 무렵 신규 매수. 장전에 "오늘은 안 산다"고 했어도 이 경로는 살아 있다.
        bought = []
        if plan.get('buys') and not self._tradable(state, now):
            self._log(state, '마감 매수 취소: 판단이 끝난 사이 장이 닫혔습니다')
        elif plan.get('buys') and not _between(now, cfg['eod_review_at'], cfg['force_close_at']):
            self._log(state, '마감 매수 취소: 강제 정리 시각이 지났습니다')
        elif cfg['close_entry'] and plan.get('buys'):
            cands = {c['symbol']: c for c in (state.get('scan') or {}).get('candidates', [])}
            self._refresh_quotes(state, [b['symbol'] for b in plan['buys'] if b['symbol'] in cands])
            slots = max(0, min(cfg['close_entry_max'], cfg['max_positions'] - len(state['positions'])))
            for b in plan['buys']:
                if slots <= 0:
                    break
                symbol = b['symbol']
                if symbol not in cands or symbol in state['positions']:
                    self._log(state, f'{symbol} 마감 매수 제외: 후보 목록 밖이거나 이미 보유')
                    continue
                tp, sl = b['take_profit_pct'], b['stop_loss_pct']
                if not (cfg['take_profit_min_pct'] <= tp <= cfg['take_profit_max_pct']
                        and cfg['stop_loss_min_pct'] <= sl <= cfg['stop_loss_max_pct']):
                    self._log(state, f'{symbol} 마감 매수 제외: 익절 {tp}% / 손절 {sl}% 가 범위 밖')
                    continue
                quote = state['quotes'].get(symbol)
                if not quote or quote.get('ask', 0) <= 0:
                    self._log(state, f'{symbol} 마감 매수 제외: 시세 없음')
                    continue
                weight = min(b['weight_pct'], cfg['position_pct'])
                ok = self._fill_buy(state, symbol, cands[symbol].get('name', ''),
                                    self.equity(state) * weight / 100, quote, tp, sl,
                                    {'source': 'close', 'thesis': b['reason'], 'hold_overnight': True,
                                     'max_entry_price': b.get('max_entry_price', 0)}, now)
                if ok:
                    slots -= 1
                    bought.append(symbol)
                    if symbol in state['positions']:          # 모의는 즉시 체결
                        state['positions'][symbol]['hold_overnight'] = True
            if bought:
                state['stamps']['close_entry'] = now.isoformat()
                self._log(state, '마감 매수(익일 보유): ' + ', '.join(
                    (cands[b].get('name') or b) for b in bought))
        notify.post(notify.eod_message(plan, state['positions'], bought))

    # ── 마감 복기와 자동 조정 ─────────────────────────────────────────────────
    def _maybe_review(self, state: dict, now: datetime, day: str) -> None:
        """장이 끝난 뒤 한 번. 오늘 왜 됐는지/안 됐는지 보고 내일 숫자를 조정한다."""
        cfg = state['config']
        tuning = state['tuning']
        if state['phase'] != 'closed' or tuning.get('review_day') == day:
            return
        if tuning.get('review_tries', 0) >= 3:
            return
        last = tuning.get('review_last')
        if last:
            try:
                if (now - stamp(last)).total_seconds() < cfg['retry_minutes'] * 60:
                    return
            except ValueError:
                pass
        if not self._ai_allowed(state, now, anchor=True):
            return
        tuning['review_tries'] = tuning.get('review_tries', 0) + 1
        tuning['review_last'] = now.isoformat()
        snapshot = tuning.get('pending_snapshot')
        self._do_review(state, now, day, snapshot=snapshot if (snapshot or {}).get('day') == day else None)

    def _review_now(self, state: dict, now: datetime) -> None:
        """사람이 누른 복기. 휴장일이든 운용 정지든 상관없이 실행한다.

        거래일이 넘어가면 그날의 계획·스캔은 지워지므로, 마감에 만들어 둔 스냅샷을 쓴다.
        """
        tuning = state['tuning']
        snapshot = tuning.get('pending_snapshot')
        day = (snapshot or {}).get('day') or state.get('day') or ''
        if not day:
            raise ValueError('복기할 거래일 기록이 없습니다. 하루 운용한 뒤에 눌러 주세요.')
        if state['ai'].get('pending'):
            raise ValueError('이전 AI 판단이 진행 중입니다. 끝난 뒤에 다시 눌러 주세요.')
        if not self._ai_allowed(state, now, anchor=True):
            raise ValueError('지금은 AI 판단을 띄울 수 없습니다. 잠시 뒤 다시 눌러 주세요.')
        tuning['review_tries'] = 0
        tuning['review_last'] = now.isoformat()
        self._log(state, f'{day} 마감 복기 요청 (수동)')
        self._do_review(state, now, day, snapshot=snapshot)

    def _do_review(self, state: dict, now: datetime, day: str, snapshot: dict | None = None) -> None:
        snapshot = snapshot or self._review_snapshot(state, day, now)
        self._ai_call(state, '마감 복기', self.ai.review_day, snapshot, self._apply_review, counts=False)

    def _review_snapshot(self, state: dict, day: str, now: datetime) -> dict:
        cfg = state['config']
        plan = state.get('plan') or {}
        picked = {p['symbol'] for p in plan.get('picks', [])}
        trades = [t for t in state['trades'] if t.get('at') and _day(stamp(t['at']))==day]
        cands = (state.get('scan') or {}).get('candidates', [])
        not_picked = []
        for c in cands:
            if c['symbol'] in picked:
                continue
            not_picked.append({'symbol': c['symbol'], 'name': c.get('name', ''), 'score': c.get('score'),
                               'prev_change_pct': c.get('prev_change_pct'),
                               'today_change_pct': self._today_change_pct(state, c),
                               'flow_foreigner_1d': c.get('flow_foreigner_1d'),
                               'flow_institution_1d': c.get('flow_institution_1d'),
                               'news_count': c.get('news_count', len(c.get('news', [])))})
        recent = [dict(v, day=k) for k, v in sorted(state.get('days', {}).items())[-10:]]
        snapshot = {
            'day': day, 'at': now.isoformat(),
            'intraday_evidence': {symbol: scanner.chart_context(self.broker, symbol, now)
                                 for symbol in sorted({t['symbol'] for t in trades} | set(state['positions']))},
            'plan': {'market_stance': plan.get('market_stance', ''), 'summary': plan.get('summary', ''),
                     'market_view': plan.get('market_view', ''),
                     'no_trade_reason': plan.get('no_trade_reason', ''),
                     'picks': [{k: v for k, v in p.items() if not k.startswith('_')} for p in plan.get('picks', [])]},
            'trades': [{k: v for k, v in t.items() if k != 'order_id'} for t in trades],
            'held_overnight': [{'symbol': p['symbol'], 'name': p.get('name', ''), 'qty': p['qty'],
                                'entry_price': p['entry_price'], 'source': p.get('source', '')}
                               for p in state['positions'].values() if p.get('hold_overnight')],
            'realized_pnl_today': sum(t.get('pnl', 0.0) for t in trades if t['side'] == 'sell'),
            'equity': self.equity(state), 'cash': state['cash'],
            'not_picked': not_picked[:10],
            'rejected': (state.get('scan') or {}).get('rejected', [])[:15],
            'days': recent,
            'lessons': state['tuning'].get('lessons', []),
            'config': {k: cfg[k] for k in list(TUNABLE) + list(TUNABLE_BOOL) if k in cfg},
            'tunable': [{'key': k, 'min': lo, 'max': hi,
                         'max_daily_step': (round(cfg[k] * 0.5) if k == 'min_trading_amount_krw' else step)}
                        for k, (lo, hi, step) in TUNABLE.items()]
                       + [{'key': k, 'type': 'boolean (1 or 0)'} for k in TUNABLE_BOOL],
            'auto_tune': cfg['auto_tune'],
            'learning':learning.context(self.store,state),
            'candidate_observations':learning.ensure(state)['observations'],
            'entry_checks':learning.ensure(state).get('entry_checks',{}),
            'note': ('조정은 하루 한도 안에서만 반영되고, 검증에 실패하면 전부 취소됩니다. '
                     '급등·테마 추격은 백테스트에서 무작위와 구분되지 않았습니다.'),
        }
        return snapshot

    def _apply_review(self, state: dict, review: dict, now: datetime) -> None:
        cfg = state['config']
        tuning = state['tuning']
        day = (tuning.get('pending_snapshot') or {}).get('day') or state['day']
        tuning['review_day'] = day
        state['stamps']['review'] = now.isoformat()
        lessons = [str(l).strip()[:200] for l in review.get('lessons', []) if str(l).strip()][:5]
        if lessons:
            tuning['lessons'] = lessons
        learning.propose(self.store,state,review,now)
        applied: list[dict] = []
        skipped: list[dict] = []
        if cfg['auto_tune']:
            applied, skipped = self._apply_tuning(state, review.get('adjustments', []))
        elif review.get('adjustments'):
            skipped = [{'key': a['key'], 'reason': '자동 조정이 꺼져 있습니다(제안만 기록)'}
                       for a in review['adjustments']]
        record = {'at': now.isoformat(), 'day': day, 'summary': review.get('summary', ''),
                  'diagnosis': review.get('diagnosis', ''), 'lessons': lessons,
                  'proposed': review.get('adjustments', []), 'applied': applied, 'skipped': skipped}
        tuning['last_review'] = record
        tuning['history'] = (tuning.get('history', []) + [record])[-60:]
        self._log(state, f'마감 복기: {review.get("summary", "")[:120]}')
        for a in applied:
            self._log(state, f'  조정 {a["key"]}: {a["from"]} → {a["to"]} · {a["reason"][:60]}')
        for l in lessons:
            self._log(state, f'  교훈: {l[:100]}')
        notify.post(notify.review_message(record))

    def _apply_tuning(self, state: dict, adjustments: list[dict]) -> tuple[list[dict], list[dict]]:
        """AI 제안을 허용 항목·하루 변동 한도로 자른 뒤, 전체를 다시 검증해서 반영한다.

        하나라도 검증에 실패하면 전부 취소한다. 반쯤 바뀐 설정이 제일 위험하다.
        """
        cfg = state['config']
        draft = dict(cfg)
        applied: list[dict] = []
        skipped: list[dict] = []
        for a in (adjustments or [])[:6]:
            key = str(a.get('key', ''))
            reason = str(a.get('reason', ''))[:300]
            try:
                value = float(a.get('value'))
            except (TypeError, ValueError):
                skipped.append({'key': key, 'reason': '숫자가 아닙니다'})
                continue
            if key in TUNABLE_BOOL:
                new: float | bool = bool(round(value))
            elif key in TUNABLE:
                lo, hi, step = TUNABLE[key]
                current = float(draft[key])
                if key == 'min_trading_amount_krw':
                    step = max(current * 0.5, 5e8)
                new = max(lo, min(hi, value))
                if abs(new - current) > step:
                    new = current + step if new > current else current - step
                    reason = (reason + ' · 하루 변동 한도까지만 반영').strip()
                if key == 'max_positions':
                    new = float(round(new))
            else:
                skipped.append({'key': key, 'reason': '조정할 수 없는 항목입니다'})
                continue
            if new == draft[key]:
                skipped.append({'key': key, 'reason': '변화 없음'})
                continue
            draft[key] = new
            applied.append({'key': key, 'from': cfg[key], 'to': new, 'reason': reason})
        if not applied:
            return [], skipped
        # 기본값이 새 범위를 벗어나지 않게 맞춘다
        draft['take_profit_default_pct'] = min(max(draft['take_profit_default_pct'],
                                                   draft['take_profit_min_pct']), draft['take_profit_max_pct'])
        draft['stop_loss_default_pct'] = min(max(draft['stop_loss_default_pct'],
                                                 draft['stop_loss_min_pct']), draft['stop_loss_max_pct'])
        try:
            state['config'] = DayTradeConfig.model_validate(draft).model_dump(mode='json')
        except Exception as exc:  # noqa: BLE001
            return [], skipped + [{'key': '전체', 'reason': f'검증 실패로 조정을 전부 취소했습니다: {exc}'}]
        return applied, skipped

    def export(self, id: str) -> dict:
        state = self.view(id)
        decisions=sorted([r for r in self.store.all('decision_evidence') if r['account_id']==id],key=lambda r:r['at'])
        results=[]
        for d in decisions:
            try:results.append(self.store.get('decision_results',d['id']))
            except KeyError:pass
        return {'account':state,'decision_evidence':decisions,'decision_results':results,
                'exported_at':utcnow().isoformat()}
