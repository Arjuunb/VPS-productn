"""Explicit, evidence-preserving cleanup of legacy Price Action PAPER exposure."""
from datetime import datetime, timezone

import pytest

from bot.types import Bar
from services.price_action_lab import PaperExecutionConfig, PriceActionPaperAccount


NOW = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)
SYNCED = {
    "state": "SYNCHRONIZED",
    "reliable": True,
    "health_reason": "candles, bid/ask and mark are reconciled and fresh",
    "bid": 104.9,
    "ask": 105.1,
    "mark": 105.0,
    "last_closed_update": NOW.isoformat(),
    "last_quote_update": NOW.isoformat(),
    "last_mark_update": NOW.isoformat(),
    "source": "deterministic reconciled fixture",
}


def _legacy_account(tmp_path):
    account = PriceActionPaperAccount(tmp_path / "pa-legacy-remediation.db")
    account.configure(
        execution_config=PaperExecutionConfig(
            operating_mode="automatic", strategy_id="PA1_SR_REJECTION",
            risk_pct=0.5, target_r=2.5,
        ),
        strategy_config={"stop_model": "rejection_extreme"},
    )
    account.broker.submit(
        symbol="BTCUSDT", side="buy", order_type="market", quantity=0.1,
    )
    account.broker.process_candle(
        "BTCUSDT", Bar(NOW, 100, 101, 99, 100, 10_000),
    )
    position = account.state()["positions"][0]
    assert position["protection_status"] == \
        "LEGACY_UNPROTECTED_REQUIRES_CLOSE_OR_PROTECTION"
    assert position["stop_loss"] is position["take_profit"] is None
    return account, position


def test_legacy_remediation_preserves_unknowns_and_requires_readiness_recheck(tmp_path):
    account, original = _legacy_account(tmp_path)

    result = account.remediate_legacy_position(
        "BTCUSDT", mark_price=SYNCED["mark"], market_evidence=SYNCED,
        initiated_by="test_operator",
    )

    assert result["close"]["closed"] is True
    assert result["close"]["reason"] == "LEGACY_POSITION_REMEDIATION"
    assert result["real_execution_allowed"] is False
    assert account.state()["positions"] == []
    assert account.state()["entry_control"]["readiness_recheck_required"] == 1

    archived = account.state()["position_remediations"][0]
    assert archived["reason_code"] == "LEGACY_POSITION_REMEDIATION"
    assert archived["original_position"]["entry_price"] == original["entry_price"]
    assert archived["original_position"]["stop_loss"] is None
    assert archived["original_position"]["take_profit"] is None

    journal = account.journal.get(result["journal_id"])
    record = journal["latest"]
    assert record["outcome"]["exit_reason"] == "LEGACY_POSITION_REMEDIATION"
    assert record["review"]["include_in_research_statistics"] is False
    assert record["order_risk"]["stop"] is None
    assert record["order_risk"]["target"] is None
    assert record["order_risk"]["expected_risk_usdt"] is None
    assert record["order_risk"]["expected_risk_r"] is None
    assert record["outcome"]["gross_r"] is None
    assert record["outcome"]["net_r"] is None
    assert record["audit"]["original_position"]["entry_price"] == original["entry_price"]

    readiness = account.recheck_readiness(SYNCED)
    assert readiness["ready"] is True
    assert readiness["state"] == "READY"
    assert readiness["blockers"] == []
    assert readiness["control"]["readiness_recheck_required"] == 0
    assert readiness["real_execution_allowed"] is False


def test_legacy_remediation_fails_closed_for_unreconciled_data(tmp_path):
    account, _ = _legacy_account(tmp_path)
    with pytest.raises(ValueError, match="requires synchronized"):
        account.remediate_legacy_position(
            "BTCUSDT", mark_price=105,
            market_evidence={"state": "STALE_CANDLES", "reliable": False},
            initiated_by="test_operator",
        )
    assert account.state()["positions"]


def test_legacy_remediation_endpoint_requires_both_confirmations(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webhook_api
    from routers.price_action import router

    class Runtime:
        def remediate_legacy_position(self, symbol, *, initiated_by):
            return {"symbol": symbol, "initiated_by": initiated_by,
                    "real_execution_allowed": False}

    monkeypatch.setattr(webhook_api, "price_action_runtime", Runtime())
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    endpoint = "/research/price-action/paper/positions/BTCUSDT/legacy-remediation-close"

    assert client.post(endpoint, headers=headers, json={
        "confirmation": "CLOSE", "acknowledge_missing_historical_protection": True,
    }).status_code == 422
    assert client.post(endpoint, headers=headers, json={
        "confirmation": "CLOSE LEGACY PAPER POSITION",
        "acknowledge_missing_historical_protection": False,
    }).status_code == 422
    response = client.post(endpoint, headers=headers, json={
        "confirmation": "CLOSE LEGACY PAPER POSITION",
        "acknowledge_missing_historical_protection": True,
    })
    assert response.status_code == 200
    assert response.json()["real_execution_allowed"] is False
