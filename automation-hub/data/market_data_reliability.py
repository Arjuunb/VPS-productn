"""Canonical market-data contracts, provider registry, and resilient transport.

No strategy or execution module needs to know a provider wire symbol.  Provider
adapters receive canonical symbols and return canonical, UTC-validated candles.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

TIMEFRAMES = {"1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"}


@dataclass(frozen=True)
class CanonicalSymbol:
    value: str
    market_type: str

    @classmethod
    def parse(cls, raw: str, market_type: str = "") -> "CanonicalSymbol":
        key = (raw or "").upper().replace("-", "/").replace(" ", "")
        aliases = {"BTCUSDT": "BTC/USDT", "ETHUSDT": "ETH/USDT", "SOLUSDT": "SOL/USDT",
                   "EURUSD": "EUR/USD", "GBPUSD": "GBP/USD", "USDJPY": "USD/JPY",
                   "XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD"}
        value = aliases.get(key.replace("/", ""), key)
        if not value or value == "/":
            raise ValueError("symbol is required")
        inferred = market_type or ("crypto" if value.endswith("/USDT") else
                                   "forex" if value.count("/") == 1 else "stock")
        return cls(value, inferred)

    def provider_symbol(self, provider: str) -> str:
        if provider == "binance-futures":
            if self.market_type != "crypto" or not self.value.endswith("/USDT"):
                raise ValueError("Binance Futures only accepts USDT crypto perpetual symbols")
            return self.value.replace("/", "")
        return self.value.replace("/", "")


@dataclass(frozen=True)
class CanonicalCandle:
    symbol: str
    timeframe: str
    timestamp_utc: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    provider: str
    market_type: str
    is_closed: bool
    received_at: str
    source_quality: str = "verified"

    def validate(self) -> None:
        if self.timeframe not in TIMEFRAMES:
            raise ValueError("malformed timeframe")
        if self.timestamp_utc < 0:
            raise ValueError("timestamp must be UTC epoch milliseconds")
        if min(self.open, self.high, self.low, self.close, self.volume) < 0:
            raise ValueError("negative OHLCV value")
        if self.high < self.low or not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("impossible OHLC values")
        if not self.provider or not self.symbol or not self.is_closed:
            raise ValueError("provider, symbol, and closed-candle state are required")


@dataclass
class ProviderInfo:
    name: str
    markets: list[str]
    timeframes: list[str]
    max_history: str
    rate_limit_per_minute: int
    authentication_required: bool
    reliability_score: int
    current_availability: str = "unknown"
    last_successful_request: Optional[str] = None
    metrics: dict = field(default_factory=lambda: {"requests": 0, "failed": 0, "retries": 0, "latency_ms": 0.0})


class ProviderRegistry:
    def __init__(self):
        self.providers = {
            "binance-futures": ProviderInfo("binance-futures", ["crypto"], sorted(TIMEFRAMES), "listing lifetime", 1200, False, 90),
            "yahoo-finance": ProviderInfo("yahoo-finance", ["stock", "index", "forex", "commodity"], sorted(TIMEFRAMES), "provider dependent", 60, False, 65),
        }
        self._lock = threading.Lock()

    def provider(self, name: str) -> ProviderInfo:
        if name not in self.providers:
            raise ValueError(f"unregistered provider '{name}'")
        return self.providers[name]

    def record(self, name: str, *, ok: bool, latency_ms: float, retries: int = 0) -> None:
        with self._lock:
            p = self.provider(name); p.metrics["requests"] += 1; p.metrics["retries"] += retries
            p.metrics["latency_ms"] = round((p.metrics["latency_ms"] + latency_ms) / 2, 2)
            if ok:
                p.current_availability = "available"; p.last_successful_request = datetime.now(timezone.utc).isoformat()
            else:
                p.current_availability = "degraded"; p.metrics["failed"] += 1

    def status(self) -> list[dict]:
        return [{**asdict(p), "supported_symbols": "provider-validated"} for p in self.providers.values()]


class ResilientRequester:
    """Bounded retries, rate pacing and a small circuit breaker around a transport."""
    def __init__(self, request: Callable, registry: ProviderRegistry, provider: str, *, retries: int = 3):
        self.request, self.registry, self.provider, self.retries = request, registry, provider, retries
        self._lock = threading.Lock(); self._next_at = 0.0; self._failures = 0; self._open_until = 0.0

    def __call__(self, url: str, params: dict):
        with self._lock:
            if time.monotonic() < self._open_until:
                raise RuntimeError(f"provider circuit open: {self.provider}")
            pace = 60 / max(1, self.registry.provider(self.provider).rate_limit_per_minute)
            wait = self._next_at - time.monotonic(); self._next_at = max(self._next_at, time.monotonic()) + pace
        if wait > 0: time.sleep(wait)
        started = time.monotonic()
        for attempt in range(self.retries + 1):
            try:
                result = self.request(url, params)
                self._failures = 0; self.registry.record(self.provider, ok=True, latency_ms=(time.monotonic()-started)*1000, retries=attempt)
                return result
            except Exception:
                if attempt == self.retries:
                    self._failures += 1
                    if self._failures >= 3: self._open_until = time.monotonic() + 30
                    self.registry.record(self.provider, ok=False, latency_ms=(time.monotonic()-started)*1000, retries=attempt)
                    raise
                time.sleep(min(2.0, 0.15 * (2 ** attempt)) + random.uniform(0, 0.05))
