import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import webhook_api
from routers.price_action import router
from services.price_action_lab import PriceActionPaperAccount


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
