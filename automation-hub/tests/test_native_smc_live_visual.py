from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from services.native_smc_live_visual import live_visual_history, live_visual_state


UTC = timezone.utc


def _bars(symbol: str, timeframe: str, venue: str, limit: int):
    assert (symbol, timeframe, venue) == ("BTCUSDT", "5m", "binance_usdm")
    start = datetime(2025, 3, 1, tzinfo=UTC)
    return [Bar(start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i, 10 + i) for i in range(250)]


def test_live_visual_uses_only_closed_bars_and_never_enables_execution():
    now = datetime(2025, 3, 1, tzinfo=UTC) + timedelta(minutes=5 * 250)
    state = live_visual_state(now=now, fetcher=_bars)
    assert state["execution_allowed"] is False
    assert state["source_strategy"]["paper_only"] is True
    assert state["source_strategy"]["execution_allowed"] is False
    assert len(state["candles"]) == 240
    assert state["forming_candle"] is None
    assert state["live_display"]["execution_uses_closed_bars_only"] is True
    assert state["data_provenance"]["mode"] == "LIVE_EXCHANGE_DISPLAY_WITH_CLOSED_BAR_SMC"
    assert state["data_provenance"]["venue"] == "Binance USDⓈ-M Futures"
    assert state["data_provenance"]["forming_candle_excluded"] is False


def test_live_visual_renders_forming_candle_without_feeding_it_to_smc():
    start = datetime(2025, 3, 1, tzinfo=UTC)
    now = start + timedelta(minutes=5 * 250)

    def source(symbol: str, timeframe: str, venue: str, limit: int):
        rows = _bars(symbol, timeframe, venue, limit)
        # This candle has just opened at ``now`` and must be visual-only.
        return rows + [Bar(now, 999, 1_005, 995, 1_002, 123)]

    state = live_visual_state(now=now, fetcher=source)
    forming = state["forming_candle"]
    assert forming["timestamp"] == now.isoformat()
    assert forming["close"] == 1_002
    assert all(row["timestamp"] != forming["timestamp"] for row in state["candles"])
    assert state["data_provenance"]["forming_candle_excluded"] is True
    assert state["live_display"]["is_forming"] is True
    assert state["live_display"]["last_price"] == 1_002
    assert state["live_display"]["price_direction"] == "unchanged"
    assert state["execution_allowed"] is False


def test_live_visual_rejects_unknown_venue():
    with pytest.raises(ValueError, match="venue"):
        live_visual_state(venue="unknown", fetcher=_bars)


def test_live_history_is_closed_only_ordered_and_execution_disabled():
    start = datetime(2025, 3, 1, tzinfo=UTC)
    before = start + timedelta(minutes=5 * 200)
    now = start + timedelta(minutes=5 * 251)
    received_since = []

    def source(symbol: str, timeframe: str, venue: str, limit: int, *, since_ms=None):
        received_since.append(since_ms)
        return _bars(symbol, timeframe, venue, limit)

    page = live_visual_history(before=before, limit=60, now=now, fetcher=source)

    timestamps = [row["timestamp"] for row in page["candles"]]
    assert received_since and received_since[0] is not None
    assert page["execution_allowed"] is False
    assert page["data_provenance"]["mode"] == "LIVE_EXCHANGE_HISTORICAL_DISPLAY_PAGE"
    assert len(timestamps) == 60
    assert timestamps == sorted(timestamps)
    assert len(timestamps) == len(set(timestamps))
    assert all(value < before.isoformat() for value in timestamps)
    assert page["newest"] == timestamps[-1]
    assert page["has_more_history"] is True


def test_live_price_direction_is_factual_and_never_interpolated():
    from services.native_smc_live_visual import _price_direction

    assert _price_direction(100.0, 101.0) == "up"
    assert _price_direction(101.0, 100.0) == "down"
    assert _price_direction(100.0, 100.0) == "unchanged"


@pytest.mark.parametrize("timeframe", ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"])
def test_live_visual_accepts_each_supported_comparison_timeframe(timeframe):
    # Validation is intentionally separated from fetching so every intended
    # TradingView comparison interval has an explicit server-side contract.
    from services.native_smc_live_visual import _validate
    assert _validate("BTCUSDT", timeframe, "mexc_perpetual")[1] == timeframe
