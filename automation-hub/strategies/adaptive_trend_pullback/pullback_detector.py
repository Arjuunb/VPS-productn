"""15M pullback and trade-location assessment."""
from __future__ import annotations

from bot.data.indicators import atr, ema
from bot.types import Bar

from .config import AdaptiveTrendPullbackConfig
from .indicators import average_volume, confirmed_swings
from .models import StageAssessment


class PullbackDetector:
    def __init__(self, config: AdaptiveTrendPullbackConfig):
        self.config = config

    def assess(self, bars: list[Bar], direction: str) -> StageAssessment:
        cfg = self.config
        if len(bars) < cfg.minimum_bars[cfg.pullback_timeframe]:
            return StageAssessment(False, 0, "INSUFFICIENT", failed=("insufficient completed 15M candles",))
        sign = 1 if direction == "LONG" else -1
        window = bars[-cfg.pullback_lookback:]
        current_atr = atr(bars, cfg.atr_period)
        ema20 = ema([bar.close for bar in bars], cfg.fast_ema)[-1]
        highs, lows = confirmed_swings(bars, cfg.structure_lookback)
        swing_low = lows[-1] if lows else min(bar.low for bar in window)
        swing_high = highs[-1] if highs else max(bar.high for bar in window)
        invalidated = bars[-1].close <= swing_low if sign > 0 else bars[-1].close >= swing_high
        near_ema = abs(bars[-1].close - ema20) <= current_atr * cfg.pullback_zone_atr
        recent_extreme = max(bar.high for bar in window[:-3]) if sign > 0 else min(bar.low for bar in window[:-3])
        near_breakout = abs(bars[-1].close - recent_extreme) <= current_atr * cfg.pullback_zone_atr
        location = "EMA20 dynamic support/resistance" if near_ema else "prior breakout structure" if near_breakout else None
        recent = bars[-4:]
        net = (recent[-1].close - recent[0].open) * sign
        corrective = net <= current_atr * cfg.corrective_move_atr
        avg_volume = average_volume(bars[:-1], 20)
        abnormal_opposite_volume = (bars[-1].volume > avg_volume * cfg.abnormal_volume_multiple
                                    and (bars[-1].close - bars[-1].open) * sign < 0) if avg_volume else False
        tests = (
            (35, not invalidated, "important pullback swing remains valid"),
            (30, bool(location), location or "not at a validated pullback location"),
            (20, corrective, "retracement is corrective, not an impulse"),
            (15, not abnormal_opposite_volume, "opposing volume is not abnormal"),
        )
        score = sum(weight for weight, ok, _ in tests if ok)
        valid = not invalidated and bool(location) and corrective and not abnormal_opposite_volume
        return StageAssessment(
            valid, score, "VALID" if valid else "INVALID",
            reasons=tuple(label for _, ok, label in tests if ok),
            failed=tuple(label for _, ok, label in tests if not ok),
            swing_low=swing_low, swing_high=swing_high, location=location,
            volume_confirmed=not abnormal_opposite_volume,
        )
