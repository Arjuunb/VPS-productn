import pytest
import sqlite3

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import webhook_api
from routers.price_action import router
from services.price_action_lab import PriceActionPaperAccount


def test_pa_read_endpoints_surface_persistence_block_instead_of_500(monkeypatch):
    class Broker:
        @staticmethod
        def positions():
            return []

    class Paper:
        broker = Broker()

        @staticmethod
        def state(_marks=None):
            raise sqlite3.OperationalError("database is locked")

        @staticmethod
        def sessions():
            raise sqlite3.OperationalError("database is locked")

    class Runtime:
        @staticmethod
        def bot_status():
            raise sqlite3.OperationalError("database is locked")

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(webhook_api, "price_action_paper", Paper())
    monkeypatch.setattr(webhook_api, "price_action_runtime", Runtime())

    for path in (
            "/research/price-action/paper",
            "/research/price-action/bot-status",
            "/research/price-action/sessions"):
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["detail"]["state"] == "PERSISTENCE_BLOCKED"
        assert response.json()["detail"]["retryable"] is True


def test_price_action_api_manifest_and_destructive_mutations_are_protected(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(webhook_api, "price_action_paper", PriceActionPaperAccount(tmp_path / "pa-api.db"))
    manifest = client.get("/research/price-action/manifest")
    assert manifest.status_code == 200
    assert manifest.json()["live_execution_allowed"] is False
    assert client.post("/research/price-action/paper/reset",
                       json={"confirmation": "RESET PRICE ACTION PAPER"}).status_code == 401


def test_exact_reset_confirmation_is_required(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(webhook_api, "price_action_paper", PriceActionPaperAccount(tmp_path / "pa-api.db"))
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    bad = client.post("/research/price-action/paper/reset", headers=headers,
                      json={"confirmation": "RESET"})
    assert bad.status_code == 422
    good = client.post("/research/price-action/paper/reset", headers=headers,
                       json={"confirmation": "RESET PRICE ACTION PAPER"})
    assert good.status_code == 200
    assert good.json()["execution_mode"] == "PAPER"


def test_strategy_order_reconciliation_is_protected_and_preserves_manual_orders(monkeypatch):
    class Runtime:
        def reconcile_paper_orders(self):
            return {"actions": [], "records_deleted": 0, "manual_orders_changed": 0}

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(webhook_api, "price_action_runtime", Runtime())

    assert client.post("/research/price-action/paper/orders/reconcile").status_code == 401
    response = client.post(
        "/research/price-action/paper/orders/reconcile",
        headers={"x-webhook-secret": webhook_api.settings.admin_key},
    )
    assert response.status_code == 200
    assert response.json() == {"actions": [], "records_deleted": 0, "manual_orders_changed": 0}


def test_manual_price_action_entries_require_and_persist_protection(monkeypatch, tmp_path):
    class Market:
        @staticmethod
        def usdm_contract_rules(_symbol):
            return {"tick_size": .1, "quantity_step": .001, "min_quantity": .001,
                    "max_quantity": 100, "min_notional": 5}

        @staticmethod
        def public_usdm_quote(_symbol):
            return {"mark": 100}

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    account = PriceActionPaperAccount(tmp_path / "pa-protected-order.db")
    monkeypatch.setattr(webhook_api, "price_action_paper", account)
    monkeypatch.setattr(webhook_api, "v2_market_data", Market())
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    base = {"symbol": "BTCUSDT", "side": "buy", "type": "market", "quantity": .1}

    rejected = client.post("/research/price-action/paper/orders", headers=headers, json=base)
    assert rejected.status_code == 400
    assert "require both stop loss and take profit" in rejected.json()["detail"]

    accepted = client.post("/research/price-action/paper/orders", headers=headers, json={
        **base, "stop_loss": 95, "take_profit": 110,
    })
    assert accepted.status_code == 200
    order = accepted.json()
    assert order["protection_stop_loss"] == 95
    assert order["protection_take_profit"] == 110
    assert order["protection_target_r"] == 2


def test_session_api_configures_paper_modes_and_resumes_without_live_path(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    account = PriceActionPaperAccount(tmp_path / "pa-sessions.db")
    monkeypatch.setattr(webhook_api, "price_action_paper", account)
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}

    created = client.post("/research/price-action/sessions", headers=headers, json={
        "mode": "LIVE_PAPER", "symbol": "BTCUSDT", "timeframe": "5m",
        "starting_balance": 10_000, "operating_mode": "manual_approval",
        "strategy_id": "PA2_TREND_PULLBACK", "risk_pct": .5,
    })
    assert created.status_code == 200
    payload = created.json()
    assert payload["session"]["operating_mode"] == "manual_approval"
    assert payload["live_execution_allowed"] is False
    session_id = payload["session"]["id"]

    configured = client.post("/research/price-action/sessions/current/configuration", headers=headers, json={
        "operating_mode": "automatic", "strategy_id": "PA2_TREND_PULLBACK", "risk_pct": .25,
    })
    assert configured.status_code == 200
    assert configured.json()["session"]["execution_config"]["risk_pct"] == .25
    assert client.get("/research/price-action/sessions").json()["sessions"][0]["id"] == session_id


def test_session_api_rejects_invalid_strategy_models_before_persisting(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    account = PriceActionPaperAccount(tmp_path / "pa-invalid-config.db")
    monkeypatch.setattr(webhook_api, "price_action_paper", account)
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    original = account.session()

    response = client.post("/research/price-action/sessions", headers=headers, json={
        "symbol": "BTCUSDT", "timeframe": "5m", "entry_model": "future_peeking_entry",
    })

    assert response.status_code == 400
    assert account.session()["id"] == original["id"]


def test_comparison_api_refuses_different_execution_assumptions(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(webhook_api, "price_action_paper", PriceActionPaperAccount(tmp_path / "pa-compare.db"))
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    assumptions = {"source_data": "dataset", "symbols": ["BTCUSDT"], "timeframes": ["5m"],
                   "date_partitions": {}, "cost_model": {}, "fill_model": "adverse",
                   "ambiguity": "stop_first", "risk_per_trade_pct": .5}
    response = client.post("/research/price-action/experiments/compare-smc", headers=headers, json={
        "price_action": {"assumptions": assumptions, "metrics": {}},
        "smc": {"assumptions": {**assumptions, "fill_model": "optimistic"}, "metrics": {}},
    })
    assert response.status_code == 409
    assert "assumptions differ" in response.json()["detail"]


def test_historical_funding_download_is_protected_and_smc_normalization_is_read_only(monkeypatch):
    class Market:
        def download_usdm_funding_history(self, symbol, *, start_ms, end_ms):
            return {"symbol": symbol, "start_ms": start_ms, "end_ms": end_ms,
                    "coverage": {"state": "HISTORICAL_FUNDING_AVAILABLE"}}

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    monkeypatch.setattr(webhook_api, "v2_market_data", Market())
    payload = {"symbol": "BTCUSDT", "start": "2026-01-01T00:00:00Z",
               "end": "2026-01-02T00:00:00Z"}
    assert client.post("/research/price-action/funding/download", json=payload).status_code == 401
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    downloaded = client.post("/research/price-action/funding/download",
                             headers=headers, json=payload)
    assert downloaded.status_code == 200
    assert downloaded.json()["coverage"]["state"] == "HISTORICAL_FUNDING_AVAILABLE"

    normalized = client.post("/research/price-action/smc-normalization", headers=headers, json={
        "source": {"proposals": [{"id": "smc-proposal"}]},
        "assumptions": {}, "experiment_configuration": {},
    })
    assert normalized.status_code == 200
    body = normalized.json()
    assert body["read_only"] is True and body["execution_allowed"] is False
    assert body["normalization"]["fair_comparison_allowed"] is False


def test_governance_read_models_are_paper_only_and_mutations_are_protected(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    account = PriceActionPaperAccount(tmp_path / "pa-governance-api.db")
    monkeypatch.setattr(webhook_api, "price_action_paper", account)

    journal = client.get("/research/price-action/journal?symbol=BTCUSDT&timeframe=5m")
    assert journal.status_code == 200
    assert journal.json()["entries"] == []
    assert journal.json()["real_execution_allowed"] is False
    exported = client.get("/research/price-action/journal/export?partition=untouched_oos")
    assert exported.status_code == 200
    assert exported.json()["immutable_source_records"] is True
    analysis = client.get("/research/price-action/learning/analysis")
    assert analysis.status_code == 200
    assert analysis.json()["active_strategy_mutated"] is False
    assert client.get("/research/price-action/learning/candidates").json()["real_execution_allowed"] is False

    candidate = {
        "rule_difference": {"first_touch_only": True}, "evidence_ids": [],
        "code_fingerprint": "engine-1", "dataset_fingerprint": "dataset-1",
        "expected_benefit": "test one isolated hypothesis",
    }
    assert client.post("/research/price-action/learning/candidates", json=candidate).status_code == 401
    assert client.post("/research/price-action/journal/missing/revisions",
                       json={"notes": "test", "tags": []}).status_code == 401
