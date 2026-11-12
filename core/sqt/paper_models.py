"""Contracts for forward paper trading. None of these models enable live orders."""
import re
from typing import Literal
from pydantic import Field, model_validator
from .models import Instrument, StrictModel

_US_TICKER_RE = re.compile(r'^[A-Z][A-Z0-9.]{0,6}$')

Bucket = Literal['scalp','swing','long']


class PaperConfig(StrictModel):
    currency: Literal['KRW','USD'] = 'USD'
    initial_cash: float = Field(default=10000,gt=0,le=1e12)
    watchlist: list[Instrument] = Field(min_length=1,max_length=12)
    max_position_pct: float = Field(default=15,ge=1,le=30)
    max_invested_pct: float = Field(default=80,ge=10,le=95)
    max_scalp_pct: float = Field(default=25,ge=0,le=40)
    max_bucket_pct: float = Field(default=50,ge=10,le=80)
    risk_per_trade_pct: float = Field(default=.5,ge=.1,le=2)
    daily_loss_pct: float = Field(default=2,ge=.5,le=5)
    max_drawdown_pct: float = Field(default=10,ge=1,le=25)
    fee_bps: float = Field(default=10,ge=0,le=100)
    sell_tax_bps: float = Field(default=0,ge=0,le=100)
    slippage_bps: float = Field(default=10,ge=0,le=100)
    max_spread_bps: float = Field(default=80,ge=5,le=300)
    quote_max_age_seconds: int = Field(default=30,ge=5,le=60)
    poll_seconds: int = Field(default=15,ge=10,le=120)
    ai_cooldown_seconds: int = Field(default=180,ge=120,le=3600)
    ai_review_seconds: int = Field(default=900,ge=180,le=14400)
    max_ai_calls_per_day: int = Field(default=32,ge=1,le=100)
    min_confidence: float = Field(default=.65,ge=.5,le=.95)
    require_news: bool = True
    decision_policy_v2: bool = True
    opening_wait_minutes: int = Field(default=5,ge=3,le=30)
    min_reward_risk: float = Field(default=1.5,ge=1,le=5)
    reentry_cooldown_minutes: int = Field(default=30,ge=10,le=240)
    max_open_gap_pct: float = Field(default=6,ge=2,le=15)
    @model_validator(mode='after')
    def valid(self):
        if any(i.currency!=self.currency for i in self.watchlist):
            raise ValueError('하나의 가상 계좌에는 동일 통화 종목만 넣으세요.')
        if len({i.symbol for i in self.watchlist})!=len(self.watchlist):
            raise ValueError('관심 종목에 중복 코드가 있습니다.')
        if self.ai_review_seconds<self.ai_cooldown_seconds:
            raise ValueError('정기 판단 간격은 최소 판단 간격 이상이어야 합니다.')
        return self


class Allocation(StrictModel):
    scalp: float = Field(ge=0,le=100)
    swing: float = Field(ge=0,le=100)
    long: float = Field(ge=0,le=100)
    cash: float = Field(ge=0,le=100)
    @model_validator(mode='after')
    def total(self):
        if abs(self.scalp+self.swing+self.long+self.cash-100)>.01:
            raise ValueError('AI 배분 비율 합계는 100%여야 합니다.')
        return self


class Proposal(StrictModel):
    symbol: str
    bucket: Bucket
    action: Literal['buy','sell','hold','protect']
    target_pct: float = Field(ge=0,le=30)
    confidence: float = Field(ge=0,le=1)
    stop_pct: float = Field(ge=.2,le=20)
    take_profit_pct: float = Field(ge=.2,le=100)
    trailing_pct: float = Field(ge=.2,le=20)
    reason: str = Field(min_length=1,max_length=1600)
    news_ids: list[str] = Field(max_length=20)
    setup: Literal['opening_breakout','momentum_breakout','trend_pullback','trend_follow','long_accumulation','none'] = 'none'
    thesis: str = Field(default='',max_length=1200)
    counter_case: str = Field(default='',max_length=1200)
    invalidation: str = Field(default='',max_length=1200)


class ThemePick(StrictModel):
    """테마 후보 중 AI 가 고른 한 종목. symbol 은 넘겨준 후보 목록에만 있을 수 있다."""
    symbol: str = Field(max_length=12)
    theme: str = Field(max_length=80)
    thesis: str = Field(min_length=12,max_length=600)
    counter_case: str = Field(min_length=12,max_length=600)


