"""Durable execution-intent state for external broker adapters.

This store deliberately contains no exchange logic.  It is the crash-safe record
that lets an adapter distinguish a new intent from a retry and resume protection
after a process restart without submitting the entry twice.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


ORDER_STATES = {
    "INTENT",
    "ENTRY_ACCEPTED",
    "FILLED",
    "PROTECTION_ACCEPTED",
    "CLOSED",
    "CANCELLED",
    "HALTED_UNPROTECTED",
}

_ALLOWED_TRANSITIONS = {
    "INTENT": {"ENTRY_ACCEPTED", "CANCELLED"},
    "ENTRY_ACCEPTED": {"FILLED", "CANCELLED"},
    "FILLED": {"PROTECTION_ACCEPTED", "HALTED_UNPROTECTED"},
    "PROTECTION_ACCEPTED": {"CLOSED", "HALTED_UNPROTECTED"},
    "HALTED_UNPROTECTED": {"CLOSED", "PROTECTION_ACCEPTED"},
    "CLOSED": set(),
    "CANCELLED": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderStateStore:
    """SQLite-backed state machine for one broker process.

    A file path is mandatory for production construction. ``:memory:`` remains
    available only for isolated unit tests.
    """

    def __init__(self, path: str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS execution_orders (
                    client_id TEXT PRIMARY KEY,
                    entry_order_id TEXT UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    requested_qty REAL NOT NULL,
                    filled_qty REAL NOT NULL DEFAULT 0,
                    limit_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    state TEXT NOT NULL,
                    stop_order_id TEXT,
                    target_order_id TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_orders_state
                    ON execution_orders(state, updated_at);
                """
            )
            self._db.commit()

    def create_intent(self, *, client_id: str, symbol: str, side: str,
                      requested_qty: float, limit_price: Optional[float],
                      stop_loss: Optional[float], take_profit: Optional[float]) -> tuple[dict, bool]:
        """Create an INTENT once; return ``(row, created)`` atomically."""
        now = _now()
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO execution_orders"
                    "(client_id,symbol,side,requested_qty,limit_price,stop_loss,take_profit,state,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,'INTENT',?,?)",
                    (client_id, symbol, side, requested_qty, limit_price,
                     stop_loss, take_profit, now, now),
                )
                self._db.commit()
                created = True
            except sqlite3.IntegrityError:
                self._db.rollback()
                created = False
            return self.by_client_id(client_id), created

    def by_client_id(self, client_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM execution_orders WHERE client_id=?", (client_id,)
            ).fetchone()
        return dict(row) if row else None

    def by_entry_id(self, entry_order_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM execution_orders WHERE entry_order_id=?", (entry_order_id,)
            ).fetchone()
        return dict(row) if row else None

    def transition(self, client_id: str, state: str, **fields) -> dict:
        if state not in ORDER_STATES:
            raise ValueError(f"unknown execution order state {state}")
        allowed_fields = {
            "entry_order_id", "filled_qty", "stop_order_id", "target_order_id", "last_error",
        }
        unexpected = set(fields) - allowed_fields
        if unexpected:
            raise ValueError(f"unsupported execution state fields: {sorted(unexpected)}")
        with self._lock:
            row = self.by_client_id(client_id)
            if row is None:
                raise KeyError(f"unknown execution intent {client_id}")
            current = row["state"]
            if state != current and state not in _ALLOWED_TRANSITIONS[current]:
                raise ValueError(f"invalid execution transition {current} -> {state}")
            assignments = ["state=?", "updated_at=?"]
            values = [state, _now()]
            for key, value in fields.items():
                assignments.append(f"{key}=?")
                values.append(value)
            values.append(client_id)
            self._db.execute(
                f"UPDATE execution_orders SET {','.join(assignments)} WHERE client_id=?",
                values,
            )
            self._db.commit()
            return self.by_client_id(client_id)

    def open_orders(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM execution_orders WHERE state NOT IN ('CLOSED','CANCELLED') "
                "ORDER BY created_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
