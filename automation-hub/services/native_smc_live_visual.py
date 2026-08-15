"""Read-only live market feed for the Native SMC visual lab.

This module is deliberately separate from Trading Instances. It obtains a
small current OHLCV window for side-by-side review, rejects an open candle,
builds a fresh research model in memory, and returns no execution authority.
Nothing here writes a checkpoint, signal, order, or trade.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from bot.data.resample import TF_SECONDS
from bot.types import Bar
from data.forward_market_data import valid_closed_bars
from services.native_smc import SMCConfig, SMCMarketStructureEngine


LIVE_VENUES = {
    "mexc_perpetual": {
        "label": "MEXC perpetual",
        "ccxt_id": "mexc",
        "market": lambda symbol: f"{symbol[:-4]}/USDT:USDT",
        "options": {"defaultType": "swap"},
    },
    "kraken_spot": {
        "label": "Kraken spot",
        "ccxt_id": "kraken",
        "market": lambda symbol: f"{symbol[:-4]}/USDT",
        "options": {},
    },
}


class NativeSMCLiveDataUnavailable(RuntimeError):
    """A live visual source did not yield valid closed candles."""


def _validate(symbol: str, timeframe: str, venue: str) -> tuple[str, str, str]:
    symbol, timeframe, venue = symbol.upper(), timeframe.lower(), venue.lower()
    if symbol not in {"BTCUSDT", "ETHUSDT", "SOLUSDT"}:
        raise ValueError("symbol must be one of BTCUSDT, ETHUSDT, or SOLUSDT")
    if timeframe not in {"1m", "3m", "5m", "30m", "1h", "4h", "1d", "1w"}:
        raise ValueError("timeframe must be one of 1m, 3m, 5m, 30m, 1h, 4h, 1d, or 1w")
    if venue not in LIVE_VENUES:
        raise ValueError("venue must be one of mexc_perpetual or kraken_spot")
    return symbol, timeframe, venue


def fetch_venue_ohlcv(symbol: str, timeframe: str, venue: str, limit: int) -> list[Bar]:
    """Fetch raw live candles from the explicitly selected visual venue."""
    config = LIVE_VENUES[venue]
    try:
        import ccxt
        exchange_class = getattr(ccxt, config["ccxt_id"])
        exchange = exchange_class({"enableRateLimit": True, "options": config["options"]})
        rows = exchange.fetch_ohlcv(config["market"](symbol), timeframe=timeframe, limit=limit)
    except Exception as exc:
        raise NativeSMCLiveDataUnavailable(
            f"{config['label']} did not provide {symbol} {timeframe}: {type(exc).__name__}: {exc}"
        ) from exc
    bars = [
        Bar(datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc), float(row[1]), float(row[2]),
            float(row[3]), float(row[4]), float(row[5] or 0))
        for row in rows
    ]
    if not bars:
        raise NativeSMCLiveDataUnavailable(f"{config['label']} returned no {symbol} {timeframe} candles")
    return bars


def live_visual_state(symbol: str = "BTCUSDT", timeframe: str = "5m", venue: str = "mexc_perpetual", *,
                      limit: int = 800, now: datetime | None = None,
                      fetcher: Callable[[str, str, str, int], list[Bar]] = fetch_venue_ohlcv) -> dict:
    """Return current visual state from real, closed venue candles only."""
    symbol, timeframe, venue = _validate(symbol, timeframe, venue)
    now = now or datetime.now(timezone.utc)
    raw = fetcher(symbol, timeframe, venue, max(200, min(int(limit), 1000)))
    closed = valid_closed_bars(raw, TF_SECONDS[timeframe], now=now)
    if len(closed) < 200:
        raise NativeSMCLiveDataUnavailable(
            f"{LIVE_VENUES[venue]['label']} returned only {len(closed)} valid closed candles; need at least 200"
        )
    engine = SMCMarketStructureEngine(SMCConfig(symbol=symbol, timeframe=timeframe))
    engine.ingest_authoritative_closed_bars(closed, timeframe_seconds=TF_SECONDS[timeframe], now=now)
    state = engine.visual_state(candle_window=min(800, len(closed)))
    state["data_provenance"] = {
        "mode": "LIVE_EXCHANGE_CLOSED_CANDLES",
        "venue": LIVE_VENUES[venue]["label"],
        "ccxt_exchange": LIVE_VENUES[venue]["ccxt_id"],
        "market": LIVE_VENUES[venue]["market"](symbol),
        "symbol": symbol,
        "timeframe": timeframe,
        "observed_at": now.isoformat(),
        "raw_candles_received": len(raw),
        "closed_candles_used": len(closed),
        "first_closed_candle": closed[0].timestamp.isoformat(),
        "last_closed_candle": closed[-1].timestamp.isoformat(),
        "forming_candle_excluded": len(raw) > len(closed),
        "execution_allowed": False,
    }
    return state
