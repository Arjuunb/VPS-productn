from __future__ import annotations

import pytest

from data import ledger as ledger_module
from data.ledger import SqliteLedger, remote_call_with_retry
from services.trading_instances import InstanceLedger, TradingInstanceManager


def _factory(_key, symbol):
    from strategies.brain_strategy import DecisionBrain
    return DecisionBrain(symbol)


def test_transient_remote_disconnect_is_retried_but_schema_error_is_not(monkeypatch):
    monkeypatch.setattr(ledger_module.time, "sleep", lambda _seconds: None)
    attempts = {"transient": 0, "schema": 0}

    class RemoteProtocolError(Exception):
        pass

    def transient():
        attempts["transient"] += 1
        if attempts["transient"] == 1:
            raise RemoteProtocolError("Server disconnected")
        return "connected"

    assert remote_call_with_retry(transient) == "connected"
    assert attempts["transient"] == 2

    def schema_error():
        attempts["schema"] += 1
        raise RuntimeError("PGRST205 missing table")

    with pytest.raises(RuntimeError, match="PGRST205"):
        remote_call_with_retry(schema_error)
    assert attempts["schema"] == 1


def test_instance_scoped_queries_return_only_owned_rows():
    ledger = SqliteLedger(":memory:")
    one, two = InstanceLedger(ledger, "one"), InstanceLedger(ledger, "two")
    one.open_position(symbol="BTCUSDT", side="long", size=1, entry=100, stop=95)
    two.open_position(symbol="ETHUSDT", side="long", size=1, entry=100, stop=95)
    one.record_paper_trade({"symbol": "BTCUSDT", "side": "long", "size": 1,
                            "entry": 100, "stop": 95})
    two.record_paper_trade({"symbol": "ETHUSDT", "side": "long", "size": 1,
                            "entry": 100, "stop": 95})
    one.log(level="info", stage="test", message="one")
    two.log(level="info", stage="test", message="two")

    assert [row["symbol"] for row in one.get_positions("open")] == ["BTCUSDT"]
    assert [row["symbol"] for row in one.get_paper_trades()] == ["BTCUSDT"]
    assert [row["message"] for row in one.get_logs()] == ["one"]


def test_platform_snapshot_reuses_already_materialized_instance_rows(monkeypatch):
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory,
                                     live=False, live_poll_s=60)
    manager.create(symbol="BTCUSDT", strategy_key="brain",
                   strategy_label="Decision Brain", strategy_version="v1",
                   timeframe="5m", risk_per_trade_pct=0.005,
                   capital_allocation=1_000)
    rows = manager.list()
    monkeypatch.setattr(manager, "status",
                        lambda _instance_id: (_ for _ in ()).throw(
                            AssertionError("status was calculated twice")))

    snapshot = manager.platform_status(runtime_states=rows)

    assert snapshot["total_instances"] == 1
    assert snapshot["instance_counts"]["stopped"] == 1
