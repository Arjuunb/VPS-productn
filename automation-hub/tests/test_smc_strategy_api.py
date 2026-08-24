import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import webhook_api
from routers.native_smc import router
from services.smc_strategy_lab import SMCPaperAccount


@pytest.fixture()
def api(monkeypatch, tmp_path):
    account = SMCPaperAccount(tmp_path / "smc-api.db")
    monkeypatch.setattr(webhook_api, "smc_paper", account)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), account


def test_model_registry_and_paper_state_never_allow_real_execution(api):
    client, _ = api
    models = client.get("/research/smc/strategy-models")
    assert models.status_code == 200
    assert models.json()["real_execution_allowed"] is False
    assert [row["status"] for row in models.json()["models"]] == ["ACTIVE", "PARKED", "PARKED"]
    paper = client.get("/research/smc/paper")
    assert paper.status_code == 200
    assert paper.json()["account_scope"] == "SMC_STRATEGY_LAB_ONLY"
    assert paper.json()["real_execution_allowed"] is False


def test_smc_mutations_are_protected_and_reset_phrase_is_exact(api):
    client, _ = api
    assert client.post("/research/smc/paper/reset", json={"confirmation": "RESET SMC PAPER"}).status_code == 401
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    bad = client.post("/research/smc/paper/reset", headers=headers, json={"confirmation": "RESET"})
    assert bad.status_code == 409
    good = client.post("/research/smc/paper/reset", headers=headers,
                       json={"confirmation": "RESET SMC PAPER"})
    assert good.status_code == 200
    assert good.json()["execution_mode"] == "PAPER"


def test_session_configuration_rejects_parked_model(api):
    client, _ = api
    headers = {"x-webhook-secret": webhook_api.settings.admin_key}
    response = client.post("/research/smc/sessions/current/configuration", headers=headers, json={
        "operating_mode": "automatic", "model_id": "SMC_M2_BOS_CONTINUATION", "risk_pct": 0.5,
    })
    assert response.status_code == 409
    assert "parked" in response.json()["detail"]


def test_journal_filters_are_session_scoped(api):
    client, account = api
    session_id = account.session()["id"]
    response = client.get(f"/research/smc/journal?session_id={session_id}")
    assert response.status_code == 200
    assert response.json()["filters"]["session_id"] == session_id
    assert response.json()["real_execution_allowed"] is False


def test_journal_csv_export_is_human_readable(api):
    client, _ = api
    response = client.get("/research/smc/journal/export?format=csv")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.text.startswith("journal_id,session_id,symbol,timeframe")
