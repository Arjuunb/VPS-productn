"""Isolated V2 research strategies.

These classes are intentionally absent from the production strategy registry.
They exist only for the blind research harness and must receive a new immutable
version before any reviewed promotion.  V1 source files remain unchanged.
"""

from __future__ import annotations

from typing import Optional

from bot.data.indicators import atr
from bot.types import Bar, Signal, SignalType
from strategies.base_strategy import HubStrategy
from strategies.brain_strategy import DecisionBrain


def efficiency_ratio(bars: list[Bar], window: int = 30) -> float:
    if len(bars) < window + 1:
        return 0.0
    closes = [bar.close for bar in bars[-(window + 1):]]
    path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
    return abs(closes[-1] - closes[0]) / path if path else 0.0


class SupertrendV2Research(HubStrategy):
    """Supertrend flips qualified by causal trend efficiency and volatility."""

    name = "supertrend_v2_research"
    label = "Supertrend V2 Research"
    research_version = "2.0.0-research.1"

    def __init__(self, symbol: str, *, period: int = 10, mult: float = 3.0,
                 er_window: int = 30, min_er: float = 0.25,
                 min_atr_pct: float = 0.003, max_history: int = 320, **params):
        params.setdefault("rr_target", 2.5)
        super().__init__(symbol, period=period, mult=mult, er_window=er_window,
                         min_er=min_er, min_atr_pct=min_atr_pct, **params)
        self.max_history = max_history
        self._tr_seed: list[float] = []
        self._wilder_atr: float | None = None
        self._final_upper: float | None = None
        self._final_lower: float | None = None
        self._direction = 1

    def generate(self, bar: Bar) -> Optional[Signal]:
        p = self.params
        if len(self.bars) > self.max_history:
            del self.bars[:-self.max_history]
        if len(self.bars) == 1:
            self._final_upper = self._final_lower = bar.close
            return None

        previous = self.bars[-2]
        tr = max(bar.high - bar.low, abs(bar.high - previous.close), abs(bar.low - previous.close))
        period = int(p["period"])
        if self._wilder_atr is None:
            self._tr_seed.append(tr)
            if len(self._tr_seed) < period:
                return None
            self._wilder_atr = sum(self._tr_seed[-period:]) / period
        else:
            self._wilder_atr = (self._wilder_atr * (period - 1) + tr) / period

        midpoint = (bar.high + bar.low) / 2.0
        upper = midpoint + float(p["mult"]) * self._wilder_atr
        lower = midpoint - float(p["mult"]) * self._wilder_atr
        prior_upper = self._final_upper if self._final_upper is not None else upper
        prior_lower = self._final_lower if self._final_lower is not None else lower
        self._final_upper = upper if upper < prior_upper or previous.close > prior_upper else prior_upper
        self._final_lower = lower if lower > prior_lower or previous.close < prior_lower else prior_lower

        prior_direction = self._direction
        if bar.close > self._final_upper:
            self._direction = 1
        elif bar.close < self._final_lower:
            self._direction = -1
        if self._direction == prior_direction:
            return None

        er = efficiency_ratio(self.bars, int(p["er_window"]))
        atr_pct = self._wilder_atr / bar.close if bar.close else 0.0
        if er < float(p["min_er"]) or atr_pct < float(p["min_atr_pct"]):
            return None
        direction = SignalType.LONG if self._direction > 0 else SignalType.SHORT
        signal = self._signal(
            bar, direction,
            f"V2 qualified Supertrend flip · ER {er:.3f} · ATR {atr_pct:.3%}",
        )
        if signal is not None:
            signal.confidence = round(min(1.0, 0.5 + er / 2.0), 3)
            signal.regime = "efficient-trend"
        return signal


