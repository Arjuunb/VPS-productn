"""Causal liquidity-sweep reversal strategy for paper trading and research.

A long setup requires the completed candle to take the lowest low of the prior
window, reclaim that level by a meaningful ATR fraction, and close bullish.
Shorts are the exact mirror. The stop remains beyond the swept extreme, so the
trade is invalidated precisely where the setup is proven wrong.

This is intentionally narrower than the SMC strategy: it does *not* invent a
CHoCH or fair-value-gap confirmation. The common engine quality/risk gates
remain in front of every paper order.
"""
from __future__ import annotations

from typing import Optional

from bot.data.indicators import atr
from bot.types import Bar, Signal, SignalType
from strategies.base_strategy import HubStrategy


class LiquiditySweepStrategy(HubStrategy):
    name = "liquidity_sweep"
    label = "Liquidity Sweep"
    supported_regimes = ()  # reversal signals are independently quality-gated

    def __init__(self, symbol: str, *, lookback: int = 20, warmup: int = 30,
                 atr_period: int = 14, min_wick_atr: float = 0.15,
                 min_reclaim_atr: float = 0.10, stop_atr_buffer: float = 0.25,
                 rr_target: float = 2.0, max_history: int = 600, **params):
        if lookback < 3:
            raise ValueError("lookback must be at least 3")
        if warmup < lookback + 1:
            raise ValueError("warmup must exceed lookback")
        if min_wick_atr < 0 or min_reclaim_atr < 0 or stop_atr_buffer < 0:
            raise ValueError("liquidity-sweep ATR thresholds must be non-negative")
        super().__init__(symbol, atr_period=atr_period, lookback=lookback, warmup=warmup,
                         min_wick_atr=min_wick_atr, min_reclaim_atr=min_reclaim_atr,
                         stop_atr_buffer=stop_atr_buffer, rr_target=rr_target, **params)
        self.max_history = max_history

    def generate(self, bar: Bar) -> Optional[Signal]:
        p = self.params
        if len(self.bars) > self.max_history:
            del self.bars[:-self.max_history]
        if len(self.bars) < max(p["warmup"] + 1, p["lookback"] + 2):
            return None

        prior = self.bars[-p["lookback"] - 1:-1]
        prior_high = max(candidate.high for candidate in prior)
        prior_low = min(candidate.low for candidate in prior)
        current_atr = atr(self.bars, p["atr_period"])
        if current_atr <= 0:
            return None

        low_wick = prior_low - bar.low
        low_reclaim = bar.close - prior_low
        high_wick = bar.high - prior_high
        high_reclaim = prior_high - bar.close
        min_wick = current_atr * p["min_wick_atr"]
        min_reclaim = current_atr * p["min_reclaim_atr"]

        bull_sweep = (low_wick >= min_wick and low_reclaim >= min_reclaim
                      and bar.close > bar.open)
        bear_sweep = (high_wick >= min_wick and high_reclaim >= min_reclaim
                      and bar.close < bar.open)
        if bull_sweep == bear_sweep:  # neither, or an ambiguous two-sided candle
            return None

        if bull_sweep:
            stop = bar.low - current_atr * p["stop_atr_buffer"]
            risk = bar.close - stop
            if risk <= 0:
                return None
            return self._sweep_signal(bar, SignalType.LONG, stop,
                                      bar.close + risk * p["rr_target"], low_wick, current_atr,
                                      f"Liquidity sweep long — reclaimed {p['lookback']}-bar low")

        stop = bar.high + current_atr * p["stop_atr_buffer"]
        risk = stop - bar.close
        if risk <= 0:
            return None
        return self._sweep_signal(bar, SignalType.SHORT, stop,
                                  bar.close - risk * p["rr_target"], high_wick, current_atr,
                                  f"Liquidity sweep short — rejected {p['lookback']}-bar high")

    def _sweep_signal(self, bar: Bar, side: SignalType, stop: float, target: float,
                      wick: float, current_atr: float, reason: str) -> Signal:
        # A larger stop-hunt gets slightly more confidence; downstream quality
        # and portfolio-risk checks remain authoritative.
        confidence = max(0.55, min(0.85, 0.60 + 0.10 * (wick / current_atr)))
        return Signal(timestamp=bar.timestamp, symbol=self.symbol, type=side, entry=bar.close,
                      stop_loss=stop, take_profit=target, reason=reason,
                      confidence=confidence)
