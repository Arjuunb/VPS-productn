import pytest
import time
from data.market_data_reliability import CanonicalCandle, CanonicalSymbol, ProviderRegistry, ResilientRequester
from data.market_data_v2 import MarketDataService

def test_canonical_symbols_and_provider_mapping():
    assert CanonicalSymbol.parse("BTCUSDT").value == "BTC/USDT"
    assert CanonicalSymbol.parse("EURUSD").value == "EUR/USD"
    assert CanonicalSymbol.parse("BTC/USDT").provider_symbol("binance-futures") == "BTCUSDT"

def test_canonical_candle_rejects_partial_and_impossible_values():
    with pytest.raises(ValueError):
        CanonicalCandle("BTC/USDT", "1h", 1, 2, 1, 1, 2, 1, "binance-futures", "crypto", True, "now").validate()
    with pytest.raises(ValueError):
        CanonicalCandle("BTC/USDT", "1h", 1, 1, 2, 0, 1, 1, "binance-futures", "crypto", False, "now").validate()

def test_retry_registry_and_deterministic_checksum(tmp_path):
    registry, calls = ProviderRegistry(), []
    def flaky(*_):
        calls.append(1)
        if len(calls) < 3: raise RuntimeError("temporary")
        return {"ok": True}
    assert ResilientRequester(flaky, registry, "binance-futures")("x", {}) == {"ok": True}
    assert registry.provider("binance-futures").metrics["retries"] == 2
    service = MarketDataService(tmp_path / "cache", request_json=lambda *_: [])
    service.upsert("BTCUSDT", "1h", [(0, 1, 2, 0, 1, 3)], provider="binance-futures")
    assert service.status("BTCUSDT", "1h")["checksum_ok"]
    assert service.quality("BTCUSDT", "1h")["quality_score"] in (60, 100)

def test_cache_checksum_mismatch_is_not_available(tmp_path):
    service = MarketDataService(tmp_path / "cache", request_json=lambda *_: [])
    service.upsert("BTCUSDT", "1h", [(0, 1, 2, 0, 1, 3)], provider="binance-futures")
    c = service._conn("BTCUSDT"); c.execute("UPDATE candles SET close=1.5"); c.commit(); c.close()
    status = service.status("BTCUSDT", "1h")
    assert status["available"] is False and status["quarantined_cache"]

def test_forming_candle_is_not_persisted(tmp_path):
    service = MarketDataService(tmp_path / "cache", request_json=lambda *_: [])
    now = int(time.time() * 1000)
    with pytest.raises(ValueError, match="no valid"):
        service.upsert("BTCUSDT", "1h", [(now, 1, 2, 0, 1, 3)], provider="binance-futures")

def test_cache_without_version_checksum_fails_closed(tmp_path):
    service = MarketDataService(tmp_path / "cache", request_json=lambda *_: [])
    c = service._conn("BTCUSDT"); c.execute("INSERT INTO candles(timeframe,open_time,open,high,low,close,volume) VALUES ('1h',0,1,2,0,1,3)"); c.commit(); c.close()
    assert service.status("BTCUSDT", "1h")["available"] is False
    assert service.bars("BTCUSDT", "1h") == []

def test_reliability_api_exposes_provider_registry():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webhook_api
    app = FastAPI(); app.include_router(webhook_api.router)
    response = TestClient(app).get("/market-data/providers")
    assert response.status_code == 200
    assert {p["name"] for p in response.json()["providers"]} >= {"binance-futures", "yahoo-finance"}
