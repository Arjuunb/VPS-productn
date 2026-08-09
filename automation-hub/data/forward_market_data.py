"""Strict data adapter for forward paper and live execution.

Unlike :mod:`data.market_data`, this module has no cache, CSV, or synthetic
fallback.  It is the only fetcher Trading Instances may use for paper-forward
execution.  A provider failure is an error which pauses new decisions; it is
never an invitation to replay old market history.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from bot.types import Bar


class ForwardMarketDataUnavailable(RuntimeError):
    """No trustworthy current market data is available for a forward worker."""


def fetch_forward_bars(symbol: str, timeframe: str, limit: int,
                       *, since_ms: Optional[int] = None,
                       exchange: Optional[str] = None) -> tuple[list[Bar], str]:
    """Fetch provider candles only, or fail closed.

    The returned final candle is intentionally retained.  The engine excludes
    it because ordinary REST OHLCV feeds identify a forming candle by position,
    not by a reliable closed flag.  This conservative contract prevents a
    partially formed candle from becoming a paper-trading opportunity.
    """
    from data.live_data import fetch_ohlcv, last_error

    exchange = (exchange or os.environ.get("HUB_EXCHANGE", "binance")).strip() or "binance"
    bars = fetch_ohlcv(symbol, timeframe=timeframe, limit=max(2, min(int(limit), 1000)),
                       exchange=exchange, since_ms=since_ms)
    if not bars:
        detail = last_error(symbol) or "provider returned no OHLCV bars"
        raise ForwardMarketDataUnavailable(f"{symbol} {timeframe}: {detail}")
    return bars, f"live (ccxt:{exchange})"


@lru_cache(maxsize=256)
def fetch_forward_symbol_rules(symbol: str, exchange: str):
    """Load the active venue's executable amount/price filters, or fail closed.

    Rules are cached per venue+symbol because exchange metadata changes much
    less frequently than candles. A paper order must not claim a quantity the
    selected venue would reject.
    """
    try:
        import ccxt
        from bot.brokers.symbol_rules import from_ccxt
        from data.live_data import _to_pair

        klass = getattr(ccxt, exchange)
        client = klass({"enableRateLimit": True})
        markets = client.load_markets()
        pair = _to_pair(symbol)
        market = markets.get(pair) or client.market(pair)
        if market.get("active") is False:
            raise RuntimeError(f"{pair} is inactive on {exchange}")
        return from_ccxt(market)
    except Exception as exc:
        raise ForwardMarketDataUnavailable(
            f"Cannot validate {symbol} order rules on {exchange}: "
            f"{type(exc).__name__}: {exc}") from exc
