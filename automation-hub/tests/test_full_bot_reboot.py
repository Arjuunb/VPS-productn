import time

import pytest

from data.ledger import SqliteLedger
from services.auto_engine import AutoStrategyEngine
from services.trading_instances import InstanceLedger, TradingInstanceManager


def _factory(_key, symbol):
    from strategies.brain_strategy import DecisionBrain
    return DecisionBrain(symbol)


def _healthy_start(self):
    self.running = True
    self.lifecycle_state = "running"
    self.market_data_status = "healthy"
    self.last_error = None
    return True


def _wait_for_reboot(manager, instance_id, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        reboot = manager.status(instance_id).get("reboot") or {}
        if reboot.get("status") in {"completed", "degraded", "failed"}:
            return reboot
        time.sleep(0.01)
    raise AssertionError("Full Bot Reboot did not reach a terminal status")


def _manager(monkeypatch, *, max_slots=2):
    monkeypatch.setattr(AutoStrategyEngine, "start", _healthy_start)
    manager = TradingInstanceManager(
        SqliteLedger(":memory:"), strategy_factory=_factory,
        live=False, live_poll_s=60, max_slots=max_slots,
        full_reboot_timeout_s=1,
    )
    manager.configure(max_active_slots=max_slots)
    return manager


def test_full_bot_reboot_recreates_runtime_and_preserves_durable_state(monkeypatch):
    manager = _manager(monkeypatch)
    instance = manager.create(
        symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=1_000,
    )
    manager.start(instance.id)
    old_runtime = manager._runtime[instance.id]
    old_engine, old_paper, _, _ = old_runtime

    closed = old_paper.open(
        symbol="BTCUSDT", side="BUY", size=0.1, entry=100, stop=95,
        target=110, alert_id="closed-before-reboot")
    old_paper.close(symbol="BTCUSDT", exit_price=105)
    opened = old_paper.open(
        symbol="BTCUSDT", side="BUY", size=0.1, entry=105, stop=100,
        target=115, alert_id="open-during-reboot")
    old_engine._adopt("BTCUSDT", old_paper.positions()[0])
    old_engine._pending["BTCUSDT"] = {
        "side": "BUY", "price": 104.0, "target": 114.0, "ttl": 2,
        "payload": {"symbol": "BTCUSDT", "side": "BUY", "entry": 104.0, "stop": 99.0},
    }
    old_engine._checkpoint_pending_orders()
    old_engine.last_processed_candle = "2026-08-20T12:00:00+00:00"
    old_engine.stats["bars"] = 99
    old_engine._multi_timeframe_context["BTCUSDT"] = {"1h": ["stale-cache"]}
    old_engine.last_error = "stale runtime error"

    before = manager.status(instance.id)
    before_trade_ids = {trade["id"] for trade in manager.ledger.get_paper_trades(instance_id=instance.id)}
    reboot = manager.request_full_reboot(instance.id)
    assert reboot["status"] == "running"
    assert old_runtime[3].trading_allowed() is False

    completed = _wait_for_reboot(manager, instance.id)
    new_runtime = manager._runtime[instance.id]
    new_engine, new_paper, _, new_controls = new_runtime
    after = manager.status(instance.id)

    assert completed["status"] == "completed"
    assert new_runtime is not old_runtime
    assert new_engine is not old_engine
    assert after["state"] == "running"
    assert new_controls.trading_allowed() is True
    assert new_paper.current_realized_equity() == pytest.approx(before["execution"]["current_realized_equity"])
    assert [row["id"] for row in new_paper.positions()] == [opened.position_id]
    assert set(new_engine._pending) == {"BTCUSDT"}
    assert new_engine._targets["BTCUSDT"] == 115
    assert new_engine.stats["bars"] == 0
    assert new_engine._multi_timeframe_context == {}
    assert new_engine.last_error is None
    assert after["simulation_session"]["id"] == before["simulation_session"]["id"]
    assert after["configuration"] == before["configuration"]
    assert {trade["id"] for trade in manager.ledger.get_paper_trades(instance_id=instance.id)} == before_trade_ids
    assert closed.trade_id in before_trade_ids


def test_full_bot_reboot_is_scoped_to_selected_instance(monkeypatch):
    manager = _manager(monkeypatch)
    first = manager.create(
        symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=500,
    )
    second = manager.create(
        symbol="ETHUSDT", strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=500,
    )
    manager.start(first.id)
    manager.start(second.id)
    sibling_runtime = manager._runtime[second.id]
    sibling_session = second.simulation_session_id

    manager.request_full_reboot(first.id)
    assert _wait_for_reboot(manager, first.id)["status"] == "completed"

    assert manager._runtime[second.id] is sibling_runtime
    assert manager.status(second.id)["state"] == "running"
    assert manager.status(second.id)["simulation_session"]["id"] == sibling_session
    assert manager.status(second.id)["reboot"] is None


def test_full_bot_reboot_degrades_and_blocks_entries_when_position_cannot_reconcile(monkeypatch):
    manager = _manager(monkeypatch)
    instance = manager.create(
        symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=500,
    )
    scoped = InstanceLedger(manager.ledger, instance.id, instance.simulation_session_id)
    position_id = scoped.open_position(
        symbol="BTCUSDT", side="long", size=0.1, entry=100, stop=95,
        target=None)
    trade_id = scoped.record_paper_trade({
        "symbol": "BTCUSDT", "side": "long", "size": 0.1,
        "entry": 100, "stop": 95, "target": None,
    })

    manager.request_full_reboot(instance.id)
    degraded = _wait_for_reboot(manager, instance.id)
    status = manager.status(instance.id)

    assert degraded["status"] == "degraded"
    assert status["state"] == "degraded"
    assert "exact protection cannot be restored" in status["last_error"]
    assert instance.id not in manager._runtime
    assert scoped.get_positions("open")[0]["id"] == position_id
    assert next(trade for trade in scoped.get_paper_trades() if trade["id"] == trade_id)["status"] == "open"


def test_full_bot_reboot_rejects_overlapping_lifecycle_actions(monkeypatch):
    manager = _manager(monkeypatch)
    instance = manager.create(
        symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=500,
    )
    blocker = __import__("threading").Event()
    original = AutoStrategyEngine.flush_runtime_state

    def blocked_flush(self):
        blocker.wait(1)
        return original(self)

    monkeypatch.setattr(AutoStrategyEngine, "flush_runtime_state", blocked_flush)
    manager.start(instance.id)
    manager.request_full_reboot(instance.id)
    with pytest.raises(ValueError, match="already in progress"):
        manager.pause(instance.id)
    with pytest.raises(ValueError, match="already in progress"):
        manager.request_full_reboot(instance.id)
    blocker.set()
    assert _wait_for_reboot(manager, instance.id)["status"] == "completed"


def test_restart_api_returns_backend_reboot_status(monkeypatch):
    pytest.importorskip("fastapi")
    from routers import instances as instance_api

    manager = _manager(monkeypatch)
    instance = manager.create(
        symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=500,
    )
    monkeypatch.setattr(instance_api._wa, "instance_manager", manager)
    monkeypatch.setattr(instance_api._wa, "_check_secret", lambda _secret: None)
    monkeypatch.setattr(instance_api._wa.engine, "running", False)

    response = instance_api.instance_action(instance.id, "restart")

    assert response["instance"]["reboot"]["id"]
    assert response["instance"]["reboot"]["status"] in {"running", "completed"}
    assert _wait_for_reboot(manager, instance.id)["status"] == "completed"
