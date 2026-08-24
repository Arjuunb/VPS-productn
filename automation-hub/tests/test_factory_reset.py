from __future__ import annotations

import sqlite3
from pathlib import Path
import re
from types import SimpleNamespace

import pytest

from data.ledger import SqliteLedger
from data.market_data_v2 import MarketDataService
from database.store import SqliteStore
from execution.paper_broker_v2 import PaperBrokerV2
from services.factory_reset import CONFIRMATION_PHRASE, FactoryResetService


class _AuditLedger:
    def __init__(self, events: list[str]):
        self.events = events
        self.audit = {}

    def begin_factory_reset_audit(self, row):
        self.events.append("audit:requested")
        self.audit = {**row, "status": "requested"}

    def factory_reset_application_data(self, reset_id, confirmation):
        assert reset_id == self.audit["id"]
        assert confirmation == CONFIRMATION_PHRASE
        self.events.append("database:cleared")

    def finish_factory_reset_audit(self, reset_id, *, status, duration_ms, error=""):
        assert reset_id == self.audit["id"]
        self.audit.update(status=status, duration_ms=duration_ms, error=error)
        self.events.append(f"audit:{status}")


class _AppStore:
    def __init__(self, events: list[str]):
        self.events = events

    def clear_application_data(self):
        self.events.append("app-store:cleared")


class _Service(FactoryResetService):
    def __init__(self, events, *, fail_at=""):
        self.events = events
        self.fail_at = fail_at
        runtime = SimpleNamespace(
            ledger=_AuditLedger(events),
            controls=SimpleNamespace(stop_all=lambda: events.append("controls:stopped")),
        )
        super().__init__(runtime, _AppStore(events), SimpleNamespace())

    def _stop_workers(self):
        self.events.append("workers:stopped")
        if self.fail_at == "stop":
            raise RuntimeError("worker would not stop")
        return {"trading_instances": 1}

    def _clear_local_state(self):
        self.events.append("local:cleared")
        if self.fail_at == "local":
            raise RuntimeError("local cleanup failed")
        return ["operational state"]

    def _reinitialize_and_check(self):
        self.events.append("health:checked")
        if self.fail_at == "health":
            raise RuntimeError("Binance health failed")
        return {"application": "healthy", "trading": "stopped"}


@pytest.mark.parametrize("phrase,final", [
    ("", True), ("factory reset", True), (CONFIRMATION_PHRASE, False),
])
def test_confirmation_is_enforced_server_side(phrase, final):
    service = _Service([])
    with pytest.raises(ValueError):
        service.run(initiated_by="owner@example.com", confirmation=phrase,
                    final_confirmation=final)


def test_factory_reset_orders_audit_stop_delete_reinitialize_and_never_enables_live():
    events: list[str] = []
    service = _Service(events)
    result = service.run(
        initiated_by="owner@example.com", confirmation=CONFIRMATION_PHRASE,
        final_confirmation=True)
    assert events == [
        "audit:requested", "workers:stopped", "database:cleared",
        "app-store:cleared", "local:cleared", "health:checked",
        "audit:succeeded",
    ]
    assert result["ok"] is True
    assert result["execution_mode"] == "paper"
    assert result["live_enabled"] is False


@pytest.mark.parametrize("phase", ["stop", "local", "health"])
def test_failure_is_audited_and_left_stopped(phase):
    events: list[str] = []
    service = _Service(events, fail_at=phase)
    with pytest.raises(RuntimeError):
        service.run(initiated_by="owner", confirmation=CONFIRMATION_PHRASE,
                    final_confirmation=True)
    assert "controls:stopped" in events
    assert service.last_result["safe_state"] == "stopped/degraded"
    assert service.runtime.ledger.audit["status"] == "failed"


