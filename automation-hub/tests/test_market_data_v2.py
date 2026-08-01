"""V2 market-data cache tests: provider data only, no invented candles."""
from pathlib import Path

import pytest

from data.market_data_v2 import MarketDataService, MarketDataUpdateJob, TF_MS, candles_for_period


def test_crypto_download_is_cached_with_metadata_and_no_duplicates(tmp_path):
    step = TF_MS["1h"]

    def provider(url, params):
        assert "/fapi/" in url and params["symbol"] == "BTCUSDT"
        return [[0, "100", "110", "90", "105", "8"],
                [step, "105", "115", "95", "110", "9"]]

    service = MarketDataService(tmp_path / "market_data", request_json=provider)
    result = service.download("BTCUSDT", "1h", candles=2)
    assert result["provider"] == "binance-usdt-perpetual"
    # A repeated provider response replaces the same primary keys, it does not
    # duplicate candles or manufacture a third one.
    service.download("BTCUSDT", "1h", candles=2)
    state = service.status("BTCUSDT", "1h")
    assert state["integrity"]["candles"] == 2
    assert state["integrity"]["timezone"] == "UTC"
    assert list((tmp_path / "market_data" / "crypto").glob("BTCUSDT.sqlite3"))


def test_corrupt_provider_rows_are_rejected_not_cached(tmp_path):
    service = MarketDataService(tmp_path / "market_data", request_json=lambda *_: [])
    with pytest.raises(ValueError, match="no valid"):
        service.upsert("BTCUSDT", "1h", [(0, 100, 90, 95, 98, 1)], provider="test")
    assert service.bars("BTCUSDT", "1h") == []


def test_gap_is_reported_and_not_interpolated(tmp_path):
    service = MarketDataService(tmp_path / "market_data", request_json=lambda *_: [])
    step = TF_MS["1h"]
    service.upsert("BTCUSDT", "1h", [(0, 1, 2, 1, 2, 3), (3 * step, 2, 3, 1, 2, 4)], provider="test")
    status = service.status("BTCUSDT", "1h")
    assert len(status["integrity"]["missing_ranges"]) == 1
    assert len(service.bars("BTCUSDT", "1h")) == 2


def test_unknown_symbol_fails_closed(tmp_path):
    service = MarketDataService(tmp_path / "market_data", request_json=lambda *_: [])
    assert service.status("???", "1h")["available"] is False
    with pytest.raises(ValueError, match="unavailable"):
        service.download("???", "1h")


def test_binance_perpetual_discovery_filters_to_active_usdt_contracts(tmp_path):
    def provider(url, params):
        assert url.endswith("/exchangeInfo")
        return {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
            {"symbol": "BTCUSD_PERP", "status": "TRADING", "quoteAsset": "USD", "contractType": "PERPETUAL"},
            {"symbol": "OLDUSDT", "status": "BREAK", "quoteAsset": "USDT", "contractType": "PERPETUAL"},
        ]}
    assert MarketDataService(tmp_path / "market_data", request_json=provider).crypto_perpetuals() == ["BTCUSDT"]


def test_provider_candle_aggregation_omits_incomplete_groups(tmp_path):
    service = MarketDataService(tmp_path / "market_data", request_json=lambda *_: [])
    minute = TF_MS["1m"]
    rows = [(0, 1, 2, 0.5, 1.5, 10), (minute, 1.5, 3, 1, 2, 20),
            (2 * minute, 2, 4, 1.5, 3, 30), (5 * minute, 9, 9, 9, 9, 9)]
    # The output comes solely from three adjacent provider rows. The later
    # singleton is not interpolated into a false 3-minute candle.
    assert service._aggregate_rows(rows, minute, 3) == [(0, 1, 4, 0.5, 3, 60)]


def test_shared_legacy_facade_can_be_opted_into_strict_v2_cache(tmp_path, monkeypatch):
    import config
    from data.market_data import get_bars

    root = tmp_path / "market_data"
    service = MarketDataService(root, request_json=lambda *_: [])
    service.upsert("BTCUSDT", "1h", [(0, 1, 2, 1, 1.5, 3)], provider="test")
    monkeypatch.setattr(config.settings, "market_data_v2_dir", str(root))
    monkeypatch.setenv("HUB_MARKET_DATA_V2", "1")
    bars, source = get_bars("BTCUSDT", n=5, timeframe="1h")
    assert len(bars) == 1 and source == "market-data-v2 (real cache)"
    missing, unavailable = get_bars("MISSINGUSDT", n=5, timeframe="1h")
    assert missing == [] and "cache required" in unavailable


def test_download_pages_binance_history_beyond_one_provider_page(tmp_path):
    step, calls = TF_MS["1h"], []

    def provider(url, params):
        calls.append(params.copy())
        limit = params["limit"]
        end = params.get("endTime", 3_000 * step - 1)
        last = (end // step) * step
        first = last - (limit - 1) * step
        return [[t, "1", "2", "1", "1.5", "10"] for t in range(first, last + step, step)]

    service = MarketDataService(tmp_path / "market", request_json=provider)
    result = service.download("BTCUSDT", "1h", candles=1600)
    assert result["stored"] == 1600
    assert len(calls) == 2 and calls[0]["limit"] == 1500 and calls[1]["limit"] == 100


def test_named_history_periods_are_timeframe_aware():
    assert candles_for_period("1h", "90d") == 2160
    assert candles_for_period("4h", "6mo") == int(183 * 6)
    assert candles_for_period("1d", "1y") == 365
    assert candles_for_period("1m", "max") == 200_000


def test_background_update_job_tracks_each_requested_series():
    class Service:
        def update(self, symbol, timeframe):
            return {"symbol": symbol, "timeframe": timeframe, "stored": 1}

    job = MarketDataUpdateJob(Service())
    result = job.start(["BTCUSDT", "ETHUSDT"], ["1h"])
    assert result["started"] is True
    job._thread.join(timeout=1)  # deterministic local test of the background worker
    state = job.status()
    assert state["running"] is False and state["done"] == 2 and len(state["results"]) == 2
