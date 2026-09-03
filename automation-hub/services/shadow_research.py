"""Physically partitioned PA/SMC shadow research persistence.

The store deliberately defines no account, margin, capacity, or real-position
table.  Every mutating API requires ``execution_class == SHADOW``.  This makes
shadow isolation structural rather than a boolean branch in a paper broker.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from data.sqlite_runtime import runtime_connection


EXECUTION_CLASS = "SHADOW"
BLOCKERS = {
    "SETUP_FOUND", "NO_SETUP", "SIGNALS_ONLY", "GATE_REJECTED",
    "MARKET_DATA_STALE", "INSTANCE_CAPACITY", "INSTANCE_PAUSED",
    "PA_UNARMED", "SMC_CONDITION_MISSING", "HTF_MISALIGNED",
    "ZONE_NOT_FRESH", "RECLAIM_FAILED", "RR_TOO_LOW", "RISK_TOO_HIGH",
    "LIMIT_EXPIRED", "NONE", "OUT_OF_ORDER_QUOTE",
}


def iso(value: datetime | str | None = None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def key(*parts: object) -> str:
    material = "|".join(str(part or "") for part in parts)
    return f"{material}|{hashlib.sha256(material.encode()).hexdigest()[:20]}"


def canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))


class ShadowResearchStore:
    """Additive immutable decision/order/fill/outcome ledger for experiments."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self._db = runtime_connection(self.path)
        self._lock = threading.RLock()
        self._schema()

    def _schema(self) -> None:
        with self._lock, self._db:
            self._db.executescript("""
              CREATE TABLE IF NOT EXISTS shadow_decisions(
                decision_id TEXT PRIMARY KEY,
                decision_key TEXT NOT NULL UNIQUE,
                engine TEXT NOT NULL,
                account_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                candle_id TEXT NOT NULL,
                action_class TEXT NOT NULL,
                direction TEXT,
                execution_class TEXT NOT NULL CHECK(execution_class='SHADOW'),
                blocker TEXT NOT NULL,
                decision_timestamp TEXT NOT NULL,
                snapshot_lineage TEXT NOT NULL,
                context_json TEXT NOT NULL,
                created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS shadow_orders(
                order_id TEXT PRIMARY KEY,
                order_key TEXT NOT NULL UNIQUE,
                decision_id TEXT NOT NULL,
                order_type TEXT NOT NULL,
                side TEXT NOT NULL,
                requested_price REAL,
                stop_loss REAL,
                take_profit REAL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(decision_id) REFERENCES shadow_decisions(decision_id));
              CREATE TABLE IF NOT EXISTS shadow_fills(
                fill_id TEXT PRIMARY KEY,
                fill_key TEXT NOT NULL UNIQUE,
                order_id TEXT NOT NULL,
                quote_event_id TEXT NOT NULL,
                event_timestamp TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                executable_side_price REAL NOT NULL,
                fill_price REAL NOT NULL,
                quantity REAL NOT NULL,
                spread_attribution REAL NOT NULL,
                spread_charged_again REAL NOT NULL DEFAULT 0,
                slippage REAL NOT NULL,
                commission REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES shadow_orders(order_id));
              CREATE TABLE IF NOT EXISTS shadow_outcomes(
                outcome_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL UNIQUE,
                exit_reason TEXT NOT NULL,
                exit_price REAL NOT NULL,
                gross_pnl REAL NOT NULL,
                commission REAL NOT NULL,
                slippage REAL NOT NULL,
                funding REAL NOT NULL,
                net_pnl REAL NOT NULL,
                gross_r REAL NOT NULL,
                net_r REAL NOT NULL,
                closed_at TEXT NOT NULL,
                validation_state TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES shadow_orders(order_id));
              CREATE TABLE IF NOT EXISTS shadow_mae_mfe(
                order_id TEXT PRIMARY KEY,
                mae_price REAL NOT NULL DEFAULT 0,
                mfe_price REAL NOT NULL DEFAULT 0,
                mae_pct REAL NOT NULL DEFAULT 0,
                mfe_pct REAL NOT NULL DEFAULT 0,
                mae_r REAL NOT NULL DEFAULT 0,
                mfe_r REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES shadow_orders(order_id));
              CREATE TABLE IF NOT EXISTS shadow_funding(
                funding_key TEXT PRIMARY KEY,
                account_id TEXT NOT NULL,
                position_id TEXT NOT NULL,
                funding_timestamp TEXT NOT NULL,
                amount REAL NOT NULL,
                created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS shadow_quote_cursors(
                symbol TEXT PRIMARY KEY,
                event_timestamp TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                quote_event_id TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS ix_shadow_decisions_strategy
                ON shadow_decisions(strategy_id,strategy_version,config_hash);
              CREATE INDEX IF NOT EXISTS ix_shadow_decisions_candle
                ON shadow_decisions(candle_id,engine);
            """)

    @staticmethod
    def _shadow(execution_class: str) -> None:
        if execution_class != EXECUTION_CLASS:
            raise ValueError("shadow store accepts execution_class=SHADOW only")

    def record_decision(self, *, engine: str, account_id: str,
                        strategy_id: str, strategy_version: str,
                        config_hash: str, candle_id: str, action_class: str,
                        direction: str | None, blocker: str,
                        decision_timestamp: datetime | str,
                        snapshot_lineage: str, context: dict,
                        execution_class: str = EXECUTION_CLASS) -> dict:
        self._shadow(execution_class)
        if blocker not in BLOCKERS:
            raise ValueError(f"unknown research blocker '{blocker}'")
        required = (engine, account_id, strategy_id, strategy_version,
                    config_hash, candle_id, action_class, snapshot_lineage)
        if not all(str(value).strip() for value in required):
            raise ValueError("complete shadow decision provenance is required")
        decision_key = key(engine, account_id, strategy_id, strategy_version,
                           candle_id, action_class)
        decision_id = "shadow-decision-" + hashlib.sha256(decision_key.encode()).hexdigest()[:28]
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO shadow_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (decision_id, decision_key, engine, account_id, strategy_id,
                 strategy_version, config_hash, candle_id, action_class,
                 direction, EXECUTION_CLASS, blocker, iso(decision_timestamp),
                 snapshot_lineage, canonical(context), iso()),
            )
            row = self._db.execute(
                "SELECT * FROM shadow_decisions WHERE decision_key=?", (decision_key,)
            ).fetchone()
        return self._decode(row)

    def record_order(self, decision_id: str, *, order_type: str, side: str,
                     requested_price: float | None, stop_loss: float | None,
                     take_profit: float | None, quantity: float = 1,
                     status: str = "INTENT",
                     execution_class: str = EXECUTION_CLASS) -> dict:
        self._shadow(execution_class)
        if quantity <= 0:
            raise ValueError("shadow quantity must be positive")
        with self._lock, self._db:
            decision = self._db.execute(
                "SELECT * FROM shadow_decisions WHERE decision_id=?", (decision_id,)
            ).fetchone()
            if not decision:
                raise KeyError(decision_id)
            order_key = key(decision["decision_key"], order_type, side)
            order_id = "shadow-order-" + hashlib.sha256(order_key.encode()).hexdigest()[:28]
            self._db.execute(
                "INSERT OR IGNORE INTO shadow_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (order_id, order_key, decision_id, order_type, side,
                 requested_price, stop_loss, take_profit, float(quantity), status, iso()),
            )
            row = self._db.execute(
                "SELECT * FROM shadow_orders WHERE order_key=?", (order_key,)
            ).fetchone()
        return self._decode(row)

    def accept_quote(self, symbol: str, quote: dict) -> tuple[bool, str]:
        event_at = iso(quote.get("event_timestamp") or quote.get("received_at"))
        sequence = int(quote.get("sequence", 0))
        quote_id = str(quote.get("quote_event_id") or key(
            symbol, event_at, sequence, quote.get("bid"), quote.get("ask"), quote.get("mark")
        ))
        normalized = symbol.upper().replace("/", "")
        with self._lock, self._db:
            prior = self._db.execute(
                "SELECT * FROM shadow_quote_cursors WHERE symbol=?", (normalized,)
            ).fetchone()
            if prior:
                prior_at = iso(prior["event_timestamp"])
                if event_at < prior_at or (event_at == prior_at and sequence <= int(prior["sequence"])):
                    return False, "OUT_OF_ORDER_QUOTE"
            self._db.execute(
                "INSERT INTO shadow_quote_cursors VALUES (?,?,?,?) "
                "ON CONFLICT(symbol) DO UPDATE SET event_timestamp=excluded.event_timestamp,"
                "sequence=excluded.sequence,quote_event_id=excluded.quote_event_id",
                (normalized, event_at, sequence, quote_id),
            )
        return True, quote_id

    def record_fill(self, order_id: str, quote: dict, *, slippage_bps: float,
                    commission_bps: float,
                    execution_class: str = EXECUTION_CLASS) -> dict | None:
        self._shadow(execution_class)
        try:
            bid, ask = float(quote["bid"]), float(quote["ask"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("shadow fill requires public bid and ask") from exc
        if bid <= 0 or ask < bid:
            raise ValueError("shadow bid/ask is invalid")
        with self._lock:
            row = self._db.execute(
                "SELECT o.*,d.decision_timestamp FROM shadow_orders o "
                "JOIN shadow_decisions d ON d.decision_id=o.decision_id WHERE o.order_id=?",
                (order_id,),
            ).fetchone()
        if not row:
            raise KeyError(order_id)
        event_at = iso(quote.get("event_timestamp") or quote.get("received_at"))
        if event_at <= iso(row["decision_timestamp"]):
            return None
        accepted, quote_id = self.accept_quote(str(quote.get("symbol") or "UNKNOWN"), quote)
        if not accepted:
            return None
        fill_key = key(order_id, quote_id)
        side = str(row["side"]).lower()
        executable = ask if side in {"buy", "long"} else bid
        slip = executable * max(0.0, float(slippage_bps)) / 10_000
        fill_price = executable + slip if side in {"buy", "long"} else executable - slip
        quantity = float(row["quantity"])
        commission = fill_price * quantity * max(0.0, float(commission_bps)) / 10_000
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO shadow_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("shadow-fill-" + uuid.uuid4().hex, fill_key, order_id, quote_id,
                 event_at, int(quote.get("sequence", 0)), executable, fill_price,
                 quantity, ask - bid, 0.0, slip * quantity, commission, iso()),
            )
            self._db.execute(
                "UPDATE shadow_orders SET status='FILLED' WHERE order_id=?", (order_id,)
            )
            fill = self._db.execute(
                "SELECT * FROM shadow_fills WHERE fill_key=?", (fill_key,)
            ).fetchone()
        return self._decode(fill)

    def record_funding(self, *, account_id: str, position_id: str,
                       funding_timestamp: datetime | str, amount: float,
                       execution_class: str = EXECUTION_CLASS) -> dict:
        self._shadow(execution_class)
        stamp = iso(funding_timestamp)
        funding_key = key(account_id, position_id, stamp)
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO shadow_funding VALUES (?,?,?,?,?,?)",
                (funding_key, account_id, position_id, stamp, float(amount), iso()),
            )
            row = self._db.execute(
                "SELECT * FROM shadow_funding WHERE funding_key=?", (funding_key,)
            ).fetchone()
        return self._decode(row)

    @staticmethod
    def _decode(row) -> dict:
        value = dict(row)
        if "context_json" in value:
            value["context"] = json.loads(value.pop("context_json"))
        return value

    def table_counts(self) -> dict[str, int]:
        names = ("shadow_decisions", "shadow_orders", "shadow_fills",
                 "shadow_outcomes", "shadow_mae_mfe", "shadow_funding")
        with self._lock:
            return {name: int(self._db.execute(
                f"SELECT COUNT(*) FROM {name}"  # trusted constant table names
            ).fetchone()[0]) for name in names}

    def decisions(self, *, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM shadow_decisions ORDER BY created_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [self._decode(row) for row in rows]

