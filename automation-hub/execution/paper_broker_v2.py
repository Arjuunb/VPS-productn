"""Candle-driven, persistent paper broker for the V2 paper-trading path.

The legacy ``PaperExecutionEngine`` remains the signal-driven compatibility
engine.  This broker accepts explicit orders and only advances them when given
a *real* OHLCV candle from ``MarketDataService``.  It deliberately has no price
generator and no client-provided fill-price escape hatch.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


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
        self._c = sqlite3.connect(self.path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
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
        return {"starting_balance": a["starting_balance"], "balance": round(a["balance"], 8),
                "equity": round(equity, 8), "unrealized_pnl": round(unrealized, 8),
                "used_margin": round(used, 8), "margin": round(used, 8),
                "free_margin": round(equity - used, 8), "buying_power": round(max(0.0, equity - used) * self.leverage, 8),
                "fees_paid": round(a["fees_paid"], 8), "funding_paid": round(a["funding_paid"], 8),
                "leverage": self.leverage, "positions": len(positions)}

    def positions(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._c.execute("SELECT * FROM v2_positions ORDER BY opened_at")]

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

    def submit(self, *, symbol: str, side: str, order_type: str, quantity: float,
               limit_price: Optional[float] = None, stop_price: Optional[float] = None,
               trailing_offset: Optional[float] = None, reduce_only: bool = False,
               market_open: bool = True) -> dict:
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
            self._c.execute("INSERT INTO v2_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (oid, symbol, side, order_type, float(quantity), float(quantity), 0.0, None,
                             limit_price, stop_price, trailing_offset, int(reduce_only), "open", None, now, now, None))
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

    def process_candle(self, symbol: str, candle) -> dict:
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
                    self._fill(order, order["side"], price, bar, events, bool(order["reduce_only"]))
            events.extend(self._process_protection(symbol, bar))
            self._c.commit()
        return {"symbol": symbol, "events": events, "account": self.account({symbol: bar["close"]})}

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
        self._c.execute("UPDATE v2_account SET balance=?,fees_paid=?,updated_at=? WHERE id=1",
                        (account["balance"] + pnl - fee, account["fees_paid"] + fee, _now()))
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
