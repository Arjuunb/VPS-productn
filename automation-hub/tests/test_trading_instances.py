from data.ledger import SqliteLedger
from data.decision_store import DecisionStore
from execution.paper_engine import PaperExecutionEngine
from services.trading_instances import InstanceLedger, ResearchExecutionEngine, TradingInstanceManager


def _factory(_key, symbol):
    from strategies.brain_strategy import DecisionBrain
    return DecisionBrain(symbol)


def test_instance_scoped_ledger_keeps_positions_and_trades_isolated():
    ledger = SqliteLedger(":memory:")
    first = PaperExecutionEngine(InstanceLedger(ledger, "one"), 1_000)
    second = PaperExecutionEngine(InstanceLedger(ledger, "two"), 1_000)
    first.open(symbol="BTCUSDT", side="BUY", size=0.1, entry=100, stop=95)
    assert len(first.positions()) == 1
    assert second.positions() == []
    assert len(first.ledger.get_paper_trades()) == 1
    assert second.ledger.get_paper_trades() == []


def test_instances_keep_pair_strategy_version_and_metrics_separate():
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    btc = manager.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                         strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                         capital_allocation=1_000)
    eth = manager.create(symbol="ETHUSDT", strategy_key="ema", strategy_label="EMA Crossover",
                         strategy_version="v2", timeframe="5m", risk_per_trade_pct=0.005,
                         capital_allocation=2_000)
    assert btc.id != eth.id
    assert manager.status(btc.id)["metrics"]["instance_id"] == btc.id
    assert manager.status(eth.id)["metrics"]["instance_id"] == eth.id
    assert manager.leaderboard()[0]["strategy_version"] in {"v1", "v2"}


def test_research_instance_execution_adapter_never_places_orders():
    ledger = SqliteLedger(":memory:")
    paper = ResearchExecutionEngine(InstanceLedger(ledger, "research"), 1_000)
    fill = paper.open(symbol="BTCUSDT", side="BUY", size=0.1, entry=100, stop=95)
    assert fill.action == "rejected"
    assert paper.positions() == []
    assert paper.history() == []


def test_instance_platform_settings_persist_and_global_risk_sees_all_scoped_positions():
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    first = manager.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                           strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                           capital_allocation=1_000)
    manager.configure(max_active_slots=2, max_global_risk_pct=0.02)
    reloaded = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    assert reloaded.max_slots == 2
    assert reloaded.max_global_risk_pct == 0.02
    InstanceLedger(ledger, first.id).open_position(symbol="BTCUSDT", side="long", size=10, entry=100, stop=95)
    allowed, reason = reloaded._global_guard(first.id, "ETHUSDT", 100, 95, 1)
    assert not allowed
    assert "Global account risk exceeded" in reason
    snapshot = reloaded.platform_status()
    assert snapshot["total_open_positions"] == 1
    assert snapshot["global_risk_status"] == "warning"


def test_instances_api_returns_platform_status_and_validates_slot_change(monkeypatch):
    import pytest
    pytest.importorskip("fastapi")
    from routers import instances as instance_api

    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    monkeypatch.setattr(instance_api._wa, "instance_manager", manager)
    monkeypatch.setattr(instance_api._wa, "_check_secret", lambda _secret: None)
    payload = instance_api.list_instances()
    assert payload["max_active_slots"] == 1
    updated = instance_api.configure_platform(instance_api.PlatformConfig(max_active_slots=3))
    assert updated["max_active_slots"] == 3


def test_instance_status_exposes_only_its_scoped_last_decision_and_real_aggregate_fields():
    ledger = SqliteLedger(":memory:")
    decisions = DecisionStore(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60,
                                     decision_store=decisions)
    instance = manager.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                              strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                              capital_allocation=1_000)
    decisions.record({"symbol": "BTCUSDT", "decision": "accepted", "instance_id": instance.id,
                      "strategy": "Decision Brain", "timeframe": "5m"})
    decisions.record({"symbol": "ETHUSDT", "decision": "rejected", "instance_id": "other"})
    status = manager.status(instance.id)
    assert status["last_decision"]["instance_id"] == instance.id
    assert status["configuration"]["capital_allocation"] == 1_000
    assert status["execution"]["current_equity"] == 1_000
    assert status["market_data"]["market_data_status"] == "stopped"
    platform = manager.platform_status()
    assert platform["total_allocated_capital"] == 1_000
    assert platform["total_instances"] == 1
