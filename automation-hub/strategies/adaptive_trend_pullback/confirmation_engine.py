"""5M entry trigger confirmation. A zone touch alone is never sufficient."""
from __future__ import annotations

from bot.types import Bar

from .config import AdaptiveTrendPullbackConfig
from .indicators import average_volume
from .models import StageAssessment


class EntryConfirmationEngine:
    def __init__(self, config: AdaptiveTrendPullbackConfig):
        self.config = config

    def assess(self, bars: list[Bar], direction: str) -> StageAssessment:
        cfg = self.config
        if len(bars) < cfg.minimum_bars[cfg.entry_timeframe]:
            return StageAssessment(False, 0, "INSUFFICIENT", failed=("insufficient completed 5M candles",))
        sign = 1 if direction == "LONG" else -1
        current, previous = bars[-1], bars[-2]
        body = abs(current.close - current.open)
        lower_wick = min(current.open, current.close) - current.low
        upper_wick = current.high - max(current.open, current.close)
        rejection = (lower_wick > body * cfg.rejection_wick_body_ratio
                     and current.close > current.open) if sign > 0 else (
                         upper_wick > body * cfg.rejection_wick_body_ratio
                         and current.close < current.open)
        engulfing = (current.close > current.open and previous.close < previous.open
                     and current.close >= previous.open and current.open <= previous.close) if sign > 0 else (
                         current.close < current.open and previous.close > previous.open
                         and current.open >= previous.close and current.close <= previous.open)
        local = bars[-cfg.confirmation_lookback - 1:-1]
        structure_break = current.close > max(bar.high for bar in local) if sign > 0 else current.close < min(bar.low for bar in local)
        dominance = body >= sum(abs(bar.close - bar.open) for bar in bars[-6:-1]) / 5 and (current.close - current.open) * sign > 0
        avg_volume = average_volume(bars[:-1], 20)
        volume = (current.volume >= avg_volume * cfg.confirmation_volume_multiple
                  if avg_volume else False)
        candle_confirmation = rejection or engulfing or dominance
        tests = (
            (40, candle_confirmation, "directional rejection/engulfing/dominance candle"),
            (35, structure_break, "5M local structure break confirmed on close"),
            (25, volume, "directional volume expanded"),
        )
        score = sum(weight for weight, ok, _ in tests if ok)
        valid = candle_confirmation and structure_break
        return StageAssessment(
            valid, score, "CONFIRMED" if valid else "NONE",
            reasons=tuple(label for _, ok, label in tests if ok),
            failed=tuple(label for _, ok, label in tests if not ok),
            volume_confirmed=volume,
        )
