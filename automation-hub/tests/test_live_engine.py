"""Live forward mode: trade only NEW closed candles, never the in-progress one."""
from datetime import datetime, timedelta, timezone
import time

from bot.types import Bar, Signal, SignalType
from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine
from services.auto_engine import AutoStrategyEngine, EngineFeedError
from services.controls import TradingControl
from services.signal_pipeline import SignalPipeline


class _AlwaysLong:
    """Emits a LONG every bar (so we can see when the engine acts)."""
    def __init__(self):
        self.bars = []

    def on_bar(self, bar):
        self.bars.append(bar)
        return Signal(timestamp=bar.timestamp, symbol="BTCUSDT", type=SignalType.LONG,
                      entry=bar.close, stop_loss=bar.close * 0.97,
                      take_profit=bar.close * 1.06, reason="test")


def _bars(n, start=100.0):
    # Keep the newest synthetic candle current enough for the strict forward
    # freshness guard. A fixed historical date makes the recovery-thread test
    # fail for the wrong reason as wall-clock time advances.
    t0 = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(hours=4 * n)
    return [Bar(t0 + timedelta(hours=4 * i), start + i, start + i + 1,
                start + i - 1, start + i, 1.0) for i in range(n)]


def _engine():
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led, 10_000)
    pipe = SignalPipeline(led, paper, TradingControl(), equity=10_000,
                          risk_per_trade_pct=0.01, exposure_limit_pct=0.5)
    eng = AutoStrategyEngine(pipe, paper, led, symbols=["BTCUSDT"], live=True,
                             entry_mode="market")  # these tests target ingest/exit logic
    return eng, paper


def test_ingest_acts_only_on_new_closed_bars():
    eng, paper = _engine()
    strat = _AlwaysLong()
    bars = _bars(5)                       # bars[-1] is "in-progress"
    # warm strategy on the closed history (bars[:-1]) without trading
    for b in bars[:-2]:
        strat.bars.append(b)
    last = bars[-3].timestamp             # already-seen up to here

    # first poll: bars[-2] is the only NEW closed bar (bars[-1] is in-progress)
    last = eng._ingest("BTCUSDT", strat, bars[:-1], last)
    assert eng.stats["bars"] == 1
    assert len(paper.positions()) == 1     # it opened on the new closed bar
    assert last == bars[-2].timestamp

    # re-poll with the SAME data -> nothing new, no action
    before = eng.stats["bars"]
    last = eng._ingest("BTCUSDT", strat, bars[:-1], last)
    assert eng.stats["bars"] == before


def test_ingest_ignores_in_progress_candle():
    eng, paper = _engine()
    strat = _AlwaysLong()
    bars = _bars(3)
    for b in bars[:-1]:
        strat.bars.append(b)
    last = bars[-2].timestamp              # seen all closed bars
    # only the in-progress bar (bars[-1]) is "new" -> must be ignored
    eng._ingest("BTCUSDT", strat, bars[:-1], last)
    assert eng.stats["bars"] == 0
    assert paper.positions() == []


def test_status_reports_mode():
    eng, _ = _engine()
    assert eng.status()["mode"] == "live"


def test_reconnect_lifecycle_is_visible_and_bounded():
    eng, _ = _engine()
    delay = eng._schedule_reconnect(EngineFeedError("temporary provider timeout"))
    state = eng.status()
    assert delay == 2.0
    assert state["lifecycle_state"] == "reconnecting"
    assert state["reconnect_attempt"] == 1
    assert state["last_error"] == "EngineFeedError: temporary provider timeout"

    for _ in range(eng.max_reconnect_attempts):
        final = eng._schedule_reconnect(EngineFeedError("still unavailable"))
    assert final is None
    eng._mark_error("Market data connection lost", EngineFeedError("still unavailable"))
    assert eng.status()["lifecycle_state"] == "error"


def test_live_worker_recovers_after_a_transient_warmup_failure():
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led, 10_000)
    pipe = SignalPipeline(led, paper, TradingControl(), equity=10_000,
                          risk_per_trade_pct=0.01, exposure_limit_pct=0.5)
    calls = {"n": 0}

    def fetcher(symbol, timeframe, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("temporary network failure")
        # This engine uses its default 1h timeframe; return actual 1h-spaced
        # provider bars so strict freshness is what the test is exercising.
        newest = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return [Bar(newest - timedelta(hours=3 - i), 100, 101, 99, 100, 1)
                for i in range(4)], "live (test)"

    eng = AutoStrategyEngine(pipe, paper, led, symbols=["BTCUSDT"], live=True,
                             live_poll_s=0.01, fetcher=fetcher)
    original = eng._schedule_reconnect
    eng._schedule_reconnect = lambda exc: 0.0 if original(exc) is not None else None
    try:
        assert eng.start() is True
        deadline = time.time() + 1
        while calls["n"] < 2 and time.time() < deadline:
            time.sleep(0.01)
        assert calls["n"] >= 2
        assert eng.status()["lifecycle_state"] == "running"
    finally:
        eng.stop()


def test_health_guard_reduces_new_entry_risk_after_measured_losses():
    eng, paper = _engine()
    # Paper history is the source of truth. A symbol-specific losing sample
    # should reduce only future entry risk and expose the reason in status.
    paper._hist_cache = [{"symbol": "BTCUSDT", "pnl": -10.0, "rr": -1.0}
                         for _ in range(10)]
    assert eng._health_factor("BTCUSDT") == 0.50
    status = eng.status()["strategy_health"]["BTCUSDT"]
    assert status["status"] == "Unhealthy" and status["factor"] == 0.50
