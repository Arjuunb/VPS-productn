"""Immutable, research-only Pine parity port for SMC PRO v2.

This module intentionally does not appear in a production strategy registry.
It models the supplied Pine source candle-by-candle, including its unusual
right-confirmed pivot rule.  ``SMC_PRO_CORE_V1`` mirrors TradingView orders;
``SMC_PRO_GATED_V1`` adds only the dashboard's documented score gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from bot.data.indicators import atr, ema
from bot.types import Bar, Signal, SignalType
from strategies.base_strategy import HubStrategy

RESEARCH_FAMILY = "SMC_PRO_V1_RESEARCH"
PINE_SOURCE_SHA256 = "95ec2874dd52abba0d26088d1fbce6208f73ed747a885b0dc0ca89fc0fb33e8c"
EXECUTION_ALLOWED = False


@dataclass
class Pivot:
    level: float | None = None
    index: int | None = None
    occurred_at: datetime | None = None
    confirmed_at: datetime | None = None
    crossed: bool = False


@dataclass
class Structure:
    bias: int = 0
    protected_high: Pivot = field(default_factory=Pivot)
    protected_low: Pivot = field(default_factory=Pivot)
    confirmed_high: Pivot = field(default_factory=Pivot)
    confirmed_low: Pivot = field(default_factory=Pivot)


@dataclass
class OrderBlock:
    high: float
    low: float
    index: int
    bias: int
    created_at: datetime


def _barssince(index: int | None, now: int) -> int:
    return 9999 if index is None else now - index


def _sma(values: list[float], period: int) -> float:
    return sum(values[-period:]) / period if len(values) >= period else 0.0


class SMCProResearchBase(HubStrategy):
    """Causal port of all Pine state that can influence actual entries."""

    variant = "SMC_PRO_CORE_V1"
    name = "smc_pro_core_v1_research"
    label = "SMC PRO Core V1 (Research only)"
    research_version = "1.0.0-parity-audit"

    def __init__(self, symbol: str, *, internal_length: int = 5,
                 swing_length: int = 50, atr_length: int = 14,
                 stop_atr_mult: float = 1.5, rr_ratio: float = 2.5,
                 sweep_lookback: int = 10, choch_lookback: int = 8,
                 fvg_lookback: int = 5, poi_atr_buffer_mult: float = .8,
                 wick_multiplier: float = 2.0, use_rejection: bool = True,
                 structure_break_atr_mult: float = .3,
                 killzone: tuple[int, int, int, int] = (7, 11, 13, 16),
                 htf_bars: int = 48, htf_ema_length: int = 50,
                 require_volume_surge: bool = True, **params):
        super().__init__(symbol, atr_period=atr_length, atr_mult=stop_atr_mult,
                         rr_target=rr_ratio, internal_length=internal_length,
                         swing_length=swing_length, sweep_lookback=sweep_lookback,
                         choch_lookback=choch_lookback, fvg_lookback=fvg_lookback,
                         poi_atr_buffer_mult=poi_atr_buffer_mult,
                         wick_multiplier=wick_multiplier,
                         use_rejection=use_rejection,
                         structure_break_atr_mult=structure_break_atr_mult,
                         killzone=killzone, htf_bars=htf_bars,
                         htf_ema_length=htf_ema_length,
                         require_volume_surge=require_volume_surge, **params)
        self.internal = Structure(); self.swing = Structure()
        self.internal_high = Pivot(); self.internal_low = Pivot()
        self.swing_high = Pivot(); self.swing_low = Pivot()
        self.internal_obs: list[OrderBlock] = []; self.swing_obs: list[OrderBlock] = []
        self.bull_fvg_at: int | None = None; self.bear_fvg_at: int | None = None
        self.bull_sweep_at: int | None = None; self.bear_sweep_at: int | None = None
        self.bull_choch_at: int | None = None; self.bear_choch_at: int | None = None
        self._bar_delta_abs_sum = 0.0
        self.last_state: dict = {}

    def _in_killzone(self, bar: Bar) -> bool:
        # Pine default Europe/London.  Timestamps supplied to research are UTC;
        # London conversion needs zoneinfo but not a market-data lookahead.
        try:
            from zoneinfo import ZoneInfo
            hour = bar.timestamp.astimezone(ZoneInfo("Europe/London")).hour
        except Exception:  # deterministic fallback for limited Python builds
            hour = bar.timestamp.hour
        a, b, c, d = self.params["killzone"]
        return a <= hour < b or c <= hour < d

    def _htf_bias(self) -> int:
        # The Pine default is 4h close[1] and EMA(close,50)[1] with
        # lookahead_off.  On a 5m chart this is 48 completed bars per candle.
        unit = int(self.params["htf_bars"])
        completed = len(self.bars) // unit
        if completed < 2:
            return -1
        closes = [self.bars[(n + 1) * unit - 1].close for n in range(completed)]
        prior = closes[-2]
        series = ema(closes[:-1], int(self.params["htf_ema_length"]))
        return 1 if series and prior > series[-1] else -1

    def _update_pivots(self, size: int, high_pivot: Pivot, low_pivot: Pivot,
                       state: Structure, *, internal: bool, bar: Bar) -> None:
        i = len(self.bars) - 1
        if i < size:
            return
        candidate = self.bars[i - size]
        # Pine leg(): candidate is compared to the *following* size bars.  Its
        # timestamp is intentionally separate from this confirmation timestamp.
        following = self.bars[i - size + 1:i + 1]
        new_high = candidate.high > max(row.high for row in following)
        new_low = candidate.low < min(row.low for row in following)
        if not (new_high or new_low):
            return
        pivot, is_low = (low_pivot, True) if new_low else (high_pivot, False)
        pivot.level = candidate.low if is_low else candidate.high
        pivot.index = i - size; pivot.occurred_at = candidate.timestamp
        pivot.confirmed_at = bar.timestamp; pivot.crossed = False
        confirmed = state.confirmed_low if is_low else state.confirmed_high
        confirmed.level, confirmed.index = pivot.level, pivot.index
        confirmed.occurred_at, confirmed.confirmed_at = pivot.occurred_at, pivot.confirmed_at
        if is_low and state.protected_low.level is None:
            state.protected_low = Pivot(**pivot.__dict__)
        if not is_low and state.protected_high.level is None:
            state.protected_high = Pivot(**pivot.__dict__)

    def _store_ob(self, pivot: Pivot, direction: int, *, internal: bool, now: Bar) -> None:
        if pivot.index is None or pivot.index >= len(self.bars) - 1:
            return
        segment = self.bars[pivot.index:len(self.bars) - 1]
        source = max(segment, key=lambda row: row.high) if direction < 0 else min(segment, key=lambda row: row.low)
        ob = OrderBlock(source.high, source.low, self.bars.index(source), direction, now.timestamp)
        target = self.internal_obs if internal else self.swing_obs
        target.insert(0, ob); del target[100:]

    def _structure(self, state: Structure, high: Pivot, low: Pivot, *, internal: bool, bar: Bar) -> tuple[bool, bool]:
        value = atr(self.bars, int(self.params["atr_period"])) * float(self.params["structure_break_atr_mult"])
        bull_choch = state.bias != 1 and state.protected_high.level is not None and bar.close > state.protected_high.level + value
        bull_bos = state.bias == 1 and high.level is not None and not high.crossed and bar.close > high.level + value
        bear_choch = state.bias != -1 and state.protected_low.level is not None and bar.close < state.protected_low.level - value
        bear_bos = state.bias == -1 and low.level is not None and not low.crossed and bar.close < low.level - value
        bull = bull_choch or bull_bos; bear = bear_choch or bear_bos
        if bull:
            high.crossed = True; state.bias = 1
            if state.confirmed_low.level is not None: state.protected_low = Pivot(**state.confirmed_low.__dict__)
            self._store_ob(high, 1, internal=internal, now=bar)
        if bear:
            low.crossed = True; state.bias = -1
            if state.confirmed_high.level is not None: state.protected_high = Pivot(**state.confirmed_high.__dict__)
            self._store_ob(low, -1, internal=internal, now=bar)
        return bool(bull_choch), bool(bear_choch)

    def _fvg(self, bar: Bar) -> tuple[bool, bool, bool]:
        if len(self.bars) < 3:
            return False, False, False
        prior, two_back = self.bars[-2], self.bars[-3]
        delta = abs((prior.close - prior.open) / prior.open * 100) if prior.open else 0.0
        self._bar_delta_abs_sum += delta
        threshold = self._bar_delta_abs_sum / max(len(self.bars) - 1, 1) * 2
        volumes = [row.volume for row in self.bars[:-1]]
        vol_surge = (not self.params["require_volume_surge"] or
                     (len(volumes) >= 21 and prior.volume > _sma(volumes[:-1], 20) * 1.2))
        bull = bar.low > two_back.high and prior.close > two_back.high and (prior.close-prior.open)/prior.open*100 > threshold and vol_surge
        bear = bar.high < two_back.low and prior.close < two_back.low and (prior.open-prior.close)/prior.open*100 > threshold and vol_surge
        return bull, bear, vol_surge

    def _near_ob(self, direction: int, close: float, buffer: float) -> bool:
        return any(ob.bias == direction and ob.low - buffer <= close <= ob.high + buffer
                   for ob in self.internal_obs + self.swing_obs)

    def generate(self, bar: Bar) -> Optional[Signal]:
        i = len(self.bars) - 1
        self._update_pivots(int(self.params["swing_length"]), self.swing_high, self.swing_low, self.swing, internal=False, bar=bar)
        self._update_pivots(int(self.params["internal_length"]), self.internal_high, self.internal_low, self.internal, internal=True, bar=bar)
        int_bull_ch, int_bear_ch = self._structure(self.internal, self.internal_high, self.internal_low, internal=True, bar=bar)
        sw_bull_ch, sw_bear_ch = self._structure(self.swing, self.swing_high, self.swing_low, internal=False, bar=bar)
        if int_bull_ch or sw_bull_ch: self.bull_choch_at = i
        if int_bear_ch or sw_bear_ch: self.bear_choch_at = i
        bull_fvg, bear_fvg, vol_surge = self._fvg(bar)
        if bull_fvg: self.bull_fvg_at = i
        if bear_fvg: self.bear_fvg_at = i
        if len(self.bars) > int(self.params["sweep_lookback"]):
            prev = self.bars[-int(self.params["sweep_lookback"])-1:-1]
            if bar.high > max(x.high for x in prev) and bar.close < max(x.high for x in prev): self.bear_sweep_at = i
            if bar.low < min(x.low for x in prev) and bar.close > min(x.low for x in prev): self.bull_sweep_at = i
        body = abs(bar.close - bar.open); lower = min(bar.close, bar.open) - bar.low; upper = bar.high - max(bar.close, bar.open)
        bull_pin = lower >= body * float(self.params["wick_multiplier"]) and upper <= body
        bear_pin = upper >= body * float(self.params["wick_multiplier"]) and lower <= body
        htf = self._htf_bias(); current_atr = atr(self.bars, int(self.params["atr_period"])); buffer = current_atr * float(self.params["poi_atr_buffer_mult"])
        recent = lambda at, n: _barssince(at, i) <= n
        core_long = bool(current_atr and recent(self.bull_sweep_at, int(self.params["sweep_lookback"])) and recent(self.bull_choch_at, int(self.params["choch_lookback"])) and recent(self.bull_fvg_at, int(self.params["fvg_lookback"])) and htf == 1 and self._in_killzone(bar) and (bull_pin or not self.params["use_rejection"]) and self._near_ob(1, bar.close, buffer))
        core_short = bool(current_atr and recent(self.bear_sweep_at, int(self.params["sweep_lookback"])) and recent(self.bear_choch_at, int(self.params["choch_lookback"])) and recent(self.bear_fvg_at, int(self.params["fvg_lookback"])) and htf == -1 and self._in_killzone(bar) and (bear_pin or not self.params["use_rejection"]) and self._near_ob(-1, bar.close, buffer))
        context, execution = self._scores(htf, bull_pin, bear_pin, vol_surge, bar, current_atr)
        gated_long = core_long and context >= 70 and execution >= 75
        gated_short = core_short and context >= 70 and execution >= 75
        self.last_state = {"timestamp": bar.timestamp.isoformat(), "htf_bias": htf, "swing_bias": self.swing.bias, "internal_bias": self.internal.bias, "bullish_fvg": bull_fvg, "bearish_fvg": bear_fvg, "bullish_choch": int_bull_ch or sw_bull_ch, "bearish_choch": int_bear_ch or sw_bear_ch, "bars_since_sweep": min(_barssince(self.bull_sweep_at,i), _barssince(self.bear_sweep_at,i)), "bars_since_choch": min(_barssince(self.bull_choch_at,i), _barssince(self.bear_choch_at,i)), "bars_since_fvg": min(_barssince(self.bull_fvg_at,i), _barssince(self.bear_fvg_at,i)), "context_score": context, "execution_score": execution, "core_long": core_long, "core_short": core_short, "gated_long": gated_long, "gated_short": gated_short, "execution_allowed": False}
        long, short = self._entry(gated_long, gated_short, core_long, core_short)
        if not long and not short: return None
        direction = SignalType.LONG if long else SignalType.SHORT
        stop = (bar.low - current_atr * float(self.params["atr_mult"]) if long else bar.high + current_atr * float(self.params["atr_mult"]))
        target = bar.close + (bar.close - stop) * float(self.params["rr_target"]) if long else bar.close - (stop - bar.close) * float(self.params["rr_target"])
        signal = Signal(bar.timestamp, self.symbol, direction, bar.close, stop, target, f"{self.variant} closed-bar Pine parity signal")
        signal.snapshot = self.last_state.copy()
        return signal

    def _scores(self, htf: int, bull_pin: bool, bear_pin: bool, vol_surge: bool, bar: Bar, current_atr: float) -> tuple[int, int]:
        aligned = self.swing.bias == htf and self.internal.bias == htf
        near = self._near_ob(htf, bar.close, current_atr * float(self.params["poi_atr_buffer_mult"]))
        sweep = _barssince(self.bull_sweep_at if htf == 1 else self.bear_sweep_at, len(self.bars)-1) <= int(self.params["sweep_lookback"])
        structure = min(_barssince(self.bull_choch_at, len(self.bars)-1), _barssince(self.bear_choch_at, len(self.bars)-1))
        fresh = structure <= 3; reject = bull_pin if htf == 1 else bear_pin
        context = (30 if aligned else 5) + (20 if near else 0) + (15 if sweep else 0) + (15 if fresh else 0) + 10
        execution = (25 if near else 0) + (25 if fresh else 0) + (20 if vol_surge else 0) + (20 if self._in_killzone(bar) else 0) + (10 if reject or not self.params["use_rejection"] else 0)
        return min(context, 100), min(execution, 100)

    def _entry(self, gated_long: bool, gated_short: bool, core_long: bool, core_short: bool) -> tuple[bool, bool]:
        return core_long, core_short


class SMCProCoreV1Research(SMCProResearchBase):
    variant = "SMC_PRO_CORE_V1"; name = "smc_pro_core_v1_research"


class SMCProGatedV1Research(SMCProResearchBase):
    variant = "SMC_PRO_GATED_V1"; name = "smc_pro_gated_v1_research"
    def _entry(self, gated_long: bool, gated_short: bool, core_long: bool, core_short: bool) -> tuple[bool, bool]:
        return gated_long, gated_short


RESEARCH_SMC_PRO_STRATEGIES = {"smc_pro_core_v1": SMCProCoreV1Research, "smc_pro_gated_v1": SMCProGatedV1Research}
