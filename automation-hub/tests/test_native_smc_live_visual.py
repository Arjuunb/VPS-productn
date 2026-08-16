from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from services.native_smc_live_visual import live_visual_state


UTC = timezone.utc


def _bars(symbol: str, timeframe: str, venue: str, limit: int):
    assert (symbol, timeframe, venue) == ("BTCUSDT", "5m", "mexc_perpetual")
    start = datetime(2025, 3, 1, tzinfo=UTC)
    return [Bar(start + timedelta(minutes=5 * i), 100 + i, 102 + i, 99 + i, 101 + i, 10 + i) for i in range(250)]


def test_live_visual_uses_only_closed_bars_and_never_enables_execution():
    now = datetime(2025, 3, 1, tzinfo=UTC) + timedelta(minutes=5 * 250)
    state = live_visual_state(now=now, fetcher=_bars)
    assert state["execution_allowed"] is False
    assert len(state["candles"]) == 240
    assert state["forming_candle"] is None
    assert state["live_display"]["execution_uses_closed_bars_only"] is True
    assert state["data_provenance"]["mode"] == "LIVE_EXCHANGE_DISPLAY_WITH_CLOSED_BAR_SMC"
    assert state["data_provenance"]["venue"] == "MEXC perpetual"
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
