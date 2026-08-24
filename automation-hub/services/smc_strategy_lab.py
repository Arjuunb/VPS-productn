"""Durable, isolated paper account for the SMC Strategy Lab.

The account uses its own SQLite file and owns no exchange client.  Public
market data may advance the local paper broker, but every response keeps real
execution hard-disabled.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from data.market_data_v2 import TF_MS, normalize_symbol
from execution.paper_broker_v2 import OPEN_STATUSES, PaperBrokerV2
from services.smc_strategy_v1 import ENTRY_MODELS, STRATEGY_ID, STRATEGY_VERSION


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


OPERATING_MODES = {"signals_only", "manual_approval", "automatic"}
SESSION_MODES = {"LIVE_PAPER", "HISTORICAL"}
ACTIVE_MODEL_IDS = {row.id for row in ENTRY_MODELS if row.status == "ACTIVE"}


@dataclass(frozen=True)
class SMCPaperConfig:
    operating_mode: Literal["signals_only", "manual_approval", "automatic"] = "signals_only"
    model_id: str = "SMC_M1_SWEEP_REVERSAL"
    risk_pct: float = 0.5
    max_risk_pct: float = 1.0
    max_concurrent_risk_pct: float = 2.0

    def validated(self) -> "SMCPaperConfig":
        if self.operating_mode not in OPERATING_MODES:
            raise ValueError("operating mode must be signals_only, manual_approval or automatic")
        if self.model_id not in ACTIVE_MODEL_IDS:
            raise ValueError("the selected SMC entry model is parked or unknown")
        if not 0 < self.risk_pct <= self.max_risk_pct <= 1.0:
            raise ValueError("SMC risk per trade must be above 0% and no greater than 1%")
        if self.max_concurrent_risk_pct < self.risk_pct or self.max_concurrent_risk_pct > 2.0:
            raise ValueError("maximum concurrent SMC risk must be between risk per trade and 2%")
        return self


class SMCPaperAccount:
    """SMC-only account, session, ownership and audit ledger."""

    def __init__(self, path: str | Path, *, starting_balance: float = 10_000.0):
        self.path = str(path)
        self.starting_balance = float(starting_balance)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.broker = PaperBrokerV2(self.path, starting_balance=self.starting_balance)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript("""
              CREATE TABLE IF NOT EXISTS smc_sessions(
                id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT,
                starting_balance REAL NOT NULL, status TEXT NOT NULL, end_reason TEXT,
                mode TEXT NOT NULL DEFAULT 'LIVE_PAPER', symbol TEXT NOT NULL DEFAULT 'BTCUSDT',
                timeframe TEXT NOT NULL DEFAULT '5m', replay_cursor INTEGER NOT NULL DEFAULT 0,
                operating_mode TEXT NOT NULL DEFAULT 'signals_only', model_id TEXT NOT NULL DEFAULT 'SMC_M1_SWEEP_REVERSAL',
                risk_pct REAL NOT NULL DEFAULT .5, state_json TEXT NOT NULL DEFAULT '{}',
                metrics_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS smc_settings(
                id INTEGER PRIMARY KEY CHECK(id=1), leverage REAL NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS smc_activity(
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL, kind TEXT NOT NULL,
                symbol TEXT, model_id TEXT, object_id TEXT, created_at TEXT NOT NULL,
                payload TEXT NOT NULL DEFAULT '{}');
              CREATE TABLE IF NOT EXISTS smc_order_meta(
                order_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, ownership TEXT NOT NULL,
                idempotency_key TEXT NOT NULL, proposal_id TEXT, setup_id TEXT, poi_id TEXT,
                model_id TEXT, model_version TEXT, direction TEXT NOT NULL,
                entry REAL, stop REAL, target_1 REAL, target_2 REAL, risk_pct REAL,
                creation_candle TEXT, expiry_candle TEXT, status TEXT NOT NULL,
                reason TEXT NOT NULL, config_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(session_id,idempotency_key));
              CREATE TABLE IF NOT EXISTS smc_candidates(
                proposal_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, setup_id TEXT,
                model_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS smc_funding_events(
                session_id TEXT NOT NULL, symbol TEXT NOT NULL, funding_time TEXT NOT NULL,
                rate REAL NOT NULL, mark_price REAL NOT NULL, amount REAL NOT NULL,
                applied INTEGER NOT NULL, PRIMARY KEY(session_id,symbol,funding_time));
              CREATE TABLE IF NOT EXISTS smc_processed_candles(
                session_id TEXT NOT NULL, symbol TEXT NOT NULL, candle_time TEXT NOT NULL,
                PRIMARY KEY(session_id,symbol,candle_time));
              CREATE TABLE IF NOT EXISTS smc_journal_revisions(
                id TEXT PRIMARY KEY, journal_id TEXT NOT NULL, session_id TEXT NOT NULL,
                note TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            self._db.execute("INSERT OR IGNORE INTO smc_settings(id,leverage) VALUES (1,1)")
            if not self._db.execute("SELECT 1 FROM smc_sessions WHERE status='active'").fetchone():
                self._insert_session(starting_balance=self.starting_balance)
            self.broker.leverage = float(self._db.execute(
                "SELECT leverage FROM smc_settings WHERE id=1").fetchone()[0])
            self._snapshot()

    def _insert_session(self, *, starting_balance: float, mode: str = "LIVE_PAPER",
                        symbol: str = "BTCUSDT", timeframe: str = "5m",
                        config: SMCPaperConfig | None = None) -> str:
        config = (config or SMCPaperConfig()).validated()
        sid, now = uuid.uuid4().hex, _iso()
        self._db.execute(
            "INSERT INTO smc_sessions(id,started_at,starting_balance,status,mode,symbol,timeframe,operating_mode,model_id,risk_pct,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, now, float(starting_balance), "active", mode, symbol, timeframe,
             config.operating_mode, config.model_id, config.risk_pct, now),
        )
        return sid

    def session(self) -> dict:
        row = self._db.execute(
            "SELECT * FROM smc_sessions WHERE status='active' ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else {}

    @staticmethod
    def _decoded(row: dict) -> dict:
        for key in ("state_json", "metrics_json"):
            row[key.removesuffix("_json")] = json.loads(row.pop(key, "{}") or "{}")
        return row

    def sessions(self) -> list[dict]:
        return [self._decoded(dict(row)) for row in self._db.execute(
            "SELECT * FROM smc_sessions ORDER BY started_at DESC")]

    def _audit(self, kind: str, *, object_id: str = "", payload: dict | None = None,
               session_id: str | None = None) -> None:
        current = self.session()
        sid = session_id or current.get("id")
        if not sid:
            return
        self._db.execute(
            "INSERT INTO smc_activity VALUES (?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, sid, kind, current.get("symbol", ""), current.get("model_id", ""),
             object_id, _iso(), json.dumps(payload or {}, sort_keys=True, default=str)),
        )

    def _snapshot(self, metrics: dict | None = None) -> None:
        current = self.session()
        if not current:
            return
        self._db.execute(
            "UPDATE smc_sessions SET state_json=?,metrics_json=COALESCE(?,metrics_json),updated_at=? WHERE id=?",
            (json.dumps(self.broker.export_state(), sort_keys=True),
             json.dumps(metrics, sort_keys=True, default=str) if metrics is not None else None,
             _iso(), current["id"]),
        )

    def state(self, marks: dict[str, float] | None = None) -> dict:
        current = self.session()
        sid = current.get("id", "")
        account = self.broker.account(marks)
        positions = self.broker.positions()
        open_risk = 0.0
        for position in positions:
            stop = position.get("stop_loss")
            if stop is not None:
                open_risk += abs(float(position["entry_price"]) - float(stop)) * float(position["size"])
        activity = [{**dict(row), "payload": json.loads(row["payload"])} for row in self._db.execute(
            "SELECT * FROM smc_activity WHERE session_id=? ORDER BY created_at DESC LIMIT 1000", (sid,))]
        metadata = [{**dict(row), "config": json.loads(row["config_json"])} for row in self._db.execute(
            "SELECT * FROM smc_order_meta WHERE session_id=? ORDER BY created_at DESC", (sid,))]
        candidates = [{**dict(row), "payload": json.loads(row["payload"])} for row in self._db.execute(
            "SELECT * FROM smc_candidates WHERE session_id=? ORDER BY created_at DESC", (sid,))]
        funding = [dict(row) for row in self._db.execute(
            "SELECT * FROM smc_funding_events WHERE session_id=? ORDER BY funding_time DESC", (sid,))]
        return {
            "research_id": STRATEGY_ID, "strategy_version": STRATEGY_VERSION,
            "account_scope": "SMC_STRATEGY_LAB_ONLY", "currency": "USDT",
            "paper_only": True, "execution_mode": "PAPER", "real_execution_allowed": False,
            "session": self._decoded(dict(current)) if current else {},
            "account": {**account, "available_margin": account["free_margin"], "open_risk": round(open_risk, 8)},
            "positions": positions, "orders": self.broker.orders(), "trades": self.broker.fills(limit=1000),
            "candidates": candidates, "order_metadata": metadata, "activity": activity,
            "funding_events": funding,
        }

    def configure(self, *, mode: str | None = None, symbol: str | None = None,
                  timeframe: str | None = None, replay_cursor: int | None = None,
                  config: SMCPaperConfig | None = None) -> dict:
        current = self.session()
        if not current:
            raise ValueError("no active SMC paper session")
        config = (config or SMCPaperConfig(
            operating_mode=current["operating_mode"], model_id=current["model_id"],
            risk_pct=float(current["risk_pct"]))).validated()
        values = {
            "mode": mode or current["mode"], "symbol": normalize_symbol(symbol or current["symbol"]),
            "timeframe": (timeframe or current["timeframe"]).lower(),
            "replay_cursor": int(replay_cursor if replay_cursor is not None else current["replay_cursor"]),
        }
        if values["mode"] not in SESSION_MODES:
            raise ValueError("SMC session mode must be LIVE_PAPER or HISTORICAL")
        if values["timeframe"] not in TF_MS:
            raise ValueError("unsupported SMC session timeframe")
        if (values["mode"], values["symbol"], values["timeframe"]) != (
                current["mode"], current["symbol"], current["timeframe"]):
            if self.broker.positions() or any(row["status"] in OPEN_STATUSES for row in self.broker.orders()):
                raise ValueError("cannot change SMC market, timeframe or mode with an open position or pending order")
        self._db.execute(
            "UPDATE smc_sessions SET mode=?,symbol=?,timeframe=?,replay_cursor=?,operating_mode=?,model_id=?,risk_pct=?,updated_at=? WHERE id=?",
            (values["mode"], values["symbol"], values["timeframe"], values["replay_cursor"],
             config.operating_mode, config.model_id, config.risk_pct, _iso(), current["id"]),
        )
        self._audit("session_configuration_changed", payload={**values, **asdict(config)})
        self._snapshot()
        return self.state()

    def start(self, *, mode: str = "LIVE_PAPER", symbol: str = "BTCUSDT", timeframe: str = "5m",
              starting_balance: float | None = None, config: SMCPaperConfig | None = None) -> dict:
        amount = float(starting_balance or self.starting_balance)
        if amount <= 0:
            raise ValueError("starting balance must be positive")
        config = (config or SMCPaperConfig()).validated()
        if mode not in SESSION_MODES or timeframe not in TF_MS:
            raise ValueError("invalid SMC session mode or timeframe")
        with self._lock, self._db:
            prior = self.session()
            if prior:
                self._snapshot()
                self._db.execute("UPDATE smc_sessions SET status='ended',ended_at=?,end_reason='new_session',updated_at=? WHERE id=?",
                                 (_iso(), _iso(), prior["id"]))
            self.broker.factory_reset(amount)
            sid = self._insert_session(starting_balance=amount, mode=mode,
                                       symbol=normalize_symbol(symbol), timeframe=timeframe, config=config)
            self._audit("session_started", object_id=sid, payload={"mode": mode, "starting_balance": amount})
            self._snapshot()
        return self.state()

    def end(self, reason: str = "user_end") -> dict:
        current = self.session()
        if not current:
            raise ValueError("no active SMC paper session")
        self._snapshot()
        self._audit("session_ended", payload={"reason": reason})
        self._db.execute("UPDATE smc_sessions SET status='ended',ended_at=?,end_reason=?,updated_at=? WHERE id=?",
                         (_iso(), reason, _iso(), current["id"]))
        return self._decoded(dict(self._db.execute("SELECT * FROM smc_sessions WHERE id=?", (current["id"],)).fetchone()))

    def resume(self, session_id: str) -> dict:
        target = self._db.execute("SELECT * FROM smc_sessions WHERE id=?", (session_id,)).fetchone()
        if not target:
            raise KeyError(session_id)
        snapshot = json.loads(target["state_json"] or "{}")
        if not snapshot:
            raise ValueError("SMC session has no resumable account snapshot")
        current = self.session()
        if current and current["id"] != session_id:
            self._snapshot()
            self._db.execute("UPDATE smc_sessions SET status='paused',updated_at=? WHERE id=?", (_iso(), current["id"]))
        self.broker.restore_state(snapshot)
        self._db.execute("UPDATE smc_sessions SET status='active',ended_at=NULL,end_reason=NULL,updated_at=? WHERE id=?",
                         (_iso(), session_id))
        self._audit("session_resumed", object_id=session_id)
        return self.state()

    def duplicate(self, session_id: str) -> dict:
        row = self._db.execute("SELECT * FROM smc_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise KeyError(session_id)
        config = SMCPaperConfig(operating_mode=row["operating_mode"], model_id=row["model_id"], risk_pct=row["risk_pct"])
        result = self.start(mode=row["mode"], symbol=row["symbol"], timeframe=row["timeframe"],
                            starting_balance=row["starting_balance"], config=config)
        self._audit("session_duplicated", payload={"source_session_id": session_id})
        return result

    def reset(self, confirmation: str) -> dict:
        if confirmation != "RESET SMC PAPER":
            raise ValueError("confirmation must exactly match RESET SMC PAPER")
        current = self.session()
        config = SMCPaperConfig(operating_mode=current.get("operating_mode", "signals_only"),
                                model_id=current.get("model_id", "SMC_M1_SWEEP_REVERSAL"),
                                risk_pct=float(current.get("risk_pct", 0.5)))
        previous_id = current.get("id")
        result = self.start(mode=current.get("mode", "LIVE_PAPER"), symbol=current.get("symbol", "BTCUSDT"),
                            timeframe=current.get("timeframe", "5m"),
                            starting_balance=current.get("starting_balance", self.starting_balance), config=config)
        self._db.execute("UPDATE smc_sessions SET end_reason='paper_reset' WHERE id=?", (previous_id,))
        self._audit("paper_account_reset", payload={"previous_session_id": previous_id,
                                                     "confirmation": "verified"})
        return result

    def factory_reset(self) -> dict:
        """Clear all SMC operational data for the global protected reset."""
        with self._lock, self._db:
            self.broker.factory_reset(self.starting_balance)
            for table in ("smc_journal_revisions", "smc_processed_candles", "smc_funding_events", "smc_order_meta",
                          "smc_candidates", "smc_activity", "smc_sessions"):
                self._db.execute(f"DELETE FROM {table}")
            self._db.execute("UPDATE smc_settings SET leverage=1 WHERE id=1")
            self.broker.leverage = 1.0
            self._insert_session(starting_balance=self.starting_balance)
            self._snapshot()
        return self.state()

    def set_leverage(self, leverage: float) -> dict:
        value = float(leverage)
        if not 1 <= value <= 10:
            raise ValueError("SMC paper leverage must be between 1x and 10x")
        prior = self.broker.leverage
        self.broker.leverage = value
        self._db.execute("UPDATE smc_settings SET leverage=? WHERE id=1", (value,))
        self._audit("paper_leverage_changed", payload={"previous": prior, "new": value,
                                                        "existing_positions_resized": False})
        self._snapshot()
        return self.state()

    @staticmethod
    def _multiple(value: float, step: float) -> bool:
        return step <= 0 or abs(value / step - round(value / step)) <= 1e-7

    @staticmethod
    def _rounded_down(value: float, step: float) -> float:
        return value if step <= 0 else math.floor(value / step + 1e-12) * step

    def submit_order(self, *, symbol: str, side: str, order_type: str, rules: dict,
                     reference_price: float, quantity: float | None = None,
                     risk_pct: float | None = None, limit_price: float | None = None,
                     trigger_price: float | None = None, stop_loss: float | None = None,
                     target_1: float | None = None,
                     target_2: float | None = None, idempotency_key: str,
                     ownership: str = "manual", proposal_id: str | None = None,
                     setup_id: str | None = None, poi_id: str | None = None,
                     model_id: str | None = None, creation_candle: str | None = None,
                     expiry_candle: str | None = None) -> dict:
        current = self.session()
        if not current:
            raise ValueError("no active SMC paper session")
        symbol = normalize_symbol(symbol)
        if symbol != current["symbol"]:
            raise ValueError("order symbol must match the active SMC session")
        if not idempotency_key.strip():
            raise ValueError("an idempotency key is required")
        existing = self._db.execute(
            "SELECT order_id FROM smc_order_meta WHERE session_id=? AND idempotency_key=?",
            (current["id"], idempotency_key)).fetchone()
        if existing:
            return {"accepted": True, "duplicate": True, "order": self.broker.order(existing["order_id"]),
                    "real_execution_allowed": False}
        side = "buy" if side.lower() in {"buy", "long", "bullish"} else "sell" if side.lower() in {"sell", "short", "bearish"} else ""
        if not side:
            raise ValueError("side must be buy/long or sell/short")
        entry = float(limit_price or (trigger_price if order_type in {"stop", "stop_limit"} else reference_price))
        if entry <= 0:
            raise ValueError("a positive server reference price is required")
        protective_stop = stop_loss
        if risk_pct is not None:
            if protective_stop is None:
                raise ValueError("risk-based sizing requires a protective stop")
            if not 0 < float(risk_pct) <= 1:
                raise ValueError("risk percentage must be above 0% and no greater than 1%")
            risk_amount = self.broker.account()["equity"] * float(risk_pct) / 100
            quantity = self._rounded_down(risk_amount / abs(entry - float(protective_stop)), rules["quantity_step"])
        if quantity is None or quantity <= 0:
            raise ValueError("quantity must be positive")
        quantity = float(quantity)
        if not self._multiple(quantity, float(rules["quantity_step"])):
            raise ValueError(f"quantity must follow Binance step size {rules['quantity_step']}")
        if quantity < float(rules["min_quantity"]) or (rules.get("max_quantity") and quantity > float(rules["max_quantity"])):
            raise ValueError("quantity is outside Binance contract limits")
        if quantity * entry < float(rules["min_notional"]):
            raise ValueError(f"order notional must be at least {rules['min_notional']} USDT")
        for value in (limit_price, trigger_price, stop_loss, target_1, target_2):
            if value is not None and not self._multiple(float(value), float(rules["tick_size"])):
                raise ValueError(f"price must follow Binance tick size {rules['tick_size']}")
        if protective_stop is not None:
            if side == "buy" and not float(protective_stop) < entry:
                raise ValueError("a long protective stop must be below entry")
            if side == "sell" and not float(protective_stop) > entry:
                raise ValueError("a short protective stop must be above entry")
        for label, target in (("T1", target_1), ("T2", target_2)):
            if target is not None and ((side == "buy" and float(target) <= entry) or
                                       (side == "sell" and float(target) >= entry)):
                raise ValueError(f"{label} must be in the profitable direction")
        if target_1 is not None and target_2 is not None and (
                (side == "buy" and target_2 <= target_1) or (side == "sell" and target_2 >= target_1)):
            raise ValueError("T2 must be farther than T1")
        order = self.broker.submit(symbol=symbol, side=side, order_type=order_type,
                                   quantity=quantity, limit_price=limit_price,
                                   stop_price=trigger_price if order_type in {"stop", "stop_limit"} else None,
                                   reduce_only=False, market_open=True)
        now = _iso()
        config = {"reference_price": entry, "stop_loss": protective_stop,
                  "target_1": target_1, "target_2": target_2, "rules": rules}
        self._db.execute(
            "INSERT INTO smc_order_meta(order_id,session_id,ownership,idempotency_key,proposal_id,setup_id,poi_id,model_id,model_version,direction,entry,stop,target_1,target_2,risk_pct,creation_candle,expiry_candle,status,reason,config_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order["id"], current["id"], ownership, idempotency_key, proposal_id, setup_id, poi_id,
             model_id, STRATEGY_VERSION if model_id else None, "bullish" if side == "buy" else "bearish",
             entry, protective_stop, target_1, target_2, risk_pct, creation_candle, expiry_candle,
             "ORDER_PENDING", "accepted by isolated SMC paper broker", json.dumps(config, sort_keys=True), now, now),
        )
        self._audit("paper_order_created", object_id=order["id"], payload={"ownership": ownership,
                    "quantity": quantity, "entry": entry, "stop": protective_stop,
                    "target_1": target_1, "target_2": target_2})
        self._snapshot()
        return {"accepted": True, "duplicate": False, "order": order, "paper_only": True,
                "real_execution_allowed": False}

    def cancel_order(self, order_id: str) -> dict:
        meta = self._db.execute("SELECT * FROM smc_order_meta WHERE order_id=?", (order_id,)).fetchone()
        if not meta or meta["session_id"] != self.session().get("id"):
            raise KeyError(order_id)
        order = self.broker.cancel(order_id)
        self._db.execute("UPDATE smc_order_meta SET status='CANCELLED',reason='user cancelled',updated_at=? WHERE order_id=?",
                         (_iso(), order_id))
        self._audit("paper_order_cancelled", object_id=order_id)
        self._snapshot()
        return {"order": order, "real_execution_allowed": False}

    def reconcile_orders(self) -> dict:
        """Reconcile statuses without deleting records or touching manual orders."""
        current = self.session()
        sid = current.get("id", "")
        broker_orders = {row["id"]: row for row in self.broker.orders()}
        actions = []
        for meta in self._db.execute("SELECT * FROM smc_order_meta WHERE session_id=?", (sid,)).fetchall():
            order = broker_orders.get(meta["order_id"])
            if not order:
                actions.append({"order_id": meta["order_id"], "action": "flagged_missing",
                                "ownership": meta["ownership"]})
                continue
            mapped = {"open": "ORDER_PENDING", "triggered": "ORDER_PENDING",
                      "partially_filled": "PARTIALLY_FILLED", "filled": "ENTERED",
                      "cancelled": "CANCELLED", "rejected": "REJECTED"}.get(order["status"], order["status"].upper())
            if mapped != meta["status"]:
                self._db.execute("UPDATE smc_order_meta SET status=?,reason=?,updated_at=? WHERE order_id=?",
                                 (mapped, f"reconciled from broker status {order['status']}", _iso(), meta["order_id"]))
                actions.append({"order_id": meta["order_id"], "action": "status_reconciled",
                                "from": meta["status"], "to": mapped, "ownership": meta["ownership"]})
        if actions:
            self._audit("paper_orders_reconciled", payload={"actions": actions, "manual_orders_cancelled": 0})
            self._snapshot()
        return {"actions": actions, "manual_orders_cancelled": 0, "records_deleted": 0,
                "real_execution_allowed": False}

    def process_candle(self, symbol: str, candle) -> dict:
        current = self.session()
        if not current:
            raise ValueError("no active SMC paper session")
        timestamp = getattr(candle, "timestamp", None)
        if timestamp is None and isinstance(candle, dict):
            timestamp = candle.get("timestamp")
        candle_time = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp or "")
        if not candle_time:
            raise ValueError("a timestamped closed candle is required")
        key = (current["id"], normalize_symbol(symbol), candle_time)
        if self._db.execute("SELECT 1 FROM smc_processed_candles WHERE session_id=? AND symbol=? AND candle_time=?", key).fetchone():
            return {"duplicate": True, "events": [], "real_execution_allowed": False}
        if isinstance(candle, dict):
            candle_values = candle
        else:
            candle_values = {key: getattr(candle, key) for key in ("open", "high", "low", "close", "volume")}
        # If a stop and T1 are both touched in one OHLC candle, chronology is
        # unknowable. Cancel the scale-out instruction so the broker's adverse
        # protective stop handles the whole remaining position first.
        position = next((row for row in self.broker.positions() if row["symbol"] == key[1]), None)
        if position and position.get("stop_loss") is not None:
            stop_hit = (position["side"] == "long" and float(candle_values["low"]) <= position["stop_loss"]) or \
                       (position["side"] == "short" and float(candle_values["high"]) >= position["stop_loss"])
            if stop_hit:
                for meta in self._db.execute(
                        "SELECT * FROM smc_order_meta WHERE session_id=? AND ownership='strategy_target_1' AND status='ORDER_PENDING'",
                        (current["id"],)).fetchall():
                    config = json.loads(meta["config_json"] or "{}")
                    target = config.get("target_1")
                    target_hit = target is not None and ((position["side"] == "long" and float(candle_values["high"]) >= target) or
                                                         (position["side"] == "short" and float(candle_values["low"]) <= target))
                    if target_hit:
                        self.broker.cancel(meta["order_id"])
                        self._db.execute("UPDATE smc_order_meta SET status='CANCELLED_AMBIGUOUS',reason=?,updated_at=? WHERE order_id=?",
                                         ("stop and T1 touched in one candle; conservative stop-first policy", _iso(), meta["order_id"]))
                        self._audit("intrabar_ambiguity_stop_first", object_id=meta["order_id"],
                                    payload={"stop": position["stop_loss"], "target_1": target,
                                             "candle_time": candle_time})
        protections = {}
        for row in self._db.execute(
                "SELECT order_id,config_json FROM smc_order_meta WHERE session_id=? AND status IN ('ORDER_PENDING','PARTIALLY_FILLED')",
                (current["id"],)).fetchall():
            config = json.loads(row["config_json"] or "{}")
            protections[row["order_id"]] = {"stop_loss": config.get("stop_loss"),
                                             "take_profit": config.get("target_2") or config.get("target_1")}
        result = self.broker.process_candle(symbol, candle, protections=protections)
        self._db.execute("INSERT INTO smc_processed_candles VALUES (?,?,?)", key)
        for event in result["events"]:
            parent = self._db.execute("SELECT * FROM smc_order_meta WHERE order_id=?", (event.get("order_id"),)).fetchone()
            if not parent or parent["ownership"] != "strategy":
                continue
            config = json.loads(parent["config_json"] or "{}")
            target_1 = config.get("target_1")
            if target_1 is None or self._db.execute(
                    "SELECT 1 FROM smc_order_meta WHERE session_id=? AND idempotency_key=?",
                    (current["id"], f"target1:{parent['order_id']}")).fetchone():
                continue
            step = float(config.get("rules", {}).get("quantity_step", 0) or 0)
            quantity = self._rounded_down(float(event["quantity"]) * 0.5, step)
            if quantity <= 0:
                continue
            side = "sell" if parent["direction"] == "bullish" else "buy"
            child = self.broker.submit(symbol=key[1], side=side, order_type="limit",
                                       quantity=quantity, limit_price=float(target_1),
                                       reduce_only=True, market_open=True)
            now = _iso()
            child_config = {**config, "parent_order_id": parent["order_id"], "scale_out_fraction": 0.5}
            self._db.execute(
                "INSERT INTO smc_order_meta(order_id,session_id,ownership,idempotency_key,proposal_id,setup_id,poi_id,model_id,model_version,direction,entry,stop,target_1,target_2,risk_pct,creation_candle,expiry_candle,status,reason,config_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (child["id"], current["id"], "strategy_target_1", f"target1:{parent['order_id']}",
                 parent["proposal_id"], parent["setup_id"], parent["poi_id"], parent["model_id"],
                 parent["model_version"], parent["direction"], parent["entry"], parent["stop"],
                 parent["target_1"], parent["target_2"], parent["risk_pct"], parent["creation_candle"],
                 parent["expiry_candle"], "ORDER_PENDING", "50% scale-out at deterministic T1",
                 json.dumps(child_config, sort_keys=True), now, now),
            )
            self._audit("paper_target_1_order_created", object_id=child["id"],
                        payload={"parent_order_id": parent["order_id"], "quantity": quantity,
                                 "target_1": target_1})
        self.reconcile_orders()
        for event in result["events"]:
            self._audit("paper_fill", object_id=event.get("order_id", ""),
                        payload={**event, "candle_time": candle_time})
        self._snapshot()
        return {**result, "duplicate": False, "paper_only": True, "real_execution_allowed": False}

    def synchronize_candidate(self, evaluation: dict, *, rules: dict,
                              reference_price: float, feed_reliable: bool) -> dict:
        """Journal one deterministic M1 decision and enforce the saved mode."""
        current = self.session()
        if not current:
            raise ValueError("no active SMC paper session")
        proposal = evaluation.get("proposal")
        plan = evaluation.get("trade_plan")
        if evaluation.get("state") != "ENTRY_READY" or not proposal or not plan:
            return {"created": False, "reason": evaluation.get("next_required_event", "not ready"),
                    "real_execution_allowed": False}
        proposal_id = proposal["id"]
        existing = self._db.execute("SELECT * FROM smc_candidates WHERE proposal_id=?", (proposal_id,)).fetchone()
        if existing:
            return {"created": False, "duplicate": True, "candidate": {**dict(existing),
                    "payload": json.loads(existing["payload"])}, "real_execution_allowed": False}
        mode = current["operating_mode"]
        status, reason = (
            ("DATA_PAUSED", "market data is not reliable") if not feed_reliable else
            ("SIGNAL_ONLY", "signals-only mode cannot create an order") if mode == "signals_only" else
            ("PENDING_APPROVAL", "waiting for explicit paper approval") if mode == "manual_approval" else
            ("APPROVED_AUTOMATIC", "automatic paper mode passed the candidate boundary")
        )
        payload = {"evaluation": evaluation, "rules": rules, "reference_price": reference_price}
        now = _iso()
        self._db.execute(
            "INSERT INTO smc_candidates VALUES (?,?,?,?,?,?,?,?,?)",
            (proposal_id, current["id"], proposal.get("setup_id"), current["model_id"],
             status, reason, json.dumps(payload, sort_keys=True, default=str), now, now),
        )
        self._audit("strategy_candidate_created", object_id=proposal_id,
                    payload={"status": status, "reason": reason,
                             "native_object_ids": evaluation.get("native_object_ids", [])})
        placed = None
        if status == "APPROVED_AUTOMATIC":
            placed = self._place_candidate(payload, proposal_id, source="automatic")
        return {"created": True, "candidate_status": status, "order": placed,
                "real_execution_allowed": False}

    def _place_candidate(self, payload: dict, proposal_id: str, *, source: str) -> dict:
        evaluation = payload["evaluation"]
        proposal, plan = evaluation["proposal"], evaluation["trade_plan"]
        tick = float(payload["rules"]["tick_size"])
        normalized = lambda value: round(round(float(value) / tick) * tick, 12) if tick > 0 else float(value)
        result = self.submit_order(
            symbol=proposal["symbol"], side=proposal["direction"], order_type="market",
            rules=payload["rules"], reference_price=float(payload["reference_price"]),
            risk_pct=float(plan["risk_percent"]), stop_loss=normalized(plan["stop"]),
            target_1=normalized(plan["target_1"]), target_2=normalized(plan["target_2"]),
            idempotency_key=f"strategy:{proposal_id}", ownership="strategy",
            proposal_id=proposal_id, setup_id=proposal.get("setup_id"),
            poi_id=next((object_id for object_id in evaluation.get("native_object_ids", [])
                         if object_id.startswith(("ob-", "fvg-"))), None),
            model_id=evaluation["model"]["id"],
            creation_candle=str(proposal.get("signal_timestamp") or ""),
        )
        self._db.execute("UPDATE smc_candidates SET status='ORDER_CREATED',reason=?,updated_at=? WHERE proposal_id=?",
                         (f"{source} paper order created", _iso(), proposal_id))
        return result

    def approve_candidate(self, proposal_id: str) -> dict:
        row = self._db.execute(
            "SELECT * FROM smc_candidates WHERE session_id=? AND proposal_id=?",
            (self.session().get("id", ""), proposal_id)).fetchone()
        if not row:
            raise KeyError(proposal_id)
        if row["status"] != "PENDING_APPROVAL":
            raise ValueError("candidate is not awaiting explicit paper approval")
        return self._place_candidate(json.loads(row["payload"]), proposal_id, source="manual_approval")

    def apply_funding_once(self, *, symbol: str, funding_time: str | None,
                           rate: float, mark_price: float) -> dict:
        """Book one factual provider funding event at most once per session."""
        session_id = self.session().get("id")
        if not session_id or not funding_time:
            return {"applied": False, "reason": "funding event unavailable"}
        key = (session_id, normalize_symbol(symbol), funding_time)
        existing = self._db.execute(
            "SELECT applied,amount FROM smc_funding_events WHERE session_id=? AND symbol=? AND funding_time=?",
            key).fetchone()
        if existing:
            return {"applied": False, "reason": "funding event already processed",
                    "originally_applied": bool(existing["applied"]), "funding": existing["amount"]}
        result = self.broker.apply_funding(key[1], float(rate), float(mark_price))
        self._db.execute(
            "INSERT INTO smc_funding_events(session_id,symbol,funding_time,rate,mark_price,amount,applied) VALUES (?,?,?,?,?,?,?)",
            (*key, float(rate), float(mark_price), float(result.get("funding") or 0),
             int(bool(result.get("applied")))))
        self._audit("paper_funding_processed", object_id=funding_time,
                    payload={"symbol": key[1], "rate": rate, "mark_price": mark_price,
                             "applied": bool(result.get("applied")),
                             "amount": result.get("funding") or 0})
        self._snapshot()
        return {**result, "funding_time": funding_time, "rate": rate,
                "source": "Binance USDⓈ-M Futures public funding history"}

    def advance_replay_cursor(self, expected_session_id: str, cursor: int) -> None:
        current = self.session()
        if current.get("id") != expected_session_id or current.get("mode") != "HISTORICAL":
            raise ValueError("historical replay cursor does not belong to the active SMC session")
        self._db.execute("UPDATE smc_sessions SET replay_cursor=?,updated_at=? WHERE id=?",
                         (int(cursor), _iso(), expected_session_id))

    def add_journal_note(self, journal_id: str, note: str) -> dict:
        if not note.strip():
            raise ValueError("journal note cannot be empty")
        row = next((item for item in self.journal()["journal"] if item["journal_id"] == journal_id), None)
        if not row:
            raise KeyError(journal_id)
        revision = {"id": uuid.uuid4().hex, "journal_id": journal_id,
                    "session_id": row["session_id"], "note": note.strip(), "created_at": _iso()}
        self._db.execute("INSERT INTO smc_journal_revisions VALUES (?,?,?,?,?)",
                         tuple(revision[key] for key in ("id", "journal_id", "session_id", "note", "created_at")))
        self._audit("journal_note_added", object_id=journal_id,
                    payload={"revision_id": revision["id"]})
        return revision

    def _journal_session_state(self, session_id: str) -> dict:
        session_row = self._db.execute("SELECT * FROM smc_sessions WHERE id=?", (session_id,)).fetchone()
        if not session_row:
            raise KeyError(session_id)
        current = self.session()
        if current.get("id") == session_id:
            return self.state()
        session = self._decoded(dict(session_row))
        snapshot = session.get("state") or {}
        metadata = [{**dict(row), "config": json.loads(row["config_json"])} for row in self._db.execute(
            "SELECT * FROM smc_order_meta WHERE session_id=? ORDER BY created_at DESC", (session_id,))]
        candidates = [{**dict(row), "payload": json.loads(row["payload"])} for row in self._db.execute(
            "SELECT * FROM smc_candidates WHERE session_id=? ORDER BY created_at DESC", (session_id,))]
        return {"session": session, "trades": snapshot.get("fills", []),
                "order_metadata": metadata, "candidates": candidates}

    def _journal_for_session(self, session_id: str) -> list[dict]:
        state = self._journal_session_state(session_id)
        fills_by_order: dict[str, list[dict]] = {}
        for fill in state["trades"]:
            fills_by_order.setdefault(fill["order_id"], []).append(fill)
        rows = []
        for candidate in state["candidates"]:
            evaluation = candidate["payload"].get("evaluation", {})
            proposal = evaluation.get("proposal") or {}
            candidate_meta = [row for row in state["order_metadata"]
                              if row.get("proposal_id") == candidate["proposal_id"]]
            meta = next((row for row in candidate_meta if row.get("ownership") == "strategy"),
                        candidate_meta[0] if candidate_meta else None)
            fills = [fill for row in candidate_meta for fill in fills_by_order.get(row["order_id"], [])]
            rows.append({
                "journal_id": f"smc-journal-{candidate['proposal_id']}",
                "session_id": candidate["session_id"], "symbol": proposal.get("symbol", state["session"].get("symbol")),
                "timeframe": proposal.get("timeframe", state["session"].get("timeframe")),
                "strategy_id": STRATEGY_ID, "model_id": candidate["model_id"], "version": STRATEGY_VERSION,
                "direction": proposal.get("direction"), "status": candidate["status"],
                "signal_timestamp": proposal.get("signal_timestamp"),
                "created_at": candidate["created_at"], "updated_at": candidate["updated_at"],
                "native_object_ids": evaluation.get("native_object_ids", []),
                "ordered_conditions": evaluation.get("ordered_condition_results", []),
                "missing_conditions": evaluation.get("missing_conditions", []),
                "trade_plan": evaluation.get("trade_plan"), "proposal_id": candidate["proposal_id"],
                "setup_id": candidate.get("setup_id"), "order_id": meta["order_id"] if meta else None,
                "fills": fills, "net_pnl": sum(float(fill["realized_pnl"]) - float(fill["fee"]) for fill in fills),
                "data_quality": "SYNCHRONIZED" if candidate["status"] != "DATA_PAUSED" else "UNRELIABLE",
                "rule_compliance": "PASS" if evaluation.get("state") == "ENTRY_READY" else "INCOMPLETE",
                "notes": [dict(note) for note in self._db.execute(
                    "SELECT id,note,created_at FROM smc_journal_revisions WHERE journal_id=? ORDER BY created_at",
                    (f"smc-journal-{candidate['proposal_id']}",))],
            })
        return rows

    def journal(self, session_id: str | None = None) -> dict:
        session_ids = ([session_id] if session_id else
                       [row["id"] for row in self._db.execute(
                           "SELECT id FROM smc_sessions ORDER BY started_at DESC")])
        rows = [row for sid in session_ids for row in self._journal_for_session(sid)]
        return {"journal": rows, "paper_only": True, "real_execution_allowed": False}

    def metrics(self) -> dict:
        current_id = self.session().get("id")
        journal = self.journal(current_id)["journal"] if current_id else []
        completed = [row for row in journal if row["fills"]]
        wins = [row for row in completed if row["net_pnl"] > 0]
        losses = [row for row in completed if row["net_pnl"] < 0]
        gross_profit = sum(row["net_pnl"] for row in wins)
        gross_loss = abs(sum(row["net_pnl"] for row in losses))
        net = sum(row["net_pnl"] for row in completed)
        target_1_hits = sum(any(meta.get("ownership") == "strategy_target_1" and
                                meta.get("proposal_id") == row["proposal_id"] and
                                any(fill["order_id"] == meta["order_id"] for fill in row["fills"])
                                for meta in self.state()["order_metadata"]) for row in completed)
        funding = self.state()["funding_events"]
        return {"session_id": self.session().get("id"), "detected_setups": len(journal),
                "orders_placed": len(self.state()["order_metadata"]), "trades_with_fills": len(completed),
                "wins": len(wins), "win_rate": (len(wins) / len(completed) if completed else None),
                "net_pnl": net, "expectancy": (net / len(completed) if completed else None),
                "profit_factor": (gross_profit / gross_loss if gross_loss else None),
                "target_1_hit_rate": (target_1_hits / len(completed) if completed else None),
                "fees_paid": self.broker.account()["fees_paid"],
                "funding_paid": sum(float(row["amount"]) for row in funding),
                "mfe": None, "mae": None,
                "evidence_limitations": ["MFE and MAE are unavailable because tick-path evidence is not stored; values are not fabricated."],
                "sample_size_warning": "INSUFFICIENT_SAMPLE" if len(completed) < 30 else None,
                "paper_only": True, "real_execution_allowed": False}


class SMCStrategyLabRuntime:
    """Small PAPER-only closed-bar worker for the active SMC session."""

    def __init__(self, market, account: SMCPaperAccount, *, poll_seconds: float = 5.0,
                 autostart: bool = True):
        self.market, self.account = market, account
        self.poll_seconds = max(1.0, float(poll_seconds))
        self._stop = threading.Event()
        self._tick_lock = threading.Lock()
        self._thread = None
        self._stream_identity: tuple[str, str] | None = None
        self.stream = None
        if callable(getattr(market, "public_usdm_window", None)):
            from services.price_action_stream import PriceActionPublicStream
            self.stream = PriceActionPublicStream(
                market.public_usdm_window,
                event_sink=lambda event: account._audit(
                    "market_data_stream_event", payload=event),
            )
        if autostart:
            self._thread = threading.Thread(target=self._run, name="smc-paper-runtime", daemon=True)
            self._thread.start()

    def reconcile_visual(self, visual: dict, *, symbol: str, timeframe: str) -> tuple[dict, dict | None]:
        """Use the shared Binance websocket state machine as entry authority."""
        from services.native_smc_live_visual import reconcile_market_state
        rest_quote = None
        try:
            rest_quote = self.market.public_usdm_quote(symbol)
        except Exception:
            pass
        if self.stream is None:
            return reconcile_market_state(visual, rest_quote, timeframe=timeframe), rest_quote
        identity = (normalize_symbol(symbol), timeframe)
        if self._stream_identity != identity:
            self.stream.start(*identity)
            self._stream_identity = identity
        snapshot = self.stream.snapshot()
        status = snapshot["connection"]
        quote = {**(rest_quote or {}), **{key: value for key, value in snapshot["quote"].items()
                                         if value is not None}}
        stream_bars = snapshot["closed_bars"]
        stream_last = stream_bars[-1].timestamp.isoformat() if stream_bars else None
        visual_last = visual.get("data_provenance", {}).get("last_closed_candle")
        histories_match = bool(stream_last and visual_last and stream_last == visual_last)
        reliable = bool(status.get("reliable")) and histories_match
        live = visual.setdefault("live_display", {})
        live.update({
            "bid": quote.get("bid"), "ask": quote.get("ask"), "mark": quote.get("mark"),
            "funding_rate": quote.get("funding_rate"),
            "last_funding_time": (rest_quote or {}).get("last_funding_time"),
            "next_funding_time": quote.get("next_funding_time"),
            **status, "connection_state": status["state"],
            "reliable": reliable, "new_entries_paused": not reliable,
            "quote_source": "BINANCE_USDM_PUBLIC_WEBSOCKET",
            "health_reason": (status["health_reason"] if histories_match else
                              "websocket and chart completed-candle histories are not reconciled"),
        })
        visual.setdefault("data_provenance", {}).update({
            "connection_state": live["connection_state"],
            "new_entries_paused": not reliable, "market_data_mode": "LIVE",
            "market_data_source": "Binance USDⓈ-M Futures public websocket with REST recovery",
            "exchange": "Binance USDⓈ-M Futures",
        })
        return visual, quote or None

    def _run(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                current = self.account.session()
                if current and current.get("mode") == "LIVE_PAPER":
                    self.tick()
            except Exception as exc:
                self.account._audit("paper_runtime_paused", payload={"error": f"{type(exc).__name__}: {exc}",
                                                                      "new_orders_created": 0})

    def tick(self) -> dict:
        if not self._tick_lock.acquire(blocking=False):
            return {"skipped": True, "reason": "tick already running", "real_execution_allowed": False}
        try:
            current = self.account.session()
            if not current:
                raise ValueError("no active SMC paper session")
            from services.native_smc_live_visual import live_visual_state
            visual = live_visual_state(current["symbol"], current["timeframe"], "binance_usdm",
                                       limit=800, visible=400, model_id=current["model_id"])
            rules = self.market.usdm_contract_rules(current["symbol"])
            visual, quote = self.reconcile_visual(
                visual, symbol=current["symbol"], timeframe=current["timeframe"])
            reliable = bool(visual["live_display"].get("reliable"))
            processed = (self.account.process_candle(current["symbol"], visual["candles"][-1])
                         if reliable else {"duplicate": False, "events": [], "paused": True,
                                           "reason": visual["live_display"].get("health_reason")})
            candidate = self.account.synchronize_candidate(
                visual["source_strategy"], rules=rules,
                reference_price=float((quote or {}).get("mark") or visual["live_display"]["last_price"]),
                feed_reliable=reliable)
            funding = self.account.apply_funding_once(
                symbol=current["symbol"], funding_time=(quote or {}).get("last_funding_time"),
                rate=float((quote or {}).get("funding_rate") or 0),
                mark_price=float((quote or {}).get("mark") or visual["live_display"]["last_price"]),
            ) if reliable else {"applied": False, "reason": "market data is not synchronized"}
            return {"processed": processed, "candidate": candidate,
                    "funding": funding, "market_data_health": visual["live_display"],
                    "paper_only": True, "real_execution_allowed": False}
        finally:
            self._tick_lock.release()

    def stop(self) -> None:
        self._stop.set()
        if self.stream is not None:
            self.stream.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def replay_step(self, *, steps: int = 1) -> dict:
        current = self.account.session()
        if not current or current.get("mode") != "HISTORICAL":
            raise ValueError("an active HISTORICAL SMC session is required")
        rows = self.market.bars(current["symbol"], current["timeframe"], limit=3000)
        if not rows:
            raise RuntimeError("verified cached Binance history is required for SMC replay")
        start = max(0, min(int(current.get("replay_cursor") or 0), len(rows)))
        end = min(len(rows), start + max(1, min(int(steps), 500)))
        from services.native_smc import SMCConfig, SMCMarketStructureEngine
        from services.smc_strategy_v1 import evaluate
        engine = SMCMarketStructureEngine(SMCConfig(symbol=current["symbol"],
                                                     timeframe=current["timeframe"]))
        if start:
            engine.ingest_authoritative_closed_bars(rows[:start],
                timeframe_seconds=TF_MS[current["timeframe"]] / 1000)
        rules = self.market.usdm_contract_rules(current["symbol"])
        results = []
        for bar in rows[start:end]:
            engine.process_closed_bar(bar)
            processed = self.account.process_candle(current["symbol"], bar)
            decision = evaluate(engine, current["model_id"], candle_at=bar.timestamp)
            candidate = self.account.synchronize_candidate(
                decision, rules=rules, reference_price=float(bar.close), feed_reliable=True)
            results.append({"candle": bar.timestamp.isoformat(), "processed": processed,
                            "candidate": candidate, "decision_state": decision["state"]})
        self.account.advance_replay_cursor(current["id"], end)
        self.account._snapshot()
        return {"session_id": current["id"], "cursor": end, "total": len(rows),
                "has_next": end < len(rows), "future_candles_visible": False,
                "steps": results, "paper_only": True, "real_execution_allowed": False}

    def set_protection(self, symbol: str, *, stop_loss: float | None = None,
                       take_profit: float | None = None) -> dict:
        position = self.broker.set_protection(symbol, stop_loss=stop_loss, take_profit=take_profit)
        self._audit("paper_position_protection_changed", object_id=symbol,
                    payload={"stop_loss": stop_loss, "take_profit": take_profit})
        self._snapshot()
        return {"position": position, "real_execution_allowed": False}

    def export_session(self, session_id: str | None = None) -> dict:
        sid = session_id or self.session().get("id")
        row = self._db.execute("SELECT * FROM smc_sessions WHERE id=?", (sid,)).fetchone()
        if not row:
            raise KeyError(sid)
        return {"format_version": "SMC_PAPER_1", "exported_at": _iso(),
                "paper_only": True, "real_execution_allowed": False,
                "session": self._decoded(dict(row)),
                "orders": [dict(item) for item in self._db.execute("SELECT * FROM smc_order_meta WHERE session_id=?", (sid,))],
                "activity": [dict(item) for item in self._db.execute("SELECT * FROM smc_activity WHERE session_id=?", (sid,))]}
