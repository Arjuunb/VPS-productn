from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from data.forward_market_data import ForwardMarketDataUnavailable, fetch_forward_bars
from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine
from services.auto_engine import AutoStrategyEngine, EngineFeedError
from services.controls import TradingControl
from services.signal_pipeline import SignalPipeline
from services.trading_instances import TradingInstanceManager


def _factory(_key, symbol):
    from strategies.brain_strategy import DecisionBrain
    return DecisionBrain(symbol)


def _bar(at: datetime) -> Bar:
    return Bar(at, 100, 101, 99, 100, 1)


def _engine(*, cursor=None, checkpoint=None):
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 1000)
    pipeline = SignalPipeline(ledger, paper, TradingControl(), equity=1000)
    return AutoStrategyEngine(pipeline, paper, ledger, symbols=["BTCUSDT"], timeframe="5m",
                              live=True, initial_last_processed_candle=cursor,
                              candle_checkpoint=checkpoint, fetcher=lambda *_: ([], "live (test)"))


def test_forward_cursor_processes_each_new_closed_candle_once_and_persists():
    start = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=15)
    saved, processed = [], []
    engine = _engine(cursor=start.isoformat(), checkpoint=saved.append)
    engine._process_bar = lambda _sym, bar, _strat: processed.append(bar.timestamp)  # type: ignore[method-assign]
    bars = [_bar(start), _bar(start + timedelta(minutes=5)), _bar(start + timedelta(minutes=10))]

    last = engine._ingest("BTCUSDT", object(), bars, start)
    assert processed == [start + timedelta(minutes=5), start + timedelta(minutes=10)]
    assert saved[-1] == (start + timedelta(minutes=10)).isoformat()
    assert engine.duplicate_candles_ignored == 1

    engine._ingest("BTCUSDT", object(), bars, last)
    assert len(processed) == 2
    assert engine.duplicate_candles_ignored == 4


def test_stale_live_data_is_fail_closed_before_any_strategy_processing():
    engine = _engine()
    stale = _bar(datetime.now(timezone.utc) - timedelta(minutes=20))
    with pytest.raises(EngineFeedError, match="stale"):
        engine._record_market_snapshot("BTCUSDT", [stale])
    assert engine.market_data_status == "stale"


def test_freshness_starts_when_the_closed_candle_closes():
    """Provider OHLCV timestamps are candle-open times, not close times."""
    engine = _engine()
    # A 5m candle which opened 9m30s ago closed 4m30s ago.  It is still within
    # the 7m30s forward-paper freshness guard and must not be treated as stale.
    recently_closed = _bar(datetime.now(timezone.utc) - timedelta(minutes=9, seconds=30))
    engine._record_market_snapshot("BTCUSDT", [recently_closed])
    assert engine.market_data_status == "healthy"


def test_forward_fetcher_does_not_fall_back_when_provider_is_unavailable(monkeypatch):
    monkeypatch.setattr("data.live_data.fetch_ohlcv", lambda *args, **kwargs: None)
    monkeypatch.setattr("data.live_data.last_error", lambda *_args: "provider offline")
    with pytest.raises(ForwardMarketDataUnavailable, match="provider offline"):
        fetch_forward_bars("BTCUSDT", "5m", 200)


def test_instance_market_cursor_persists_across_manager_recreation():
    ledger = SqliteLedger(":memory:")
    first = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    instance = first.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                            strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                            capital_allocation=1000)
    first.store.save_market_state(instance.id, last_processed_candle_timestamp="2026-08-08T22:55:00+00:00",
                                  market_data_mode="paper_forward", market_data_status="healthy")
    restored = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    state = restored.store.market_state(instance.id)
    assert state["last_processed_candle_timestamp"] == "2026-08-08T22:55:00+00:00"
    assert state["market_data_mode"] == "paper_forward"
