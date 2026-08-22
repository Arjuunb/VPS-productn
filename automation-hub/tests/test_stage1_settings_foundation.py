from datetime import datetime, timezone

import pytest

from bot.types import Bar
from data.forward_market_data import test_forward_connection as probe_connection
from data.ledger import SqliteLedger
from services.runtime_settings import load_overrides, save_overrides
from services.trading_instances import TradingInstanceManager


def _factory(_key, _symbol):
    return object()


def _manager(path=":memory:"):
    return TradingInstanceManager(SqliteLedger(str(path)), strategy_factory=_factory,
                                  live=False, live_poll_s=60)


def test_platform_defaults_persist_without_mutating_existing_instances(tmp_path):
    path = tmp_path / "ledger.db"
    manager = _manager(path)
    existing = manager.create(symbol="BTCUSDT", strategy_key="brain",
                              strategy_label="Decision Brain", strategy_version="v1",
                              timeframe="5m", risk_per_trade_pct=0.005,
                              capital_allocation=500)
    manager.configure(defaults={
        "default_symbol": "ETHUSDT", "default_timeframe": "15m",
        "default_strategy": "ema", "default_capital": 750.0,
        "default_risk_per_trade_pct": 0.004, "default_max_open_positions": 2,
        "default_entry_mode": "market", "default_fill_model": "PerfectFill",
    })
    assert manager.status(existing.id)["symbol"] == "BTCUSDT"
    assert manager.status(existing.id)["capital_allocation"] == 500

    restarted = _manager(path)
    assert restarted.instance_defaults == {
        "default_symbol": "ETHUSDT", "default_timeframe": "15m",
        "default_strategy": "ema", "default_capital": 750.0,
        "default_risk_per_trade_pct": 0.004, "default_max_open_positions": 2,
        "default_entry_mode": "market", "default_fill_model": "PerfectFill",
    }
    assert restarted.status(existing.id)["symbol"] == "BTCUSDT"


def test_platform_instance_risk_ceiling_rejects_create_and_update():
    manager = _manager()
    manager.configure(max_instance_risk_per_trade_pct=0.01)
    with pytest.raises(ValueError, match="platform ceiling"):
        manager.create(symbol="BTCUSDT", strategy_key="brain",
                       strategy_label="Decision Brain", strategy_version="v1",
                       timeframe="5m", risk_per_trade_pct=0.02,
                       capital_allocation=500)
    accepted = manager.create(symbol="BTCUSDT", strategy_key="brain",
                              strategy_label="Decision Brain", strategy_version="v1",
                              timeframe="5m", risk_per_trade_pct=0.005,
                              capital_allocation=500)
    with pytest.raises(ValueError, match="platform ceiling"):
        manager.update_configuration(accepted.id, risk_per_trade_pct=0.02)


def test_rejected_platform_update_does_not_partially_change_other_limits():
    manager = _manager()
    manager.create(symbol="BTCUSDT", strategy_key="brain",
                   strategy_label="Decision Brain", strategy_version="v1",
                   timeframe="5m", risk_per_trade_pct=0.005,
                   capital_allocation=500)
    with pytest.raises(ValueError, match="allocated capital"):
        manager.configure(max_instance_risk_per_trade_pct=0.01,
                          paper_account_capital=100)
    assert manager.max_instance_risk_per_trade_pct == 0.05
    assert manager.paper_account_capital == 10_000


def test_fill_model_runtime_override_survives_restart(tmp_path):
    path = str(tmp_path / "runtime-settings.json")
    save_overrides(path, {"fill_model": "RealisticFill"})
    assert load_overrides(path)["fill_model"] == "RealisticFill"


def test_market_connection_probe_returns_only_real_probe_evidence():
    stamp = datetime(2026, 8, 22, tzinfo=timezone.utc)
    def fetcher(symbol, timeframe, limit, *, exchange):
        assert (symbol, timeframe, limit, exchange) == ("BTCUSDT", "5m", 3, "binance")
        return [Bar(stamp, 100, 102, 99, 101, 50)], "live (ccxt:binance)"
    result = probe_connection("BTCUSDT", "5m", "binance", fetcher=fetcher)
    assert result["ok"] is True
    assert result["market"] == "spot"
    assert result["last_price"] == 101
    assert "credential" not in result and "secret" not in result