class WatchlistPlan(StrictModel):
    """관심 종목 추천. 주문이 아니라 '무엇을 감시할지' 만 정한다."""
    summary: str = Field(min_length=1,max_length=2000)
    us_context: str = Field(default='',max_length=1500)
    picks: list[ThemePick] = Field(min_length=1,max_length=12)

    @model_validator(mode='after')
    def unique(self):
        if len({p.symbol for p in self.picks})!=len(self.picks):
            raise ValueError('추천 종목에 중복이 있습니다.')
        return self


class Plan(StrictModel):
    snapshot_id: str
    summary: str = Field(min_length=1,max_length=3000)
    allocation: Allocation
    decisions: list[Proposal] = Field(max_length=36)
    next_review_seconds: int = Field(ge=180,le=14400)
    @model_validator(mode='after')
    def unique(self):
        keys=[(d.symbol,d.bucket) for d in self.decisions]
        if len(keys)!=len(set(keys)):
            raise ValueError('AI 판단에 같은 종목·운용 구간이 중복되었습니다.')
        return self


# ── 당일 단타 ─────────────────────────────────────────────────────────────────
# 세 시점에서 AI 가 답하는 계약. 익절·손절 폭은 여기서 범위만 선언하고,
# 실제 자르기는 서비스 코드가 한다(스키마 범위를 넘긴 값은 통째로 폐기).

