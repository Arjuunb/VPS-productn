"""Deterministic behaviour and registry wiring for Liquidity Sweep."""
from datetime import datetime, timedelta, timezone

from bot.types import Bar, SignalType
from bots.registry import build_strategy
from services.strategy_presets import make_replay_strategy, resolve
from strategies.liquidity_sweep_strategy import LiquiditySweepStrategy


def _bar(index: int, open_: float = 100.0, high: float = 101.0,
         low: float = 99.0, close: float = 100.0) -> Bar:
    return Bar(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index),
               open_, high, low, close, 1000.0)


def _warm(strategy: LiquiditySweepStrategy) -> None:
    for index in range(30):
        assert strategy.on_bar(_bar(index)) is None


def test_bullish_sweep_reclaim_has_a_valid_long_bracket():
    strategy = LiquiditySweepStrategy("BTCUSDT")
    _warm(strategy)

    signal = strategy.on_bar(_bar(30, open_=98.0, high=101.0, low=97.0, close=100.5))

    assert signal is not None
    assert signal.type == SignalType.LONG
    assert signal.symbol == "BTCUSDT"
    assert signal.stop_loss < signal.entry < signal.take_profit
    assert "reclaimed" in signal.reason


def test_bearish_sweep_rejection_has_a_valid_short_bracket():
    strategy = LiquiditySweepStrategy("ETHUSDT")
    _warm(strategy)

    signal = strategy.on_bar(_bar(30, open_=102.0, high=103.0, low=99.0, close=99.5))

    assert signal is not None
    assert signal.type == SignalType.SHORT
    assert signal.take_profit < signal.entry < signal.stop_loss
    assert "rejected" in signal.reason


def test_no_signal_when_the_swept_level_is_not_reclaimed():
    strategy = LiquiditySweepStrategy("BTCUSDT")
    _warm(strategy)

    assert strategy.on_bar(_bar(30, open_=99.0, high=101.0, low=97.0, close=98.5)) is None


def test_registry_builds_the_strategy():
    strategy = build_strategy("liquidity_sweep", "SOLUSDT")
    assert strategy.name == "liquidity_sweep"


def test_control_center_and_replay_resolve_to_the_same_builtin_strategy():
    descriptor = resolve("Liquidity Sweep", "BTCUSDT", "15m", {})
    replay_strategy, error, strategy_id = make_replay_strategy(
        "Liquidity Sweep", "BTCUSDT", "15m"
    )

    assert descriptor == {
        "kind": "builtin", "key": "liquidity_sweep", "label": "Liquidity Sweep"
    }
    assert error is None
    assert strategy_id == "liquidity_sweep"
    assert replay_strategy.name == "liquidity_sweep"
