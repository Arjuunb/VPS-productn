"""Strict data adapter for forward paper and live execution.

Unlike :mod:`data.market_data`, this module has no cache, CSV, or synthetic
fallback.  It is the only fetcher Trading Instances may use for paper-forward
execution.  A provider failure is an error which pauses new decisions; it is
never an invitation to replay old market history.
"""
from __future__ import annotations

import os
from typing import Optional

from bot.types import Bar


class ForwardMarketDataUnavailable(RuntimeError):
    """No trustworthy current market data is available for a forward worker."""


def fetch_forward_bars(symbol: str, timeframe: str, limit: int,
                       *, since_ms: Optional[int] = None) -> tuple[list[Bar], str]:
    """Fetch provider candles only, or fail closed.

    The returned final candle is intentionally retained.  The engine excludes
    it because ordinary REST OHLCV feeds identify a forming candle by position,
    not by a reliable closed flag.  This conservative contract prevents a
    partially formed candle from becoming a paper-trading opportunity.
    """
    from data.live_data import fetch_ohlcv, last_error

    exchange = os.environ.get("HUB_EXCHANGE", "binance").strip() or "binance"
    bars = fetch_ohlcv(symbol, timeframe=timeframe, limit=max(2, min(int(limit), 1000)),
                       exchange=exchange, since_ms=since_ms)
    if not bars:
        detail = last_error(symbol) or "provider returned no OHLCV bars"
        raise ForwardMarketDataUnavailable(f"{symbol} {timeframe}: {detail}")
    return bars, f"live (ccxt:{exchange})"
