"""Small, isolated V3 research candidates.

These candidates are deliberately absent from the production strategy registry.
They only consume already-closed 15-minute bars, keep the 1h context causally
aggregated, and have no learning-engine or AI dependency.
"""
from __future__ import annotations

from typing import Optional

from bot.data.indicators import atr
from bot.types import Bar, Signal, SignalType
from strategies.base_strategy import HubStrategy


def _efficiency(bars: list[Bar], period: int) -> float:
    if len(bars) < period + 1:
        return 0.0
    closes = [bar.close for bar in bars[-period - 1:]]
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / path if path else 0.0


class _ClosedHourContext(HubStrategy):
    """Build a 1h bar only after all four constituent 15m bars have closed."""

    def __init__(self, symbol: str, **params):
        super().__init__(symbol, **params)
        self._hour_bars: list[Bar] = []
        self._hour_bucket: list[Bar] = []
        self._bucket_hour = None

    def _record_closed_hour(self, bar: Bar) -> None:
        key = bar.timestamp.replace(minute=0, second=0, microsecond=0)
        if self._bucket_hour is None:
            self._bucket_hour = key
        if key != self._bucket_hour:
            # This only happens if input has a discontinuity. Never construct a
            # partial higher-timeframe candle from it.
            self._hour_bucket, self._bucket_hour = [], key
        self._hour_bucket.append(bar)
        if len(self._hour_bucket) != 4:
            return
        rows = self._hour_bucket
        self._hour_bars.append(Bar(
            timestamp=self._bucket_hour, open=rows[0].open,
            high=max(row.high for row in rows), low=min(row.low for row in rows),
            close=rows[-1].close, volume=sum(row.volume for row in rows),
        ))
        if len(self._hour_bars) > 80:
            del self._hour_bars[:-80]
        self._hour_bucket = []
        self._bucket_hour = None

    def _hour_direction(self, lookback: int, minimum_er: float) -> int:
        if len(self._hour_bars) < lookback + 1:
            return 0
        rows = self._hour_bars
        change = rows[-1].close - rows[-lookback - 1].close
        if _efficiency(rows, lookback) < minimum_er:
            return 0
        return 1 if change > 0 else -1 if change < 0 else 0

    def _structural_signal(self, bar: Bar, direction: SignalType, *, structure: int,
                           rr_target: float, reason: str, regime: str) -> Optional[Signal]:
        current_atr = atr(self.bars, int(self.params["atr_period"]))
        if current_atr <= 0 or len(self.bars) < structure + 1:
            return None
        prior = self.bars[-structure - 1:-1]
        if direction == SignalType.LONG:
            stop = min(row.low for row in prior) - .10 * current_atr
            risk = bar.close - stop
            target = bar.close + rr_target * risk
        else:
            stop = max(row.high for row in prior) + .10 * current_atr
            risk = stop - bar.close
            target = bar.close - rr_target * risk
        if risk <= 0:
            return None
        signal = Signal(bar.timestamp, self.symbol, direction, bar.close, stop, target, reason)
        signal.regime = regime
        signal.confidence = .60
        return signal


