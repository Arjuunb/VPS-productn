from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from data.market_data_v2 import MarketDataService
from services.price_action_lab import (
    PaperExecutionConfig,
    PriceActionPaperAccount,
    binance_visual_state,
    replay_state,
)


NOW = datetime(2026, 1, 2, 12, 2, tzinfo=timezone.utc)


def bars(count=20):
    start = NOW - timedelta(minutes=5 * (count - 1))
    return [Bar(start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i, 1000)
            for i in range(count)]


class PublicMarket:
    def public_usdm_window(self, symbol, timeframe, *, limit):
        return bars()

    def public_usdm_quote(self, symbol):
        return {"symbol": symbol, "bid": 119.9, "ask": 120.1, "mark": 120,
                "funding_rate": .0001, "last_funding_time": NOW.isoformat(),
                "next_funding_time": (NOW + timedelta(hours=8)).isoformat(),
                "provider_time": NOW.isoformat()}


def test_live_visual_keeps_forming_candle_outside_native_engine():
    state = binance_visual_state(PublicMarket(), "BTCUSDT", "5m", observed_at=NOW)
    assert state["forming_candle"]["timestamp"] == bars()[-1].timestamp.isoformat()
    assert state["candles"][-1]["timestamp"] == bars()[-2].timestamp
    assert state["snapshot"]["candle_open"] == bars()[-2].timestamp
    assert state["data_provenance"]["forming_candle_excluded"] is True
    assert state["execution_allowed"] is False
    assert state["live_display"]["connection_state"] == "SYNCHRONIZED"
    assert state["live_display"]["transport_state"] == "REST_POLL"
    assert state["live_display"]["reliable"] is True


def test_replay_reveals_only_cursor_prefix(monkeypatch, tmp_path):
    market = MarketDataService(tmp_path / "market", request_json=lambda *_: [])
    rows = bars(30)
    monkeypatch.setattr(market, "bars", lambda *args, **kwargs: rows)
    state = replay_state(market, "BTCUSDT", "5m", cursor=12, limit=100)
    assert len(state["candles"]) == 12
    assert state["candles"][-1]["timestamp"] == rows[11].timestamp
    assert state["replay"] == {"cursor": 12, "total": 30, "future_candles_visible": False, "has_next": True}


def test_price_action_account_is_separate_persistent_and_reset_creates_session(tmp_path):
    path = tmp_path / "pa.db"
    account = PriceActionPaperAccount(path, starting_balance=10_000)
    old = account.session()["id"]
    account.broker.submit(symbol="BTCUSDT", side="buy", order_type="market", quantity=.1)
    account.broker.process_candle("BTCUSDT", Bar(NOW, 100, 102, 99, 101, 10_000))
    assert account.state()["positions"]
    before_funding = account.state()["account"]["balance"]
    funding = account.apply_funding_once(symbol="BTCUSDT", funding_time=NOW.isoformat(),
                                         rate=.001, mark_price=101)
    assert funding["applied"] is True
    after_funding = account.state()["account"]["balance"]
    assert after_funding < before_funding
    duplicate = account.apply_funding_once(symbol="BTCUSDT", funding_time=NOW.isoformat(),
                                           rate=.001, mark_price=101)
    assert duplicate["reason"] == "funding event already processed"
    assert account.state()["account"]["balance"] == after_funding
    reset = account.reset()
    assert reset["session"]["id"] != old
    assert reset["positions"] == []
    assert reset["orders"] == []
    assert reset["account"]["balance"] == 10_000
    account.set_leverage(12)
    reopened = PriceActionPaperAccount(path, starting_balance=10_000)
    assert reopened.session()["id"] == reset["session"]["id"]
    assert reopened.state()["positions"] == []
    assert reopened.state()["account"]["leverage"] == 12


def test_live_execution_flag_cannot_appear_in_mutation_contract(tmp_path):
    state = PriceActionPaperAccount(tmp_path / "pa.db").state()
    assert state["live_execution_allowed"] is False
    assert state["real_funds"] is False
    with pytest.raises(TypeError):
        PriceActionPaperAccount(tmp_path / "other.db", live_execution=True)  # type: ignore[call-arg]


def test_binance_public_contract_rules_and_quote_are_provider_derived(tmp_path):
    def provider(url, _params):
        if url.endswith("exchangeInfo"):
            return {"symbols": [{
                "symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT",
                "contractType": "PERPETUAL", "pricePrecision": 1, "quantityPrecision": 3,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10", "minPrice": "0.10"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100"},
                    {"filterType": "MIN_NOTIONAL", "notional": "5"},
                ],
            }]}
        if url.endswith("bookTicker"):
            return {"bidPrice": "50000", "askPrice": "50000.1", "time": 1_767_225_600_000}
        if url.endswith("premiumIndex"):
            return {"markPrice": "50000.05", "indexPrice": "50000", "lastFundingRate": ".0001",
                    "nextFundingTime": 1_767_254_400_000, "time": 1_767_225_600_000}
        if url.endswith("fundingRate"):
            return [{"fundingRate": ".0001", "fundingTime": 1_767_225_600_000}]
        raise AssertionError(url)

    market = MarketDataService(tmp_path / "market", request_json=provider)
    rules = market.usdm_contract_rules("BTCUSDT")
    assert rules["tick_size"] == .1
    assert rules["quantity_step"] == .001
    assert rules["min_notional"] == 5
    quote = market.public_usdm_quote("BTCUSDT")
    assert quote["bid"] == 50_000
    assert quote["ask"] == 50_000.1
    assert quote["mark"] == 50_000.05
    assert quote["funding_rate"] == .0001


