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


class PriceActionPublicStream:
    def __init__(self, rest_loader: Callable, *, max_bars: int = 1500,
                 stale_after_seconds: float = 15.0,
                 event_sink: Callable[[dict], None] | None = None,
                 bar_sink: Callable[[Bar], None] | None = None):
        self.rest_loader = rest_loader
        self.max_bars = max_bars
        self.stale_after_seconds = stale_after_seconds
        self.event_sink = event_sink
        self.bar_sink = bar_sink
        self.symbol = ""
        self.timeframe = ""
        self.state = "DISCONNECTED"
        self.last_error = ""
        self.last_update: datetime | None = None
        self.last_closed_update: datetime | None = None
        self.reconnect_attempt = 0
        self.duplicate_events = 0
        self.missing_candles = 0
        self.reconciled_candles = 0
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
                             "timestamp": datetime.now(timezone.utc).isoformat(),
                             "symbol": self.symbol, "timeframe": self.timeframe})

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
        now = datetime.now(timezone.utc)
        step = timedelta(milliseconds=TF_MS[timeframe])
        closed = sorted({row.timestamp: row for row in rows if row.timestamp + step <= now}.values(),
                        key=lambda row: row.timestamp)
        with self._lock:
            self.symbol, self.timeframe = symbol, timeframe
            self._bars = deque(closed[-self.max_bars:], maxlen=self.max_bars)
            self._forming = next((row for row in reversed(rows) if row.timestamp + step > now), None)
            self.last_closed_update = closed[-1].timestamp + step if closed else None
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
            now = datetime.now(timezone.utc)
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
            self._set_state("CONNECTED")
            return count
        except Exception as exc:
            self._set_state("DELAYED", f"gap reconciliation failed: {exc}")
            return 0

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
            self.last_closed_update = bar.timestamp + step
            return True

    def ingest_event(self, message: dict) -> dict:
        data = message.get("data", message)
        event = data.get("e")
        now = datetime.now(timezone.utc)
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
            self._quote["last"] = bar.close
            if bool(kline["x"]):
                accepted = self._merge_closed(bar)
                self._forming = None
                if accepted and self.bar_sink:
                    self.bar_sink(bar)
                return {"accepted": accepted, "closed": True, "bar": bar}
            self._forming = bar
            return {"accepted": True, "closed": False, "bar": bar}
        if event == "bookTicker" or {"b", "a", "s"}.issubset(data):
            self._quote.update({"bid": float(data["b"]), "ask": float(data["a"])})
            return {"accepted": True, "quote": True}
        if event == "markPriceUpdate":
            self._quote.update({"mark": float(data["p"]), "funding_rate": float(data.get("r") or 0),
                                "next_funding_time": datetime.fromtimestamp(int(data["T"]) / 1000, tz=timezone.utc).isoformat()})
            return {"accepted": True, "mark": True}
        return {"accepted": False, "ignored": True}

    def status(self, *, now: datetime | None = None) -> dict:
        observed = now or datetime.now(timezone.utc)
        age = (observed - self.last_update).total_seconds() if self.last_update else None
        state = self.state
        if state == "CONNECTED" and (age is None or age > self.stale_after_seconds):
            state = "DELAYED"
        reliable = state == "CONNECTED" and self.missing_candles <= self.reconciled_candles
        with self._lock:
            return {"state": state, "reliable": reliable, "new_entries_paused": not reliable,
                    "symbol": self.symbol, "timeframe": self.timeframe,
                    "last_update": self.last_update.isoformat() if self.last_update else None,
                    "last_closed_update": self.last_closed_update.isoformat() if self.last_closed_update else None,
                    "age_seconds": age, "reconnect_attempt": self.reconnect_attempt,
                    "duplicate_events": self.duplicate_events, "missing_candles": self.missing_candles,
                    "reconciled_candles": self.reconciled_candles, "last_error": self.last_error,
                    "public_streams": ["kline", "bookTicker", "markPrice"],
                    "private_key_required": False, "real_execution_allowed": False}

    def snapshot(self) -> dict:
        with self._lock:
            return {"closed_bars": list(self._bars), "forming": self._forming,
                    "quote": dict(self._quote), "connection": self.status()}
