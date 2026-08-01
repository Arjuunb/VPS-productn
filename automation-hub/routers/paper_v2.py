"""Strict real-data Paper Trading V2 API.

All mutation endpoints are protected by the existing operator credential.  The
only way to advance an order is ``/paper-v2/process`` which reads the latest
provider candle from the V2 cache; a browser cannot inject a made-up price.
"""
from __future__ import annotations

import webhook_api as _wa
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Optional

from services import symbol_universe as _symbols
from data.market_data_v2 import TIMEFRAMES

router = APIRouter(tags=["paper-trading-v2"])


def _error(exc: Exception):
    raise HTTPException(400, str(exc)) from exc


def _direct_ticker_candidate(query: str) -> list[dict]:
    """Permit direct lookup of any normal US-listed ticker not yet in the UI catalog.

    The provider validates it at download time. This avoids pretending the
    small curated navigation catalog is a complete S&P/NASDAQ/NYSE master.
    """
    ticker = (query or "").upper().strip()
    if ticker.isalpha() and 1 <= len(ticker) <= 6:
        return [{"symbol": ticker, "ticker": ticker, "name": "Direct listed ticker lookup",
                 "asset_class": "stock", "exchange": "provider-validated", "session": "exchange session"}]
    return []


@router.get("/market-data-v2/symbols")
def available_symbols(asset_class: str = "", q: str = "", limit: int = Query(100, ge=1, le=2000)):
    """Available catalog symbols; live Binance perpetual discovery remains at /symbols/sync."""
    if (asset_class or "").lower() == "crypto-perpetual":
        pairs = _wa.v2_market_data.crypto_perpetuals()
        rows = [{"symbol": p, "ticker": p, "name": p, "asset_class": "crypto",
                 "exchange": "Binance Futures", "type": "perpetual", "session": "24/7"}
                for p in pairs[:limit]]
    else:
        rows = _symbols.search(q, limit=limit) if q else _symbols.filter_symbols(asset_class=asset_class, limit=limit)
        if q and not rows:
            rows = _direct_ticker_candidate(q)
    return {"count": len(rows), "symbols": rows, "timeframes": list(TIMEFRAMES)}


@router.get("/market-data-v2/search")
def search_symbols(q: str = Query(..., min_length=1), limit: int = Query(30, ge=1, le=100)):
    rows = _symbols.search(q, limit=limit)
    return {"query": q, "results": rows or _direct_ticker_candidate(q)}


@router.get("/market-data-v2/status/{symbol}")
def market_data_status(symbol: str, timeframe: str = "1h"):
    try:
        return _wa.v2_market_data.status(symbol, timeframe)
    except Exception as exc:
        _error(exc)


@router.get("/market-data-v2/metadata/{symbol}")
def market_metadata(symbol: str, timeframe: str = "1h"):
    return market_data_status(symbol, timeframe)


@router.get("/market-data-v2/latest/{symbol}")
def latest_candle(symbol: str, timeframe: str = "1h"):
    try:
        candle = _wa.v2_market_data.latest(symbol, timeframe)
    except Exception as exc:
        _error(exc)
    if candle is None:
        raise HTTPException(404, "no real historical candle cached; download history first")
    return {"symbol": symbol.upper(), "timeframe": timeframe, "candle": candle}


class DownloadBody(BaseModel):
    symbol: str
    timeframe: str = "1h"
    period: str = Field(default="90d", description="90d | 6mo | 1y | 2y | 5y | max")
    candles: Optional[int] = Field(default=None, ge=1, le=200_000,
                                   description="Optional exact override for the named period")


@router.post("/market-data-v2/download")
def download_history(body: DownloadBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.v2_market_data.download(body.symbol, body.timeframe,
                                           candles=body.candles, period=body.period)
    except Exception as exc:
        _error(exc)


@router.post("/market-data-v2/update")
def update_history(body: DownloadBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.v2_market_data.update(body.symbol, body.timeframe)
    except Exception as exc:
        _error(exc)


class UpdateBatchBody(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT"])
    timeframes: list[str] = Field(default_factory=lambda: ["1h"])


@router.post("/market-data-v2/update-batch")
def update_history_batch(body: UpdateBatchBody, x_webhook_secret: Optional[str] = Header(default=None)):
    """Start an asynchronous cache update; poll ``/update-batch/status``."""
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.v2_market_update_job.start(body.symbols, body.timeframes)
    except ValueError as exc:
        _error(exc)


@router.get("/market-data-v2/update-batch/status")
def update_history_batch_status():
    return _wa.v2_market_update_job.status()


@router.post("/market-data-v2/repair")
def repair_history(body: DownloadBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.v2_market_data.repair(body.symbol, body.timeframe)
    except Exception as exc:
        _error(exc)


@router.get("/paper-v2/account")
def account():
    marks = {p["symbol"]: (_wa.v2_market_data.latest(p["symbol"], "1h") or {}).get("close", p["entry_price"])
             for p in _wa.paper_broker_v2.positions()}
    return _wa.paper_broker_v2.account(marks)


@router.get("/paper-v2/orders")
def orders(status: str = ""):
    return {"orders": _wa.paper_broker_v2.orders(status=status or None)}


@router.get("/paper-v2/positions")
def positions():
    return {"positions": _wa.paper_broker_v2.positions()}


@router.get("/paper-v2/fills")
def fills(limit: int = Query(100, ge=1, le=1000)):
    return {"fills": _wa.paper_broker_v2.fills(limit=limit)}


class OrderBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    symbol: str
    side: str
    order_type: str = Field(alias="type")
    quantity: float
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trailing_offset: Optional[float] = None
    reduce_only: bool = False

@router.post("/paper-v2/orders")
def create_order(body: OrderBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    info = _symbols.market_info(body.symbol)
    # Known crypto perpetuals are 24/7 even before a catalog sync; all other
    # catalog entries honour their current session status.
    market_open = info.get("market_status", "open") == "open" if info.get("found") else body.symbol.upper().endswith("USDT")
    try:
        return _wa.paper_broker_v2.submit(symbol=body.symbol, side=body.side, order_type=body.order_type,
                                           quantity=body.quantity, limit_price=body.limit_price,
                                           stop_price=body.stop_price, trailing_offset=body.trailing_offset,
                                           reduce_only=body.reduce_only, market_open=market_open)
    except Exception as exc:
        _error(exc)


@router.post("/paper-v2/orders/{order_id}/cancel")
def cancel_order(order_id: str, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.paper_broker_v2.cancel(order_id)
    except (KeyError, ValueError) as exc:
        _error(exc)


class ProtectionBody(BaseModel):
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_offset: Optional[float] = None


@router.post("/paper-v2/positions/{symbol}/protection")
def set_protection(symbol: str, body: ProtectionBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.paper_broker_v2.set_protection(symbol, stop_loss=body.stop_loss,
                                                   take_profit=body.take_profit,
                                                   trailing_offset=body.trailing_offset)
    except ValueError as exc:
        _error(exc)


@router.post("/paper-v2/process/{symbol}")
def process_latest_candle(symbol: str, timeframe: str = "1h",
                          x_webhook_secret: Optional[str] = Header(default=None)):
    """Advance orders from the cached real candle only (no supplied price)."""
    _wa._check_secret(x_webhook_secret)
    try:
        candle = _wa.v2_market_data.latest(symbol, timeframe)
        if candle is None:
            raise ValueError("no real historical candle cached; download history first")
        return _wa.paper_broker_v2.process_candle(symbol, candle)
    except Exception as exc:
        _error(exc)
