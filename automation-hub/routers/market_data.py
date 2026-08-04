"""Production-safe Market Data Manager endpoints (additive to /market-data-v2)."""
from __future__ import annotations
import webhook_api as _wa
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(tags=["market-data"])

class Request(BaseModel):
    symbol: str
    timeframe: str = "1h"
    period: str = "90d"

def _call(fn):
    try: return fn()
    except Exception as exc: raise HTTPException(400, detail={"code": "market_data_error", "message": str(exc)}) from exc

@router.get("/market-data/providers")
def providers(): return {"providers": _wa.v2_market_data.registry.status()}

@router.get("/market-data/symbols")
def symbols(): return {"symbols": _wa.v2_market_data.crypto_perpetuals(), "canonical_examples": ["BTC/USDT", "AAPL", "EUR/USD", "XAU/USD"]}

@router.get("/market-data/status")
def status(symbol: str, timeframe: str = "1h"): return _call(lambda: _wa.v2_market_data.status(symbol, timeframe))

@router.get("/market-data/quality")
def quality(symbol: str, timeframe: str = "1h"): return _call(lambda: _wa.v2_market_data.quality(symbol, timeframe))

@router.get("/market-data/gaps")
def gaps(symbol: str, timeframe: str = "1h"): return _call(lambda: {"symbol": symbol, "timeframe": timeframe, "gaps": _wa.v2_market_data.quality(symbol, timeframe)["gaps"]})

@router.post("/market-data/download")
def download(body: Request, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret); return _call(lambda: _wa.v2_market_data.download(body.symbol, body.timeframe, period=body.period))

@router.post("/market-data/update")
def update(body: Request, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret); return _call(lambda: _wa.v2_market_data.update(body.symbol, body.timeframe))

@router.post("/market-data/repair")
def repair(body: Request, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret); return _call(lambda: _wa.v2_market_data.repair(body.symbol, body.timeframe))

@router.delete("/market-data/cache/{symbol}/{timeframe}")
def delete_cache(symbol: str, timeframe: str, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret); return _call(lambda: _wa.v2_market_data.delete_cache(symbol, timeframe))
