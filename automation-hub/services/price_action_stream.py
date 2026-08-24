"""Public Binance USD-M stream for the isolated Price Action Visual Lab.

Only public combined streams are used. REST remains authoritative for bootstrap
and gap repair. The class is deliberately injectable so parsing, deduplication,
staleness and recovery can be tested without a network connection.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Callable

from bot.types import Bar
from data.market_data_v2 import TF_MS, normalize_symbol


CONNECTION_STATES = {"CONNECTING", "CONNECTED", "DELAYED", "RECONNECTING", "DISCONNECTED", "ERROR"}
HEALTH_STATES = {
    "CONNECTING", "LOADING_HISTORY", "RECONCILING", "SYNCHRONIZED",
    "DELAYED", "STALE_CANDLES", "STALE_QUOTE", "STALE_MARK",
    "QUOTE_MISMATCH", "RECONNECTING", "DISCONNECTED", "DATA_ERROR", "ERROR",
}


class PriceActionPublicStream:
    def __init__(self, rest_loader: Callable, *, max_bars: int = 1500,
                 stale_after_seconds: float = 15.0,
                 event_sink: Callable[[dict], None] | None = None,
                 bar_sink: Callable[[Bar], None] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 quote_mismatch_bps: float = 100.0):
        self.rest_loader = rest_loader
        self.max_bars = max_bars
        self.stale_after_seconds = stale_after_seconds
        self.event_sink = event_sink
        self.bar_sink = bar_sink
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.quote_mismatch_bps = float(quote_mismatch_bps)
        self.symbol = ""
        self.timeframe = ""
        self.state = "DISCONNECTED"
        self.last_error = ""
        self.last_update: datetime | None = None
        self.last_candle_update: datetime | None = None
        self.last_quote_update: datetime | None = None
        self.last_mark_update: datetime | None = None
        self.last_closed_update: datetime | None = None
        self.reconnect_attempt = 0
        self.duplicate_events = 0
        self.missing_candles = 0
        self.reconciled_candles = 0
        self.history_loaded = False
        self.reconciliation_complete = False
        self._health_state = "DISCONNECTED"
        self._health_reason = "public stream is disconnected"
        self._bars: deque[Bar] = deque(maxlen=max_bars)
        self._forming: Bar | None = None
        self._quote = {"last": None, "bid": None, "ask": None, "mark": None,
                       "funding_rate": None, "next_funding_time": None}
        self._seen_events: set[tuple] = set()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop = None
        self._socket = None

    def _set_state(self, state: str, error: str = "") -> None:
        if state not in CONNECTION_STATES:
            raise ValueError(state)
        changed = state != self.state or error != self.last_error
        self.state, self.last_error = state, error[:500]
        if changed and self.event_sink:
            self.event_sink({"kind": "connection", "state": state, "error": self.last_error,
                             "timestamp": self.clock().isoformat(),
                             "symbol": self.symbol, "timeframe": self.timeframe})

    def _emit_health(self, state: str, reason: str) -> None:
        if state not in HEALTH_STATES:
            raise ValueError(state)
        changed = state != self._health_state or reason != self._health_reason
        self._health_state, self._health_reason = state, reason
        if changed and self.event_sink:
            self.event_sink({
                "kind": "market_data_health", "state": state, "reason": reason,
                "timestamp": self.clock().isoformat(), "symbol": self.symbol,
                "timeframe": self.timeframe,
            })

    @staticmethod
    def _bar(kline: dict) -> Bar:
        return Bar(datetime.fromtimestamp(int(kline["t"]) / 1000, tz=timezone.utc),
                   float(kline["o"]), float(kline["h"]), float(kline["l"]),
                   float(kline["c"]), float(kline.get("v") or 0))

    def bootstrap(self, symbol: str, timeframe: str) -> list[Bar]:
        symbol = normalize_symbol(symbol)
        if timeframe not in TF_MS:
            raise ValueError(f"unsupported timeframe '{timeframe}'")
        rows = self.rest_loader(symbol, timeframe, limit=self.max_bars)
        now = self.clock()
        step = timedelta(milliseconds=TF_MS[timeframe])
        closed = sorted({row.timestamp: row for row in rows if row.timestamp + step <= now}.values(),
                        key=lambda row: row.timestamp)
        with self._lock:
            self.symbol, self.timeframe = symbol, timeframe
            self._bars = deque(closed[-self.max_bars:], maxlen=self.max_bars)
            self._forming = next((row for row in reversed(rows) if row.timestamp + step > now), None)
            self.last_closed_update = closed[-1].timestamp + step if closed else None
            self.history_loaded = bool(closed)
            self.reconciliation_complete = False
            self.last_update = None
            self.last_candle_update = None
            self.last_quote_update = None
            self.last_mark_update = None
            self._quote = {"last": None, "bid": None, "ask": None, "mark": None,
                           "funding_rate": None, "next_funding_time": None}
            self._seen_events.clear()
        return closed

    def start(self, symbol: str, timeframe: str) -> bool:
        symbol = normalize_symbol(symbol)
        if self._thread and self._thread.is_alive() and (symbol, timeframe) == (self.symbol, self.timeframe):
            return True
        self.stop()
        self._stop.clear()
        self._set_state("CONNECTING")
        try:
            self.bootstrap(symbol, timeframe)
        except Exception as exc:
            self._set_state("ERROR", f"REST bootstrap failed: {exc}")
            return False
        self._thread = threading.Thread(target=self._run, name="pa-public-stream", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        loop = self._loop
        socket = self._socket
        if loop and loop.is_running() and socket is not None:
            try:
                asyncio.run_coroutine_threadsafe(socket.close(), loop).result(timeout=2)
            except Exception:
                pass
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=3)
        self._thread = None
        self._set_state("DISCONNECTED")

    @property
    def url(self) -> str:
        lower = self.symbol.lower()
        streams = f"{lower}@kline_{self.timeframe}/{lower}@bookTicker/{lower}@markPrice@1s"
        return f"wss://fstream.binance.com/stream?streams={streams}"

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        try:
            loop.run_until_complete(self._stream_forever())
        finally:
            loop.close()
            self._loop = None
            if not self._stop.is_set():
                self._set_state("ERROR", self.last_error or "stream worker stopped")

    async def _stream_forever(self) -> None:
        try:
            import websockets
        except Exception as exc:
            self._set_state("ERROR", f"websockets unavailable: {exc}")
            return
        while not self._stop.is_set():
            try:
                self._set_state("CONNECTING" if self.reconnect_attempt == 0 else "RECONNECTING")
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20,
                                              close_timeout=5, max_queue=2048) as socket:
                    self._socket = socket
                    self._set_state("CONNECTED")
                    self.reconnect_attempt = 0
                    with self._lock:
                        # Values may remain useful for visual continuity, but no
                        # prior socket receipt may prove the new connection fresh.
                        self.last_candle_update = None
                        self.last_quote_update = None
                        self.last_mark_update = None
                        self.reconciliation_complete = False
                    await self.reconcile()
                    async for raw in socket:
                        if self._stop.is_set():
                            break
                        result = self.ingest_event(json.loads(raw))
                        if result.get("closed") and self.state == "DELAYED":
                            await self.reconcile()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.reconnect_attempt += 1
                self._set_state("RECONNECTING", f"{type(exc).__name__}: {exc}")
                await asyncio.sleep(min(2 ** min(self.reconnect_attempt, 5), 30))
            finally:
                self._socket = None

    async def reconcile(self) -> int:
        """Recover missing closed bars using REST before entries may resume."""
        try:
            rows = await asyncio.to_thread(self.rest_loader, self.symbol, self.timeframe, limit=self.max_bars)
            now = self.clock()
            step = timedelta(milliseconds=TF_MS[self.timeframe])
            count = 0
            for row in sorted(rows, key=lambda item: item.timestamp):
                if row.timestamp + step > now:
                    continue
                with self._lock:
                    known = any(existing.timestamp == row.timestamp for existing in self._bars)
                if not known:
                    if self._merge_closed(row):
                        count += 1
                        if self.bar_sink:
                            self.bar_sink(row)
            self.reconciled_candles += count
            with self._lock:
                self.reconciliation_complete = self._unresolved_gaps() == 0
            self._set_state("CONNECTED")
            return count
        except Exception as exc:
            self._set_state("DELAYED", f"gap reconciliation failed: {exc}")
            with self._lock:
                self.reconciliation_complete = False
            return 0

    def _unresolved_gaps(self) -> int:
        if len(self._bars) < 2 or not self.timeframe:
            return 0
        step = timedelta(milliseconds=TF_MS[self.timeframe])
        return sum(
            max(0, int((right.timestamp - left.timestamp) / step) - 1)
            for left, right in zip(self._bars, list(self._bars)[1:])
        )

    def _message_identity(self, message: dict, data: dict, event: str | None) -> tuple[str | None, str | None]:
        stream = str(message.get("stream") or "")
        stream_symbol = stream.split("@", 1)[0].upper() if "@" in stream else None
        symbol = data.get("s") or (data.get("k") or {}).get("s") or stream_symbol
        timeframe = (data.get("k") or {}).get("i") if event == "kline" else None
        return (normalize_symbol(str(symbol)) if symbol else None, str(timeframe) if timeframe else None)

    def _merge_closed(self, bar: Bar) -> bool:
        with self._lock:
            if any(row.timestamp == bar.timestamp for row in self._bars):
                self.duplicate_events += 1
                return False
            step = timedelta(milliseconds=TF_MS[self.timeframe])
            if self._bars and bar.timestamp > self._bars[-1].timestamp + step:
                self.missing_candles += int((bar.timestamp - self._bars[-1].timestamp) / step) - 1
                self._set_state("DELAYED", "missing completed candle detected; REST reconciliation required")
            if self._bars and bar.timestamp < self._bars[-1].timestamp:
                merged = {row.timestamp: row for row in self._bars}
                merged[bar.timestamp] = bar
                self._bars = deque(sorted(merged.values(), key=lambda row: row.timestamp)[-self.max_bars:],
                                   maxlen=self.max_bars)
            else:
                self._bars.append(bar)
            closed_at = bar.timestamp + step
            self.last_closed_update = max(self.last_closed_update, closed_at) if self.last_closed_update else closed_at
            return True

    def ingest_event(self, message: dict) -> dict:
        data = message.get("data", message)
        event = data.get("e")
        now = self.clock()
        event_symbol, event_timeframe = self._message_identity(message, data, event)
        if event_symbol and event_symbol != self.symbol:
            reason = f"ignored {event or 'unknown'} event for {event_symbol}; active stream is {self.symbol}"
            self._emit_health("DATA_ERROR", reason)
            return {"accepted": False, "identity_mismatch": True, "reason": reason}
        if event_timeframe and event_timeframe != self.timeframe:
            reason = f"ignored kline event for {event_timeframe}; active timeframe is {self.timeframe}"
            self._emit_health("DATA_ERROR", reason)
            return {"accepted": False, "identity_mismatch": True, "reason": reason}
        with self._lock:
            self.last_update = now
        if event == "kline":
            kline = data["k"]
            key = ("kline", int(kline["t"]), bool(kline["x"]), str(kline["c"]), str(kline.get("v")))
            if key in self._seen_events:
                self.duplicate_events += 1
                return {"accepted": False, "duplicate": True}
            self._seen_events.add(key)
            if len(self._seen_events) > 10000:
                self._seen_events = set(list(self._seen_events)[-5000:])
            bar = self._bar(kline)
            with self._lock:
                self._quote["last"] = bar.close
                self.last_candle_update = now
            if bool(kline["x"]):
                accepted = self._merge_closed(bar)
                self._forming = None
                if accepted and self.bar_sink:
                    self.bar_sink(bar)
                return {"accepted": accepted, "closed": True, "bar": bar}
            self._forming = bar
            return {"accepted": True, "closed": False, "bar": bar}
        if event == "bookTicker" or {"b", "a", "s"}.issubset(data):
            with self._lock:
                self._quote.update({"bid": float(data["b"]), "ask": float(data["a"])})
                self.last_quote_update = now
            return {"accepted": True, "quote": True}
        if event == "markPriceUpdate":
            with self._lock:
                self._quote.update({"mark": float(data["p"]), "funding_rate": float(data.get("r") or 0),
                                    "next_funding_time": datetime.fromtimestamp(int(data["T"]) / 1000, tz=timezone.utc).isoformat()})
                self.last_mark_update = now
            return {"accepted": True, "mark": True}
        return {"accepted": False, "ignored": True}

    def status(self, *, now: datetime | None = None) -> dict:
        with self._lock:
            observed = now or self.clock()
            age_for = lambda stamp: max(0.0, (observed - stamp).total_seconds()) if stamp else None
            age = age_for(self.last_update)
            candle_age = age_for(self.last_candle_update)
            quote_age = age_for(self.last_quote_update)
            mark_age = age_for(self.last_mark_update)
            closed_age = age_for(self.last_closed_update)
            timeframe_seconds = TF_MS.get(self.timeframe, 60_000) / 1000
            completed_candle_threshold = timeframe_seconds + max(
                self.stale_after_seconds, min(timeframe_seconds * .25, 300.0))
            unresolved = self._unresolved_gaps()
            bid, ask, mark, last = (self._quote.get(key) for key in ("bid", "ask", "mark", "last"))
            bid_ask_valid = all(isinstance(value, (int, float)) and value > 0 for value in (bid, ask)) and bid <= ask
            mark_valid = isinstance(mark, (int, float)) and mark > 0
            quote_valid = bid_ask_valid and mark_valid
            reference = last or (self._forming.close if self._forming else (self._bars[-1].close if self._bars else None))
            mismatch_bps = None
            if quote_valid and reference and reference > 0:
                mismatch_bps = max(abs(float(bid) - reference), abs(float(ask) - reference),
                                   abs(float(mark) - reference)) / reference * 10_000

            if self.state in {"DISCONNECTED", "ERROR", "RECONNECTING", "CONNECTING"}:
                health = self.state
                reason = self.last_error or f"transport is {self.state.lower()}"
            elif not self.history_loaded:
                health, reason = "LOADING_HISTORY", "completed-candle history has not loaded"
            elif not self.reconciliation_complete or unresolved:
                health, reason = "RECONCILING", f"{unresolved} completed candle(s) remain missing"
            elif closed_age is None or closed_age > completed_candle_threshold or candle_age is None or candle_age > self.stale_after_seconds:
                health, reason = "STALE_CANDLES", "completed history or live kline stream is stale"
            elif quote_age is None or quote_age > self.stale_after_seconds or not bid_ask_valid:
                health, reason = "STALE_QUOTE", "bid/ask stream is missing, invalid or stale"
            elif mark_age is None or mark_age > self.stale_after_seconds or not mark_valid:
                health, reason = "STALE_MARK", "mark-price stream is missing or stale"
            elif mismatch_bps is not None and mismatch_bps > self.quote_mismatch_bps:
                health, reason = "QUOTE_MISMATCH", f"candle/quote deviation is {mismatch_bps:.2f} bps"
            else:
                health, reason = "SYNCHRONIZED", "candles, bid/ask and mark are reconciled and fresh"
            reliable = health == "SYNCHRONIZED"
            payload = {"state": health, "transport_state": self.state,
                    "health_reason": reason, "reliable": reliable, "new_entries_paused": not reliable,
                    "symbol": self.symbol, "timeframe": self.timeframe,
                    "last_update": self.last_update.isoformat() if self.last_update else None,
                    "last_candle_update": self.last_candle_update.isoformat() if self.last_candle_update else None,
                    "last_quote_update": self.last_quote_update.isoformat() if self.last_quote_update else None,
                    "last_mark_update": self.last_mark_update.isoformat() if self.last_mark_update else None,
                    "last_closed_update": self.last_closed_update.isoformat() if self.last_closed_update else None,
                    "age_seconds": age, "candle_age_seconds": candle_age,
                    "quote_age_seconds": quote_age, "mark_age_seconds": mark_age,
                    "closed_candle_age_seconds": closed_age,
                    "freshness_thresholds_seconds": {
                        "stream": self.stale_after_seconds,
                        "completed_candle": completed_candle_threshold,
                    },
                    "history_loaded": self.history_loaded,
                    "reconciliation_complete": self.reconciliation_complete and unresolved == 0,
                    "unresolved_missing_candles": unresolved,
                    "candle_quote_deviation_bps": mismatch_bps,
                    "reconnect_attempt": self.reconnect_attempt,
                    "duplicate_events": self.duplicate_events, "missing_candles": self.missing_candles,
                    "reconciled_candles": self.reconciled_candles, "last_error": self.last_error,
                    "public_streams": ["kline", "bookTicker", "markPrice"],
                    "private_key_required": False, "real_execution_allowed": False}
        self._emit_health(health, reason)
        return payload

    def snapshot(self) -> dict:
        with self._lock:
            return {"closed_bars": list(self._bars), "forming": self._forming,
                    "quote": dict(self._quote), "connection": self.status()}