class DayTradeConfig(StrictModel):
    """당일 단타 계좌 설정. 판단 시점·범위·비용을 전부 숫자로 둔다."""
    currency: Literal['KRW'] = 'KRW'
    strategy: Literal['ai','orb','retest','bull_flag','in_play','top5_morning','portfolio'] = 'ai'
    portfolio_style: Literal['balanced','growth','all_weather','short','medium','long'] = 'balanced'
    markets: list[Literal['KR','US']] = Field(default_factory=lambda:['KR'],min_length=1,max_length=2)
    portfolio_core: list[str] = Field(default_factory=lambda:['069500','229200'],max_length=12)
    us_core: list[str] = Field(default_factory=lambda:['NVDA','MSFT','AAPL','GOOGL','AMZN','META','AVGO','QQQ','SPY'],max_length=20)
    portfolio_risk_pct: float = Field(default=.5,ge=.1,le=2.5)
    portfolio_watchlists: dict[Literal['short','medium','long'], list[str]] = Field(default_factory=dict)
    us_watchlists: dict[Literal['short','medium','long'], list[str]] = Field(default_factory=dict)
    breakeven_lock: bool = True
    partial_tp: bool = True
    self_improve: bool = True
    ai_office: bool = True
    basket_entry_at: str = Field(default='09:05',pattern=r'^\d{2}:\d{2}$')
    basket_exit_at: str = Field(default='11:50',pattern=r'^\d{2}:\d{2}$')
    basket_entry_minutes: int = Field(default=10,ge=5,le=30)
    basket_tp_pct: float = Field(default=4,ge=1,le=15)
    basket_sl_pct: float = Field(default=2,ge=.5,le=5)
    risk_per_trade_pct: float = Field(default=.25, ge=.05, le=1)
    approval_minutes: int = Field(default=20, ge=5, le=30)
    max_price_reviews_per_day: int = Field(default=16, ge=2, le=20)
    # paper: 가상 체결. live: 토스 실계좌에 실제 주문 (ENABLE_LIVE_TRADING + TOSS_ACCOUNT_SEQ 필요)
    mode: Literal['paper','live'] = 'paper'
    initial_cash: float = Field(default=10000000,gt=0,le=1e12)
    max_positions: int = Field(default=5,ge=1,le=16)
    position_pct: float = Field(default=15,ge=1,le=30)
    take_profit_min_pct: float = Field(default=1,ge=1,le=30)
    take_profit_max_pct: float = Field(default=15,ge=1,le=50)
    take_profit_default_pct: float = Field(default=10,ge=1,le=50)
    stop_loss_min_pct: float = Field(default=2,ge=.5,le=10)
    stop_loss_max_pct: float = Field(default=5,ge=.5,le=20)
    stop_loss_default_pct: float = Field(default=3,ge=.5,le=20)
    # 거래 가능 시간 (KST). 실제 값은 매일 토스 장 캘린더에서 받아 오고, 이건 조회 실패 시 대비책이다.
    #   프리마켓(NXT) 08:00~09:00 · 정규장 09:00~15:30(종가 단일가 15:20~) · 애프터마켓(NXT) 15:30~20:00
    session_open: str = Field(default='08:00',pattern=r'^\d{2}:\d{2}$')
    session_close: str = Field(default='20:00',pattern=r'^\d{2}:\d{2}$')
    # 시각은 KST HH:MM. 프리마켓(NXT) 진입은 계좌가 SOR 로 설정돼 있어야 한다.
    scan_at: str = Field(default='07:30',pattern=r'^\d{2}:\d{2}$')
    entry_session: Literal['regular','premarket'] = 'regular'
    entry_at: str = Field(default='09:00',pattern=r'^\d{2}:\d{2}$')
    entry_window_minutes: int = Field(default=60,ge=5,le=390)
    eod_review_at: str = Field(default='15:00',pattern=r'^\d{2}:\d{2}$')
    force_close_at: str = Field(default='15:20',pattern=r'^\d{2}:\d{2}$')
    # 마감 무렵 신규 매수. 오늘 크게 눌린 종목을 내일 반등 목적으로 사는 경로다.
    # 장전에 "오늘은 안 산다"고 판단해도 이 경로는 살아 있다.
    close_entry: bool = True
    close_entry_max: int = Field(default=2,ge=0,le=5)
    # 마감 복기: 오늘 무엇이 왜 됐는지/안 됐는지 AI 가 분석하고, 숫자를 조금씩 조정한다.
    daily_review: bool = True
    auto_tune: bool = False
    # 장전 스캔·판단이 실패하면 이 간격으로 다시 시도한다. 예전엔 한 번 실패하면 하루가 끝났다.
    retry_minutes: int = Field(default=3,ge=1,le=60)
    max_scan_attempts: int = Field(default=5,ge=1,le=20)
    # 장중 이벤트 트리거. 둘 다 끄면 AI 는 장전·마감 2회만 판단한다.
    news_trigger: bool = True
    surge_trigger_pct: float = Field(default=4,ge=1,le=20)
    max_ai_calls_per_day: int = Field(default=8,ge=2,le=30)
    # 스캐너 조건
    scan_theme_count: int = Field(default=20,ge=5,le=50)
    scan_ranking_count: int = Field(default=50,ge=10,le=100)
    min_trading_amount_krw: float = Field(default=3e9,ge=1e8)
    max_prev_change_pct: float = Field(default=25,ge=5,le=30)
    require_flow: bool = False
    require_news: bool = False
    max_candidates: int = Field(default=15,ge=3,le=30)
    # 비용
    fee_bps: float = Field(default=10,ge=0,le=100)
    sell_tax_bps: float = Field(default=15,ge=0,le=100)
    slippage_bps: float = Field(default=10,ge=0,le=100)
    poll_seconds: int = Field(default=15,ge=5,le=120)
    # 실거래 지정가 여유(매수 +, 매도 -)와 미체결 취소 시간
    live_limit_buffer_bps: float = Field(default=30,ge=0,le=200)
    live_fill_timeout_seconds: int = Field(default=180,ge=30,le=1800)

    @model_validator(mode='after')
    def sane(self):
        if len(set(self.markets))!=len(self.markets):
            raise ValueError('운용 시장(KR/US)에 중복이 있습니다.')
        if len(set(self.portfolio_core))!=len(self.portfolio_core) or any(len(s)!=6 or not s.isdigit() for s in self.portfolio_core):
            raise ValueError('기본 관심종목은 중복 없는 국내 6자리 코드로 입력하세요.')
        for symbols in self.portfolio_watchlists.values():
            if len(symbols)>12 or len(set(symbols))!=len(symbols) or any(len(s)!=6 or not s.isdigit() for s in symbols):
                raise ValueError('기간별 관심종목은 중복 없는 국내 6자리 코드 최대 12개입니다.')
        if len(set(self.us_core))!=len(self.us_core) or any(not _US_TICKER_RE.match(s) for s in self.us_core):
            raise ValueError('해외 기본 관심종목은 중복 없는 영문 대문자 티커(예: NVDA, AAPL)로 입력하세요.')
        for symbols in self.us_watchlists.values():
            if len(symbols)>12 or len(set(symbols))!=len(symbols) or any(not _US_TICKER_RE.match(s) for s in symbols):
                raise ValueError('해외 기간별 관심종목은 중복 없는 영문 대문자 티커 최대 12개입니다.')
        if self.strategy=='portfolio':
            self.entry_at='09:05'
            self.entry_window_minutes=365
            self.eod_review_at='15:10'
            self.force_close_at='15:30'  # reporting time, never a liquidation in portfolio mode
            self.entry_session='regular'
            self.close_entry=False
            self.auto_tune=False
        if self.strategy=='top5_morning':
            if not '09:00'<=self.basket_entry_at<self.basket_exit_at<='12:00':
                raise ValueError('시나리오2는 09:00~12:00 사이에서 매수 시작 < 청산 시각이어야 합니다.')
            h,m=map(int,self.basket_entry_at.split(':'))
            eh,em=map(int,self.basket_exit_at.split(':'))
            if m>59 or em>59:
                raise ValueError('시나리오2 시간의 분은 00~59여야 합니다.')
            if h*60+m+self.basket_entry_minutes>=eh*60+em:
                raise ValueError('시나리오2 진입 창은 오전 청산 시각 전에 끝나야 합니다.')
            if self.max_candidates<5 or self.position_pct>20:
                raise ValueError('시나리오2는 후보 5개 이상·종목당 비중 20% 이하여야 합니다.')
            self.max_positions=5
            self.entry_at=self.basket_entry_at
            self.entry_window_minutes=self.basket_entry_minutes
            self.eod_review_at=self.basket_exit_at
            self.force_close_at=self.basket_exit_at
            self.entry_session='regular'
            self.close_entry=False
            self.auto_tune=False
        if not '07:30' <= self.scan_at < self.entry_at:
            raise ValueError('장전 검토는 한국시간 07:30 이후, 매수 시작 전이어야 합니다.')
        if not self.take_profit_min_pct<=self.take_profit_default_pct<=self.take_profit_max_pct:
            raise ValueError('익절 기본값은 최소~최대 범위 안이어야 합니다.')
        if not self.stop_loss_min_pct<=self.stop_loss_default_pct<=self.stop_loss_max_pct:
            raise ValueError('손절 기본값은 최소~최대 범위 안이어야 합니다.')
        if self.max_positions*self.position_pct>100:
            raise ValueError('종목 수 x 비중이 100% 를 넘습니다.')
        if not self.entry_at<self.eod_review_at<=self.force_close_at:
            raise ValueError('시간표 순서가 맞지 않습니다: 매수 < 마감 판단 <= 강제 정리.')
        if not self.session_open<self.session_close:
            raise ValueError('장 시작이 장 종료보다 늦습니다.')
        if self.force_close_at>self.session_close:
            raise ValueError(f'강제 정리({self.force_close_at})가 장 종료({self.session_close}) 뒤입니다. '
                             '장이 닫힌 뒤에는 팔 수 없습니다.')
        if self.entry_at<self.session_open:
            raise ValueError(f'매수 시각({self.entry_at})이 장 시작({self.session_open})보다 이릅니다.')
        return self


