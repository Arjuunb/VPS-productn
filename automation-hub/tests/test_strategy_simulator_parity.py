"""Research simulator contracts that must match the paper-forward runtime."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random

from bot.types import Bar, Signal, SignalType
from strategies.custom import _RiskGate, simulate_strategy
from strategies.brain_strategy import DecisionBrain
from strategies.donchian_strategy import DonchianStrategy
from strategies.supertrend_strategy import SupertrendStrategy


class OneSignal:
    """Minimal deterministic strategy for execution-ordering regression tests."""

    def __init__(self, side: SignalType, *, entry=100.0, stop=99.0, target=102.5):
        self.side, self.entry, self.stop, self.target = side, entry, stop, target
        self.symbol, self.bars, self.emitted = "BTCUSDT", [], False

    def on_bar(self, bar):
        self.bars.append(bar)
        if self.emitted:
            return None
        self.emitted = True
        return Signal(bar.timestamp, self.symbol, self.side, self.entry,
                      self.stop, self.target, "fixture")


def _bar(minute, open_, high, low, close):
    return Bar(datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minute),
               open_, high, low, close, 1_000)


def test_limit_fill_candle_can_stop_but_cannot_win():
    bars = [
        _bar(0, 100, 100.2, 99.8, 100),
        # Both stop and target are inside this bar. Production permits only the
        # adverse stop because the target may have occurred before the fill.
        _bar(5, 100.1, 103.0, 98.8, 101),
        _bar(10, 101, 103, 100, 102),
    ]
    result = simulate_strategy(OneSignal(SignalType.LONG), bars, fee=0,
                               slippage=0, entry_mode="limit", retain_all=True)
    assert result["total_trades"] == 1
    assert result["trades"][0]["exit_reason"] == "stop"
    assert result["trades"][0]["r"] == -1.0


def test_stop_gap_uses_worse_open_price():
    bars = [
        _bar(0, 100, 100.2, 99.8, 100),
        _bar(5, 100, 100.5, 99.5, 100.2),  # maker entry survives
        _bar(10, 97.5, 98.0, 97.0, 97.8),  # gaps through the 99 stop
    ]
    result = simulate_strategy(OneSignal(SignalType.LONG), bars, fee=0,
                               slippage=0, entry_mode="limit", retain_all=True)
    assert result["trades"][0]["exit"] == 97.5
    assert result["trades"][0]["r"] == -2.5


def test_consecutive_loss_streak_survives_utc_day_change():
    gate = _RiskGate(max_consec=3)
    before_midnight = datetime(2025, 1, 1, 23, 55, tzinfo=timezone.utc)
    for _ in range(3):
        gate.on_exit(before_midnight, -1.0)
    assert gate.blocked_reason(before_midnight + timedelta(minutes=10)) == (
        "max drawdown or consecutive-loss auto-halt")


def test_daily_loss_resets_but_drawdown_halt_does_not():
    ts = datetime(2025, 1, 1, 23, 55, tzinfo=timezone.utc)
    daily = _RiskGate(max_daily_loss_r=2)
    daily.on_exit(ts, -2.1)
    assert daily.blocked_reason(ts) == "daily loss limit reached"
    assert daily.blocked_reason(ts + timedelta(minutes=10)) is None

    drawdown = _RiskGate(max_drawdown_r=2)
    drawdown.on_exit(ts, -2.1)
    assert drawdown.blocked_reason(ts + timedelta(days=1)) == (
        "max drawdown or consecutive-loss auto-halt")


def test_retain_all_keeps_more_than_api_preview_limit():
    bars = []
    strategy = OneSignal(SignalType.LONG)
    # Contract-only check: ordinary output remains bounded while validation may
    # opt into the full audit trail. The detailed close paths are tested above.
    from strategies.custom import _results
    trades = [{"r": 1.0, "side": "long", "exit_time": str(i), "bars_held": 1}
              for i in range(205)]
    preview = _results(trades, 1_000, 0.005, bars)
    audit = _results(trades, 1_000, 0.005, bars, retain_all=True)
    assert len(preview["trades"]) == 200
    assert len(audit["trades"]) == 205


def _causal_fixture(count=900):
    """Deterministic alternating trends that exercise every built-in."""
    rnd = Random(42)
    price = 100.0
    bars = []
    for i in range(count):
        drift = 0.35 if (i // 70) % 2 == 0 else -0.35
        price = max(10.0, price + drift + rnd.uniform(-0.7, 0.7))
        bars.append(_bar(i * 5, price - 0.1,
                         price + rnd.uniform(0.2, 1.0),
                         price - rnd.uniform(0.2, 1.0), price))
    return bars


def _signal_signature(strategy, bars):
    return [
        None if (signal := strategy.on_bar(bar)) is None else (
            signal.type.value, round(signal.entry, 8),
            round(signal.stop_loss, 8), round(signal.take_profit, 8),
        )
        for bar in bars
    ]


def test_builtin_signal_streams_do_not_read_future_bars():
    """Changing bars after a cutoff cannot change an earlier decision."""
    original = _causal_fixture()
    cutoff = 700
    mutated = list(original)
    for i, bar in enumerate(mutated[cutoff:], start=cutoff):
        # Keep OHLC valid while making the unseen future radically different.
        factor = 2.0 + (i - cutoff) / 50
        center = bar.close * factor
        mutated[i] = Bar(bar.timestamp, center - 1, center + 2,
                         center - 2, center, bar.volume * 10)

    for strategy_type in (SupertrendStrategy, DonchianStrategy, DecisionBrain):
        baseline = _signal_signature(strategy_type("BTCUSDT"), original)
        changed = _signal_signature(strategy_type("BTCUSDT"), mutated)
        assert baseline[:cutoff] == changed[:cutoff], strategy_type.__name__
        assert any(item is not None for item in baseline[:cutoff]), strategy_type.__name__