class DonchianV2Research(HubStrategy):
    """Breakout requiring close penetration, channel width and volume support."""

    name = "donchian_v2_research"
    label = "Donchian V2 Research"
    research_version = "2.0.0-research.1"

    def __init__(self, symbol: str, *, channel: int = 30,
                 volume_window: int = 20, min_volume_ratio: float = 1.0,
                 min_penetration_atr: float = 0.10,
                 min_channel_width_pct: float = 0.004,
                 max_history: int = 320, **params):
        params.setdefault("rr_target", 2.5)
        super().__init__(symbol, channel=channel, volume_window=volume_window,
                         min_volume_ratio=min_volume_ratio,
                         min_penetration_atr=min_penetration_atr,
                         min_channel_width_pct=min_channel_width_pct, **params)
        self.max_history = max_history
        self._last_dir = 0

    def generate(self, bar: Bar) -> Optional[Signal]:
        p = self.params
        if len(self.bars) > self.max_history:
            del self.bars[:-self.max_history]
        channel = int(p["channel"])
        volume_window = int(p["volume_window"])
        if len(self.bars) < max(channel, volume_window) + 2:
            return None
        prior = self.bars[-channel - 1:-1]
        highest = max(row.high for row in prior)
        lowest = min(row.low for row in prior)
        width_pct = (highest - lowest) / bar.close if bar.close else 0.0
        current_atr = atr(self.bars, 14)
        volumes = [row.volume for row in self.bars[-volume_window - 1:-1]]
        average_volume = sum(volumes) / len(volumes) if volumes else 0.0
        volume_ratio = bar.volume / average_volume if average_volume else 0.0
        if (current_atr <= 0 or width_pct < float(p["min_channel_width_pct"])
                or volume_ratio < float(p["min_volume_ratio"])):
            return None

        long_penetration = (bar.close - highest) / current_atr
        short_penetration = (lowest - bar.close) / current_atr
        threshold = float(p["min_penetration_atr"])
        if long_penetration >= threshold and self._last_dir != 1:
            self._last_dir = 1
            direction = SignalType.LONG
            penetration = long_penetration
        elif short_penetration >= threshold and self._last_dir != -1:
            self._last_dir = -1
            direction = SignalType.SHORT
            penetration = short_penetration
        else:
            return None

        signal = self._signal(
            bar, direction,
            f"V2 confirmed Donchian breakout · penetration {penetration:.2f} ATR · volume {volume_ratio:.2f}x",
        )
        if signal is not None:
            signal.confidence = round(min(1.0, 0.5 + min(0.5, penetration / 2.0)), 3)
            signal.regime = "volume-confirmed-breakout"
        return signal


class DecisionBrainV2Research(DecisionBrain):
    """Explainable V1 Brain plus a causal efficiency and optional side gate."""

    name = "brain_v2_research"
    label = "Decision Brain V2 Research"
    research_version = "2.0.0-research.1"

    def __init__(self, symbol: str, *, er_window: int = 30,
                 min_er: float = 0.20, allow_short: bool = True, **params):
        super().__init__(symbol, **params)
        self.er_window = int(er_window)
        self.min_er = float(min_er)
        self.allow_short = bool(allow_short)
        self.params.update({"er_window": self.er_window, "min_er": self.min_er,
                            "allow_short": self.allow_short})

    def generate(self, bar: Bar) -> Optional[Signal]:
        signal = super().generate(bar)
        if signal is None:
            return None
        er = efficiency_ratio(self.bars, self.er_window)
        if er < self.min_er:
            return None
        if signal.type == SignalType.SHORT and not self.allow_short:
            return None
        signal.reason = f"V2 ER {er:.3f} · {signal.reason}"
        signal.confidence = round(min(1.0, signal.confidence * (0.75 + er / 2.0)), 3)
        signal.snapshot = {**getattr(signal, "snapshot", {}), "efficiency_ratio": round(er, 4)}
        signal.checklist = [
            *getattr(signal, "checklist", []),
            {"name": "Directional efficiency", "status": "Passed",
             "detail": f"ER {er:.3f} ≥ {self.min_er:.3f}"},
            {"name": "Research side policy", "status": "Passed",
             "detail": "bidirectional" if self.allow_short else "long only"},
        ]
        return signal


RESEARCH_STRATEGIES = {
    "supertrend_v2": SupertrendV2Research,
    "donchian_v2": DonchianV2Research,
    "brain_v2": DecisionBrainV2Research,
}