def test_unreliable_feed_cancels_strategy_order_and_never_infers_fill(tmp_path):
    account = PriceActionPaperAccount(tmp_path / "pa.db")
    account.start(
        mode="LIVE_PAPER", symbol="BTCUSDT", timeframe="5m",
        execution_config=PaperExecutionConfig(
            operating_mode="automatic", strategy_id="PA1_SR_REJECTION"),
    )
    state = {
        "symbol": "BTCUSDT", "timeframe": "5m", "setups": [],
        "proposals": [{
            "id": "proposal-1", "setup_id": "setup-1", "strategy_id": "PA1_SR_REJECTION",
            "direction": "bullish", "entry": 105, "stop": 100, "target": 117.5,
            "valid_until_index": 20,
        }],
        "metrics": {},
    }
    rules = {"tick_size": .1, "quantity_step": .001, "min_quantity": .001,
             "max_quantity": 1000, "min_notional": 5}
    created = account.synchronize_strategy(
        state, contract_rules=rules, candle=Bar(NOW, 100, 104, 99, 101, 1000),
        feed_reliable=True, feed_status={"state": "SYNCHRONIZED"})
    assert len(created["created"]) == 1
    assert account.audit_pending_orders()["pending_strategy_orders"] == 1
    balance = account.state()["account"]["balance"]
    paused = account.synchronize_strategy(
        state, contract_rules=rules, candle=Bar(NOW + timedelta(minutes=5), 101, 110, 95, 105, 1000),
        feed_reliable=False,
        feed_status={"state": "STALE_CANDLES", "health_reason": "test stale feed"})
    assert paused["broker"]["paused"] is True
    assert paused["broker"]["events"] == []
    assert account.audit_pending_orders()["pending_strategy_orders"] == 0
    assert account.state()["positions"] == []
    assert account.state()["account"]["balance"] == balance
    assert any(row["action"] == "metadata_reconciled"
               for row in paused["reconciliation"]["actions"])


def test_reconciled_fill_quote_and_funding_are_order_scoped_and_persistent(tmp_path):
    path = tmp_path / "evidence.db"
    account = PriceActionPaperAccount(path)
    account.start(
        mode="LIVE_PAPER", symbol="BTCUSDT", timeframe="5m",
        execution_config=PaperExecutionConfig(
            operating_mode="automatic", strategy_id="PA1_SR_REJECTION"),
    )
    state = {
        "symbol": "BTCUSDT", "timeframe": "5m", "setups": [],
        "proposals": [{
            "id": "proposal-evidence", "setup_id": "setup-evidence",
            "strategy_id": "PA1_SR_REJECTION", "direction": "bullish",
            "entry": 105, "stop": 100, "target": 117.5, "valid_until_index": 20,
        }], "metrics": {},
    }
    rules = {"tick_size": .1, "quantity_step": .001, "min_quantity": .001,
             "max_quantity": 1000, "min_notional": 5}
    account.synchronize_strategy(
        state, contract_rules=rules, candle=Bar(NOW, 100, 104, 99, 103, 1000),
        feed_reliable=True, feed_status={"state": "SYNCHRONIZED"})
    account.synchronize_strategy(
        state, contract_rules=rules,
        candle=Bar(NOW + timedelta(minutes=5), 104, 106, 101, 105, 1000),
        feed_reliable=True,
        feed_status={"state": "SYNCHRONIZED", "last_quote_update": NOW.isoformat(),
                     "last_mark_update": NOW.isoformat()},
        execution_quote={"bid": 104.9, "ask": 105.1, "mark": 105.0},
    )
    snapshot = account.state()
    order_id = snapshot["order_metadata"][0]["order_id"]
    fill_event = next(row for row in snapshot["activity"]
                      if row["kind"] == "paper_order_filled")
    assert fill_event["object_id"] == order_id
    assert fill_event["payload"]["execution_quote"]["bid"] == 104.9
    applied = account.apply_funding_once(
        symbol="BTCUSDT", funding_time=NOW.isoformat(), rate=.001, mark_price=105)
    assert applied["applied"] is True
    funding = account.state()["funding_events"][0]
    assert funding["order_id"] == order_id
    reopened = PriceActionPaperAccount(path)
    assert reopened.state()["funding_events"][0]["order_id"] == order_id
