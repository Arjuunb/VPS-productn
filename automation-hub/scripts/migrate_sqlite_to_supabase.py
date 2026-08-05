#!/usr/bin/env python3
"""Safely copy the durable local TradeLogX ledger into Supabase.

The script is deliberately opt-in: without ``--apply`` it only reports the
rows it would copy.  Writes use idempotent UPSERTs, so an interrupted run can
be run again without duplicating trade history.  It never deletes local or
remote data.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Iterable


LEDGER_TABLES: dict[str, tuple[str, ...]] = {
    "webhook_events": (
        "id", "alert_id", "symbol", "side", "entry", "stop", "payload_json",
        "received_at", "status", "reason",
    ),
    "positions": (
        "id", "symbol", "side", "size", "entry", "stop", "status", "pnl",
        "opened_at", "closed_at",
    ),
    "paper_trades": (
        "id", "alert_id", "symbol", "side", "size", "entry", "stop", "exit",
        "pnl", "rr", "status", "source", "opened_at", "closed_at",
    ),
    "bot_logs": ("id", "ts", "symbol", "level", "stage", "message"),
    "alerts": ("id", "ts", "severity", "category", "title", "detail", "read"),
}
SETTINGS_COLUMNS = ("username", "namespace", "data", "updated_at")
# ``source`` was added after the earliest local paper-ledger deployments.  The
# old rows were all paper trades, so this faithful default permits a safe
# one-time upgrade rather than rejecting otherwise valid historical records.
OPTIONAL_COLUMN_DEFAULTS = {("paper_trades", "source"): "paper"}


def _chunks(rows: list[dict], size: int) -> Iterable[list[dict]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def _read_table(path: Path, table: str, columns: tuple[str, ...]) -> list[dict]:
    """Read only known columns from one local SQLite table.

    Newer local schemas have additive columns such as ``tenant_id`` and
    ``strategy_id``.  Selecting an explicit portable column list keeps this
    migration compatible with the Supabase ledger schema.
    """
    if not path.exists():
        return []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return []
        available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        missing = set(columns) - available - {
            column for candidate_table, column in OPTIONAL_COLUMN_DEFAULTS
            if candidate_table == table
        }
        if missing:
            raise RuntimeError(f"{path.name}:{table} is missing expected columns: {sorted(missing)}")
        selected, params = [], []
        for column in columns:
            if column in available:
                selected.append(column)
            else:
                selected.append(f"? AS {column}")
                params.append(OPTIONAL_COLUMN_DEFAULTS[(table, column)])
        rows = connection.execute(
            f"SELECT {','.join(selected)} FROM {table} ORDER BY rowid", params
        ).fetchall()
    return [dict(row) for row in rows]


def collect_local_rows(ledger_path: Path, hub_path: Path) -> dict[str, list[dict]]:
    rows = {
        table: _read_table(ledger_path, table, columns)
        for table, columns in LEDGER_TABLES.items()
    }
    rows["user_settings"] = _read_table(hub_path, "user_settings", SETTINGS_COLUMNS)
    return rows


def _upsert(client: object, table: str, rows: list[dict], conflict_columns: str, batch_size: int) -> int:
    """Write rows in bounded idempotent batches without printing user data."""
    written = 0
    for batch in _chunks(rows, batch_size):
        client.table(table).upsert(batch, on_conflict=conflict_columns).execute()
        written += len(batch)
    return written


def apply_rows(client: object, rows_by_table: dict[str, list[dict]], batch_size: int = 100) -> dict[str, int]:
    written: dict[str, int] = {}
    for table, rows in rows_by_table.items():
        conflict = "username,namespace" if table == "user_settings" else "id"
        written[table] = _upsert(client, table, rows, conflict, batch_size) if rows else 0
    return written


def _default_path(env_name: str, filename: str) -> Path:
    data_dir = Path(os.environ.get("HUB_DATA_DIR", "/var/lib/tradexa"))
    return Path(os.environ.get(env_name, data_dir / filename))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="perform idempotent Supabase UPSERTs")
    parser.add_argument("--ledger-path", type=Path, default=_default_path("HUB_LEDGER_PATH", "ledger.db"))
    parser.add_argument("--hub-path", type=Path, default=_default_path("HUB_DB_PATH", "hub.db"))
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()

    if args.batch_size < 1 or args.batch_size > 500:
        parser.error("--batch-size must be between 1 and 500")
    rows_by_table = collect_local_rows(args.ledger_path, args.hub_path)
    print("Local SQLite migration inventory (no row contents are printed):")
    for table, rows in rows_by_table.items():
        print(f"  {table}: {len(rows)} row(s)")

    if not args.apply:
        print("Dry run only. Re-run with --apply after the Supabase SQL is applied.")
        return 0

    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        print("SUPABASE_URL and SUPABASE_KEY must be configured; no data was written.", file=sys.stderr)
        return 2
    try:
        from supabase import create_client
        written = apply_rows(create_client(url, key), rows_by_table, args.batch_size)
    except Exception as exc:  # noqa: BLE001 - keep the local data untouched on every remote failure
        print(f"Supabase migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for table, count in written.items():
        print(f"  migrated {table}: {count} row(s)")
    print("Migration completed. Local SQLite data was not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
