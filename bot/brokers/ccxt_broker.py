"""Crypto broker via the ccxt library.

Install:  pip install ccxt

Notes
-----
* ``submit_order`` persists and submits only the entry. Protective orders are
  created by ``protect_filled_entry`` after a fill is confirmed; every exit is
  reduce-only. This prevents an unfilled limit entry from leaving naked reverse
  orders on the venue.
* ``get_account`` marks each non-quote holding to its **last price** before
  summing into equity.  USDT is treated as the quote/cash currency.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from bot.brokers.base import Broker
from bot.brokers.order_state import OrderStateStore
from bot.types import AccountSnapshot, Bar, Fill, Order, OrderType, Position, Side

log = logging.getLogger("bot.ccxt")


_TIMEFRAME_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


class CCXTBroker(Broker):
    def __init__(
        self,
        exchange_id: str = "binance",
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        sandbox: bool = True,
        quote_currency: str = "USDT",
        state_path: Optional[str] = None,
    ):
        import ccxt   # lazy import — stays optional
        klass = getattr(ccxt, exchange_id)
        self._x = klass({
            "apiKey": api_key or "",
            "secret": api_secret or "",
            "enableRateLimit": True,
        })
        if sandbox and hasattr(self._x, "set_sandbox_mode"):
            self._x.set_sandbox_mode(True)
        self._exchange_id = exchange_id
        self._quote = quote_currency
        # entry_id -> {"sl_id": ..., "tp_id": ..., "symbol": ...}
        self._brackets: dict[str, dict] = {}
        self._rules: dict[str, object] = {}       # symbol -> SymbolRules cache
        durable_path = state_path or os.environ.get("HUB_ORDER_STATE_DB", "")
        if not durable_path:
            raise ValueError(
                "CCXTBroker requires state_path or HUB_ORDER_STATE_DB; "
                "external execution cannot use volatile order state"
            )
        self._state = OrderStateStore(durable_path)
        # Retained as a compatibility view for callers/tests that inspected it;
        # durable idempotency is enforced by ``self._state``.
        self._submitted_client_ids: set[str] = {
            row["client_id"] for row in self._state.open_orders()
        }
        self._restore_brackets()

    def _order_state(self) -> OrderStateStore:
        """Return the durable store (lazy in tests that bypass ``__init__``)."""
        state = getattr(self, "_state", None)
        if state is None:
            state = self._state = OrderStateStore(":memory:")
        return state

    def _restore_brackets(self) -> None:
        for row in self._order_state().open_orders():
            if row["state"] == "PROTECTION_ACCEPTED" and row.get("entry_order_id"):
                self._brackets[row["entry_order_id"]] = {
                    "sl_id": row.get("stop_order_id"),
                    "tp_id": row.get("target_order_id"),
                    "symbol": row["symbol"],
                    "client_id": row["client_id"],
                }

    # ------------------------------------------------------------ symbol rules
    def rules_for(self, symbol: str):
        """Exchange filters for a symbol (lot/tick/min-notional), cached.
        Falls back to unconstrained rules if the market can't be loaded."""
        from bot.brokers.symbol_rules import SymbolRules, from_ccxt
        if symbol not in self._rules:
            try:
                self._x.load_markets()
                self._rules[symbol] = from_ccxt(self._x.market(symbol))
            except Exception as e:
                log.warning("Could not load market rules for %s: %s", symbol, e)
                return SymbolRules(symbol=symbol)
        return self._rules[symbol]

    @property
    def name(self) -> str:
        return f"ccxt:{self._exchange_id}"

    # ------------------------------------------------------------------ data
    def get_historical_bars(self, symbol, timeframe, start, end=None, limit=None):
        since = int(start.timestamp() * 1000)
        out: list[Bar] = []
        step = _TIMEFRAME_MS.get(timeframe, 3_600_000)
        while True:
            chunk = self._x.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
            if not chunk:
                break
            for ts, o, h, l, c, v in chunk:
                bar_ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                if end and bar_ts > end:
                    return out
                out.append(Bar(bar_ts, o, h, l, c, v))
            since = chunk[-1][0] + step
            if limit and len(out) >= limit:
                return out[:limit]
            if len(chunk) < 1000:
                break
        return out

    def stream_bars(self, symbol: str, timeframe: str) -> Iterable[Bar]:
        import time
        last_ts = None
        step = _TIMEFRAME_MS.get(timeframe, 60_000) / 1000
        while True:
            bars = self._x.fetch_ohlcv(symbol, timeframe, limit=2)
            if bars and len(bars) >= 2:
                ts, o, h, l, c, v = bars[-2]
                if ts != last_ts:
                    last_ts = ts
                    yield Bar(datetime.fromtimestamp(ts / 1000, tz=timezone.utc),
                              o, h, l, c, v)
            time.sleep(max(1.0, step / 4))

    # ---------------------------------------------------------------- account
    def get_account(self) -> AccountSnapshot:
        bal = self._x.fetch_balance()
        total = bal.get("total", {})
        cash = float(total.get(self._quote, 0.0))
        equity = cash
        positions: list[Position] = []
        for asset, qty in total.items():
            qty = float(qty or 0.0)
            if asset == self._quote or not qty:
                continue
            symbol = f"{asset}/{self._quote}"
            # Mark to last traded price.
            try:
                ticker = self._x.fetch_ticker(symbol)
                last_px = float(ticker.get("last") or ticker.get("close") or 0.0)
            except Exception as e:
                log.warning("Could not fetch ticker for %s: %s", symbol, e)
                last_px = 0.0
            positions.append(Position(symbol=symbol, qty=qty, avg_price=last_px))
            equity += qty * last_px
        return AccountSnapshot(cash=cash, equity=equity, positions=positions)

    def get_position(self, symbol: str) -> Optional[Position]:
        for p in self.get_account().positions:
            if p.symbol == symbol:
                return p
        return None

    def _last_price(self, symbol: str) -> float:
        try:
            t = self._x.fetch_ticker(symbol)
            return float(t.get("last") or t.get("close") or 0.0)
        except Exception as e:
            log.warning("No reference price for %s: %s", symbol, e)
            return 0.0

    # ----------------------------------------------------------------- orders
    def submit_order(self, order: Order) -> str:
        """Persist and submit an entry; never place exits before a confirmed fill."""
        side = order.side.value
        type_ = order.order_type.value

        # Exchange symbol filters: floor qty to lot size, prices to tick size,
        # and refuse orders below the minimum notional BEFORE the API rejects
        # them (the #1 first-live-order failure).
        rules = self.rules_for(order.symbol)
        ref_price = order.limit_price or self._last_price(order.symbol)
        qty, why = rules.clamp(order.qty, ref_price or 0.0)
        if qty <= 0:
            raise ValueError(f"Order violates exchange filters: {why}")
        limit_price = rules.round_price(order.limit_price)
        sl_price = rules.round_price(order.stop_loss)
        tp_price = rules.round_price(order.take_profit)
        if sl_price is None or tp_price is None:
            raise ValueError("External entries require both stop_loss and take_profit")

        client_id = order.client_id or f"nexus-{uuid.uuid4().hex}"
        order.client_id = client_id
        existing, created = self._order_state().create_intent(
            client_id=client_id, symbol=order.symbol, side=side,
            requested_qty=qty, limit_price=limit_price,
            stop_loss=sl_price, take_profit=tp_price,
        )
        if not created:
            log.warning("Duplicate client order id %s — returning durable state %s",
                        client_id, existing["state"])
            return str(existing.get("entry_order_id") or f"duplicate:{client_id}")
        self._submitted_client_ids.add(client_id)

        params = {"clientOrderId": client_id}
        try:
            result = self._x.create_order(
                symbol=order.symbol, type=type_, side=side,
                amount=qty, price=limit_price, params=params,
            )
        except Exception as exc:
            log.exception("Entry order failed on %s", order.symbol)
            # A transport exception is not proof the venue rejected the order:
            # it may have accepted it before the response was lost. Preserve the
            # INTENT so startup reconciliation can locate it by clientOrderId;
            # marking it CANCELLED here would permit a duplicate entry retry.
            self._order_state().transition(
                client_id, "INTENT",
                last_error=f"entry submission outcome unknown: {type(exc).__name__}: {exc}",
            )
            raise

        entry_id = str(result.get("id") or "")
        if not entry_id:
            self._order_state().transition(client_id, "CANCELLED",
                                           last_error="exchange returned no entry order id")
            raise RuntimeError("Exchange accepted entry without returning an order id")
        self._order_state().transition(
            client_id, "ENTRY_ACCEPTED", entry_order_id=entry_id,
        )
        # Some venues return a synchronously completed market order. Protection
        # is still fill-driven: it is created only when the response proves fill.
        immediate_fill = self._confirmed_fill_qty(result)
        if immediate_fill > 0:
            self.protect_filled_entry(entry_id, immediate_fill)
        return entry_id

    @staticmethod
    def _confirmed_fill_qty(result: dict) -> float:
        filled = float(result.get("filled") or 0.0)
        # ``filled`` is the venue's authoritative cumulative execution quantity.
        # A limit order can remain ``open`` after a partial fill; waiting for a
        # terminal status would leave that real exposure unprotected until the
        # next reconciliation poll. Any positive confirmed quantity therefore
        # enters the same cancel-remainder -> protect-exact-fill state path.
        return filled if filled > 0 else 0.0

    def protect_filled_entry(self, entry_order_id: str, filled_qty: float) -> dict:
        """Cancel any unfilled remainder and protect exactly the confirmed fill."""
        row = self._order_state().by_entry_id(entry_order_id)
        if row is None:
            raise KeyError(f"Unknown entry order {entry_order_id}")
        if row["state"] == "PROTECTION_ACCEPTED":
            return row
        if row["state"] not in {"ENTRY_ACCEPTED", "FILLED"}:
            raise RuntimeError(f"Cannot protect entry in state {row['state']}")
        qty = min(float(filled_qty), float(row["requested_qty"]))
        if qty <= 0:
            raise ValueError("A positive confirmed fill quantity is required")
        if qty < float(row["requested_qty"]) and row["state"] == "ENTRY_ACCEPTED":
            # Freeze exposure before sizing protection; later fills must not race
            # a bracket sized to an earlier partial quantity.
            try:
                self._x.cancel_order(entry_order_id, row["symbol"])
                refreshed = self._x.fetch_order(entry_order_id, row["symbol"])
                status = str(refreshed.get("status") or "").lower()
                if status not in {"cancelled", "canceled", "closed", "filled"}:
                    raise RuntimeError(f"entry remainder cancellation is unconfirmed ({status or 'unknown'})")
                qty = min(float(refreshed.get("filled") or qty), float(row["requested_qty"]))
            except Exception as exc:
                exit_side = "sell" if row["side"] == Side.BUY.value else "buy"
                flatten_id = "FAILED"
                try:
                    flatten_id = self._emergency_flatten(row, qty, exit_side)
                except Exception as flatten_exc:
                    exc = RuntimeError(f"{exc}; flatten_failed={flatten_exc}")
                if row["state"] == "ENTRY_ACCEPTED":
                    row = self._order_state().transition(
                        row["client_id"], "FILLED", filled_qty=qty,
                    )
                self._order_state().transition(
                    row["client_id"], "HALTED_UNPROTECTED", filled_qty=qty,
                    last_error=(f"partial-fill remainder could not be frozen: {exc}; "
                                f"emergency_flatten={flatten_id}"),
                )
                raise RuntimeError(
                    f"Partial entry {entry_order_id} could not be frozen; execution halted"
                ) from exc
        if row["state"] == "ENTRY_ACCEPTED":
            row = self._order_state().transition(
                row["client_id"], "FILLED", filled_qty=qty,
            )

        exit_side = "sell" if row["side"] == Side.BUY.value else "buy"
        sl_id = row.get("stop_order_id")
        tp_id = row.get("target_order_id")
        try:
            common = {"reduceOnly": True}
            if not sl_id:
                sl_id = self._submit_protection_leg(
                    row, kind="sl", type_="stop_market", exit_side=exit_side,
                    qty=qty, trigger=row["stop_loss"], common=common,
                )
                row = self._order_state().transition(
                    row["client_id"], "FILLED", filled_qty=qty,
                    stop_order_id=sl_id,
                )
            if not tp_id:
                tp_id = self._submit_protection_leg(
                    row, kind="tp", type_="take_profit_market", exit_side=exit_side,
                    qty=qty, trigger=row["take_profit"], common=common,
                )
                row = self._order_state().transition(
                    row["client_id"], "FILLED", filled_qty=qty,
                    target_order_id=tp_id,
                )
        except Exception as exc:
            log.exception("Protection failed on %s; cancelling leg and flattening", row["symbol"])
            if sl_id:
                self._safe_cancel(sl_id, row["symbol"])
            flatten_id = "FAILED"
            flatten_error = ""
            try:
                flatten_id = self._emergency_flatten(row, qty, exit_side)
            except Exception as flatten_exc:
                flatten_error = f"; flatten_failed={flatten_exc}"
            self._order_state().transition(
                row["client_id"], "HALTED_UNPROTECTED", filled_qty=qty,
                stop_order_id=sl_id, target_order_id=tp_id,
                last_error=(f"protection failed: {exc}; emergency_flatten={flatten_id}"
                            f"{flatten_error}"),
            )
            outcome = ("emergency reduce-only flatten submitted"
                       if flatten_id != "FAILED" else "EMERGENCY FLATTEN FAILED")
            raise RuntimeError(f"Protection failed for {entry_order_id}; {outcome}") from exc

        protected = self._order_state().transition(
            row["client_id"], "PROTECTION_ACCEPTED", filled_qty=qty,
            stop_order_id=sl_id, target_order_id=tp_id, last_error="",
        )
        self._brackets[entry_order_id] = {
            "sl_id": sl_id, "tp_id": tp_id, "symbol": row["symbol"],
            "client_id": row["client_id"],
        }
        return protected

    def _submit_protection_leg(self, row: dict, *, kind: str, type_: str,
                               exit_side: str, qty: float, trigger: float,
                               common: dict) -> str:
        """Create one leg idempotently, recovering a pre-crash venue order."""
        client_id = f"{row['client_id']}:{kind}"
        existing = self._find_order_by_client_id(row["symbol"], client_id)
        if existing:
            order_id = str(existing.get("id") or "")
            if order_id:
                return order_id
        result = self._x.create_order(
            row["symbol"], type_, exit_side, qty, None,
            {**common, "stopPrice": trigger, "clientOrderId": client_id},
        )
        order_id = str(result.get("id") or "")
        if not order_id:
            raise RuntimeError(f"exchange returned no {kind} order id")
        return order_id

    def _find_order_by_client_id(self, symbol: str, client_id: str) -> Optional[dict]:
        """Best-effort venue lookup used only to recover crash boundaries."""
        for method_name in ("fetch_open_orders", "fetch_orders"):
            method = getattr(self._x, method_name, None)
            if not callable(method):
                continue
            try:
                rows = method(symbol)
            except Exception:
                continue
            for order in rows or []:
                candidate = (order.get("clientOrderId")
                             or (order.get("info") or {}).get("clientOrderId")
                             or (order.get("info") or {}).get("clientOid"))
                if str(candidate or "") == client_id:
                    return order
        return None

    def _emergency_flatten(self, row: dict, qty: float, exit_side: str) -> str:
        result = self._x.create_order(
            row["symbol"], "market", exit_side, qty, None,
            {"reduceOnly": True, "clientOrderId": f"{row['client_id']}:flatten"},
        )
        flatten_id = str(result.get("id") or "")
        if not flatten_id:
            raise RuntimeError("Emergency flatten returned no order id")
        return flatten_id

    def refresh_entry(self, entry_order_id: str) -> dict:
        """Reconcile one accepted entry with the venue and attach protection."""
        row = self._order_state().by_entry_id(entry_order_id)
        if row is None:
            raise KeyError(f"Unknown entry order {entry_order_id}")
        if row["state"] != "ENTRY_ACCEPTED":
            return row
        result = self._x.fetch_order(entry_order_id, row["symbol"])
        filled = float(result.get("filled") or 0.0)
        if filled > 0:
            return self.protect_filled_entry(entry_order_id, filled)
        return row

    def cancel_unfilled_entry(self, entry_order_id: str) -> dict:
        """Resolve a timed-out accepted entry before the runner can continue."""
        row = self._order_state().by_entry_id(entry_order_id)
        if row is None:
            raise KeyError(f"Unknown entry order {entry_order_id}")
        if row["state"] != "ENTRY_ACCEPTED":
            return row
        result = self._x.fetch_order(entry_order_id, row["symbol"])
        filled = float(result.get("filled") or 0.0)
        if filled > 0:
            return self.protect_filled_entry(entry_order_id, filled)
        self._x.cancel_order(entry_order_id, row["symbol"])
        # Close the fetch/cancel race: an entry can fill between the first
        # observation and cancellation acknowledgement.
        settled = self._x.fetch_order(entry_order_id, row["symbol"])
        status = str(settled.get("status") or "").lower()
        settled_fill = min(float(settled.get("filled") or 0.0),
                           float(row["requested_qty"]))
        if settled_fill > 0:
            row = self._order_state().transition(
                row["client_id"], "FILLED", filled_qty=settled_fill,
            )
            return self.protect_filled_entry(entry_order_id, settled_fill)
        if status not in {"cancelled", "canceled", "closed"}:
            raise RuntimeError(
                f"Entry {entry_order_id} cancellation is unconfirmed ({status or 'unknown'})"
            )
        return self._order_state().transition(
            row["client_id"], "CANCELLED", last_error="entry timed out without a fill",
        )

    def recover_open_orders(self) -> list[dict]:
        """Reconcile durable intents before any new exposure is permitted.

        Protected brackets are restored locally. Filled-but-unprotected entries
        resume the idempotent leg workflow. Any unresolved intent or emergency
        state raises so the caller fails closed.
        """
        recovered: list[dict] = []
        unresolved: list[str] = []
        for row in self._order_state().open_orders():
            state = row["state"]
            if state == "PROTECTION_ACCEPTED":
                recovered.append(row)
                continue
            if state == "HALTED_UNPROTECTED":
                unresolved.append(f"{row['client_id']} is HALTED_UNPROTECTED")
                continue
            if state == "INTENT":
                venue = self._find_order_by_client_id(row["symbol"], row["client_id"])
                entry_id = str((venue or {}).get("id") or "")
                if not entry_id:
                    unresolved.append(f"{row['client_id']} intent has no confirmed venue order")
                    continue
                row = self._order_state().transition(
                    row["client_id"], "ENTRY_ACCEPTED", entry_order_id=entry_id,
                )
            if row["state"] == "ENTRY_ACCEPTED":
                result = self._x.fetch_order(row["entry_order_id"], row["symbol"])
                filled = float(result.get("filled") or 0.0)
                if filled <= 0:
                    unresolved.append(f"{row['client_id']} entry remains unfilled")
                    continue
                row = self.protect_filled_entry(row["entry_order_id"], filled)
            elif row["state"] == "FILLED":
                row = self.protect_filled_entry(
                    row["entry_order_id"], float(row["filled_qty"]),
                )
            recovered.append(row)
        if unresolved:
            raise RuntimeError("Unresolved durable execution state: " + "; ".join(unresolved))
        return recovered

    def on_fill(self, filled_order_id: str) -> None:
        """Caller hook: when one bracket leg fills, cancel its sibling.

        Searches all tracked brackets for any leg matching the filled id and
        cancels the surviving sibling.
        """
        for entry_id, br in list(self._brackets.items()):
            symbol = br["symbol"]
            if filled_order_id == br.get("sl_id") and br.get("tp_id"):
                self._safe_cancel(br["tp_id"], symbol)
                self._mark_bracket_closed(entry_id, br)
                self._brackets.pop(entry_id, None)
                return
            if filled_order_id == br.get("tp_id") and br.get("sl_id"):
                self._safe_cancel(br["sl_id"], symbol)
                self._mark_bracket_closed(entry_id, br)
                self._brackets.pop(entry_id, None)
                return

    def _mark_bracket_closed(self, entry_id: str, bracket: dict) -> None:
        row = self._order_state().by_entry_id(entry_id)
        if row and row["state"] == "PROTECTION_ACCEPTED":
            self._order_state().transition(row["client_id"], "CLOSED")

    def _safe_cancel(self, order_id: str, symbol: str) -> None:
        try:
            self._x.cancel_order(order_id, symbol)
        except Exception as e:
            log.warning("Failed to cancel sibling %s on %s: %s", order_id, symbol, e)

    def cancel_order(self, order_id: str) -> None:
        # Symbol may be required by some venues; best-effort search.
        symbol = None
        for br in self._brackets.values():
            if order_id in (br.get("sl_id"), br.get("tp_id")):
                symbol = br["symbol"]
                break
        try:
            if symbol:
                self._x.cancel_order(order_id, symbol)
            else:
                self._x.cancel_order(order_id)
        except Exception:
            log.exception("cancel_order failed for %s", order_id)
            raise

    def get_fills(self, since: Optional[datetime] = None) -> list[Fill]:
        since_ms = int(since.timestamp() * 1000) if since else None
        trades = self._x.fetch_my_trades(since=since_ms)
        return [
            Fill(
                order_id=t.get("order", ""),
                symbol=t["symbol"],
                side=Side(t["side"]),
                qty=float(t["amount"]),
                price=float(t["price"]),
                timestamp=datetime.fromtimestamp(t["timestamp"] / 1000, tz=timezone.utc),
                fee=float((t.get("fee") or {}).get("cost", 0.0)),
            )
            for t in trades
        ]
