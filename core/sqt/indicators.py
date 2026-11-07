"""Pure indicator math. No configuration, no I/O, no market-specific assumptions.

Series arrive oldest-first and every function returns a list of the same length,
with ``None`` in the leading positions that do not yet have enough history.
Keeping the shape aligned with the input lets callers index by bar position
without tracking an offset.
"""
from __future__ import annotations

import math
from typing import Sequence


def sma(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def stdev(values: Sequence[float], period: int) -> list[float | None]:
    """Population standard deviation, matching the usual Bollinger convention."""
    out: list[float | None] = [None] * len(values)
    if period <= 1 or len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / period
        out[i] = math.sqrt(var)
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI. A flat series has no losses, so it reports 100 by definition."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out

    gains = losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)

    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))


def bollinger(closes: Sequence[float], period: int = 20, k: float = 2.0):
    """Returns (middle, upper, lower) as three aligned lists."""
    mid = sma(closes, period)
    dev = stdev(closes, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for m, d in zip(mid, dev):
        if m is None or d is None:
            upper.append(None)
            lower.append(None)
        else:
            upper.append(m + k * d)
            lower.append(m - k * d)
    return mid, upper, lower
