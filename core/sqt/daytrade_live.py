"""당일 단타 실거래 실행. 토스 실계좌에 지정가 주문을 내고, 확인된 체결만 장부에 반영한다.

원칙 (실제 돈이다)
    - 주문은 절대 자동으로 다시 보내지 않는다. 응답이 불명확하면 UNKNOWN 으로 남기고 사람이 확인한다.
    - 현금·보유량은 토스가 확인해 준 체결만 반영한다. 모의 모드의 가상 체결 코드는 여기서 쓰지 않는다.
    - 지정가만 쓴다. 매수는 매도호가 + 여유, 매도는 매수호가 - 여유. 호가 단위로 맞춘다.
      제한 시간 안에 안 채워지면 취소한다. 부분 체결은 그대로 장부에 남는다.
    - 익절·손절은 프로그램이 시세를 보고 매도 주문을 낸다. NAS 가 꺼지면 이 괄호도 멈춘다.
    - 상한: LIVE_CAPITAL_KRW(이 계좌가 쓸 수 있는 돈), LIVE_MAX_ORDER_KRW(주문 1건).
    - 시작할 때 토스 보유량이 프로그램 장부와 다르면 시작하지 않는다. 다른 계좌의 장기 보유분과
      섞이지 않도록 단타 전용 계좌를 쓴다.

장부
    daytrade_orders : 주문 의도(intent) 하나가 주문 1건. 상태 DRAFT → SUBMITTING → OPEN → DONE,
                      곁길로 CANCELING / UNKNOWN / BLOCKED.
    포지션은 첫 체결에서 만들어지고, 매도 체결이 수량을 다 지우면 없어진다.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

from . import notify
from .models import stamp
from .toss_orders import OrderUncertain, TossError, TossOrders, number

log = logging.getLogger('bqt.daytrade.live')

ORDER_KIND = 'daytrade_orders'
ACTIVE = {'DRAFT', 'SUBMITTING', 'OPEN', 'CANCELING', 'UNKNOWN'}
BROKER_OPEN = {'PENDING', 'PARTIAL_FILLED', 'PENDING_CANCEL'}
BROKER_DONE = {'FILLED', 'CANCELED', 'REJECTED'}
MAX_SELL_ATTEMPTS = 4


# ── 호가 단위 ────────────────────────────────────────────────────────────────
def krx_tick(price: float) -> int:
    """KRX 호가 단위 (2023-01 이후 코스피·코스닥 공통)."""
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000


def to_tick(price: float, up: bool) -> int:
    """호가 단위로 올림(up) 또는 내림. 매수는 올려서, 매도는 내려서 체결 확률을 높인다."""
    tick = krx_tick(price)
    units = price / tick
    n = int(units) + (1 if up and units > int(units) else 0)
    return max(tick, n * tick)


def capital_cap() -> float:
    return float(number(os.getenv('LIVE_CAPITAL_KRW', '1000000'), True))


def order_cap() -> float:
    return float(number(os.getenv('LIVE_MAX_ORDER_KRW', '1000000'), True))


class LiveExecution:
    """DayTradeService 가 mode == 'live' 인 계좌에서 매수·매도 대신 부르는 실행기."""

    def __init__(self, store, orders: TossOrders):
        self.store = store
        self.orders = orders

    # ── 상태 ────────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return bool(self.orders.enabled and self.orders.account_seq.isdigit())

    def status(self) -> dict[str, Any]:
        return {'enabled': self.orders.enabled, 'account_seq_set': self.orders.account_seq.isdigit(),
                # 주문이 나갈 계좌. 계좌번호가 아니라 토스가 주는 내부 식별자다(화면에 보여도 된다).
                'account_seq': self.orders.account_seq,
                'capital_cap_krw': capital_cap(), 'order_cap_krw': order_cap(),
                'available': self.enabled}

    def pending(self, account_id: str, side: str | None = None) -> list[dict]:
        return [o for o in self.store.all(ORDER_KIND)
                if o['account_id'] == account_id and o['status'] in ACTIVE and (side is None or o['side'] == side)]

    def journal(self, account_id: str, limit: int = 60) -> list[dict]:
        rows = [o for o in self.store.all(ORDER_KIND) if o['account_id'] == account_id]
        rows.sort(key=lambda o: o['created_at'])
        return rows[-limit:]

    # ── 사전 검사 ───────────────────────────────────────────────────────────
    def check_before_create(self, initial_cash: float) -> str:
        if not self.orders.enabled:
            raise ValueError('NAS .env 에 ENABLE_LIVE_TRADING=true 를 넣고 컨테이너를 다시 만들어야 실거래 계좌를 만들 수 있습니다.')
        if not self.orders.account_seq.isdigit():
            raise ValueError('NAS .env 의 TOSS_ACCOUNT_SEQ 가 비어 있습니다. 설정 화면의 "토스 계좌 식별자 조회"로 값을 확인하세요.')
        if initial_cash > capital_cap():
            raise ValueError(f'투입 자금이 NAS 의 LIVE_CAPITAL_KRW 상한({capital_cap():,.0f}원)을 넘습니다.')
        self.orders.validate_account()
        if any(r['currency'] == 'KRW' and number(r['quantity']) > 0 for r in self.orders.holdings()):
            raise ValueError('이 토스 계좌에 이미 보유 주식이 있습니다. 단타는 보유가 없는 전용 계좌에서만 시작합니다 '
                             '(장기 보유분과 섞이지 않게).')
        if self.orders.open_orders():
            raise ValueError('토스 계좌에 미체결 주문이 남아 있습니다. 먼저 정리하세요.')
        power = self.orders.buying_power('KRW')
        if power < initial_cash:
            raise ValueError(f'토스 계좌의 현금 매수 가능 금액({power:,.0f}원)이 투입 자금보다 적습니다.')
        return self.orders.account_seq

    def reconcile(self, state: dict) -> None:
        """토스 보유량 == 프로그램 장부. 다르면 예외. 시작 전과 주기적으로 부른다."""
        if state.get('account_seq') != self.orders.account_seq:
            raise ValueError('실계좌 식별자(TOSS_ACCOUNT_SEQ)가 이 장부를 만들 때와 다릅니다.')
        actual = {r['symbol']: int(number(r['quantity'])) for r in self.orders.holdings()
                  if r['currency'] == 'KRW' and number(r['quantity']) > 0}
        expected = {p['symbol']: int(p['qty']) for p in state['positions'].values() if p['qty'] > 0}
        if actual != expected:
            raise ValueError(f'토스 보유량과 프로그램 장부가 다릅니다 (토스 {actual} / 장부 {expected}). '
                             '수동 매매·분할·이전 체결을 확인하세요.')
        known = {o['broker_order_id'] for o in self.store.all(ORDER_KIND) if o.get('broker_order_id')}
        if any(o.get('orderId') not in known for o in self.orders.open_orders()):
            raise ValueError('프로그램이 내지 않은 미체결 주문이 토스 계좌에 있습니다.')

    # ── 주문 만들기 ─────────────────────────────────────────────────────────
    def _intent(self, state: dict, side: str, symbol: str, name: str, qty: int, limit_price: int,
                reason: str, meta: dict, now: datetime) -> dict:
        intent = {'id': uuid.uuid4().hex, 'account_id': state['id'], 'status': 'DRAFT',
                  'created_at': now.isoformat(), 'side': side, 'symbol': symbol, 'name': name,
                  'qty': int(qty), 'limit_price': int(limit_price), 'reason': reason, 'meta': meta,
                  'broker_order_id': None, 'filled_qty': 0, 'filled_amount': 0.0, 'fees': 0.0,
                  'error': '', 'cancel_attempted': False, 'broker_status': None,
                  'submitted_at': None, 'last_checked_at': None, 'done_at': None}
        self.store.put(ORDER_KIND, intent['id'], intent)
        return intent

    def queue_buy(self, state: dict, symbol: str, name: str, krw: float, quote: dict,
                  tp_pct: float, sl_pct: float, meta: dict, now: datetime, log_fn) -> bool:
        cfg = state['config']
        pending = self.pending(state['id'])
        if any(o['status'] == 'UNKNOWN' for o in pending):
            log_fn(state, f'{symbol} 매수 보류: 결과를 모르는 주문이 있어 새 주문을 내지 않습니다')
            return False
        if any(o['symbol'] == symbol and o['side'] == 'buy' for o in pending):
            return False
        limit = to_tick(quote['ask'] * (1 + cfg['live_limit_buffer_bps'] / 10000), up=True)
        ceiling = meta.get('max_entry_price') or 0
        if ceiling:
            limit = min(limit, to_tick(ceiling, up=False))
            if limit > ceiling or limit < quote['ask']:
                log_fn(state, f'{symbol} 매수 보류: 유효한 매수 상한 호가가 없습니다')
                return False
        unit = limit * (1 + cfg['fee_bps'] / 10000)
        # 아직 안 채워진 매수 주문이 쓸 돈은 이미 잡아 둔 것으로 본다.
        reserved = sum((o['qty'] - o['filled_qty']) * o['limit_price'] * (1 + cfg['fee_bps'] / 10000)
                       for o in pending if o['side'] == 'buy')
        budget = min(krw, order_cap(), state['cash'] - reserved)
        qty = int(budget // unit)
        if meta.get('max_qty') is not None:
            qty=min(qty,int(meta['max_qty']))
        if meta.get('risk_budget_krw') and meta.get('strategy_stop'):
            stop=max(meta['strategy_stop'],limit*(1-sl_pct/100))
            costs=(2*cfg['fee_bps']+cfg['sell_tax_bps']+2*cfg['slippage_bps']+max(0,quote.get('spread_bps',0)))/10000
            loss=limit-stop+limit*costs
            qty=min(qty,int(meta['risk_budget_krw']/loss)) if loss>0 else 0
        if qty < 1:
            log_fn(state, f'{symbol} 매수 불가: 예산 {budget:,.0f}원으로 1주도 못 삼')
            return False
        intent = self._intent(state, 'buy', symbol, name, qty, limit, meta.get('source', 'plan'),
                              {'tp_pct': tp_pct, 'sl_pct': sl_pct, **meta}, now)
        log_fn(state, f'{name}({symbol}) {qty}주 매수 주문 준비 @{limit:,} (실거래) · 익절 +{tp_pct}% 손절 -{sl_pct}%')
        return True

    def queue_sell(self, state: dict, symbol: str, quote: dict | None, reason: str, now: datetime, log_fn) -> bool:
        pos = state['positions'].get(symbol)
        if not pos or pos['qty'] <= 0:
            return False
        if pos.get('closing'):
            return False        # 이미 매도 주문이 나가 있다
        if pos.get('sell_attempts', 0) >= MAX_SELL_ATTEMPTS:
            state['errors'][f'sell:{symbol}'] = f'{pos["name"]} 매도가 {MAX_SELL_ATTEMPTS}번 미체결. 토스 앱에서 직접 확인하세요.'
            return False
        cfg = state['config']
        bid = (quote or {}).get('bid') or pos['entry_price']
        limit = to_tick(bid * (1 - cfg['live_limit_buffer_bps'] / 10000), up=False)
        intent = self._intent(state, 'sell', symbol, pos['name'], int(pos['qty']), limit, reason, {}, now)
        pos['closing'] = intent['id']
        pos['close_reason'] = reason
        log_fn(state, f'{pos["name"]}({symbol}) {pos["qty"]}주 매도 주문 준비 @{limit:,} · {reason}')
        return True

    def cancel_all(self, state: dict, side: str | None = None) -> int:
        n = 0
        for intent in self.pending(state['id'], side):
            if intent['status'] in ('OPEN', 'CANCELING') and intent.get('broker_order_id') and not intent['cancel_attempted']:
                intent['cancel_requested'] = True
                self.store.put(ORDER_KIND, intent['id'], intent)
                n += 1
            elif intent['status'] == 'DRAFT':
                intent['status'] = 'BLOCKED'
                intent['error'] = '전송 전에 취소됨'
                self.store.put(ORDER_KIND, intent['id'], intent)
        return n

    # ── 주기 실행 ───────────────────────────────────────────────────────────
    def pulse(self, state: dict, now: datetime, log_fn) -> None:
        """DRAFT 전송 → OPEN 조회·체결 반영 → 제한 시간 지난 주문 취소. 서비스 락 안에서 부른다."""
        for intent in self.pending(state['id']):
            try:
                if intent['status'] == 'DRAFT':
                    self._submit(state, intent, now, log_fn)
                    continue
                if not intent.get('broker_order_id'):
                    continue        # UNKNOWN 이고 주문 ID 도 없다. 사람이 연결해야 한다.
                detail = self.orders.detail(intent['broker_order_id'])
                self._ingest(state, intent, detail, now, log_fn)
                if intent['status'] not in ('OPEN', 'CANCELING'):
                    continue
                overdue = intent.get('submitted_at') and \
                    (now - stamp(intent['submitted_at'])).total_seconds() > state['config']['live_fill_timeout_seconds']
                expiry=intent.get('meta',{}).get('approval_expires_at')
                if intent['side']=='buy' and expiry and stamp(expiry)<=now:overdue=True
                if (overdue or intent.get('cancel_requested') or state['status'] != 'running' and intent['side'] == 'buy') \
                        and not intent['cancel_attempted'] and intent.get('broker_status') != 'PENDING_CANCEL':
                    intent['cancel_attempted'] = True
                    intent['status'] = 'CANCELING'
                    self.store.put(ORDER_KIND, intent['id'], intent)
                    response = self.orders.cancel(intent['broker_order_id'])
                    intent['cancel_operation_id'] = (response or {}).get('orderId')
                    self.store.put(ORDER_KIND, intent['id'], intent)
                    log_fn(state, f'{intent["name"]} {"매수" if intent["side"] == "buy" else "매도"} 주문 취소 요청 (미체결 {intent["qty"] - intent["filled_qty"]}주)')
            except OrderUncertain as exc:
                intent['status'] = 'UNKNOWN'
                intent['error'] = str(exc)
                self.store.put(ORDER_KIND, intent['id'], intent)
                state['errors']['orders'] = f'주문 결과 불명확: {intent["name"]} {intent["side"]}. 토스 앱에서 확인 후 화면에서 연결하세요.'
                notify.post(notify.error_message('실거래 주문 결과 불명확', f'{intent["name"]} {intent["side"]} · {exc}'),'alert')
            except (TossError, ValueError, OSError) as exc:
                intent['error'] = str(exc)[:200]
                self.store.put(ORDER_KIND, intent['id'], intent)

    def _submit(self, state: dict, intent: dict, now: datetime, log_fn) -> None:
        try:
            if not self.enabled:
                raise ValueError('실거래가 꺼져 있습니다 (ENABLE_LIVE_TRADING / TOSS_ACCOUNT_SEQ)')
            if state['status'] != 'running' and intent['side'] == 'buy':
                raise ValueError('운용이 정지된 계좌의 매수 주문은 보내지 않습니다')
            expiry=intent.get('meta',{}).get('approval_expires_at')
            if intent['side']=='buy' and expiry and stamp(expiry)<=now:
                raise ValueError('매수 허용 시간이 만료된 주문은 보내지 않습니다')
            if (now - stamp(intent['created_at'])).total_seconds() > 90:
                raise ValueError('준비된 지 90초가 지난 주문은 보내지 않습니다 (시세가 바뀌었을 수 있음)')
            value = intent['qty'] * intent['limit_price']
            if intent['side'] == 'buy':
                if value > order_cap():
                    raise ValueError(f'주문 1건 상한 LIVE_MAX_ORDER_KRW({order_cap():,.0f}원) 초과')
                need = value * (1 + state['config']['fee_bps'] / 10000)
                reserved = sum((o['qty'] - o['filled_qty']) * o['limit_price'] * (1 + state['config']['fee_bps'] / 10000)
                               for o in self.pending(state['id'], 'buy') if o['id'] != intent['id'] and o['status'] != 'DRAFT')
                if need > state['cash'] - reserved:
                    raise ValueError('이 계좌에 배정한 현금이 부족합니다')
                if self.orders.buying_power('KRW') < need:
                    raise ValueError('토스 계좌의 현금 매수 가능 금액이 부족합니다')
            else:
                if self.orders.sellable(intent['symbol']) < intent['qty']:
                    raise ValueError('토스 계좌의 매도 가능 수량이 부족합니다')
            intent['status'] = 'SUBMITTING'
            intent['submitted_at'] = now.isoformat()
            self.store.put(ORDER_KIND, intent['id'], intent)
            broker_id = self.orders.create(intent)
            intent['broker_order_id'] = broker_id
            intent['status'] = 'OPEN'
            notify.post(notify.order_message(intent))
            log_fn(state, f'{intent["name"]} {"매수" if intent["side"] == "buy" else "매도"} 주문 접수 '
                          f'{intent["qty"]}주 @{intent["limit_price"]:,} · 주문번호 {broker_id}')
        except OrderUncertain as exc:
            intent['status'] = 'UNKNOWN'
            intent['error'] = str(exc)
            state['errors']['orders'] = f'주문 결과 불명확: {intent["name"]}. 토스 앱에서 확인 후 화면에서 연결하세요.'
            notify.post(notify.error_message('실거래 주문 결과 불명확', f'{intent["name"]} · {exc}'),'alert')
        except (ValueError, TossError, OSError) as exc:
            intent['status'] = 'UNKNOWN' if intent['status'] == 'SUBMITTING' and not isinstance(exc, (ValueError, TossError)) else 'BLOCKED'
            intent['error'] = str(exc)[:200]
            log_fn(state, f'{intent["name"]} 주문 보류: {intent["error"]}')
            if intent['side'] == 'sell':
                self._release_sell(state, intent)
        except Exception as exc:  # noqa: BLE001
            intent['status'] = 'UNKNOWN' if intent['status'] == 'SUBMITTING' else 'BLOCKED'
            intent['error'] = f'주문 처리 예외: {type(exc).__name__}'
            if intent['side'] == 'sell' and intent['status'] == 'BLOCKED':
                self._release_sell(state, intent)
        self.store.put(ORDER_KIND, intent['id'], intent)

    def _release_sell(self, state: dict, intent: dict) -> None:
        pos = state['positions'].get(intent['symbol'])
        if pos and pos.get('closing') == intent['id']:
            pos.pop('closing', None)
            pos['sell_attempts'] = pos.get('sell_attempts', 0) + 1

    def _ingest(self, state: dict, intent: dict, detail: dict, now: datetime, log_fn) -> None:
        """누적 체결 → 증분. 같은 조회를 두 번 해도 체결이 두 번 반영되지 않는다."""
        for key, want in {'orderId': intent['broker_order_id'], 'symbol': intent['symbol'], 'side': intent['side'].upper()}.items():
            if detail.get(key) != want:
                raise ValueError(f'주문 상세 불일치 ({key})')
        execution = detail.get('execution') or {}
        qty = number(execution.get('filledQuantity', 0))
        if qty != qty.to_integral_value() or not intent['filled_qty'] <= qty <= intent['qty']:
            raise ValueError('누적 체결 수량이 역행하거나 범위를 벗어남')
        qty = int(qty)
        amount = float(number(execution.get('filledAmount', 0))) if qty else 0.0
        if amount < intent['filled_amount'] - 1e-6:
            raise ValueError('누적 체결 금액 역행')
        fees = 0.0
        if execution.get('commission') is not None:
            fees += float(number(execution['commission']))
        else:
            fees += amount * state['config']['fee_bps'] / 10000
        if execution.get('tax') is not None:
            fees += float(number(execution['tax']))
        elif intent['side'] == 'sell':
            fees += amount * state['config']['sell_tax_bps'] / 10000

        delta = qty - intent['filled_qty']
        gross = amount - intent['filled_amount']
        fee = fees - intent['fees']
        if delta > 0:
            self._apply_fill(state, intent, delta, gross, fee, now, log_fn)
        intent.update(filled_qty=qty, filled_amount=amount, fees=fees,
                      broker_status=detail.get('status'), last_checked_at=now.isoformat(), error='')
        if intent['status'] == 'UNKNOWN' and detail.get('status') in BROKER_OPEN:
            intent['status'] = 'OPEN'
        if detail.get('status') in BROKER_DONE:
            intent['status'] = 'DONE'
            intent['done_at'] = now.isoformat()
            remaining = intent['qty'] - qty
            if intent['side'] == 'buy' and remaining:
                log_fn(state, f'{intent["name"]} 매수 {detail["status"]}: {qty}주 체결, {remaining}주 미체결')
            if intent['side'] == 'sell':
                pos = state['positions'].get(intent['symbol'])
                if pos and pos.get('closing') == intent['id']:
                    self._release_sell(state, intent)
                    log_fn(state, f'{intent["name"]} 매도 {detail["status"]}: {remaining}주 남음 · 다시 시도합니다')
        elif detail.get('status') not in BROKER_OPEN:
            intent['status'] = 'UNKNOWN'
            intent['error'] = f'알 수 없는 주문 상태 {detail.get("status")}'
        self.store.put(ORDER_KIND, intent['id'], intent)

    def _apply_fill(self, state: dict, intent: dict, delta: int, gross: float, fee: float, now: datetime, log_fn) -> None:
        symbol = intent['symbol']
        price = gross / delta
        if intent['side'] == 'buy':
            state['cash'] -= gross + fee
            pos = state['positions'].get(symbol)
            meta = intent['meta']
            if pos:
                total = pos['qty'] + delta
                pos['entry_price'] = (pos['entry_price'] * pos['qty'] + gross) / total
                pos['qty'] = total
                pos['cost'] += gross + fee
            else:
                pos = {'symbol': symbol, 'name': intent['name'], 'qty': delta, 'entry_price': price,
                       'entry_at': now.isoformat(), 'cost': gross + fee,
                       'tp_pct': meta['tp_pct'], 'sl_pct': meta['sl_pct'],
                       'hold_overnight': bool(meta.get('hold_overnight')), 'order_id': intent['id'],
                       'strategy':meta.get('strategy','ai'),'strategy_stop':meta.get('strategy_stop'),
                       'horizon':meta.get('horizon','short'),
                       'basket_rank':meta.get('basket_rank'),
                       'decision_id':meta.get('decision_id'),'policy_version':meta.get('policy_version'),
                       'source': meta.get('source', 'plan'), 'thesis': meta.get('thesis', ''),
                       'counter_case': meta.get('counter_case', ''), 'invalidation': meta.get('invalidation', '')}
                state['positions'][symbol] = pos
            pos['tp_price'] = pos['entry_price'] * (1 + pos['tp_pct'] / 100)
            initial_stop=pos['entry_price'] * (1 - pos['sl_pct'] / 100)
            pos['sl_price'] = max(pos.get('sl_price',0),initial_stop) if pos.get('strategy')=='portfolio' else initial_stop
            state['trades'].append({'symbol': symbol, 'side': 'buy', 'qty': delta, 'price': price, 'fee': fee,
                                    'name':intent['name'],
                                    'at': now.isoformat(), 'reason': intent['reason'],
                                    'strategy':meta.get('strategy','ai'),'plan_decided_at':meta.get('plan_decided_at'),
                                    'horizon':meta.get('horizon','short'),
                                    'basket_rank':meta.get('basket_rank'),
                                    'decision_id':meta.get('decision_id'),'policy_version':meta.get('policy_version'),
                                    'order_id': intent['broker_order_id']})
            log_fn(state, f'{intent["name"]}({symbol}) {delta}주 매수 체결 @{price:,.0f} (실거래)')
            notify.post(notify.buy_message(dict(pos,qty=delta,entry_price=price,entry_at=now.isoformat()),
                        mode='실거래 · 부분 체결' if intent['filled_qty']<intent['qty'] else '실거래',state=state),'trade')
        else:
            pos = state['positions'].get(symbol)
            if not pos or pos['qty'] < delta:
                raise ValueError('체결 매도량이 장부 보유량을 넘습니다')
            basis = pos['cost'] * delta / pos['qty']
            pnl = gross - fee - basis
            state['cash'] += gross - fee
            state['realized_pnl'] += pnl
            pos['qty'] -= delta
            pos['cost'] -= basis
            trade = {'symbol': symbol, 'side': 'sell', 'qty': delta, 'price': price, 'fee': fee,
                     'name':pos.get('name') or symbol,
                     'horizon':pos.get('horizon','short'),
                     'at': now.isoformat(), 'pnl': pnl, 'pnl_pct': pnl / basis * 100 if basis else 0.0,
                     'strategy':pos.get('strategy','ai'),
                     'basket_rank':pos.get('basket_rank'),
                     'decision_id':pos.get('decision_id'),'policy_version':pos.get('policy_version'),
                     'entry_at':pos.get('entry_at'),'cost_basis':basis,
                     'reason': pos.get('close_reason') or intent['reason'], 'order_id': intent['broker_order_id']}
            state['trades'].append(trade)
            log_fn(state, f'{pos["name"]}({symbol}) {delta}주 매도 체결 @{price:,.0f} · {pnl:+,.0f}원 · {trade["reason"]} (실거래)')
            notify.post(notify.sell_message(trade, pos['name'], mode='실거래',state=state),'trade')
            if pos['qty'] <= 0:
                state['positions'].pop(symbol, None)

    # ── 사람이 하는 복구 ─────────────────────────────────────────────────────
    def bind(self, state: dict, intent_id: str, broker_order_id: str, now: datetime, log_fn) -> dict:
        intent = self.store.get(ORDER_KIND, intent_id)
        if intent['account_id'] != state['id']:
            raise ValueError('이 계좌의 주문이 아닙니다')
        if intent['status'] != 'UNKNOWN' or intent.get('broker_order_id'):
            raise ValueError('주문 ID 가 없는 미확인 주문만 연결할 수 있습니다')
        if any(o.get('broker_order_id') == broker_order_id for o in self.store.all(ORDER_KIND)):
            raise ValueError('이미 연결된 주문 ID')
        detail = self.orders.detail(broker_order_id)
        intent['broker_order_id'] = broker_order_id
        intent['status'] = 'OPEN'
        self._ingest(state, intent, detail, now, log_fn)
        state['errors'].pop('orders', None)
        return intent

    def discard_unknown(self, state: dict, intent_id: str, log_fn) -> dict:
        """토스 앱에서 '그런 주문은 없다' 를 확인한 뒤에만. 장부는 바뀌지 않는다."""
        intent = self.store.get(ORDER_KIND, intent_id)
        if intent['account_id'] != state['id'] or intent['status'] != 'UNKNOWN' or intent.get('broker_order_id'):
            raise ValueError('주문 ID 가 없는 미확인 주문만 폐기할 수 있습니다')
        intent['status'] = 'BLOCKED'
        intent['error'] = '사용자가 토스 앱에서 주문 없음을 확인하고 폐기'
        self.store.put(ORDER_KIND, intent['id'], intent)
        if intent['side'] == 'sell':
            self._release_sell(state, intent)
        state['errors'].pop('orders', None)
        log_fn(state, f'{intent["name"]} 미확인 주문 폐기 (사용자 확인)')
        return intent
