from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine
from services.auto_engine import AutoStrategyEngine
from services.controls import TradingControl
from services.signal_pipeline import SignalPipeline
from services.trading_instances import TradingInstanceManager


@pytest.mark.parametrize("side", ["REDUCE", "CLOSE", "FLATTEN"])
def test_risk_reducing_events_bypass_pause_session_and_entry_gates(side):
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    controls = TradingControl()
    pipeline = SignalPipeline(
        ledger, paper, controls, equity=10_000,
        session_start=1, session_end=2,
    )
    paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
               stop=95, target=110, alert_id=f"open-{side}")
    controls.pause_all()

    result = pipeline.process({
        "alert_id": f"close-{side}", "symbol": "BTCUSDT", "side": side,
        "entry": 99, "stop": None,
        "timestamp": "2026-08-30T12:00:00+00:00",
    })

    assert result.accepted is True
    assert result.fill["action"] == "closed"
    assert paper.positions() == []
    assert any(step.rule == "controls" and step.passed for step in result.steps)
    assert not any(step.rule == "session" and not step.passed for step in result.steps)


def test_pipeline_rejection_has_stable_blocker_code():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    controls = TradingControl()
    controls.pause_all()
    result = SignalPipeline(ledger, paper, controls).process({
        "alert_id": "blocked-entry", "symbol": "BTCUSDT", "side": "BUY",
        "entry": 100, "stop": 95,
        "timestamp": "2026-08-30T12:00:00+00:00",
    })
    assert result.accepted is False
    assert result.blocker == "GATE_REJECTED: PAUSED"
    assert result.to_dict()["blocker"] == result.blocker


def test_missing_mandatory_risk_engine_refuses_to_construct_pipeline(monkeypatch):
    import services.signal_pipeline as module

    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    monkeypatch.setattr(module, "_RiskEngine", None)

    with pytest.raises(RuntimeError, match="mandatory risk veto"):
        module.SignalPipeline(ledger, paper, TradingControl())


def test_closed_candle_without_trade_returns_and_exposes_no_setup_blocker():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    pipeline = SignalPipeline(ledger, paper, TradingControl())
    engine = AutoStrategyEngine(
        pipeline, paper, ledger, symbols=["BTCUSDT"], timeframe="5m",
        entry_mode="market",
    )

    class NoSetup:
        label = "No setup"

        def __init__(self):
            self.bars = []

        def on_bar(self, bar):
            self.bars.append(bar)
            return None

    timestamp = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
    blocker = engine._process_bar(
        "BTCUSDT", Bar(timestamp, 100, 101, 99, 100, 1), NoSetup(),
    )
    assert blocker == "GATE_REJECTED: NO_SETUP"
    assert engine.status()["last_blocker"] == blocker
    assert engine.status()["last_blocker_timestamp"] == timestamp.isoformat()


def test_instance_market_state_persists_blocker_for_ui_after_restart():
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(
        ledger, strategy_factory=lambda _key, symbol: object(), live=False,
        live_poll_s=60,
    )
    instance = manager.create(
        symbol="BTCUSDT", strategy_key="test", strategy_label="Test",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=1_000,
    )
    stamp = "2026-08-30T12:00:00+00:00"
    manager.store.save_market_state(
        instance.id, last_processed_candle_timestamp=stamp,
        market_data_status="healthy", last_blocker="GATE_REJECTED: NO_SETUP",
        last_blocker_timestamp=stamp,
    )
    restored = manager.store.market_state(instance.id)
    assert restored["last_blocker"] == "GATE_REJECTED: NO_SETUP"
    assert restored["last_blocker_timestamp"] == stamp


def test_resampler_rejects_missing_internal_candle():
    from backtest import resample

    start = datetime(2026, 8, 30, tzinfo=timezone.utc)
    rows = [
        Bar(start + timedelta(minutes=5 * index), 100, 101, 99, 100, 1)
        for index in (0, 1, 3, 4)
    ]
    with pytest.raises(ValueError, match="missing or have an irregular interval"):
        resample(rows, 2)
