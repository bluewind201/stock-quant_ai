from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('시각에 시간대가 필요합니다. 예: 2026-09-01T13:30:00Z')
    return result.astimezone(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Instrument(StrictModel):
    symbol: str = Field(max_length=12)
    exchange: Literal['KRX', 'NASDAQ', 'NYSE', 'AMEX']
    name: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def valid_symbol(self):
        pattern = r'[A-Z0-9]{6}' if self.exchange == 'KRX' else r'[A-Z][A-Z0-9.\-]{0,9}'
        if not re.fullmatch(pattern, self.symbol):
            raise ValueError('국내 종목은 6자리 코드, 미국 종목은 대문자 티커를 입력하세요.')
        return self

    @property
    def currency(self):
        return 'KRW' if self.exchange == 'KRX' else 'USD'


class Bar(StrictModel):
    date: str
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode='after')
    def valid_bar(self):
        datetime.strptime(self.date, '%Y-%m-%d')
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError('OHLC 가격 범위가 올바르지 않습니다.')
        return self


class Evidence(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    url: str = Field(max_length=2000)
    source: Literal['fed', 'white_house', 'bls', 'bea', 'company', 'news', 'manual', 'demo']
    published_at: datetime
    available_at: datetime
    symbols: list[str] = Field(default_factory=list, max_length=100)
    sentiment: float = Field(default=0, ge=-1, le=1)
    confidence: float = Field(default=0, ge=0, le=1)
    impact: Literal['low', 'medium', 'high'] = 'medium'
    assessment: str = Field(default='미평가: 제목만으로 매수 방향을 추정하지 않습니다.', max_length=2000)
    assessed_at: datetime | None = None

    @field_validator('published_at', 'available_at', 'assessed_at')
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError('뉴스 시각에 시간대가 필요합니다.')
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode='after')
    def valid_evidence(self):
        if not self.url.startswith('https://'):
            raise ValueError('HTTPS 원문 링크가 필요합니다.')
        if self.available_at < self.published_at:
            raise ValueError('확인 가능 시각은 발표 시각보다 빠를 수 없습니다.')
        if self.confidence and self.assessed_at is None:
            raise ValueError('평가를 작성한 시각이 필요합니다.')
        if self.assessed_at and self.assessed_at < self.available_at:
            raise ValueError('평가 시각은 확인 가능 시각 이후여야 합니다.')
        return self


class Event(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    source: Literal['fed', 'white_house', 'bls', 'bea', 'company', 'manual', 'demo']
    url: str = Field(max_length=2000)
    scheduled_at: datetime
    known_at: datetime
    impact: Literal['low', 'medium', 'high'] = 'high'
    symbols: list[str] = Field(default_factory=list, max_length=100)
    actual: float | None = None
    consensus: float | None = None
    previous: float | None = None
    unit: str = Field(default='', max_length=30)

    @field_validator('scheduled_at', 'known_at')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('일정 시각에 시간대가 필요합니다.')
        return value.astimezone(timezone.utc)

    @field_validator('url')
    @classmethod
    def https(cls, value):
        if not value.startswith('https://'):
            raise ValueError('HTTPS 출처가 필요합니다.')
        return value


class Strategy(StrictModel):
    initial_cash: float = Field(default=10000000, ge=100, le=1e12)
    threshold: float = Field(default=65, ge=0, le=100)
    min_technical: float = Field(default=45, ge=0, le=70)
    position_pct: float = Field(default=20, gt=0, le=100)
    risk_pct: float = Field(default=0.5, gt=0, le=5)
    stop_atr: float = Field(default=2, ge=0.5, le=10)
    reward_risk: float = Field(default=2, ge=0.5, le=10)
    max_gap_pct: float = Field(default=4, gt=0, le=30)
    daily_loss_pct: float = Field(default=2, gt=0, le=20)
    fee_bps: float = Field(default=10, ge=0, le=500)
    sell_tax_bps: float = Field(default=0, ge=0, le=500)
    slippage_bps: float = Field(default=10, ge=0, le=500)
    news_required: bool = True
    news_max_hours: int = Field(default=72, ge=1, le=168)
    event_before_minutes: int = Field(default=60, ge=0, le=1440)
    event_after_minutes: int = Field(default=120, ge=0, le=1440)


class DatasetInput(StrictModel):
    instrument: Instrument
    bars: list[Bar] = Field(min_length=65, max_length=5000)
    source: str = Field(default='CSV 업로드', min_length=1, max_length=200)
    adjusted: bool = True
    demo: bool = False

    @model_validator(mode='after')
    def sorted_unique(self):
        dates = [b.date for b in self.bars]
        if dates != sorted(set(dates)):
            raise ValueError('일봉은 과거부터 최신순이어야 하며 중복 날짜가 없어야 합니다.')
        return self
