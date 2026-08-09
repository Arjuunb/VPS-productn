from data.ledger import SqliteLedger
from data.decision_store import DecisionStore
from execution.paper_engine import PaperExecutionEngine
from services.trading_instances import InstanceLedger, ResearchExecutionEngine, TradingInstanceManager
import pytest


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


def test_instance_execution_configuration_is_persisted_and_does_not_inherit_legacy_values():
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    instance = manager.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                              strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                              capital_allocation=1_000, sizing_mode="fixed", fixed_position_size=0.02,
                              entry_mode="market")
    reloaded = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    saved = reloaded.status(instance.id)
    assert saved["configuration"]["symbol"] == "BTCUSDT"
    assert saved["configuration"]["strategy_key"] == "brain"
    assert saved["configuration"]["timeframe"] == "5m"
    assert saved["configuration"]["sizing_mode"] == "fixed"
    assert saved["configuration"]["fixed_position_size"] == 0.02
    assert saved["configuration"]["entry_mode"] == "market"


def test_active_duplicate_and_overallocation_are_rejected():
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60,
                                     paper_account_capital=1_000)
    first = manager.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                           strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                           capital_allocation=700)
    first.state = "running"  # duplicate protection is about active workers.
    with pytest.raises(ValueError, match="already active"):
        manager.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                       strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                       capital_allocation=100)
    with pytest.raises(ValueError, match="exceeds paper account capacity"):
        manager.create(symbol="ETHUSDT", strategy_key="ema", strategy_label="EMA Crossover",
                       strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                       capital_allocation=400)


def test_worker_start_uses_persisted_instance_execution_not_legacy_runtime(monkeypatch):
    from services.auto_engine import AutoStrategyEngine
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    instance = manager.create(symbol="ETHUSDT", strategy_key="ema", strategy_label="EMA Crossover",
                              strategy_version="v2", timeframe="15m", risk_per_trade_pct=0.01,
                              capital_allocation=800, sizing_mode="fixed", fixed_position_size=3.0,
                              entry_mode="market")
    monkeypatch.setattr(AutoStrategyEngine, "start", lambda self: True)
    manager.start(instance.id)
    engine, _paper, pipeline, _controls = manager._runtime[instance.id]
    assert engine.symbols == ["ETHUSDT"]
    assert engine.timeframe == "15m"
    assert engine.entry_mode == "market"
    assert pipeline.position_sizing_mode == "fixed"
    assert pipeline.fixed_position_size == 3.0


def test_instances_start_and_stop_independently_and_slot_limit_is_backend_enforced(monkeypatch):
    from services.auto_engine import AutoStrategyEngine
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    manager.configure(max_active_slots=2)
    one = manager.create(symbol="BTCUSDT", strategy_key="brain", strategy_label="Decision Brain",
                         strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005, capital_allocation=500)
    two = manager.create(symbol="ETHUSDT", strategy_key="ema", strategy_label="EMA Crossover",
                         strategy_version="v1", timeframe="15m", risk_per_trade_pct=0.005, capital_allocation=500)
    three = manager.create(symbol="SOLUSDT", strategy_key="ema", strategy_label="EMA Crossover",
                           strategy_version="v1", timeframe="15m", risk_per_trade_pct=0.005, capital_allocation=500)
    monkeypatch.setattr(AutoStrategyEngine, "start", lambda self: (setattr(self, "running", True), setattr(self, "lifecycle_state", "running"), True)[-1])
    manager.start(one.id); manager.start(two.id)
    with pytest.raises(ValueError, match="Maximum active trading slots"):
        manager.start(three.id)
    manager.stop(one.id)
    assert manager.status(one.id)["state"] == "stopped"
    assert manager.status(two.id)["state"] == "running"


def test_restore_never_exceeds_the_persisted_active_slot_limit(monkeypatch):
    from services.auto_engine import AutoStrategyEngine
    ledger = SqliteLedger(":memory:")
    manager = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    manager.configure(max_active_slots=2)
    for symbol, strategy in (("BTCUSDT", "brain"), ("ETHUSDT", "ema"), ("SOLUSDT", "donchian")):
        instance = manager.create(symbol=symbol, strategy_key=strategy, strategy_label=strategy,
                                  strategy_version="v1", timeframe="5m", risk_per_trade_pct=0.005,
                                  capital_allocation=500)
        instance.state, instance.desired_running = "running", True
        manager.store.save(instance)

    reloaded = TradingInstanceManager(ledger, strategy_factory=_factory, live=False, live_poll_s=60)
    monkeypatch.setattr(AutoStrategyEngine, "start", lambda self: (setattr(self, "running", True), setattr(self, "lifecycle_state", "running"), True)[-1])

    restored = reloaded.restore_desired_instances()

    assert len(restored) == 2
    rows = reloaded.list()
    assert sum(row["state"] == "running" for row in rows) == 2
    paused = [row for row in rows if row["state"] == "paused"]
    assert len(paused) == 1
    assert "maximum active trading slots" in paused[0]["last_error"]