def test_sqlite_ledger_reset_clears_operational_rows_but_preserves_audit_and_schema(tmp_path):
    ledger = SqliteLedger(tmp_path / "ledger.db")
    with ledger._lock:
        ledger._c.executescript("""
          CREATE TABLE trading_instances(id TEXT PRIMARY KEY);
          CREATE TABLE simulation_sessions(id TEXT PRIMARY KEY);
          CREATE TABLE trading_instance_platform_settings(id TEXT PRIMARY KEY);
          INSERT INTO trading_instances VALUES ('i1');
          INSERT INTO simulation_sessions VALUES ('s1');
          INSERT INTO trading_instance_platform_settings VALUES ('platform');
        """)
        ledger._c.commit()
    reset_id = "reset-1"
    ledger.begin_factory_reset_audit({
        "id": reset_id, "requested_at": "2026-01-01T00:00:00+00:00",
        "initiated_by": "owner", "reset_version": "factory-reset-v1",
        "preserved_scope": {"authentication": "preserved"},
    })
    ledger.record_paper_trade({"symbol": "BTCUSDT", "side": "buy", "size": 1, "entry": 1})
    ledger.factory_reset_application_data(reset_id, CONFIRMATION_PHRASE)
    ledger.finish_factory_reset_audit(reset_id, status="succeeded", duration_ms=1)
    with ledger._lock:
        for table in ("paper_trades", "trading_instances", "simulation_sessions",
                      "trading_instance_platform_settings"):
            assert ledger._c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            assert ledger._c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
        audit = ledger._c.execute(
            "SELECT status FROM factory_reset_audit WHERE id=?", (reset_id,)
        ).fetchone()
        assert audit["status"] == "succeeded"
    ledger.close()
    reopened = SqliteLedger(tmp_path / "ledger.db")
    with reopened._lock:
        assert reopened._c.execute("SELECT COUNT(*) FROM trading_instances").fetchone()[0] == 0
        assert reopened._c.execute(
            "SELECT status FROM factory_reset_audit WHERE id=?", (reset_id,)
        ).fetchone()["status"] == "succeeded"


def test_identity_store_reset_preserves_login_and_auth_schema(tmp_path):
    store = SqliteStore(tmp_path / "hub.db")
    store.create_user("owner@example.com", "strong-password", role="owner")
    with store._lock:
        store._conn.execute(
            "INSERT INTO user_settings(username,namespace,data,updated_at) VALUES (?,?,?,?)",
            ("owner@example.com", "dashboard", "{}", "now"))
        store._conn.commit()
    store.clear_application_data()
    assert store.authenticate("owner@example.com", "strong-password") is not None
    with store._lock:
        assert store._conn.execute("SELECT COUNT(*) FROM user_settings").fetchone()[0] == 0
        for table in ("users", "auth_tokens", "totp_recovery", "oauth_identities", "_migrations"):
            assert store._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()


def test_paper_broker_factory_reset_is_fresh_and_persistent(tmp_path):
    path = tmp_path / "paper-v2.db"
    broker = PaperBrokerV2(path, starting_balance=1_000)
    broker.submit(symbol="BTCUSDT", side="buy", order_type="limit", quantity=0.001,
                  limit_price=50_000)
    broker.factory_reset(2_500)
    assert broker.orders() == []
    assert broker.positions() == []
    assert broker.fills() == []
    assert broker.account()["balance"] == 2_500
    broker._c.close()
    reopened = PaperBrokerV2(path, starting_balance=999)
    assert reopened.account()["starting_balance"] == 2_500
    assert reopened.account()["equity"] == 2_500


def test_market_cache_cleanup_is_scoped_and_binance_health_is_real(tmp_path):
    payload = {"symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT",
         "contractType": "PERPETUAL"},
    ]}
    service = MarketDataService(tmp_path / "market-data",
                                request_json=lambda _url, _params: payload)
    cache = service.root / "crypto" / "BTCUSDT.sqlite3"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"cache")
    preserved = service.root / "README.keep"
    preserved.write_text("not a cache database")
    assert service.clear_cache() == 1
    assert not cache.exists()
    assert preserved.exists()
    health = service.verify_binance_usdm()
    assert health == {"connected": True, "provider": "Binance USD-M Futures",
                      "active_usdt_perpetuals": 1}


def test_supabase_factory_reset_sql_is_allowlisted_and_preserves_identity():
    root = Path(__file__).resolve().parents[2]
    sql = (root / "supabase/migrations/0004_factory_reset.sql").read_text().lower()
    assert "factory_reset_application_data" in sql
    assert "factory_reset_audit" in sql
    assert "delete from public.trading_instances" in sql
    assert "delete from public.user_settings" in sql
    assert "delete from public.factory_reset_audit" not in sql
    assert "delete from auth.users" not in sql
    assert "drop table" not in sql
    assert "truncate" not in sql
    assert "live" not in sql
    deletes = re.findall(r"delete\s+from\s+public\.[a-z_]+[^;]*;", sql)
    assert len(deletes) == 15
    assert all(" where true" in statement for statement in deletes)
