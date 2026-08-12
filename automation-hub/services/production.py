"""Production Readiness (#19) — operational health of the running system.

Monitors API, database, data freshness, recent errors, memory and engine uptime
and rolls them into one status. Pure aggregator: the endpoint gathers the live
inputs and passes them in, so this stays unit-testable.
"""
from __future__ import annotations

from datetime import datetime, timezone


def memory_mb():
    try:
        import resource
        # ru_maxrss is KB on Linux, bytes on macOS — assume Linux (the deploy)
        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except Exception:  # noqa: BLE001
        return None


def _age_seconds(iso) -> float | None:
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if not d.tzinfo:
            d = d.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - d).total_seconds()
    except (ValueError, TypeError):
        return None


def freshness_summary(coverage: list) -> dict:
    """coverage = [{symbol, timeframe, candles, last(iso)}, ...]."""
    have = [c for c in coverage if (c.get("candles") or 0) > 0]
    newest = None
    for c in have:
        a = _age_seconds(c.get("last"))
        if a is not None and (newest is None or a < newest):
            newest = a
    return {"datasets": len(coverage), "with_data": len(have),
            "freshest_age_s": round(newest) if newest is not None else None}


def _check(name, ok, detail, level="ok"):
    return {"name": name, "ok": bool(ok), "level": level if ok else ("warn" if level == "warn" else "down"),
            "detail": detail}


def readiness(*, api_ok: bool, db_ok: bool, db_detail: str, coverage: list,
              strategy_errors: int, order_errors: int, uptime_s: float | None,
              engine_running: bool, active_instances: list | None = None) -> dict:
    fresh = freshness_summary(coverage)
    mem = memory_mb()
    instance_mode = active_instances is not None
    active_instances = active_instances or []
    desired = [row for row in active_instances if row.get("desired_running")]
    instance_engine_ready = any(row.get("state") in ("ready", "running") for row in desired)
    active_feed_ok = bool(desired) and all(
        (row.get("market_data") or {}).get("market_data_status") == "healthy"
        for row in desired)
    ws_states = [(row.get("engine") or {}).get("websocket") for row in desired]
    ws_connected = bool(ws_states) and all(bool(ws and ws.get("available")) for ws in ws_states)
    ws_required = any(ws is not None for ws in ws_states)
    rest_fallback_active = bool(ws_states) and all(
        ws is None or (not ws.get("available") and int(ws.get("rest_fallback_reads") or 0) > 0)
        for ws in ws_states)
    worker_heartbeats = [_age_seconds((row.get("engine") or {}).get("last_heartbeat")) for row in desired]
    heartbeat_ok = bool(worker_heartbeats) and all(age is not None and age < 180 for age in worker_heartbeats)
    checks = [
        _check("API", api_ok, "FastAPI responding"),
        _check("Database", db_ok, db_detail or ("SQLite reachable" if db_ok else "unreachable")),
        _check("Historical cache coverage",
               fresh["with_data"] > 0,
               (f"{fresh['with_data']}/{fresh['datasets']} datasets cached"
                + (f", freshest {fresh['freshest_age_s']}s old" if fresh["freshest_age_s"] is not None else ""))
               if fresh["with_data"] else "No real candles cached — run /data/sync",
               level="warn"),
        _check("Strategy errors", strategy_errors == 0, f"{strategy_errors} in recent logs", level="warn"),
        _check("Order errors", order_errors == 0, f"{order_errors} in recent logs", level="warn"),
        _check("Active feed freshness", active_feed_ok or not instance_mode,
               "all desired instance feeds healthy" if active_feed_ok else
               ("legacy engine readiness applies" if not instance_mode else
                "no desired paper instances" if not desired else "one or more active feeds are not fresh"),
               level="warn"),
        _check("WebSocket connected", ws_connected or rest_fallback_active or not ws_required,
               "connected" if ws_connected else "REST forward fallback active" if (rest_fallback_active or not ws_required) else "disconnected",
               level="warn"),
        _check("Worker heartbeat", heartbeat_ok or not instance_mode,
               "all desired worker heartbeats current" if heartbeat_ok else "no current desired worker heartbeat",
               level="warn"),
        _check("Execution readiness", instance_engine_ready or (not desired and engine_running),
               "ready and waiting for a valid signal" if instance_engine_ready else
               "legacy engine running" if engine_running else "no ready execution worker", level="warn"),
    ]
    down = [c for c in checks if c["level"] == "down"]
    warn = [c for c in checks if c["level"] == "warn" and not c["ok"]]
    overall = "down" if down else "degraded" if warn else "healthy"
    return {
        "status": overall, "checks": checks,
        "memory_mb": mem, "uptime_s": round(uptime_s) if uptime_s else None,
        "data_freshness": fresh,
        "active_instances": len(desired),
        "execution_state": "waiting_for_signal" if instance_engine_ready else "not_ready",
        "summary": f"{sum(1 for c in checks if c['ok'])}/{len(checks)} checks passing",
    }
