"""Strict, local-first market-data service for Paper Trading V2.

This module is deliberately separate from :mod:`data.market_data`: the latter
has legacy demo fallbacks which remain available for backwards compatibility.
V2 never calls those fallbacks.  A V2 request therefore returns real provider
candles, cached provider candles, or a clear availability error -- never an
invented price.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from bot.types import Bar

TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d")
TF_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
         "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000,
         "1d": 86_400_000}
CRYPTO_SEEDS = {"BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT",
                "DOGEUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT"}
_FUTURES_HOSTS = ("https://fapi.binance.com", "https://fstream.binance.com")
_YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_YAHOO_INTERVALS = {"1m": ("1m", "7d"), "3m": ("1m", "7d"),
                    "5m": ("5m", "60d"), "15m": ("15m", "60d"),
                    "30m": ("30m", "60d"), "1h": ("1h", "730d"),
                    "4h": ("1h", "730d"), "1d": ("1d", "max")}
_YAHOO_AGGREGATES = {"3m": ("1m", 3), "4h": ("1h", 4)}
_YAHOO_ALIASES = {"SPX": "^GSPC", "NASDAQ": "^NDX", "DOW": "^DJI",
                  "FTSE100": "^FTSE", "DAX": "^GDAXI", "NIKKEI": "^N225",
                  "GOLD": "GC=F", "SILVER": "SI=F", "CRUDEOIL": "CL=F",
                  "NATURALGAS": "NG=F"}
_ASSET_ALIASES = {"SPX": "stocks", "NASDAQ": "stocks", "DOW": "stocks",
                  "FTSE100": "stocks", "DAX": "stocks", "NIKKEI": "stocks",
                  "GOLD": "commodities", "SILVER": "commodities",
                  "CRUDEOIL": "commodities", "NATURALGAS": "commodities"}


def candles_for_period(timeframe: str, period: str = "90d") -> int:
    """Translate an operator-facing history range into a bounded candle count."""
    if timeframe not in TF_MS:
        raise ValueError(f"unsupported timeframe '{timeframe}'")
    key = (period or "90d").lower().replace(" ", "")
    if key == "max":
        return 200_000
    days = {"90d": 90, "3mo": 90, "6mo": 183, "1y": 365,
            "2y": 730, "5y": 1826}.get(key)
    if days is None:
        raise ValueError("period must be 90d, 6mo, 1y, 2y, 5y, or max")
    return max(1, int(days * 86_400_000 / TF_MS[timeframe]))


def normalize_symbol(symbol: str) -> str:
    return (symbol or "").upper().replace("/", "").replace("-", "").strip()


def _iso(ms: Optional[int]) -> Optional[str]:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else None


class MarketDataService:
    """Provider-backed OHLCV cache with per-asset SQLite files and metadata.

    Cache files intentionally live under ``market_data/<asset>/`` rather than
    beside application state, making market data portable, inspectable and
    safe to delete/rebuild independently of trade/account data.
    """
    def __init__(self, root: str | Path, *, request_json: Optional[Callable] = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.request_json = request_json or self._request_json
        self._lock = threading.RLock()
        self._perpetuals: tuple[float, list[str]] = (0.0, [])

    @staticmethod
    def _request_json(url: str, params: dict) -> object:
        # Stdlib keeps the strict V2 path available even in a minimal runtime;
        # no optional HTTP client should turn an otherwise reachable provider
        # into a misleading "no market data" condition.
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        query = urlencode(params or {})
        request = Request(url + ("?" + query if query else ""),
                          headers={"User-Agent": "TradeLogX-MarketDataV2/1.0"})
        with urlopen(request, timeout=15) as response:  # nosec B310: fixed trusted provider URLs
            return json.loads(response.read().decode("utf-8"))

    def asset_for(self, symbol: str) -> str:
        key = normalize_symbol(symbol)
        if key in CRYPTO_SEEDS or key.endswith("USDT"):
            return "crypto"
        if key in _ASSET_ALIASES:
            return _ASSET_ALIASES[key]
        try:
            from services.symbol_universe import find
            rec = find(symbol)
            if rec:
                return {"stock": "stocks", "etf": "stocks", "index": "stocks",
                        "forex": "forex", "commodity": "commodities"}.get(
                    rec["asset_class"], "stocks")
        except Exception:  # catalog unavailability must not fabricate a market
            pass
        # Yahoo is the trusted no-key provider for exchange-listed equities.
        # Do not require every S&P/NASDAQ/NYSE constituent to be duplicated in
        # a hand-maintained catalog: a normal alphabetic ticker is resolvable
        # directly and will still fail closed if Yahoo does not recognise it.
        if key.isalpha() and 1 <= len(key) <= 6:
            return "stocks"
        return "unknown"

    def _db_path(self, symbol: str, asset: Optional[str] = None) -> Path:
        asset = asset or self.asset_for(symbol)
        if asset == "unknown":
            raise ValueError(f"unavailable symbol '{symbol}'")
        safe = "".join(c for c in normalize_symbol(symbol) if c.isalnum() or c == "_")
        directory = self.root / asset
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{safe}.sqlite3"

    def _conn(self, symbol: str, asset: Optional[str] = None) -> sqlite3.Connection:
        c = sqlite3.connect(self._db_path(symbol, asset))
        c.execute("""CREATE TABLE IF NOT EXISTS candles (
            timeframe TEXT NOT NULL, open_time INTEGER NOT NULL,
            open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
            close REAL NOT NULL, volume REAL NOT NULL,
            PRIMARY KEY(timeframe, open_time))""")
        c.execute("""CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
        return c

    def _meta(self, c: sqlite3.Connection) -> dict:
        return {k: json.loads(v) for k, v in c.execute("SELECT key,value FROM metadata")}

    @staticmethod
    def _set_meta(c: sqlite3.Connection, **values: object) -> None:
        c.executemany("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                      [(k, json.dumps(v)) for k, v in values.items()])

    @staticmethod
    def _valid(row: tuple) -> bool:
        try:
            t, o, h, l, close, v = row
            return int(t) >= 0 and min(float(o), float(h), float(l), float(close), float(v)) >= 0 and \
                float(h) >= max(float(o), float(close), float(l)) and \
                float(l) <= min(float(o), float(close), float(h))
        except (TypeError, ValueError):
            return False

    def upsert(self, symbol: str, timeframe: str, rows: list[tuple], *, provider: str) -> dict:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe '{timeframe}'")
        valid = sorted({int(r[0]): tuple(r[:6]) for r in rows if self._valid(tuple(r[:6]))}.values())
        if not valid:
            raise ValueError("provider returned no valid OHLCV candles")
        asset = self.asset_for(symbol)
        with self._lock:
            c = self._conn(symbol, asset)
            try:
                c.executemany("INSERT OR REPLACE INTO candles VALUES (?,?,?,?,?,?,?)",
                              [(timeframe, *r) for r in valid])
                report = self._integrity_conn(c, timeframe, asset)
                self._set_meta(c, symbol=normalize_symbol(symbol), asset_class=asset,
                               provider=provider, downloaded_at=datetime.now(timezone.utc).isoformat(),
                               last_updated=datetime.now(timezone.utc).isoformat(),
                               missing_ranges=report["missing_ranges"], schema_version=2)
                c.commit()
            finally:
                c.close()
        return {"symbol": normalize_symbol(symbol), "timeframe": timeframe,
                "stored": len(valid), "provider": provider, "integrity": report}

    def _rows(self, symbol: str, timeframe: str, *, limit: Optional[int] = None) -> list[tuple]:
        try:
            c = self._conn(symbol)
        except ValueError:
            return []
        try:
            rows = c.execute("SELECT open_time,open,high,low,close,volume FROM candles "
                             "WHERE timeframe=? ORDER BY open_time", (timeframe,)).fetchall()
        finally:
            c.close()
        return rows[-limit:] if limit else rows

    def bars(self, symbol: str, timeframe: str, *, limit: int = 1500) -> list[Bar]:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe '{timeframe}'")
        return [Bar(datetime.fromtimestamp(r[0] / 1000, timezone.utc), *map(float, r[1:]))
                for r in self._rows(symbol, timeframe, limit=limit)]

    def latest(self, symbol: str, timeframe: str = "1h") -> Optional[dict]:
        rows = self._rows(symbol, timeframe, limit=1)
        if not rows:
            return None
        r = rows[-1]
        return {"timestamp": _iso(r[0]), "open": r[1], "high": r[2], "low": r[3],
                "close": r[4], "volume": r[5], "source": "local real cache"}

    def _integrity_conn(self, c: sqlite3.Connection, timeframe: str, asset: str) -> dict:
        rows = c.execute("SELECT open_time,open,high,low,close,volume FROM candles "
                         "WHERE timeframe=? ORDER BY open_time", (timeframe,)).fetchall()
        corrupt = sum(1 for r in rows if not self._valid(r))
        # A crypto feed is continuous.  Other markets close overnight/weekends;
        # flagging every normal equity close as a bad data gap would be false.
        missing: list[dict] = []
        if asset == "crypto":
            step = TF_MS[timeframe]
            for a, b in zip(rows, rows[1:]):
                if b[0] - a[0] > step:
                    missing.append({"from": _iso(a[0] + step), "to": _iso(b[0] - step)})
        return {"candles": len(rows), "corrupt": corrupt, "duplicates": 0,
                "timezone": "UTC", "ascending": all(a[0] < b[0] for a, b in zip(rows, rows[1:])),
                "missing_ranges": missing}

    def status(self, symbol: str, timeframe: str = "1h") -> dict:
        asset = self.asset_for(symbol)
        if asset == "unknown":
            return {"available": False, "symbol": normalize_symbol(symbol), "reason": "unknown symbol"}
        try:
            c = self._conn(symbol, asset)
            meta = self._meta(c)
            integrity = self._integrity_conn(c, timeframe, asset)
            last = c.execute("SELECT MAX(open_time) FROM candles WHERE timeframe=?", (timeframe,)).fetchone()[0]
        finally:
            c.close()
        return {"available": integrity["candles"] > 0, "symbol": normalize_symbol(symbol),
                "asset_class": asset, "timeframe": timeframe, "last_candle": _iso(last),
                "freshness_seconds": max(0, int(time.time() - last / 1000)) if last else None,
                "metadata": meta, "integrity": integrity}

    def _crypto_rows(self, symbol: str, timeframe: str, *, start_ms: Optional[int], limit: int,
                     end_ms: Optional[int] = None) -> list[tuple]:
        params = {"symbol": normalize_symbol(symbol), "interval": timeframe, "limit": min(1500, limit)}
        if start_ms is not None:
            params["startTime"] = start_ms
        if end_ms is not None:
            params["endTime"] = end_ms
        error = None
        for host in _FUTURES_HOSTS:
            try:
                payload = self.request_json(host + "/fapi/v1/klines", params)
                return [(int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in payload]
            except Exception as exc:  # try public mirror before reporting failure
                error = exc
        raise RuntimeError(f"Binance USDT perpetual data unavailable: {error}")

    def _crypto_history(self, symbol: str, timeframe: str, candles: int) -> list[tuple]:
        """Page backwards through real Binance Futures candles without overlap."""
        rows: list[tuple] = []
        end_ms: Optional[int] = None
        while len(rows) < candles:
            batch = self._crypto_rows(symbol, timeframe, start_ms=None,
                                      end_ms=end_ms, limit=min(1500, candles - len(rows)))
            if not batch:
                break
            rows = batch + rows
            if len(batch) < min(1500, candles - len(rows) + len(batch)):
                break  # provider reached listing/retention boundary
            next_end = batch[0][0] - 1
            if end_ms is not None and next_end >= end_ms:
                break  # defensive provider-loop guard
            end_ms = next_end
            time.sleep(0.05)  # public endpoint rate-limit courtesy
        # Provider rows can be repeated at paging boundaries. The upsert would
        # dedupe them too; doing it here keeps returned status exact.
        return sorted({r[0]: r for r in rows}.values())[-candles:]

    def _yahoo_rows(self, symbol: str, timeframe: str, *, limit: int) -> list[tuple]:
        from data.yahoo_bars import yahoo_symbol_for
        ticker = yahoo_symbol_for(symbol) or _YAHOO_ALIASES.get(normalize_symbol(symbol)) or normalize_symbol(symbol)
        interval, range_ = _YAHOO_INTERVALS[timeframe]
        data = self.request_json(_YAHOO.format(symbol=ticker), {"interval": interval, "range": range_})
        try:
            result = data["chart"]["result"][0]
            quote = result["indicators"]["quote"][0]
            out = []
            for i, stamp in enumerate(result.get("timestamp") or []):
                vals = (quote["open"][i], quote["high"][i], quote["low"][i], quote["close"][i])
                if any(v is None for v in vals):
                    continue
                volume = (quote.get("volume") or [0] * len(result["timestamp"]))[i] or 0
                out.append((int(stamp) * 1000, *map(float, vals), float(volume)))
            aggregate = _YAHOO_AGGREGATES.get(timeframe)
            if aggregate:
                base_tf, factor = aggregate
                out = self._aggregate_rows(out, TF_MS[base_tf], factor)
            return out[-limit:]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Yahoo returned malformed historical data: {exc}") from exc

    @staticmethod
    def _aggregate_rows(rows: list[tuple], step_ms: int, factor: int) -> list[tuple]:
        """Aggregate complete adjacent provider candles only.

        This is an OHLCV transformation of genuine smaller provider candles,
        never interpolation. Incomplete groups (including market/session gaps)
        are omitted rather than made into a plausible-looking larger candle.
        """
        out: list[tuple] = []
        bucket: list[tuple] = []
        width = step_ms * factor
        for row in rows:
            if bucket and (row[0] // width != bucket[0][0] // width or row[0] - bucket[-1][0] != step_ms):
                if len(bucket) == factor:
                    out.append((bucket[0][0], bucket[0][1], max(x[2] for x in bucket),
                                min(x[3] for x in bucket), bucket[-1][4], sum(x[5] for x in bucket)))
                bucket = []
            bucket.append(row)
        if len(bucket) == factor:
            out.append((bucket[0][0], bucket[0][1], max(x[2] for x in bucket),
                        min(x[3] for x in bucket), bucket[-1][4], sum(x[5] for x in bucket)))
        return out

    def download(self, symbol: str, timeframe: str = "1h", *, candles: Optional[int] = None,
                 period: str = "90d") -> dict:
        """Download real provider data. Repeated calls are idempotent upserts."""
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"unsupported timeframe '{timeframe}'")
        asset = self.asset_for(symbol)
        if asset == "unknown":
            raise ValueError(f"unavailable symbol '{symbol}'")
        candles = int(candles) if candles is not None else candles_for_period(timeframe, period)
        if candles <= 0 or candles > 200_000:
            raise ValueError("candles must be between 1 and 200000")
        if asset == "crypto":
            rows, provider = self._crypto_history(symbol, timeframe, candles), "binance-usdt-perpetual"
        else:
            rows, provider = self._yahoo_rows(symbol, timeframe, limit=candles), "yahoo-finance"
        return self.upsert(symbol, timeframe, rows, provider=provider)

    def crypto_perpetuals(self, *, ttl_seconds: int = 3600) -> list[str]:
        """Discover active Binance USDT perpetual pairs, with a safe seed fallback.

        Discovery is metadata only; candle requests still validate a pair at the
        provider. A temporary exchange outage must not make the paper UI empty.
        """
        with self._lock:
            cached_at, cached = self._perpetuals
            if cached and time.time() - cached_at < ttl_seconds:
                return list(cached)
        try:
            payload = self.request_json(_FUTURES_HOSTS[0] + "/fapi/v1/exchangeInfo", {})
            pairs = sorted({x["symbol"] for x in payload.get("symbols", [])
                            if x.get("status") == "TRADING" and x.get("quoteAsset") == "USDT"
                            and x.get("contractType") == "PERPETUAL"})
            if not pairs:
                raise ValueError("no USDT perpetual pairs returned")
        except Exception:
            pairs = sorted(CRYPTO_SEEDS)
        with self._lock:
            self._perpetuals = (time.time(), pairs)
        return pairs

    def update(self, symbol: str, timeframe: str = "1h") -> dict:
        """Incremental update. Provider rows are upserted; duplicates cannot accrue."""
        existing = self._rows(symbol, timeframe, limit=1)
        asset = self.asset_for(symbol)
        if asset == "crypto":
            start = existing[-1][0] + TF_MS[timeframe] if existing else None
            rows, provider = self._crypto_rows(symbol, timeframe, start_ms=start, limit=1500), "binance-usdt-perpetual"
        else:
            # Yahoo's public range endpoint does not provide reliable universal
            # cursor paging; retrieve its finite window and idempotently upsert.
            rows, provider = self._yahoo_rows(symbol, timeframe, limit=1500), "yahoo-finance"
        if not rows:
            return {"symbol": normalize_symbol(symbol), "timeframe": timeframe, "stored": 0,
                    "provider": provider, "message": "provider had no newer candles"}
        return self.upsert(symbol, timeframe, rows, provider=provider)

    def repair(self, symbol: str, timeframe: str = "1h") -> dict:
        """Repair a continuous crypto series by refetching missing ranges.

        No interpolation is performed.  If a provider cannot supply a range it
        remains visible in ``missing_ranges`` for the operator to investigate.
        """
        state = self.status(symbol, timeframe)
        if state.get("asset_class") != "crypto":
            return {**state, "repaired": 0, "message": "session-aware provider; no synthetic gap repair"}
        repaired = 0
        for gap in state["integrity"]["missing_ranges"]:
            start = int(datetime.fromisoformat(gap["from"]).timestamp() * 1000)
            rows = self._crypto_rows(symbol, timeframe, start_ms=start, limit=1500)
            repaired += self.upsert(symbol, timeframe, rows, provider="binance-usdt-perpetual")["stored"]
        return {**self.status(symbol, timeframe), "repaired": repaired}


class MarketDataUpdateJob:
    """Single background update batch with observable, bounded progress.

    The job intentionally does not schedule its own process-wide timer: Docker
    restarts and deployment windows should not create surprise external traffic.
    An operator (or a platform scheduler) explicitly starts a batch through the
    API and can poll the returned status.
    """
    def __init__(self, service: MarketDataService):
        self.service = service
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.state: dict = {"running": False, "started_at": None, "finished_at": None,
                            "total": 0, "done": 0, "current": None, "results": []}

    def start(self, symbols: list[str], timeframes: list[str]) -> dict:
        pairs = [(normalize_symbol(s), tf) for s in symbols for tf in timeframes]
        if not pairs:
            raise ValueError("at least one symbol and timeframe is required")
        if any(tf not in TIMEFRAMES for _, tf in pairs):
            raise ValueError("one or more timeframes are unsupported")
        with self._lock:
            if self._thread and self._thread.is_alive():
                return {"started": False, "reason": "V2 update already running", **self.state}
            self.state = {"running": True, "started_at": datetime.now(timezone.utc).isoformat(),
                          "finished_at": None, "total": len(pairs), "done": 0,
                          "current": None, "results": []}
            self._thread = threading.Thread(target=self._run, args=(pairs,), name="market-data-v2-update", daemon=True)
            self._thread.start()
        return {"started": True, **self.state}

    def _run(self, pairs: list[tuple[str, str]]) -> None:
        for symbol, timeframe in pairs:
            self.state["current"] = {"symbol": symbol, "timeframe": timeframe}
            try:
                result = self.service.update(symbol, timeframe)
            except Exception as exc:  # one provider error must not stop other markets
                result = {"symbol": symbol, "timeframe": timeframe, "error": str(exc)}
            self.state["results"].append(result)
            self.state["done"] += 1
        self.state["running"] = False
        self.state["current"] = None
        self.state["finished_at"] = datetime.now(timezone.utc).isoformat()

    def status(self) -> dict:
        with self._lock:
            return dict(self.state)
