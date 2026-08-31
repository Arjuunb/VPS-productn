"""Candle-driven, persistent paper broker for the V2 paper-trading path.

The legacy ``PaperExecutionEngine`` remains the signal-driven compatibility
engine.  This broker accepts explicit orders and only advances them when given
a *real* OHLCV candle from ``MarketDataService``.  It deliberately has no price
generator and no client-provided fill-price escape hatch.
"""
from __future__ import annotations

import math
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from data.sqlite_runtime import runtime_connection


ORDER_TYPES = {"market", "limit", "stop", "stop_limit", "trailing_stop"}
OPEN_STATUSES = {"open", "partially_filled", "triggered"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return uuid.uuid4().hex


class PaperBrokerV2:
    """A deterministic broker simulation with SQLite-persisted account state.

    A candle contains only OHLCV, so intrabar sequencing is unknowable.  V2
    uses conservative rules: stops fill at the adverse open/trigger, resting
    limits fill at the better of the open and limit, and a bar can fill only the
    configurable fraction of its reported volume.  That avoids claiming
    impossible price improvement or liquidity.
    """
    def __init__(self, path: str | Path, *, starting_balance: float = 10_000.0,
                 leverage: float = 1.0, fee_rate: float = 0.0004,
                 spread_bps: float = 2.0, slippage_bps: float = 3.0,
                 participation_rate: float = 0.02):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.leverage = max(1.0, float(leverage))
        self.fee_rate = max(0.0, float(fee_rate))
        self.spread_bps = max(0.0, float(spread_bps))
        self.slippage_bps = max(0.0, float(slippage_bps))
        self.participation_rate = min(1.0, max(0.0, float(participation_rate)))
        self._lock = threading.RLock()
        self._c = runtime_connection(self.path)
        self._schema(starting_balance)

    def _schema(self, starting_balance: float) -> None:
        with self._lock:
            self._c.executescript("""
              CREATE TABLE IF NOT EXISTS v2_account(
                id INTEGER PRIMARY KEY CHECK(id=1), starting_balance REAL NOT NULL,
                balance REAL NOT NULL, fees_paid REAL NOT NULL DEFAULT 0,
                funding_paid REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS v2_positions(
                symbol TEXT PRIMARY KEY, side TEXT NOT NULL, size REAL NOT NULL,
                entry_price REAL NOT NULL, stop_loss REAL, take_profit REAL,
                trailing_offset REAL, peak_price REAL, opened_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS v2_orders(
                id TEXT PRIMARY KEY, symbol TEXT NOT NULL, side TEXT NOT NULL,
                type TEXT NOT NULL, quantity REAL NOT NULL, remaining REAL NOT NULL,
                filled REAL NOT NULL DEFAULT 0, average_price REAL, limit_price REAL,
                stop_price REAL, trailing_offset REAL, reduce_only INTEGER NOT NULL,
                status TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, triggered_at TEXT);
              CREATE TABLE IF NOT EXISTS v2_fills(
                id TEXT PRIMARY KEY, order_id TEXT NOT NULL, symbol TEXT NOT NULL,
                side TEXT NOT NULL, quantity REAL NOT NULL, price REAL NOT NULL,
                fee REAL NOT NULL, realized_pnl REAL NOT NULL, timestamp TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS idx_v2_orders_open ON v2_orders(symbol,status);
            """)
            self._c.execute("INSERT OR IGNORE INTO v2_account(id,starting_balance,balance,updated_at) VALUES (1,?,?,?)",
                            (float(starting_balance), float(starting_balance), _now()))
            columns = {row[1] for row in self._c.execute("PRAGMA table_info(v2_account)")}
            for name, ddl in (
                ("realized_pnl", "REAL NOT NULL DEFAULT 0"),
                ("peak_equity", "REAL NOT NULL DEFAULT 0"),
                ("max_drawdown", "REAL NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    self._c.execute(f"ALTER TABLE v2_account ADD COLUMN {name} {ddl}")
            order_columns = {row[1] for row in self._c.execute("PRAGMA table_info(v2_orders)")}
            for name in (
                "protection_stop_loss", "protection_take_profit",
                "protection_target_r", "protection_tick_size",
            ):
                if name not in order_columns:
                    self._c.execute(f"ALTER TABLE v2_orders ADD COLUMN {name} REAL")
            self._c.execute(
                "UPDATE v2_account SET peak_equity=CASE WHEN peak_equity<=0 THEN balance ELSE peak_equity END WHERE id=1"
            )
            self._c.commit()

    @staticmethod
    def _norm_side(side: str) -> str:
        value = (side or "").lower()
        if value in {"buy", "long"}:
            return "buy"
        if value in {"sell", "short"}:
            return "sell"
        raise ValueError("side must be buy/sell (or long/short)")

    @staticmethod
    def _candle(candle) -> dict:
        if isinstance(candle, dict):
            data = candle
        else:
            data = {k: getattr(candle, k) for k in ("open", "high", "low", "close", "volume", "timestamp")}
        try:
            out = {k: float(data[k]) for k in ("open", "high", "low", "close", "volume")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("a complete real OHLCV candle is required") from exc
        if out["low"] < 0 or out["high"] < max(out["open"], out["close"], out["low"]) or \
           out["low"] > min(out["open"], out["close"], out["high"]):
            raise ValueError("invalid OHLCV candle")
        return out

    def _account_row(self):
        return self._c.execute("SELECT * FROM v2_account WHERE id=1").fetchone()

    def account(self, marks: Optional[dict[str, float]] = None) -> dict:
        with self._lock:
            a = self._account_row()
            positions = [dict(r) for r in self._c.execute("SELECT * FROM v2_positions")]
        unrealized = 0.0
        used = 0.0
        for p in positions:
            mark = (marks or {}).get(p["symbol"], p["entry_price"])
            sign = 1 if p["side"] == "long" else -1
            unrealized += sign * (float(mark) - p["entry_price"]) * p["size"]
            used += p["entry_price"] * p["size"] / self.leverage
        equity = a["balance"] + unrealized
        peak = max(float(a["peak_equity"]), equity)
        drawdown = max(float(a["max_drawdown"]), peak - equity)
        with self._lock:
            self._c.execute("UPDATE v2_account SET peak_equity=?,max_drawdown=? WHERE id=1", (peak, drawdown))
            self._c.commit()
        return {"starting_balance": a["starting_balance"], "balance": round(a["balance"], 8),
                "equity": round(equity, 8), "unrealized_pnl": round(unrealized, 8),
                "realized_pnl": round(float(a["realized_pnl"]), 8),
                "used_margin": round(used, 8), "margin": round(used, 8),
                "free_margin": round(equity - used, 8), "buying_power": round(max(0.0, equity - used) * self.leverage, 8),
                "fees_paid": round(a["fees_paid"], 8), "funding_paid": round(a["funding_paid"], 8),
                "peak_equity": round(peak, 8), "max_drawdown": round(drawdown, 8),
                "leverage": self.leverage, "positions": len(positions)}

    def positions(self) -> list[dict]:
        with self._lock:
            rows = [dict(r) for r in self._c.execute("SELECT * FROM v2_positions ORDER BY opened_at")]
        maintenance = 0.005
        for row in rows:
            if self.leverage <= 1:
                liquidation = None
            elif row["side"] == "long":
                liquidation = row["entry_price"] * (1 - 1 / self.leverage + maintenance)
            else:
                liquidation = row["entry_price"] * (1 + 1 / self.leverage - maintenance)
            row["estimated_liquidation_price"] = liquidation
            row["liquidation_model"] = "isolated estimate; mark-price trigger"
        return rows

    def orders(self, *, status: Optional[str] = None) -> list[dict]:
        with self._lock:
            q, args = "SELECT * FROM v2_orders", []
            if status:
                q += " WHERE status=?"; args.append(status)
            q += " ORDER BY created_at DESC"
            return [dict(r) for r in self._c.execute(q, args)]

    def fills(self, *, limit: int = 500) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._c.execute(
                "SELECT * FROM v2_fills ORDER BY timestamp DESC LIMIT ?", (int(limit),))]

    def factory_reset(self, starting_balance: float) -> None:
        amount = float(starting_balance)
        if amount <= 0:
            raise ValueError("starting balance must be positive")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._c.execute("DELETE FROM v2_fills")
                self._c.execute("DELETE FROM v2_orders")
                self._c.execute("DELETE FROM v2_positions")
                self._c.execute(
                    "UPDATE v2_account SET starting_balance=?,balance=?,fees_paid=0,funding_paid=0,realized_pnl=0,peak_equity=?,max_drawdown=0,updated_at=? WHERE id=1",
                    (amount, amount, amount, _now()))
                self._c.commit()
            except Exception:
                self._c.rollback()
                raise

    def submit(self, *, symbol: str, side: str, order_type: str, quantity: float,
               limit_price: Optional[float] = None, stop_price: Optional[float] = None,
               trailing_offset: Optional[float] = None, reduce_only: bool = False,
               market_open: bool = True,
               protection_stop_loss: Optional[float] = None,
               protection_take_profit: Optional[float] = None,
               protection_target_r: Optional[float] = None,
               protection_tick_size: Optional[float] = None) -> dict:
        symbol = (symbol or "").upper().replace("/", "")
        side, order_type = self._norm_side(side), (order_type or "").lower()
        if order_type not in ORDER_TYPES:
            raise ValueError(f"unsupported order type '{order_type}'")
        if not market_open:
            raise ValueError("market is closed")
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if order_type in {"limit", "stop_limit"} and (limit_price is None or limit_price <= 0):
            raise ValueError("limit price must be positive")
        if order_type in {"stop", "stop_limit"} and (stop_price is None or stop_price <= 0):
            raise ValueError("stop price must be positive")
        if order_type == "trailing_stop" and (trailing_offset is None or trailing_offset <= 0):
            raise ValueError("trailing offset must be positive")
        if any(value is not None and value <= 0 for value in (
                protection_stop_loss, protection_take_profit,
                protection_target_r, protection_tick_size)):
            raise ValueError("stored order protection values must be positive")
        with self._lock:
            pos = self._c.execute("SELECT * FROM v2_positions WHERE symbol=?", (symbol,)).fetchone()
            if reduce_only and (not pos or (pos["side"] == "long") == (side == "buy")):
                raise ValueError("reduce-only order requires an opposite open position")
            if not reduce_only:
                reference = float(limit_price or stop_price or 0)
                # A market price is unknown until a candle arrives. Its final margin
                # check is repeated at fill time; priced orders can be checked now.
                if reference:
                    needed = reference * float(quantity) / self.leverage
                    if needed > self.account()["free_margin"]:
                        raise ValueError("insufficient free margin")
            oid, now = _id(), _now()
            self._c.execute(
                "INSERT INTO v2_orders(id,symbol,side,type,quantity,remaining,filled,average_price,limit_price,stop_price,trailing_offset,reduce_only,status,reason,created_at,updated_at,triggered_at,protection_stop_loss,protection_take_profit,protection_target_r,protection_tick_size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, symbol, side, order_type, float(quantity), float(quantity), 0.0, None,
                 limit_price, stop_price, trailing_offset, int(reduce_only), "open", None, now, now, None,
                 protection_stop_loss, protection_take_profit,
                 protection_target_r, protection_tick_size),
            )
            self._c.commit()
        return self.order(oid)

    def order(self, order_id: str) -> dict:
        with self._lock:
            row = self._c.execute("SELECT * FROM v2_orders WHERE id=?", (order_id,)).fetchone()
        if not row:
            raise KeyError(order_id)
        return dict(row)

    def cancel(self, order_id: str) -> dict:
        with self._lock:
            row = self._c.execute("SELECT * FROM v2_orders WHERE id=?", (order_id,)).fetchone()
            if not row:
                raise KeyError(order_id)
            if row["status"] not in OPEN_STATUSES:
                raise ValueError("only open orders can be cancelled")
            self._c.execute("UPDATE v2_orders SET status='cancelled',updated_at=? WHERE id=?", (_now(), order_id))
            self._c.commit()
        return self.order(order_id)

    def set_order_protection(self, order_id: str, *, stop_loss: float,
                             take_profit: float, target_r: float,
                             tick_size: Optional[float] = None) -> dict:
        if any(value is None or float(value) <= 0 for value in (stop_loss, take_profit, target_r)):
            raise ValueError("order protection requires positive stop, target and R:R")
        if tick_size is not None and float(tick_size) <= 0:
            raise ValueError("protection tick size must be positive")
        with self._lock:
            if not self._c.execute("SELECT 1 FROM v2_orders WHERE id=?", (order_id,)).fetchone():
                raise KeyError(order_id)
            self._c.execute(
                "UPDATE v2_orders SET protection_stop_loss=?,protection_take_profit=?,protection_target_r=?,protection_tick_size=?,updated_at=? WHERE id=?",
                (float(stop_loss), float(take_profit), float(target_r),
                 float(tick_size) if tick_size is not None else None, _now(), order_id),
            )
            self._c.commit()
        return self.order(order_id)

    def _price(self, side: str, raw: float) -> float:
        adverse = (self.spread_bps / 2 + self.slippage_bps) / 10_000
        return raw * (1 + adverse if side == "buy" else 1 - adverse)

    def _candidate_price(self, row: dict, bar: dict) -> Optional[float]:
        side, typ = row["side"], row["type"]
        if typ == "market":
            return bar["open"]
        if typ == "limit":
            limit = row["limit_price"]
            if side == "buy" and bar["low"] <= limit:
                return min(bar["open"], limit)
            if side == "sell" and bar["high"] >= limit:
                return max(bar["open"], limit)
        if typ in {"stop", "stop_limit"}:
            stop = row["stop_price"]
            triggered = row["status"] == "triggered" or (side == "buy" and bar["high"] >= stop) or (side == "sell" and bar["low"] <= stop)
            if not triggered:
                return None
            if typ == "stop":
                return max(bar["open"], stop) if side == "buy" else min(bar["open"], stop)
            limit = row["limit_price"]
            if side == "buy" and bar["low"] <= limit:
                return min(bar["open"], limit)
            if side == "sell" and bar["high"] >= limit:
                return max(bar["open"], limit)
            return None
        return None

    def process_candle(self, symbol: str, candle, *, protections: Optional[dict[str, dict]] = None) -> dict:
        """Advance open orders and protective stops using one verified candle."""
        symbol, bar = (symbol or "").upper().replace("/", ""), self._candle(candle)
        events: list[dict] = []
        with self._lock:
            # A trailing-stop order is an explicit close instruction. Its trigger
            # follows the candle's favourable extreme, never a fabricated tick.
            for row in self._c.execute("SELECT * FROM v2_orders WHERE symbol=? AND status IN ('open','partially_filled','triggered')", (symbol,)).fetchall():
                order = dict(row)
                if order["type"] == "trailing_stop":
                    pos = self._c.execute("SELECT * FROM v2_positions WHERE symbol=?", (symbol,)).fetchone()
                    if not pos:
                        continue
                    trigger = (bar["high"] - order["trailing_offset"] if pos["side"] == "long"
                               else bar["low"] + order["trailing_offset"])
                    if (pos["side"] == "long" and bar["low"] <= trigger) or (pos["side"] == "short" and bar["high"] >= trigger):
                        self._fill(order, "sell" if pos["side"] == "long" else "buy", trigger, bar, events, True)
                    continue
                price = self._candidate_price(order, bar)
                if price is None and order["type"] == "stop_limit" and ((order["side"] == "buy" and bar["high"] >= order["stop_price"]) or (order["side"] == "sell" and bar["low"] <= order["stop_price"])):
                    self._c.execute("UPDATE v2_orders SET status='triggered',triggered_at=?,updated_at=? WHERE id=?", (_now(), _now(), order["id"]))
                    continue
                if price is not None:
                    protection = (protections or {}).get(order["id"])
                    if not protection and order.get("protection_stop_loss") is not None:
                        protection = {
                            "stop_loss": order.get("protection_stop_loss"),
                            "take_profit": order.get("protection_take_profit"),
                            "target_r": order.get("protection_target_r"),
                            "tick_size": order.get("protection_tick_size"),
                        }
                    stop_loss = protection.get("stop_loss") if protection else None
                    simulated_fill = self._price(order["side"], price)
                    invalid_stop_geometry = stop_loss is not None and (
                        (order["side"] == "buy" and simulated_fill <= float(stop_loss)) or
                        (order["side"] == "sell" and simulated_fill >= float(stop_loss))
                    )
                    if not order["reduce_only"] and invalid_stop_geometry:
                        self._c.execute(
                            "UPDATE v2_orders SET status='rejected',reason=?,updated_at=? WHERE id=?",
                            ("simulated gap/slippage crossed the protective stop; entry rejected fail-closed",
                             _now(), order["id"]),
                        )
                        continue
                    self._fill(order, order["side"], price, bar, events, bool(order["reduce_only"]))
                    if protection and any(event.get("order_id") == order["id"] for event in events):
                        position = self._c.execute(
                            "SELECT * FROM v2_positions WHERE symbol=?", (symbol,)
                        ).fetchone()
                        stop_loss, take_profit = self._resolved_protection(
                            dict(position) if position else None, protection
                        )
                        self._c.execute(
                            "UPDATE v2_positions SET stop_loss=?,take_profit=? WHERE symbol=?",
                            (stop_loss, take_profit, symbol),
                        )
                        for event in events:
                            if event.get("order_id") == order["id"]:
                                event["stop_loss"] = stop_loss
                                event["take_profit"] = take_profit
                                event["risk_reward"] = protection.get("target_r")
            events.extend(self._process_protection(symbol, bar))
            self._c.commit()
        return {"symbol": symbol, "events": events, "account": self.account({symbol: bar["close"]})}

    @staticmethod
    def _resolved_protection(position: Optional[dict], protection: dict) -> tuple[Optional[float], Optional[float]]:
        """Resolve protection from the actual simulated fill when a frozen R is supplied."""
        stop_loss = protection.get("stop_loss")
        take_profit = protection.get("take_profit")
        target_r = protection.get("target_r")
        if not position or stop_loss is None or target_r is None:
            return stop_loss, take_profit
        entry, stop, ratio = (
            float(position["entry_price"]), float(stop_loss), float(target_r)
        )
        if ratio <= 0 or entry == stop:
            raise ValueError("risk-reward protection requires a positive stop distance and target R")
        is_long = position["side"] == "long"
        if (is_long and stop >= entry) or (not is_long and stop <= entry):
            raise ValueError("protective stop is on the wrong side of the simulated fill")
        raw_target = entry + abs(entry - stop) * ratio * (1 if is_long else -1)
        tick = float(protection.get("tick_size") or 0)
        if tick > 0:
            units = (math.ceil(raw_target / tick - 1e-12) if is_long
                     else math.floor(raw_target / tick + 1e-12))
            raw_target = units * tick
        return stop, round(raw_target, 12)

    def _process_protection(self, symbol: str, bar: dict) -> list[dict]:
        pos = self._c.execute("SELECT * FROM v2_positions WHERE symbol=?", (symbol,)).fetchone()
        if not pos:
            return []
        p, events = dict(pos), []
        if p["side"] == "long":
            peak = max(p["peak_price"] or p["entry_price"], bar["high"])
            stop = max(p["stop_loss"] or float("-inf"), peak - p["trailing_offset"]) if p["trailing_offset"] else p["stop_loss"]
            hit = stop is not None and bar["low"] <= stop
            raw = min(bar["open"], stop) if hit else None
            exit_side = "sell"
        else:
            peak = min(p["peak_price"] or p["entry_price"], bar["low"])
            stop = min(p["stop_loss"] or float("inf"), peak + p["trailing_offset"]) if p["trailing_offset"] else p["stop_loss"]
            hit = stop is not None and bar["high"] >= stop
            raw = max(bar["open"], stop) if hit else None
            exit_side = "buy"
        self._c.execute("UPDATE v2_positions SET peak_price=? WHERE symbol=?", (peak, symbol))
        target = p.get("take_profit")
        target_hit = target is not None and ((p["side"] == "long" and bar["high"] >= target) or (p["side"] == "short" and bar["low"] <= target))
        if hit or target_hit:
            raw = raw if hit else (max(bar["open"], target) if p["side"] == "long" else min(bar["open"], target))
            order = {"id": "protective-" + _id(), "symbol": symbol, "remaining": p["size"], "filled": 0,
                     "quantity": p["size"], "reduce_only": 1, "type": "stop", "side": exit_side}
            self._fill(order, exit_side, raw, bar, events, True, persisted=False)
        return events

    def _fill(self, order: dict, side: str, raw_price: float, bar: dict, events: list, reduce_only: bool, *, persisted: bool = True) -> None:
        quantity = min(float(order["remaining"]), max(0.0, bar["volume"] * self.participation_rate))
        if quantity <= 0:
            return
        pos = self._c.execute("SELECT * FROM v2_positions WHERE symbol=?", (order["symbol"],)).fetchone()
        if reduce_only:
            if not pos or (pos["side"] == "long") == (side == "buy"):
                if persisted:
                    self._c.execute("UPDATE v2_orders SET status='rejected',reason=?,updated_at=? WHERE id=?", ("reduce-only has no opposing position", _now(), order["id"]))
                return
            quantity = min(quantity, float(pos["size"]))
        price = self._price(side, raw_price)
        fee = quantity * price * self.fee_rate
        if not reduce_only and quantity * price / self.leverage + fee > self.account()["free_margin"]:
            if persisted:
                self._c.execute("UPDATE v2_orders SET status='rejected',reason=?,updated_at=? WHERE id=?", ("insufficient free margin at fill", _now(), order["id"]))
            return
        pnl = self._apply_position(order["symbol"], side, quantity, price, reduce_only)
        account = self._account_row()
        self._c.execute("UPDATE v2_account SET balance=?,fees_paid=?,realized_pnl=?,updated_at=? WHERE id=1",
                        (account["balance"] + pnl - fee, account["fees_paid"] + fee,
                         account["realized_pnl"] + pnl, _now()))
        fid = _id()
        self._c.execute("INSERT INTO v2_fills VALUES (?,?,?,?,?,?,?,?,?)",
                        (fid, order["id"], order["symbol"], side, quantity, price, fee, pnl, _now()))
        if persisted:
            filled, remaining = float(order["filled"]) + quantity, float(order["remaining"]) - quantity
            status = "filled" if remaining <= 1e-12 else "partially_filled"
            avg = ((float(order["average_price"] or 0) * float(order["filled"]) + price * quantity) / filled)
            self._c.execute("UPDATE v2_orders SET filled=?,remaining=?,average_price=?,status=?,updated_at=? WHERE id=?",
                            (filled, max(0.0, remaining), avg, status, _now(), order["id"]))
        events.append({"type": "fill", "order_id": order["id"], "symbol": order["symbol"], "side": side,
                       "quantity": quantity, "price": price, "fee": fee, "realized_pnl": pnl})

    def _apply_position(self, symbol: str, side: str, qty: float, price: float, reduce_only: bool) -> float:
        pos = self._c.execute("SELECT * FROM v2_positions WHERE symbol=?", (symbol,)).fetchone()
        direction = "long" if side == "buy" else "short"
        if not pos:
            if reduce_only:
                return 0.0
            self._c.execute("INSERT INTO v2_positions VALUES (?,?,?,?,?,?,?,?,?)",
                            (symbol, direction, qty, price, None, None, None, price, _now()))
            return 0.0
        p = dict(pos)
        if p["side"] == direction and not reduce_only:
            size = p["size"] + qty
            entry = (p["entry_price"] * p["size"] + price * qty) / size
            self._c.execute("UPDATE v2_positions SET size=?,entry_price=?,peak_price=? WHERE symbol=?",
                            (size, entry, entry, symbol))
            return 0.0
        close_qty = min(qty, p["size"])
        pnl = (price - p["entry_price"]) * close_qty * (1 if p["side"] == "long" else -1)
        remaining = p["size"] - close_qty
        if remaining <= 1e-12:
            self._c.execute("DELETE FROM v2_positions WHERE symbol=?", (symbol,))
        else:
            self._c.execute("UPDATE v2_positions SET size=? WHERE symbol=?", (remaining, symbol))
        if qty > close_qty and not reduce_only:
            self._c.execute("INSERT OR REPLACE INTO v2_positions VALUES (?,?,?,?,?,?,?,?,?)",
                            (symbol, direction, qty - close_qty, price, None, None, None, price, _now()))
        return pnl

    def set_protection(self, symbol: str, *, stop_loss: Optional[float] = None,
                       take_profit: Optional[float] = None, trailing_offset: Optional[float] = None) -> dict:
        if any(v is not None and v <= 0 for v in (stop_loss, take_profit, trailing_offset)):
            raise ValueError("protection prices/offset must be positive")
        with self._lock:
            pos = self._c.execute("SELECT * FROM v2_positions WHERE symbol=?", (symbol.upper(),)).fetchone()
            if not pos:
                raise ValueError("no open position")
            self._c.execute("UPDATE v2_positions SET stop_loss=COALESCE(?,stop_loss),take_profit=COALESCE(?,take_profit),trailing_offset=COALESCE(?,trailing_offset) WHERE symbol=?",
                            (stop_loss, take_profit, trailing_offset, symbol.upper()))
            self._c.commit()
        return next(p for p in self.positions() if p["symbol"] == symbol.upper())

    def close_position_at_mark(self, symbol: str, mark_price: float, *,
                               reason: str) -> dict:
        """Close one PAPER position using a reconciled provider mark as reference.

        The caller owns market-data reconciliation.  The broker still applies
        its configured paper spread/slippage, so the returned fill price may be
        slightly worse than the reference mark.  No order is sent to an
        exchange and the original position must be archived by the caller.
        """
        symbol, mark = symbol.upper(), float(mark_price)
        if mark <= 0:
            raise ValueError("a positive reconciled paper mark price is required")
        if reason != "LEGACY_POSITION_REMEDIATION":
            raise ValueError("unsupported paper position close reason")
        with self._lock:
            position = self._c.execute(
                "SELECT * FROM v2_positions WHERE symbol=?", (symbol,)
            ).fetchone()
            if not position:
                raise ValueError("no open paper position")
            original = dict(position)
            side = "sell" if original["side"] == "long" else "buy"
            event_order_id = "remediation-" + _id()
            events: list[dict] = []
            # Supply exactly enough synthetic candle volume to close the known
            # paper quantity.  Price discovery still comes exclusively from
            # the reconciled mark supplied by the runtime.
            volume = max(
                float(original["size"]) / max(self.participation_rate, 1e-9),
                float(original["size"]),
            )
            order = {
                "id": event_order_id, "symbol": symbol, "side": side,
                "remaining": original["size"], "filled": 0.0,
                "quantity": original["size"], "reduce_only": 1,
                "type": "market",
            }
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._fill(
                    order, side, mark,
                    {"open": mark, "high": mark, "low": mark,
                     "close": mark, "volume": volume},
                    events, True, persisted=False,
                )
                if self._c.execute(
                        "SELECT 1 FROM v2_positions WHERE symbol=?", (symbol,)
                ).fetchone():
                    raise RuntimeError("paper remediation did not fully close the position")
                self._c.commit()
            except Exception:
                self._c.rollback()
                raise
        if not events:
            raise RuntimeError("paper remediation produced no fill")
        fill = next((row for row in self.fills(limit=20)
                     if row["order_id"] == event_order_id), None)
        return {
            "closed": True, "execution_mode": "PAPER",
            "real_execution_allowed": False, "reason": reason,
            "reference_mark_price": mark, "original_position": original,
            "event": events[-1], "fill": fill,
        }

    def apply_funding(self, symbol: str, rate: float, mark_price: float) -> dict:
        """Book a known provider funding rate; callers must supply the real rate."""
        with self._lock:
            pos = self._c.execute("SELECT * FROM v2_positions WHERE symbol=?", (symbol.upper(),)).fetchone()
            if not pos:
                return {"applied": False, "reason": "no position"}
            amount = pos["size"] * float(mark_price) * float(rate) * (1 if pos["side"] == "long" else -1)
            a = self._account_row()
            self._c.execute("UPDATE v2_account SET balance=?,funding_paid=?,updated_at=? WHERE id=1",
                            (a["balance"] - amount, a["funding_paid"] + amount, _now()))
            self._c.commit()
        return {"applied": True, "symbol": symbol.upper(), "funding": amount}

    def process_mark(self, symbol: str, mark_price: float) -> dict:
        """Apply the documented, conservative paper liquidation estimate."""
        symbol, mark = symbol.upper(), float(mark_price)
        if mark <= 0:
            raise ValueError("mark price must be positive")
        with self._lock:
            position = next((row for row in self.positions() if row["symbol"] == symbol), None)
            if not position or position["estimated_liquidation_price"] is None:
                return {"liquidated": False, "symbol": symbol}
            boundary = float(position["estimated_liquidation_price"])
            hit = mark <= boundary if position["side"] == "long" else mark >= boundary
            if not hit:
                return {"liquidated": False, "symbol": symbol, "estimated_liquidation_price": boundary}
            side = "sell" if position["side"] == "long" else "buy"
            events: list[dict] = []
            order = {"id": "liquidation-" + _id(), "symbol": symbol,
                     "remaining": position["size"], "filled": 0.0,
                     "quantity": position["size"], "reduce_only": 1,
                     "type": "market", "side": side}
            self._fill(order, side, mark,
                       {"open": mark, "high": mark, "low": mark, "close": mark,
                        "volume": max(position["size"] / max(self.participation_rate, 1e-9), position["size"])},
                       events, True, persisted=False)
            self._c.commit()
            return {"liquidated": True, "symbol": symbol,
                    "estimated_liquidation_price": boundary, "mark_price": mark,
                    "model": "isolated maintenance estimate; not Binance account liquidation", "events": events}

    def export_state(self) -> dict:
        """Return a complete JSON-safe snapshot used by resumable PA sessions."""
        with self._lock:
            return {
                "account": dict(self._account_row()),
                "positions": [dict(row) for row in self._c.execute("SELECT * FROM v2_positions")],
                "orders": [dict(row) for row in self._c.execute("SELECT * FROM v2_orders")],
                "fills": [dict(row) for row in self._c.execute("SELECT * FROM v2_fills")],
                "leverage": self.leverage,
                "costs": {"fee_rate": self.fee_rate, "spread_bps": self.spread_bps,
                          "slippage_bps": self.slippage_bps,
                          "participation_rate": self.participation_rate},
            }

    def restore_state(self, snapshot: dict) -> None:
        """Atomically restore a previously exported state without changing schema."""
        required = {"account", "positions", "orders", "fills"}
        if not required.issubset(snapshot):
            raise ValueError("incomplete paper broker snapshot")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                for table in ("v2_fills", "v2_orders", "v2_positions"):
                    self._c.execute(f"DELETE FROM {table}")
                account = snapshot["account"]
                self._c.execute(
                    "UPDATE v2_account SET starting_balance=?,balance=?,fees_paid=?,funding_paid=?,realized_pnl=?,peak_equity=?,max_drawdown=?,updated_at=? WHERE id=1",
                    (account["starting_balance"], account["balance"], account["fees_paid"],
                     account["funding_paid"], account.get("realized_pnl", 0),
                     account.get("peak_equity", account["balance"]), account.get("max_drawdown", 0), _now()),
                )
                for row in snapshot["positions"]:
                    keys = ("symbol", "side", "size", "entry_price", "stop_loss", "take_profit",
                            "trailing_offset", "peak_price", "opened_at")
                    self._c.execute("INSERT INTO v2_positions VALUES (?,?,?,?,?,?,?,?,?)", tuple(row.get(k) for k in keys))
                for row in snapshot["orders"]:
                    keys = ("id", "symbol", "side", "type", "quantity", "remaining", "filled",
                            "average_price", "limit_price", "stop_price", "trailing_offset", "reduce_only",
                            "status", "reason", "created_at", "updated_at", "triggered_at",
                            "protection_stop_loss", "protection_take_profit",
                            "protection_target_r", "protection_tick_size")
                    self._c.execute(
                        "INSERT INTO v2_orders(id,symbol,side,type,quantity,remaining,filled,average_price,limit_price,stop_price,trailing_offset,reduce_only,status,reason,created_at,updated_at,triggered_at,protection_stop_loss,protection_take_profit,protection_target_r,protection_tick_size) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        tuple(row.get(k) for k in keys),
                    )
                for row in snapshot["fills"]:
                    keys = ("id", "order_id", "symbol", "side", "quantity", "price", "fee", "realized_pnl", "timestamp")
                    self._c.execute("INSERT INTO v2_fills VALUES (?,?,?,?,?,?,?,?,?)", tuple(row.get(k) for k in keys))
                self.leverage = max(1.0, float(snapshot.get("leverage", 1)))
                self._c.commit()
            except Exception:
                self._c.rollback()
                raise
