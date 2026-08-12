"""1H directional trend confirmation."""
from __future__ import annotations

from bot.data.indicators import atr
from bot.types import Bar

from .config import AdaptiveTrendPullbackConfig
from .indicators import adx, confirmed_swings, ema_read, structure_direction
from .models import StageAssessment


class HigherTimeframeTrendEngine:
    def __init__(self, config: AdaptiveTrendPullbackConfig):
        self.config = config

    def assess(self, bars: list[Bar], direction: str) -> StageAssessment:
        cfg = self.config
        if len(bars) < cfg.minimum_bars[cfg.trend_timeframe]:
            return StageAssessment(False, 0, "INSUFFICIENT", failed=("insufficient completed 1H candles",))
        sign = 1 if direction == "LONG" else -1
        read = ema_read(bars, cfg.fast_ema, cfg.slow_ema)
        current_adx = adx(bars, cfg.adx_period)
        structure = structure_direction(bars, cfg.structure_lookback)
        highs, lows = confirmed_swings(bars, cfg.structure_lookback)
        window = bars[-cfg.structure_lookback:]
        # Use the latest *confirmed* pivot. The two candles on the right of a
        # pivot are required by confirmed_swings, so an in-progress/local
        # extreme can never move the invalidation level retroactively.
        meaningful_low = lows[-1] if lows else min(bar.low for bar in window[:-2])
        meaningful_high = highs[-1] if highs else max(bar.high for bar in window[:-2])
        structure_held = bars[-1].close > meaningful_low if sign > 0 else bars[-1].close < meaningful_high
        tests = (
            (30, (read["fast"] - read["slow"]) * sign > 0, "EMA20/EMA50 aligned"),
            (25, structure == sign, "1H swing structure aligned"),
            (20, structure_held, "meaningful swing invalidation not breached"),
            (15, current_adx >= cfg.adx_min, f"ADX {current_adx:.1f} acceptable"),
            (10, (bars[-1].close - read["slow"]) * sign >= -atr(bars, cfg.atr_period),
             "price remains near directional structure"),
        )
        score = sum(weight for weight, ok, _ in tests if ok)
        return StageAssessment(
            score >= cfg.trend_confidence_min, score,
            "BULLISH" if sign > 0 else "BEARISH",
            reasons=tuple(label for _, ok, label in tests if ok),
            failed=tuple(label for _, ok, label in tests if not ok),
            swing_low=meaningful_low, swing_high=meaningful_high,
        )
