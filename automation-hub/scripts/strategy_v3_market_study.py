#!/usr/bin/env python3
"""Train-only V3 market-behaviour study.

This is deliberately *not* a strategy backtest.  It reads a fixed January--June
2025 train window from official Binance Vision spot OHLCV archives and measures
simple, causal behaviours that could justify a future hypothesis.  The sealed
October--December 2025 set is inaccessible to this command by construction.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "automation-hub"
for item in (str(HUB), str(ROOT)):
    if item not in sys.path:
        sys.path.insert(0, item)

from bot.data.resample import resample, tf_seconds  # noqa: E402
from bot.types import Bar  # noqa: E402

UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = ("5m", "15m", "1h")
TRAIN_START = datetime(2025, 1, 1, tzinfo=UTC)
VALIDATION_START = datetime(2025, 7, 1, tzinfo=UTC)
TEST_START = datetime(2025, 10, 1, tzinfo=UTC)
TRAIN_MONTHS = tuple(range(1, 7))


class SealedDatasetAccessError(RuntimeError):
    """Raised before any non-train archive may be opened."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_train_only(months: tuple[int, ...], end: datetime) -> None:
    """Enforce the sealed boundaries *before* touching an archive path."""
    if tuple(months) != TRAIN_MONTHS or end > VALIDATION_START:
        raise SealedDatasetAccessError(
            "V3 discovery is train-only (2025-01-01 through 2025-06-30); "
            "validation and Oct--Dec 2025 remain sealed."
        )


def load_train_symbol(data_dir: Path, symbol: str, *, months: tuple[int, ...] = TRAIN_MONTHS,
                      end: datetime = VALIDATION_START) -> tuple[list[Bar], dict]:
    assert_train_only(months, end)
    bars: list[Bar] = []
    archives: dict[str, str] = {}
    for month in months:
        path = data_dir / f"{symbol}-5m-2025-{month:02d}.zip"
        if not path.exists():
            raise RuntimeError(f"missing official train archive: {path.name}")
        archives[path.name] = sha256_file(path)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise RuntimeError(f"{path.name}: expected exactly one CSV member")
            with archive.open(names[0]) as raw:
                for row in csv.reader(line.decode("utf-8") for line in raw):
                    if not row:
                        continue
                    stamp = int(row[0])
                    divisor = 1_000_000 if stamp > 100_000_000_000_000 else 1_000
                    bars.append(Bar(
                        datetime.fromtimestamp(stamp / divisor, tz=UTC),
                        float(row[1]), float(row[2]), float(row[3]),
                        float(row[4]), float(row[5]),
                    ))
    bars.sort(key=lambda item: item.timestamp)
    if not bars or bars[0].timestamp != TRAIN_START or bars[-1].timestamp >= VALIDATION_START:
        raise RuntimeError(f"{symbol}: train coverage is invalid")
    duplicates = sum(a.timestamp == b.timestamp for a, b in zip(bars, bars[1:]))
    missing = sum(max(0, round((b.timestamp - a.timestamp).total_seconds() / 300) - 1)
                  for a, b in zip(bars, bars[1:]))
    invalid = sum(not (bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high)
                  for bar in bars)
    if duplicates or missing or invalid:
        raise RuntimeError(f"{symbol}: duplicates={duplicates}, gaps={missing}, invalid={invalid}")
    return bars, {
        "exchange": "Binance Spot", "provenance": "official Binance Vision monthly kline archives",
        "symbol": symbol, "timeframe": "5m", "first": bars[0].timestamp.isoformat(),
        "last": bars[-1].timestamp.isoformat(), "candles": len(bars), "gaps": missing,
        "duplicates": duplicates, "invalid_ohlc": invalid, "archives": archives,
    }


