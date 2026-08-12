"""Small deterministic indicator and confirmed-structure helpers."""
from __future__ import annotations

from statistics import median
from typing import Sequence

from bot.data.indicators import atr, ema, true_range
from bot.types import Bar


def adx(bars: Sequence[Bar], period: int = 14) -> float:
    """Wilder ADX; returns zero until a complete 2*period history exists."""
    if period < 1 or len(bars) < period * 2 + 1:
        return 0.0
    tr, plus_dm, minus_dm = [], [], []
    for previous, current in zip(bars, bars[1:]):
        tr.append(true_range(previous.close, current))
        up, down = current.high - previous.high, previous.low - current.low
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    sm_tr = sum(tr[:period])
    sm_plus = sum(plus_dm[:period])
    sm_minus = sum(minus_dm[:period])
    dx: list[float] = []
    for i in range(period, len(tr)):
        sm_tr = sm_tr - sm_tr / period + tr[i]
        sm_plus = sm_plus - sm_plus / period + plus_dm[i]
        sm_minus = sm_minus - sm_minus / period + minus_dm[i]
        plus_di = 100 * sm_plus / sm_tr if sm_tr else 0.0
        minus_di = 100 * sm_minus / sm_tr if sm_tr else 0.0
        denom = plus_di + minus_di
        dx.append(100 * abs(plus_di - minus_di) / denom if denom else 0.0)
    if len(dx) < period:
        return 0.0
    value = sum(dx[:period]) / period
    for sample in dx[period:]:
        value = (value * (period - 1) + sample) / period
    return value


def ema_read(bars: Sequence[Bar], fast: int, slow: int) -> dict[str, float]:
    closes = [bar.close for bar in bars]
    fast_values, slow_values = ema(closes, fast), ema(closes, slow)
    lag = min(3, len(closes) - 1)
    return {
        "fast": fast_values[-1], "slow": slow_values[-1],
        "fast_slope": fast_values[-1] - fast_values[-1 - lag],
        "slow_slope": slow_values[-1] - slow_values[-1 - lag],
    }


def confirmed_swings(bars: Sequence[Bar], lookback: int, pivot: int = 2) -> tuple[list[float], list[float]]:
    window = list(bars[-max(lookback, pivot * 2 + 1):])
    highs: list[float] = []
    lows: list[float] = []
    for index in range(pivot, len(window) - pivot):
        segment = window[index - pivot:index + pivot + 1]
        candidate = window[index]
        if candidate.high == max(item.high for item in segment):
            highs.append(candidate.high)
        if candidate.low == min(item.low for item in segment):
            lows.append(candidate.low)
    return highs, lows


def structure_direction(bars: Sequence[Bar], lookback: int) -> int:
    highs, lows = confirmed_swings(bars, lookback)
    if len(highs) < 2 or len(lows) < 2:
        return 0
    if highs[-1] > highs[-2] and lows[-1] > lows[-2]:
        return 1
    if highs[-1] < highs[-2] and lows[-1] < lows[-2]:
        return -1
    return 0


def atr_expansion(bars: Sequence[Bar], period: int) -> float:
    current = atr(bars, period)
    samples = []
    for end in range(max(period + 1, len(bars) - 20), len(bars)):
        value = atr(bars[:end], period)
        if value > 0:
            samples.append(value)
    baseline = median(samples) if samples else current
    return current / baseline if baseline else 0.0


def average_volume(bars: Sequence[Bar], length: int = 20) -> float:
    window = list(bars[-length:])
    return sum(float(bar.volume or 0) for bar in window) / len(window) if window else 0.0