class DayPick(StrictModel):
    symbol: str = Field(max_length=12)
    horizon: Literal['short','medium','long'] = 'short'
    max_entry_price: float = Field(default=0, ge=0, allow_inf_nan=False)
    take_profit_pct: float = Field(ge=.5,le=60)
    stop_loss_pct: float = Field(ge=.2,le=30)
    weight_pct: float = Field(ge=1,le=30)
    thesis: str = Field(min_length=12,max_length=700)
    counter_case: str = Field(min_length=12,max_length=700)
    invalidation: str = Field(min_length=8,max_length=400)


class PortfolioExit(StrictModel):
    symbol: str
    reason: str = Field(min_length=8,max_length=400)
    evidence_ids: list[str] = Field(default_factory=list,max_length=5)


class HorizonReviews(StrictModel):
    short: str = Field(default='',max_length=500)
    medium: str = Field(default='',max_length=500)
    long: str = Field(default='',max_length=500)


class PortfolioPlan(StrictModel):
    summary: str = Field(min_length=1,max_length=1000)
    picks: list[DayPick] = Field(default_factory=list,max_length=16)
    exits: list[PortfolioExit] = Field(default_factory=list,max_length=16)
    horizon_reviews: HorizonReviews = Field(default_factory=HorizonReviews)

    @model_validator(mode='after')
    def unique(self):
        buys=[p.symbol for p in self.picks];sells=[p.symbol for p in self.exits]
        if len(set(buys))!=len(buys) or len(set(sells))!=len(sells) or set(buys)&set(sells):
            raise ValueError('동일 종목의 중복·매수 매도 동시 제안은 허용하지 않습니다.')
        return self