def atr(bars: list[Bar], index: int, period: int = 20) -> float | None:
    if index < period:
        return None
    values = []
    for pos in range(index - period + 1, index + 1):
        prior = bars[pos - 1].close if pos else bars[pos].close
        values.append(max(bars[pos].high - bars[pos].low, abs(bars[pos].high - prior),
                          abs(bars[pos].low - prior)))
    return sum(values) / len(values)


def efficiency(bars: list[Bar], index: int, period: int = 20) -> float | None:
    if index < period:
        return None
    net = abs(bars[index].close - bars[index - period].close)
    path = sum(abs(bars[p].close - bars[p - 1].close) for p in range(index - period + 1, index + 1))
    return net / path if path else 0.0


def session_name(stamp: datetime) -> str:
    hour = stamp.hour
    if hour < 8:
        return "Asia"
    if hour < 13:
        return "Europe"
    if hour < 17:
        return "US-Europe overlap"
    if hour < 21:
        return "US"
    return "Late UTC"


def sample_summary(values: list[float]) -> dict:
    """Descriptive evidence only; no return is interpreted as profitability."""
    if not values:
        return {"n": 0, "mean_bps": None, "median_bps": None, "positive_rate": None,
                "positive_rate_wilson_lower_95": None}
    n = len(values)
    wins = sum(value > 0 for value in values)
    p = wins / n
    z = 1.96
    lower = (p + z*z/(2*n) - z * math.sqrt((p*(1-p) + z*z/(4*n))/n)) / (1 + z*z/n)
    return {"n": n, "mean_bps": round(statistics.fmean(values), 4),
            "median_bps": round(statistics.median(values), 4), "positive_rate": round(p, 4),
            "positive_rate_wilson_lower_95": round(lower, 4)}


def add(bucket: dict[str, list[float]], name: str, value: float) -> None:
    bucket[name].append(value)