class TrendPullbackV3Research(_ClosedHourContext):
    """15m closed-bar pullback continuation with a minimal 1h trend context."""

    name = "trend_pullback_v3_research"
    label = "Trend Pullback V3 Research"
    research_version = "3.0.0-research.1"

    def __init__(self, symbol: str, *, htf_lookback: int = 4, htf_min_er: float = .35,
                 trend_lookback: int = 16, trend_min_er: float = .45,
                 pullback_bars: int = 3, pullback_atr: float = .50,
                 confirmation_body_atr: float = .15, structure_bars: int = 4,
                 rr_target: float = 2.0, allow_short: bool = True, **params):
        super().__init__(symbol, atr_period=14, htf_lookback=htf_lookback,
                         htf_min_er=htf_min_er, trend_lookback=trend_lookback,
                         trend_min_er=trend_min_er, pullback_bars=pullback_bars,
                         pullback_atr=pullback_atr,
                         confirmation_body_atr=confirmation_body_atr,
                         structure_bars=structure_bars, rr_target=rr_target,
                         allow_short=allow_short, **params)

    def generate(self, bar: Bar) -> Optional[Signal]:
        self._record_closed_hour(bar)
        p = self.params
        lookback = max(int(p["trend_lookback"]), int(p["pullback_bars"]) + 1)
        if len(self.bars) < lookback + 2:
            return None
        hour_dir = self._hour_direction(int(p["htf_lookback"]), float(p["htf_min_er"]))
        local_change = bar.close - self.bars[-int(p["trend_lookback"]) - 1].close
        local_dir = 1 if local_change > 0 else -1 if local_change < 0 else 0
        if not hour_dir or hour_dir != local_dir or _efficiency(self.bars, int(p["trend_lookback"])) < float(p["trend_min_er"]):
            return None
        current_atr = atr(self.bars, 14)
        if current_atr <= 0:
            return None
        pullback_change = bar.close - self.bars[-int(p["pullback_bars"]) - 1].close
        body = bar.close - bar.open
        if hour_dir > 0:
            if pullback_change > -float(p["pullback_atr"]) * current_atr or body < float(p["confirmation_body_atr"]) * current_atr:
                return None
            return self._structural_signal(bar, SignalType.LONG, structure=int(p["structure_bars"]),
                                           rr_target=float(p["rr_target"]),
                                           reason="V3 1h trend + 15m closed pullback confirmation",
                                           regime="htf-trend-pullback")
        if not bool(p["allow_short"]):
            return None
        if pullback_change < float(p["pullback_atr"]) * current_atr or body > -float(p["confirmation_body_atr"]) * current_atr:
            return None
        return self._structural_signal(bar, SignalType.SHORT, structure=int(p["structure_bars"]),
                                       rr_target=float(p["rr_target"]),
                                       reason="V3 1h trend + 15m closed pullback confirmation",
                                       regime="htf-trend-pullback")


class VolatilityExpansionV3Research(_ClosedHourContext):
    """Compression-to-expansion with independent 1h direction, not a breakout."""

    name = "volatility_expansion_v3_research"
    label = "Volatility Expansion V3 Research"
    research_version = "3.0.0-research.1"

    def __init__(self, symbol: str, *, htf_lookback: int = 4, htf_min_er: float = .35,
                 compression_ratio: float = .80, expansion_ratio: float = 1.25,
                 compression_lookback: int = 8, body_atr: float = .35,
                 structure_bars: int = 4, rr_target: float = 2.0,
                 allow_short: bool = True, **params):
        super().__init__(symbol, atr_period=14, htf_lookback=htf_lookback,
                         htf_min_er=htf_min_er, compression_ratio=compression_ratio,
                         expansion_ratio=expansion_ratio,
                         compression_lookback=compression_lookback, body_atr=body_atr,
                         structure_bars=structure_bars, rr_target=rr_target,
                         allow_short=allow_short, **params)

    def generate(self, bar: Bar) -> Optional[Signal]:
        self._record_closed_hour(bar)
        p = self.params
        lookback = int(p["compression_lookback"])
        if len(self.bars) < 55 + lookback:
            return None
        hour_dir = self._hour_direction(int(p["htf_lookback"]), float(p["htf_min_er"]))
        if not hour_dir or (hour_dir < 0 and not bool(p["allow_short"])):
            return None
        fast, slow = atr(self.bars, 10), atr(self.bars, 50)
        if fast <= 0 or slow <= 0 or fast / slow < float(p["expansion_ratio"]):
            return None
        # The preceding state must have been compressed; the current expansion
        # is not itself treated as a channel/price breakout.
        prior_ratios = []
        for offset in range(1, lookback + 1):
            prior_fast = atr(self.bars[:-offset], 10)
            prior_slow = atr(self.bars[:-offset], 50)
            if prior_fast > 0 and prior_slow > 0:
                prior_ratios.append(prior_fast / prior_slow)
        if not prior_ratios or min(prior_ratios) > float(p["compression_ratio"]):
            return None
        body = bar.close - bar.open
        if hour_dir > 0 and body >= float(p["body_atr"]) * fast:
            return self._structural_signal(bar, SignalType.LONG, structure=int(p["structure_bars"]),
                                           rr_target=float(p["rr_target"]),
                                           reason="V3 compression-to-expansion + independent 1h direction",
                                           regime="compression-expansion")
        if hour_dir < 0 and body <= -float(p["body_atr"]) * fast:
            return self._structural_signal(bar, SignalType.SHORT, structure=int(p["structure_bars"]),
                                           rr_target=float(p["rr_target"]),
                                           reason="V3 compression-to-expansion + independent 1h direction",
                                           regime="compression-expansion")
        return None


RESEARCH_V3_STRATEGIES = {
    "trend_pullback_v3": TrendPullbackV3Research,
    "volatility_expansion_v3": VolatilityExpansionV3Research,
}
