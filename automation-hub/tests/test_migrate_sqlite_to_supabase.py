import importlib.util
import sqlite3
from pathlib import Path


_SCRIPT = Path(__file__).parents[1] / "scripts" / "migrate_sqlite_to_supabase.py"
_SPEC = importlib.util.spec_from_file_location("sqlite_to_supabase", _SCRIPT)
assert _SPEC and _SPEC.loader
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


def _make_ledger(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE paper_trades (
            id TEXT PRIMARY KEY, alert_id TEXT, symbol TEXT, side TEXT, size REAL,
            entry REAL, stop REAL, exit REAL, pnl REAL, rr REAL, status TEXT,
            source TEXT, opened_at TEXT, closed_at TEXT, tenant_id TEXT, strategy_id TEXT
        )""")
        db.execute("""INSERT INTO paper_trades VALUES
            ('trade-1', NULL, 'BTCUSDT', 'long', 1, 100, 90, 110, 10, 1,
             'closed', 'paper', '2026-01-01', '2026-01-02', 'owner', 'brain')""")


def _make_hub(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE user_settings (
            username TEXT, namespace TEXT, data TEXT, updated_at TEXT,
            PRIMARY KEY (username, namespace)
        )""")
        db.execute("INSERT INTO user_settings VALUES ('owner', 'dashboard', '{}', '2026-01-01')")


def test_collects_only_portable_columns_from_additive_sqlite_schema(tmp_path):
    ledger, hub = tmp_path / "ledger.db", tmp_path / "hub.db"
    _make_ledger(ledger)
    _make_hub(hub)

    rows = migration.collect_local_rows(ledger, hub)

    assert rows["paper_trades"] == [{
        "id": "trade-1", "alert_id": None, "symbol": "BTCUSDT", "side": "long",
        "size": 1.0, "entry": 100.0, "stop": 90.0, "exit": 110.0, "pnl": 10.0,
        "rr": 1.0, "status": "closed", "source": "paper", "opened_at": "2026-01-01",
        "closed_at": "2026-01-02",
    }]
    assert rows["user_settings"] == [{
        "username": "owner", "namespace": "dashboard", "data": "{}", "updated_at": "2026-01-01",
    }]
    assert rows["positions"] == []


def test_apply_rows_is_batched_and_uses_idempotent_conflicts():
    calls = []

    class Table:
        def __init__(self, name): self.name = name
        def upsert(self, rows, on_conflict):
            calls.append((self.name, rows, on_conflict))
            return self
        def execute(self): return self

    class Client:
        def table(self, name): return Table(name)

    written = migration.apply_rows(Client(), {
        "paper_trades": [{"id": "a"}, {"id": "b"}, {"id": "c"}],
        "user_settings": [{"username": "owner", "namespace": "dashboard"}],
    }, batch_size=2)

    assert written == {"paper_trades": 3, "user_settings": 1}
    assert [call[2] for call in calls] == ["id", "id", "username,namespace"]


def test_legacy_paper_trade_without_source_is_marked_paper(tmp_path):
    ledger = tmp_path / "ledger.db"
    with sqlite3.connect(ledger) as db:
        db.execute("""CREATE TABLE paper_trades (
            id TEXT, alert_id TEXT, symbol TEXT, side TEXT, size REAL, entry REAL,
            stop REAL, exit REAL, pnl REAL, rr REAL, status TEXT,
            opened_at TEXT, closed_at TEXT
        )""")
        db.execute("INSERT INTO paper_trades VALUES ('legacy', NULL, 'ETHUSDT', 'long', 1, 1, NULL, NULL, NULL, NULL, 'open', '2026-01-01', NULL)")

    rows = migration.collect_local_rows(ledger, tmp_path / "missing-hub.db")

    assert rows["paper_trades"][0]["source"] == "paper"
