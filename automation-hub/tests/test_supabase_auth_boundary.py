"""Focused no-network checks for the Supabase customer-auth boundary."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

import app as hub_app
from services.supabase_auth import Principal, SupabaseAuth


def test_supabase_client_verifies_profile_and_caches_token(monkeypatch):
    auth = SupabaseAuth(url="https://example.supabase.co", anon_key="anon")
    calls = []

    def fake_request(path, **kwargs):
        calls.append(path)
        if path == "/auth/v1/user":
            return {"id": "11111111-1111-1111-1111-111111111111", "email": "person@example.com",
                    "email_confirmed_at": "2026-01-01T00:00:00Z", "user_metadata": {"full_name": "Person"}}
        return [{"id": "11111111-1111-1111-1111-111111111111", "full_name": "Person", "role": "user"}]

    monkeypatch.setattr(auth, "_request", fake_request)
    first = auth.principal("access-token")
    second = auth.principal("access-token")
    assert first == second
    assert first.email_confirmed is True
    assert calls.count("/auth/v1/user") == 1


def test_regular_supabase_user_cannot_read_shared_legacy_engine(monkeypatch):
    principal = Principal(id="11111111-1111-1111-1111-111111111111", email="person@example.com",
                          email_confirmed=True, full_name="Person", role="user")
    monkeypatch.setattr(hub_app.settings, "auth_mode", "supabase")
    monkeypatch.setattr(hub_app.supabase_auth, "principal", lambda _token: principal)
    client = TestClient(hub_app.app)

    session = client.post("/auth/supabase/session", headers={"Authorization": "Bearer access"}, json={"remember": False})
    assert session.status_code == 200
    assert "hub_supabase_access" in session.headers["set-cookie"]
    assert client.get("/auth/me").json()["id"] == principal.id
    assert client.get("/paper/trades").status_code == 403
    assert client.post("/engine/timeframe?timeframe=1h").status_code == 403
    assert client.patch("/auth/me", headers={"Origin": "https://evil.example"}, json={"full_name": "Attack"}).status_code == 403


def test_verified_supabase_admin_can_control_engine_without_browser_secret(monkeypatch, tmp_path):
    principal = Principal(id="33333333-3333-3333-3333-333333333333", email="admin@example.com",
                          email_confirmed=True, full_name="Admin", role="admin")
    monkeypatch.setattr(hub_app.settings, "auth_mode", "supabase")
    monkeypatch.setattr(hub_app.settings, "admin_key", "test-admin-control-key")
    monkeypatch.setattr(hub_app.settings, "settings_path", str(tmp_path / "runtime_settings.json"))
    monkeypatch.setattr(hub_app.supabase_auth, "principal", lambda _token: principal)
    client = TestClient(hub_app.app)

    session = client.post("/auth/supabase/session", headers={"Authorization": "Bearer access"}, json={"remember": False})
    assert session.status_code == 200

    import webhook_api
    original_timeframe = webhook_api.engine.timeframe
    try:
        response = client.post("/engine/timeframe?timeframe=1h")
        assert response.status_code == 200
        assert response.json()["timeframe"] == "1h"
    finally:
        webhook_api.engine.timeframe = original_timeframe


def test_unverified_supabase_account_cannot_create_dashboard_session(monkeypatch):
    principal = Principal(id="22222222-2222-2222-2222-222222222222", email="pending@example.com",
                          email_confirmed=False, full_name="Pending", role="user")
    monkeypatch.setattr(hub_app.settings, "auth_mode", "supabase")
    monkeypatch.setattr(hub_app.supabase_auth, "principal", lambda _token: principal)
    client = TestClient(hub_app.app)
    response = client.post("/auth/supabase/session", headers={"Authorization": "Bearer access"})
    assert response.status_code == 403
