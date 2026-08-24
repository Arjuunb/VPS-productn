"""Candle-driven Paper Broker V2 unit tests."""
import pytest

from execution.paper_broker_v2 import PaperBrokerV2


def _broker(tmp_path):
    return PaperBrokerV2(tmp_path / "broker.db", starting_balance=1_000, leverage=2,
                         fee_rate=0, spread_bps=0, slippage_bps=0, participation_rate=1)


def _bar(open_=100, high=110, low=90, close=105, volume=10):
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def test_market_order_fills_only_when_real_candle_is_processed(tmp_path):
    broker = _broker(tmp_path)
    order = broker.submit(symbol="BTCUSDT", side="buy", order_type="market", quantity=1)
    assert broker.positions() == []
    broker.process_candle("BTCUSDT", _bar())
    assert broker.order(order["id"])["status"] == "filled"
    assert broker.positions()[0]["entry_price"] == 100


def test_limit_stop_and_partial_close_use_actual_candle_extremes(tmp_path):
    broker = _broker(tmp_path)
    broker.submit(symbol="BTCUSDT", side="buy", order_type="limit", quantity=2, limit_price=98)
    broker.process_candle("BTCUSDT", _bar(open_=101, high=103, low=97, close=100))
    assert broker.positions()[0]["entry_price"] == 98
    broker.submit(symbol="BTCUSDT", side="sell", order_type="stop", quantity=1, stop_price=95, reduce_only=True)
    broker.process_candle("BTCUSDT", _bar(open_=94, high=100, low=90, close=95))
    assert broker.positions()[0]["size"] == 1
    assert broker.fills()[0]["price"] == 94  # adverse gap-through, not a made-up 95 fill


def test_rejects_invalid_reduce_only_and_insufficient_margin(tmp_path):
    broker = _broker(tmp_path)
    with pytest.raises(ValueError, match="reduce-only"):
        broker.submit(symbol="BTCUSDT", side="sell", order_type="market", quantity=1, reduce_only=True)
    with pytest.raises(ValueError, match="insufficient"):
        broker.submit(symbol="BTCUSDT", side="buy", order_type="limit", quantity=100, limit_price=100)


def test_stop_loss_and_persistence(tmp_path):
    path = tmp_path / "broker.db"
    broker = _broker(tmp_path)
    broker.submit(symbol="BTCUSDT", side="buy", order_type="market", quantity=1)
    broker.process_candle("BTCUSDT", _bar())
    broker.set_protection("BTCUSDT", stop_loss=95)
    broker.process_candle("BTCUSDT", _bar(open_=94, high=100, low=90, close=92))
    assert broker.positions() == []
    # A new process sees the order/fill/account state from SQLite.
    restored = PaperBrokerV2(path, starting_balance=9_999, fee_rate=0, spread_bps=0, slippage_bps=0)
    assert restored.fills() and restored.account()["balance"] == 994


@pytest.mark.parametrize("side,stop,ratio", [("buy", 95, 2.5), ("sell", 105, 3.0)])
def test_fill_bound_protection_preserves_rr_from_actual_entry(tmp_path, side, stop, ratio):
    broker = PaperBrokerV2(tmp_path / f"{side}.db", starting_balance=10_000,
                           fee_rate=0, spread_bps=2, slippage_bps=3,
                           participation_rate=1)
    order = broker.submit(symbol="BTCUSDT", side=side, order_type="market", quantity=1)
    result = broker.process_candle("BTCUSDT", _bar(open_=100, high=101, low=99, close=100),
                                   protections={order["id"]: {
                                       "stop_loss": stop, "take_profit": 120 if side == "buy" else 80,
                                       "target_r": ratio, "tick_size": 0.1,
                                   }})
    position = broker.positions()[0]
    effective = abs(position["take_profit"] - position["entry_price"]) / abs(position["entry_price"] - stop)
    assert effective >= ratio
    assert effective < ratio + 0.03
    assert result["events"][0]["risk_reward"] == ratio
    assert result["events"][0]["stop_loss"] == stop
    assert result["events"][0]["take_profit"] == position["take_profit"]


def test_entry_is_rejected_if_gap_or_slippage_crosses_protective_stop(tmp_path):
    broker = _broker(tmp_path)
    order = broker.submit(symbol="BTCUSDT", side="buy", order_type="stop",
                          quantity=1, stop_price=100)
    result = broker.process_candle("BTCUSDT", _bar(open_=95, high=101, low=94, close=99),
                                   protections={order["id"]: {
                                       "stop_loss": 100.1, "take_profit": 106,
                                       "target_r": 2, "tick_size": .1,
                                   }})
    assert result["events"] == []
    assert broker.positions() == []
    rejected = broker.order(order["id"])
    assert rejected["status"] == "rejected"
    assert "protective stop" in rejected["reason"]


def test_order_level_protection_is_durable_and_needs_no_runtime_side_map(tmp_path):
    path = tmp_path / "durable-protection.db"
    broker = PaperBrokerV2(path, starting_balance=10_000, fee_rate=0,
                           spread_bps=2, slippage_bps=3, participation_rate=1)
    order = broker.submit(
        symbol="BTCUSDT", side="buy", order_type="market", quantity=1,
        protection_stop_loss=95, protection_take_profit=112.5,
        protection_target_r=2.5, protection_tick_size=.1,
    )
    broker.process_candle("BTCUSDT", _bar(open_=100, high=101, low=99, close=100))
    position = broker.positions()[0]
    assert position["stop_loss"] == 95
    assert position["take_profit"] > 112.5
    restored = PaperBrokerV2(path, starting_balance=1)
    persisted = restored.order(order["id"])
    assert persisted["protection_stop_loss"] == 95
    assert persisted["protection_target_r"] == 2.5


def test_bad_candle_is_rejected(tmp_path):
    broker = _broker(tmp_path)
    broker.submit(symbol="BTCUSDT", side="buy", order_type="market", quantity=1)
    with pytest.raises(ValueError, match="invalid"):
        broker.process_candle("BTCUSDT", _bar(high=90, low=100))


def test_v2_api_processes_only_a_cached_provider_candle(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webhook_api
    from data.market_data_v2 import MarketDataService

    market = MarketDataService(tmp_path / "market", request_json=lambda *_: [])
    market.upsert("BTCUSDT", "1h", [(0, 100, 110, 90, 105, 10)], provider="test-provider")
    monkeypatch.setattr(webhook_api, "v2_market_data", market)
    monkeypatch.setattr(webhook_api, "paper_broker_v2", PaperBrokerV2(tmp_path / "api-broker.db", fee_rate=0,
                                                                        spread_bps=0, slippage_bps=0, participation_rate=1))
    app = FastAPI(); app.include_router(webhook_api.router)
    client, headers = TestClient(app), {"X-Webhook-Secret": "dev-webhook-secret"}
    assert client.get("/market-data-v2/latest/BTCUSDT").status_code == 200
    order = client.post("/paper-v2/orders", json={"symbol": "BTCUSDT", "side": "buy",
                                                    "type": "market", "quantity": 1}, headers=headers)
    assert order.status_code == 200, order.text
    assert client.post("/paper-v2/process/BTCUSDT", headers=headers).status_code == 200
    assert client.get("/paper-v2/positions").json()["positions"][0]["size"] == 1
