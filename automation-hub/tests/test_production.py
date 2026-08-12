"""Production Readiness (#19)."""
from datetime import datetime, timedelta, timezone

import pytest

from services.production import readiness, freshness_summary, memory_mb


def _cov(n_with_data, last_age_s=120):
    last = (datetime.now(timezone.utc) - timedelta(seconds=last_age_s)).isoformat()
    rows = [{"symbol": f"S{i}", "timeframe": "1d", "candles": 100, "last": last} for i in range(n_with_data)]
    rows += [{"symbol": "EMPTY", "timeframe": "1d", "candles": 0, "last": None}]
    return rows


def test_healthy_when_all_pass():
    r = readiness(api_ok=True, db_ok=True, db_detail="ok", coverage=_cov(3),
                  strategy_errors=0, order_errors=0, uptime_s=500, engine_running=True)
    assert r["status"] == "healthy"
    assert all(c["ok"] for c in r["checks"])
    assert r["summary"].startswith("9/9")


def test_degraded_on_errors_or_no_data():
    r = readiness(api_ok=True, db_ok=True, db_detail="ok", coverage=_cov(0),
                  strategy_errors=2, order_errors=1, uptime_s=10, engine_running=False)
    assert r["status"] == "degraded"
    names = {c["name"]: c for c in r["checks"]}
    assert names["Historical cache coverage"]["ok"] is False
    assert names["Strategy errors"]["ok"] is False and names["Execution readiness"]["ok"] is False


def test_down_when_db_unreachable():
    r = readiness(api_ok=True, db_ok=False, db_detail="boom", coverage=[],
                  strategy_errors=0, order_errors=0, uptime_s=5, engine_running=True)
    assert r["status"] == "down"
    assert any(c["name"] == "Database" and c["level"] == "down" for c in r["checks"])


def test_active_instance_freshness_and_execution_readiness_are_not_cache_coverage():
    stale = {"desired_running": True, "state": "data_stale",
             "market_data": {"market_data_status": "stale"},
             "engine": {"last_heartbeat": datetime.now(timezone.utc).isoformat(),
                        "websocket": {"available": False}}}
    result = readiness(api_ok=True, db_ok=True, db_detail="ok", coverage=_cov(3),
                       strategy_errors=0, order_errors=0, uptime_s=50,
                       engine_running=False, active_instances=[stale])
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["Historical cache coverage"]["ok"] is True
    assert checks["Active feed freshness"]["ok"] is False
    assert checks["Execution readiness"]["ok"] is False


def test_readiness_requires_every_desired_websocket_or_explicit_rest_fallback():
    now = datetime.now(timezone.utc).isoformat()
    connected = {"desired_running": True, "state": "running",
                 "market_data": {"market_data_status": "healthy"},
                 "engine": {"last_heartbeat": now,
                            "websocket": {"available": True, "rest_fallback_reads": 0}}}
    disconnected = {"desired_running": True, "state": "running",
                    "market_data": {"market_data_status": "healthy"},
                    "engine": {"last_heartbeat": now,
                               "websocket": {"available": False, "rest_fallback_reads": 0}}}
    result = readiness(api_ok=True, db_ok=True, db_detail="ok", coverage=_cov(2),
                       strategy_errors=0, order_errors=0, uptime_s=50,
                       engine_running=False, active_instances=[connected, disconnected])
    checks = {item["name"]: item for item in result["checks"]}
    assert checks["WebSocket connected"]["ok"] is False

    disconnected["engine"]["websocket"]["rest_fallback_reads"] = 2
    connected["engine"]["websocket"] = {"available": False, "rest_fallback_reads": 1}
    fallback = readiness(api_ok=True, db_ok=True, db_detail="ok", coverage=_cov(2),
                         strategy_errors=0, order_errors=0, uptime_s=50,
                         engine_running=False, active_instances=[connected, disconnected])
    fallback_check = {item["name"]: item for item in fallback["checks"]}["WebSocket connected"]
    assert fallback_check["ok"] is True
    assert "REST forward fallback" in fallback_check["detail"]


def test_freshness_summary_counts_and_age():
    f = freshness_summary(_cov(2, last_age_s=300))
    assert f["with_data"] == 2 and f["datasets"] == 3
    assert f["freshest_age_s"] is not None and f["freshest_age_s"] >= 290


def test_memory_mb_returns_number_or_none():
    m = memory_mb()
    assert m is None or m > 0


# ───────────────────────── endpoint ─────────────────────────
@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webhook_api
    app = FastAPI(); app.include_router(webhook_api.router)
    return TestClient(app)


def test_production_endpoint(client):
    r = client.get("/production/readiness").json()
    # Cache coverage, live-feed freshness, transport health, worker heartbeat,
    # and execution readiness are deliberately separate operational signals.
    assert "status" in r and "checks" in r and len(r["checks"]) == 9
    assert "memory_mb" in r and "uptime_s" in r and "data_freshness" in r