def study_series(bars: list[Bar], timeframe: str) -> dict:
    """Causal event studies; each outcome begins after its condition is known."""
    seconds = tf_seconds(timeframe) or 300
    h1 = max(1, 3600 // seconds)
    h6 = max(1, 21600 // seconds)
    events: dict[str, list[float]] = defaultdict(list)
    false_breakouts: dict[str, list[bool]] = defaultdict(list)
    regimes: dict[str, list[float]] = defaultdict(list)
    sessions: dict[str, list[float]] = defaultdict(list)
    persistence: dict[str, list[float]] = defaultdict(list)
    # 60 bars is sufficient for the longest causal lookback below.
    for i in range(60, len(bars) - h6):
        now, future_1h, future_6h = bars[i], bars[i + h1], bars[i + h6]
        a20, a10, a50 = atr(bars, i, 20), atr(bars, i, 10), atr(bars, i, 50)
        er20 = efficiency(bars, i, 20)
        if not a20 or not a10 or not a50 or er20 is None:
            continue
        forward_1h = (future_1h.close / now.close - 1) * 10_000
        forward_6h = (future_6h.close / now.close - 1) * 10_000
        direction_20 = 1 if now.close > bars[i - 20].close else -1
        signed_1h, signed_6h = direction_20 * forward_1h, direction_20 * forward_6h
        volatility_ratio = a10 / a50
        regime = "trend" if er20 >= .45 else "range"
        regime += "_high_vol" if volatility_ratio >= 1.25 else "_low_vol" if volatility_ratio <= .8 else "_normal_vol"
        add(regimes, regime, signed_6h)
        add(sessions, session_name(now.timestamp), signed_6h)
        # Regime persistence: is the same regime still observable six hours later?
        future_er = efficiency(bars, i + h6, 20)
        future_a10, future_a50 = atr(bars, i + h6, 10), atr(bars, i + h6, 50)
        if future_er is not None and future_a10 and future_a50:
            future_label = ("trend" if future_er >= .45 else "range")
            future_label += "_high_vol" if future_a10 / future_a50 >= 1.25 else "_low_vol" if future_a10 / future_a50 <= .8 else "_normal_vol"
            persistence[regime].append(10_000.0 if future_label == regime else 0.0)
        # Trend persistence and momentum continuation need a sufficiently directional prior move.
        if er20 >= .45 and abs(now.close - bars[i - 20].close) >= a20:
            add(events, f"trend_persistence_{'long' if direction_20 > 0 else 'short'}", signed_6h)
            add(events, f"momentum_continuation_{'long' if direction_20 > 0 else 'short'}", signed_1h)
        # Pullback: directional context, then a 5-bar countertrend movement of >= half an ATR.
        pullback = direction_20 * (now.close - bars[i - 5].close)
        if er20 >= .45 and pullback <= -.5 * a20:
            add(events, f"pullback_continuation_{'long' if direction_20 > 0 else 'short'}", signed_6h)
        # 20-bar close breakout. A false breakout is measured after the breakout (re-entry in 1h).
        highest = max(item.high for item in bars[i - 20:i])
        lowest = min(item.low for item in bars[i - 20:i])
        if now.close > highest:
            add(events, "breakout_follow_through_long", forward_6h)
            false_breakouts["long"].append(future_1h.close <= highest)
        elif now.close < lowest:
            add(events, "breakout_follow_through_short", -forward_6h)
            false_breakouts["short"].append(future_1h.close >= lowest)
        # Volatility state is known at i. Score directionless future movement by absolute expansion.
        if volatility_ratio >= 1.25:
            add(events, "volatility_expansion_abs_move", abs(forward_6h))
        elif volatility_ratio <= .8:
            add(events, "volatility_contraction_abs_move", abs(forward_6h))
        # Mean-reversion event uses a trailing SMA only; result points toward its prior mean.
        mean20 = sum(item.close for item in bars[i - 19:i + 1]) / 20
        deviation = now.close - mean20
        if abs(deviation) >= 1.5 * a20:
            add(events, f"mean_reversion_{'from_high' if deviation > 0 else 'from_low'}", -1 * (1 if deviation > 0 else -1) * forward_1h)
    return {
        "bars": len(bars), "coverage": [bars[0].timestamp.isoformat(), bars[-1].timestamp.isoformat()],
        "horizons": {"one_hour_bars": h1, "six_hour_bars": h6},
        "events": {key: sample_summary(value) for key, value in sorted(events.items())},
        "false_breakout_frequency": {
            side: {"n": len(values), "rate": round(sum(values) / len(values), 4)}
            for side, values in sorted(false_breakouts.items()) if values
        },
        "regimes": {key: sample_summary(value) for key, value in sorted(regimes.items())},
        "sessions": {key: sample_summary(value) for key, value in sorted(sessions.items())},
        "regime_persistence": {key: {"n": len(value), "same_regime_rate": round(sum(v > 0 for v in value) / len(value), 4)}
                               for key, value in sorted(persistence.items()) if value},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HUB / "data" / "strategy_v3_market_study.json")
    args = parser.parse_args()
    raw, inventory = {}, {}
    for symbol in SYMBOLS:
        raw[symbol], inventory[symbol] = load_train_symbol(args.data_dir, symbol)
    result = {
        "protocol": "V3 market-behaviour discovery; train only; not a strategy backtest",
        "sealed_boundaries": {"train": [TRAIN_START.isoformat(), VALIDATION_START.isoformat()],
                              "validation_sealed_from": VALIDATION_START.isoformat(),
                              "untouched_test_sealed_from": TEST_START.isoformat(),
                              "test_data_opened": False},
        "inventory": inventory, "study": {},
    }
    for symbol, bars in raw.items():
        series = {"5m": bars, "15m": resample(bars, "15m", "5m"), "1h": resample(bars, "1h", "5m")}
        result["study"][symbol] = {timeframe: study_series(rows, timeframe) for timeframe, rows in series.items()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "test_data_opened": False,
                      "symbols": list(SYMBOLS), "timeframes": list(TIMEFRAMES)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
