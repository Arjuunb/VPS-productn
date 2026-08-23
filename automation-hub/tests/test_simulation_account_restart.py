from types import SimpleNamespace

import pytest

from data.journal_store import JournalStore
from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine
from services.trading_instances import InstanceLedger, TradingInstanceManager


def _factory(_key, symbol):
    from strategies.brain_strategy import DecisionBrain
    return DecisionBrain(symbol)


def _instance(manager, *, symbol="BTCUSDT", capital=1_000):
    return manager.create(
        symbol=symbol, strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=capital,
    )


def _journal_entry(store: JournalStore, *, trade_id: str, instance_id: str):
    store.record_entry({
        "trade_id": trade_id, "mode": "paper", "symbol": "BTCUSDT",
        "side": "long", "strategy": "Decision Brain", "timeframe": "5m",
        "entry": 100, "stop": 95, "target": 110, "size": 1,
        "risk_amount": 5, "planned_rr": 2, "confidence": 80,
        "brain_score": 80, "regime": "trend", "sections": {},
        "instance_id": instance_id, "execution_mode": "paper",
    })


def test_restart_resets_only_current_simulation_account_and_preserves_history(tmp_path):
    ledger_path = tmp_path / "ledger.db"
    journal = JournalStore(str(tmp_path / "journal.db"))
    ledger = SqliteLedger(ledger_path)
    manager = TradingInstanceManager(
        ledger, strategy_factory=_factory, live=False, live_poll_s=60,
        decision_journal=SimpleNamespace(store=journal),
    )
    account = _instance(manager)
    sibling = _instance(manager, symbol="ETHUSDT", capital=2_000)
    original_config = {
        "strategy_key": account.strategy_key,
        "strategy_version": account.strategy_version,
        "risk_per_trade_pct": account.risk_per_trade_pct,
        "capital_allocation": account.capital_allocation,
        "timeframe": account.timeframe,
    }
    old_session = account.simulation_session_id
    paper = PaperExecutionEngine(
        InstanceLedger(ledger, account.id, old_session), account.starting_equity)
    closed = paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100, stop=95)
    paper.close(symbol="BTCUSDT", exit_price=80)
    open_fill = paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=90, stop=85)
    _journal_entry(journal, trade_id=open_fill.trade_id, instance_id=account.id)
    manager.store.save_pending_orders(account.id, {"BTCUSDT": {"price": 88}})

    before_trades = ledger.get_paper_trades(instance_id=account.id)
    assert paper.balance() == 980
    assert len(before_trades) == 2
    assert ledger.get_paper_trades(instance_id=sibling.id) == []

    result = manager.restart_simulation_account(account.id, initiated_by="operator@example.com")

    assert result["previous_session_id"] == old_session
    assert result["new_session_id"] != old_session
    assert result["previous_balance"] == 980
    assert result["new_balance"] == 1_000
    assert result["open_positions_cleared"] == 1
    assert result["pending_orders_cleared"] == 1
    assert result["resumed"] is False
    status = result["instance"]
    assert status["execution"]["current_equity"] == 1_000
    assert status["execution"]["current_realized_equity"] == 1_000
    assert status["execution"]["realized_pnl"] == 0
    assert status["execution"]["unrealized_pnl"] == 0
    assert status["execution"]["pending_orders"] == 0
    assert status["metrics"]["trades"] == 0
    assert status["metrics"]["current_drawdown_pct"] == 0
    assert status["current_position"] is None
    assert status["simulation_session"]["number"] == 2
    assert manager.store.market_state(account.id)["pending_orders_json"] == {}

    all_trades = ledger.get_paper_trades(instance_id=account.id)
    assert len(all_trades) == len(before_trades)
    assert next(row for row in all_trades if row["id"] == closed.trade_id)["status"] == "closed"
    assert next(row for row in all_trades if row["id"] == open_fill.trade_id)["status"] == "cancelled"
    assert {row["simulation_session_id"] for row in all_trades} == {old_session}
    assert journal.get(open_fill.trade_id, instance_id=account.id)["status"] == "cancelled"
    assert journal.get(open_fill.trade_id, instance_id=account.id)["events"][-1]["kind"] == "simulation-account-restarted"
    assert manager.store.simulation_sessions(account.id)[0]["status"] == "active"
    assert manager.store.simulation_sessions(account.id)[1]["end_reason"] == "account restart"
    audit = manager.store.simulation_restart_audit(account.id)[0]
    assert audit["action"] == "simulation_account_restart"
    assert audit["initiated_by"] == "operator@example.com"
    assert audit["result"] == "success"
    assert ledger.get_paper_trades(instance_id=sibling.id) == []
    assert manager.status(sibling.id)["execution"]["current_equity"] == 2_000
    assert {key: getattr(account, key) for key in original_config} == original_config

    restored = TradingInstanceManager(
        SqliteLedger(ledger_path), strategy_factory=_factory, live=False, live_poll_s=60)
    restored_status = restored.status(account.id)
    assert restored_status["simulation_session"]["id"] == result["new_session_id"]
    assert restored_status["execution"]["current_equity"] == 1_000
    assert restored_status["metrics"]["trades"] == 0
    assert len(restored.ledger.get_paper_trades(instance_id=account.id)) == 2


