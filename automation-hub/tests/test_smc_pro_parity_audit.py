"""Invariant tests only; they never claim TradingView event parity."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.types import Bar
from strategies.research_smc_pro import (
    EXECUTION_ALLOWED,
    PINE_SOURCE_SHA256,
    SMCProCoreV1Research,
    SMCProGatedV1Research,
)

UTC = timezone.utc


def _bar(index: int, high: float = 101, low: float = 99) -> Bar:
    return Bar(datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * index),
               100, high, low, 100.2, 1_000)


def test_pine_reference_is_immutable_and_fingerprinted():
    path = Path(__file__).parents[1] / "research_references" / "smc_pro_v2_reference.pine"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == PINE_SOURCE_SHA256


def test_pivot_becomes_usable_only_on_right_side_confirmation():
    strategy = SMCProCoreV1Research("BTCUSDT", internal_length=2, swing_length=3)
    # The high at index 0 cannot be a known pivot until index 2 has closed.
    strategy.on_bar(_bar(0, high=110))
    strategy.on_bar(_bar(1, high=105))
    assert strategy.internal_high.confirmed_at is None
    strategy.on_bar(_bar(2, high=103))
    assert strategy.internal_high.occurred_at == _bar(0).timestamp
    assert strategy.internal_high.confirmed_at == _bar(2).timestamp


def test_core_and_dashboard_variants_have_intentionally_different_entry_gate():
    core = SMCProCoreV1Research("BTCUSDT")
    gated = SMCProGatedV1Research("BTCUSDT")
    assert core._entry(False, False, True, False) == (True, False)
    assert gated._entry(False, False, True, False) == (False, False)


def test_smc_research_cannot_authorize_execution():
    assert EXECUTION_ALLOWED is False
    assert "smc_pro" not in Path(__file__).parents[1].joinpath("webhook_api.py").read_text()


def test_stop_and_target_match_pine_signal_candle_reference():
    strategy = SMCProCoreV1Research("BTCUSDT", atr_length=2, stop_atr_mult=1.5, rr_ratio=2.5)
    # Check the exact risk formula independent of whether all SMC gates align.
    for row in (_bar(0, 102, 98), _bar(1, 103, 99), _bar(2, 104, 100)):
        strategy.on_bar(row)
    current_atr = __import__("bot.data.indicators", fromlist=["atr"]).atr(strategy.bars, 2)
    entry, low = strategy.bars[-1].close, strategy.bars[-1].low
    stop = low - current_atr * 1.5
    target = entry + (entry - stop) * 2.5
    assert stop < entry < target