class DayTheme(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    catalyst: str = Field(min_length=12, max_length=700)
    risk: str = Field(min_length=8, max_length=700)
    invalidation: str = Field(min_length=8, max_length=400)
    symbols: list[str] = Field(min_length=1, max_length=12)


class DayPlan(StrictModel):
    """장전 판단. 후보 밖 종목·범위 밖 폭은 서비스가 걸러낸다.

    market_stance 는 필수다. 'no_trade' 면 오전에는 아무것도 사지 않는다 - 하락이 예상되는 날
    억지로 채우지 않기 위해 스키마에 박아 둔다. 그런 날도 마감 판단에서 다시 묻는다.
    """
    summary: str = Field(min_length=1,max_length=2500)
    market_view: str = Field(default='',max_length=1500)
    market_stance: Literal['normal','cautious','no_trade']
    no_trade_reason: str = Field(default='',max_length=700)
    picks: list[DayPick] = Field(max_length=12)
    top_themes: list[DayTheme] = Field(default_factory=list, max_length=5)
    ranked_symbols: list[str] = Field(default_factory=list,max_length=5)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.ranked_symbols))!=len(self.ranked_symbols):
            raise ValueError('종목 TOP5에 같은 종목이 두 번 들어 있습니다.')
        if len({t.name for t in self.top_themes}) != len(self.top_themes):
            raise ValueError('같은 테마가 두 번 들어 있습니다.')
        if len({p.symbol for p in self.picks})!=len(self.picks):
            raise ValueError('같은 종목이 두 번 들어 있습니다.')
        if self.market_stance=='no_trade' and self.picks:
            raise ValueError('no_trade 면 picks 는 비어 있어야 합니다.')
        return self


class EventDecision(StrictModel):
    symbol: str = Field(max_length=12)
    max_entry_price: float = Field(default=0, ge=0, allow_inf_nan=False)
    action: Literal['buy','hold','close']
    take_profit_pct: float = Field(default=0,ge=0,le=60)
    stop_loss_pct: float = Field(default=0,ge=0,le=30)
    reason: str = Field(min_length=8,max_length=700)


class EventPlan(StrictModel):
    """장중 이벤트(뉴스·급등) 판단."""
    summary: str = Field(min_length=1,max_length=2000)
    decisions: list[EventDecision] = Field(max_length=12)


class EodDecision(StrictModel):
    symbol: str = Field(max_length=12)
    action: Literal['hold_overnight','close']
    reason: str = Field(min_length=8,max_length=700)


class EodBuy(StrictModel):
    """마감 무렵 신규 매수. 오늘 눌린 종목을 내일 반등 목적으로 담는 경로다."""
    symbol: str = Field(max_length=12)
    max_entry_price: float = Field(default=0, ge=0, allow_inf_nan=False)
    weight_pct: float = Field(ge=1,le=30)
    take_profit_pct: float = Field(ge=.5,le=60)
    stop_loss_pct: float = Field(ge=.2,le=30)
    reason: str = Field(min_length=12,max_length=700)


class EodPlan(StrictModel):
    """마감 전 판단. 여기서 hold_overnight 가 아닌 보유분은 전부 강제 청산된다."""
    summary: str = Field(min_length=1,max_length=2000)
    decisions: list[EodDecision] = Field(max_length=12)
    buys: list[EodBuy] = Field(default_factory=list,max_length=5)

    @model_validator(mode='after')
    def unique_buys(self):
        if len({b.symbol for b in self.buys})!=len(self.buys):
            raise ValueError('같은 종목을 두 번 살 수 없습니다.')
        return self


class Adjustment(StrictModel):
    """복기에서 나온 설정 조정 제안. 서비스가 허용 키·변동 폭으로 다시 자른다."""
    key: str = Field(max_length=40)
    value: float
    reason: str = Field(min_length=4,max_length=400)


class LearningImprovement(StrictModel):
    lesson: str = Field(min_length=8,max_length=400)
    evidence_ids: list[str] = Field(default_factory=list,max_length=10)


class DayReview(StrictModel):
    """장 마감 후 복기. 왜 됐는지/안 됐는지와, 내일 바꿀 숫자·방향."""
    summary: str = Field(min_length=1,max_length=2000)
    diagnosis: str = Field(min_length=1,max_length=2500)
    lessons: list[str] = Field(default_factory=list,max_length=5)
    adjustments: list[Adjustment] = Field(default_factory=list,max_length=6)
    improvements: list[LearningImprovement] = Field(default_factory=list,max_length=3)