def test_restart_rejects_live_and_research_instances_without_mutation():
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    live = _instance(manager)
    live.execution_mode = "live"
    manager.store.save(live)
    prior_session = live.simulation_session_id

    with pytest.raises(ValueError, match="only for Paper Trading"):
        manager.restart_simulation_account(live.id, initiated_by="operator")
    assert manager.store.simulation_sessions(live.id)[0]["id"] == prior_session
    assert manager.store.simulation_restart_audit(live.id) == []

    research = manager.create(
        symbol="ETHUSDT", strategy_key="brain", strategy_label="Decision Brain",
        strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
        capital_allocation=500, mode="research",
    )
    with pytest.raises(ValueError, match="only for Paper Trading"):
        manager.restart_simulation_account(research.id, initiated_by="operator")
    assert manager.store.simulation_restart_audit(research.id) == []


def test_running_worker_is_reinitialized_with_fresh_runtime_counters(monkeypatch):
    from data.ws_feed import WebSocketFeed
    from services.auto_engine import AutoStrategyEngine

    def fake_start(engine):
        engine.running = True
        engine.lifecycle_state = "running"
        return True

    def fake_stop(engine, reason="Stopped by operator"):
        engine.running = False
        engine.lifecycle_state = "stopped"
        engine.stop_reason = reason
        return True

    monkeypatch.setattr(AutoStrategyEngine, "start", fake_start)
    monkeypatch.setattr(AutoStrategyEngine, "stop", fake_stop)
    monkeypatch.setattr(WebSocketFeed, "start", lambda _feed: False)
    monkeypatch.setattr(WebSocketFeed, "stop", lambda _feed: None)

    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    account = _instance(manager)
    manager.start(account.id)
    old_engine, old_paper = manager._runtime[account.id][:2]
    old_engine.stats.update({"bars": 20, "signals": 5, "accepted_signals": 2, "trades": 2})
    old_engine._pending["BTCUSDT"] = {"price": 99}
    manager.store.save_pending_orders(account.id, old_engine._pending)
    old_paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100, stop=95)

    result = manager.restart_simulation_account(account.id, initiated_by="operator")

    new_engine, new_paper = manager._runtime[account.id][:2]
    assert result["resumed"] is True
    assert new_engine is not old_engine
    assert new_engine.running is True
    assert new_engine._pending == {}
    assert new_engine.stats["bars"] == 0
    assert new_engine.stats["signals"] == 0
    assert new_engine.stats["trades"] == 0
    assert new_paper.balance() == account.starting_equity
    assert new_paper.positions() == []
    assert new_paper.history() == []


def test_restart_endpoint_requires_explicit_confirmation(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from routers import instances as instances_router

    monkeypatch.setattr(instances_router._wa, "_check_secret", lambda _secret: None, raising=False)

    with pytest.raises(fastapi.HTTPException) as exc:
        instances_router.restart_simulation_account(
            "instance", instances_router.SimulationAccountRestart(confirm=False),
            SimpleNamespace(), x_webhook_secret="ignored")
    assert exc.value.status_code == 400
    assert "confirmation" in str(exc.value.detail).lower()


def test_restart_endpoint_executes_backend_reset_and_maps_live_rejection(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from routers import instances as instances_router

    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    account = _instance(manager)
    monkeypatch.setattr(instances_router._wa, "_check_secret", lambda _secret: None, raising=False)
    monkeypatch.setattr(instances_router, "_manager", lambda: manager)
    monkeypatch.setattr(instances_router, "_initiated_by", lambda _request: "api-operator")

    result = instances_router.restart_simulation_account(
        account.id, instances_router.SimulationAccountRestart(confirm=True),
        SimpleNamespace(), x_webhook_secret="ignored")
    assert result["result"] == "success"
    assert manager.status(account.id)["simulation_session"]["number"] == 2

    account.execution_mode = "live"
    manager.store.save(account)
    with pytest.raises(fastapi.HTTPException) as exc:
        instances_router.restart_simulation_account(
            account.id, instances_router.SimulationAccountRestart(confirm=True),
            SimpleNamespace(), x_webhook_secret="ignored")
    assert exc.value.status_code == 409
