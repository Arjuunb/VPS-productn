"""Control and TradingView credentials are unconditionally separated."""
import pytest
from fastapi.testclient import TestClient

import app as app_module
import webhook_api as wa
from config import settings

WH = settings.webhook_secret


@pytest.fixture
def restore():
    admin, scope = settings.admin_key, settings.scope_webhook_secret
    yield
    settings.admin_key, settings.scope_webhook_secret = admin, scope


def test_admin_key_controls(restore):
    settings.admin_key, settings.scope_webhook_secret = "ADMIN-KEY-123", True
    wa._check_secret("ADMIN-KEY-123")           # admin key controls
    with pytest.raises(Exception):
        wa._check_secret(WH)                       # webhook key never controls
    wa._check_webhook_secret(WH)                   # but remains valid for ingestion


def test_bad_credential_always_rejected(restore):
    settings.admin_key, settings.scope_webhook_secret = "ADMIN-KEY-123", True
    for bad in (None, "", "nope"):
        with pytest.raises(Exception):
            wa._check_secret(bad)


# ─────────────────────────── auth wall (reads) honours scoping ───────────────────────────
def test_auth_wall_scopes_reads(restore):
    settings.admin_key, settings.scope_webhook_secret = "ADMIN-KEY-123", True
    c = TestClient(app_module.app)
    # webhook secret no longer opens the read wall...
    assert c.get("/risk/summary", headers={"X-Webhook-Secret": WH}).status_code == 401
    # ...but the admin key does
    assert c.get("/risk/summary", headers={"X-Webhook-Secret": "ADMIN-KEY-123"}).status_code == 200


def test_webhook_endpoint_accepts_webhook_secret_when_scoped(restore):
    settings.admin_key, settings.scope_webhook_secret = "ADMIN-KEY-123", True
    c = TestClient(app_module.app)
    payload = {"alert_id": "scope-test-1", "symbol": "BTCUSDT", "side": "BUY",
               "entry": 60000, "stop": 59000}
    # TradingView's webhook secret still works on /webhook — that's the whole point
    r = c.post("/webhook/tradingview", json=payload, headers={"X-Webhook-Secret": WH})
    assert r.status_code == 200
    # a wrong secret is still rejected
    assert c.post("/webhook/tradingview", json=payload,
                  headers={"X-Webhook-Secret": "nope"}).status_code == 401
