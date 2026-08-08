from __future__ import annotations

import sqlite3
from pathlib import Path

from scripts.trading_performance_forensics import effective_config, inspect_v2_cache


def test_effective_config_gives_manual_symbol_runtime_precedence():
    env = {"HUB_AUTO_SYMBOLS": "BTCUSDT,ETHUSDT", "HUB_AUTO_TIMEFRAME": "4h"}
    result = effective_config(env, {
        "symbol_selection_mode": "manual", "manual_symbol": "sol/usdt",
        "engine_timeframe": "15m", "entry_mode": "market",
    })
    assert result["symbols"] == ["SOLUSDT"]
    assert result["symbol_source"] == "runtime.manual_symbol"
    assert result["timeframe"] == "15m"
    assert result["entry_mode"] == "market"


def test_v2_cache_inspection_is_read_only_and_reports_integrity(tmp_path: Path):
    path = tmp_path / "crypto" / "BTCUSDT.sqlite3"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE candles(timeframe TEXT, open_time INTEGER)")
        db.executemany("INSERT INTO candles VALUES (?, ?)", [
            ("1h", 0), ("1h", 7_200_000), ("1h", 3_600_000), ("1h", 7_200_000),
        ])
    before = path.stat().st_mtime_ns
    result = inspect_v2_cache(tmp_path, "BTCUSDT", "1h", now_ms=10_800_000)
    assert result["bars"] == 4
    assert result["missing_bars"] == 0
    assert result["duplicate_bars"] == 1
    assert result["out_of_order_bars"] == 1
    assert result["indicator_history_ready"] is False
    assert path.stat().st_mtime_ns == before


def test_v2_missing_cache_reports_fail_closed_reason(tmp_path: Path):
    result = inspect_v2_cache(tmp_path, "BTCUSDT", "1h")
    assert result["reason"] == "Market Data V2 cache required"
    assert result["get_bars_expected"] == "empty"
