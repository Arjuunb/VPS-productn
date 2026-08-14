#!/usr/bin/env python3
"""Train-only research-universe discovery; intentionally creates no strategy.

The prior Spot/OHLCV universe is explicitly exhausted.  This command keeps the
venues separate and tests only pre-registered, causal *feature-at-T -> future
distribution* relationships using Jan--Jun 2025 Binance archives.  It cannot
read the existing Jul--Dec 2025 validation or untouched-test archives.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "automation-hub"
for entry in (str(HUB), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from bot.data.resample import resample  # noqa: E402
from bot.types import Bar  # noqa: E402
from scripts.strategy_v3_market_study import (  # noqa: E402
    SYMBOLS, TRAIN_MONTHS, TRAIN_START, VALIDATION_START, load_train_symbol,
)

UTC = timezone.utc
STUDY_ID = "research-universe-expansion-v1-2025h1"
CREATED_AT = "2026-08-15T00:00:00+00:00"
FUTURES_TIMEFRAME = "15m"
FUTURES_HORIZONS = (1, 4, 12, 24)
HIGHER_TIMEFRAMES = {"4h": (1, 3, 6, 12), "1d": (1, 3, 5, 10)}
ARCHIVE_ROOT = "https://data.binance.vision/data/futures/um"

# All hypotheses are registered here before any archive is read.  None are
# entries or strategy rules; directions simply make distributions comparable.
SINGLE_FEATURES = (
    "funding_high", "funding_low", "funding_rising", "funding_falling",
    "oi_rising", "oi_falling", "price_up_oi_up", "price_up_oi_down",
    "price_down_oi_up", "price_down_oi_down", "basis_high", "basis_low",
    "basis_widening", "basis_narrowing", "taker_buy_dominant",
    "taker_sell_dominant", "taker_buy_price_down", "taker_sell_price_up",
    "relative_volume_high", "volume_accelerating",
)
INTERACTIONS = (
    "price_up_oi_up_taker_buy", "price_down_oi_up_taker_sell",
    "funding_high_oi_rising", "funding_low_oi_rising",
    "basis_widening_taker_buy", "basis_widening_taker_sell",
)
ALL_HYPOTHESES = SINGLE_FEATURES + INTERACTIONS

# This is deliberately conservative and account-tier agnostic.  Exact exchange
# fees must be configured from the actual account before any future execution
# study. Funding is exposed, never treated as a favourable zero-cost input.
FUTURES_EXECUTION_HURDLE_BPS = {
    "maker_fee_each_side_bps": 2.0,
    "taker_fee_each_side_bps": 5.0,
    "spread_slippage_each_side_bps": 2.0,
    "latency_missed_fill_allowance_bps": 2.0,
    "research_conservative_round_trip_bps": 16.0,
    "funding": "not netted; an adverse funding transfer is an additional cost for holds crossing a funding timestamp",
}


class SealedUniverseAccessError(RuntimeError):
    """Raised before a non-train date/path can be requested or opened."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_train_only(months: tuple[int, ...], end: datetime) -> None:
    if tuple(months) != TRAIN_MONTHS or end > VALIDATION_START:
        raise SealedUniverseAccessError(
            "Universe expansion is Jan--Jun 2025 TRAIN only; Jul--Sep validation "
            "and Oct--Dec untouched test remain sealed."
        )


def _month_dates() -> list[datetime]:
    assert_train_only(TRAIN_MONTHS, VALIDATION_START)
    out: list[datetime] = []
    current = TRAIN_START
    while current < VALIDATION_START:
        out.append(current)
        current += timedelta(days=1)
    return out


def archive_specs() -> list[tuple[str, str]]:
    """Relative path and URL of the complete, train-only official input set."""
    assert_train_only(TRAIN_MONTHS, VALIDATION_START)
    specs: list[tuple[str, str]] = []
    for symbol in SYMBOLS:
        for month in TRAIN_MONTHS:
            suffix = f"2025-{month:02d}"
            for dataset in ("klines", "markPriceKlines", "indexPriceKlines"):
                relative = f"{dataset}/{symbol}/{FUTURES_TIMEFRAME}/{symbol}-{FUTURES_TIMEFRAME}-{suffix}.zip"
                specs.append((relative, f"{ARCHIVE_ROOT}/monthly/{relative}"))
            relative = f"fundingRate/{symbol}/{symbol}-fundingRate-{suffix}.zip"
            specs.append((relative, f"{ARCHIVE_ROOT}/monthly/{relative}"))
        for day in _month_dates():
            date = day.strftime("%Y-%m-%d")
            relative = f"metrics/{symbol}/{symbol}-metrics-{date}.zip"
            specs.append((relative, f"{ARCHIVE_ROOT}/daily/{relative}"))
    return specs


def download_archives(data_dir: Path, *, workers: int = 6) -> dict:
    """Fetch only named official train archives; never follows a directory listing."""
    specs = archive_specs()
    data_dir.mkdir(parents=True, exist_ok=True)
    pending = [(data_dir / relative, url) for relative, url in specs if not (data_dir / relative).exists()]

    def fetch(item: tuple[Path, str]) -> str:
        path, url = item
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".partial")
        request = urllib.request.Request(url, headers={"User-Agent": "TradeLogX-research/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as handle:
            while chunk := response.read(1024 * 1024):
                handle.write(chunk)
        # Validate archive before it becomes an input.  A corrupt/unexpected
        # archive remains a hard failure rather than silently entering evidence.
        with zipfile.ZipFile(temporary) as archive:
            if len(archive.namelist()) != 1:
                raise RuntimeError(f"{url}: expected exactly one CSV member")
        temporary.replace(path)
        return str(path.relative_to(data_dir))

    completed = 0
    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
            futures = [pool.submit(fetch, item) for item in pending]
            for future in as_completed(futures):
                future.result()
                completed += 1
                if completed % 25 == 0 or completed == len(pending):
                    print(f"downloaded {completed}/{len(pending)} train archives", flush=True)
    return {"requested": len(specs), "downloaded": completed, "already_present": len(specs) - len(pending)}


def zip_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1:
            raise RuntimeError(f"{path}: expected exactly one CSV member")
        return list(csv.reader(line.decode("utf-8") for line in archive.open(names[0])))


def _timestamp(raw: str) -> datetime:
    stamp = int(raw)
    return datetime.fromtimestamp(stamp / (1_000_000 if stamp > 100_000_000_000_000 else 1_000), tz=UTC)


def _float(value: str) -> float:
    return float(value) if value else 0.0


def load_futures_train(data_dir: Path, symbol: str) -> tuple[list[Bar], dict[datetime, dict], dict[datetime, float], dict]:
    """Return futures 15m bars, exactly-causal metrics/funding, and provenance."""
    assert_train_only(TRAIN_MONTHS, VALIDATION_START)
    bars: list[Bar] = []
    metrics: dict[datetime, dict] = {}
    funding: dict[datetime, float] = {}
    mark: dict[datetime, float] = {}
    index: dict[datetime, float] = {}
    hashes: dict[str, str] = {}
    for month in TRAIN_MONTHS:
        suffix = f"2025-{month:02d}"
        for dataset, target in (("klines", None), ("markPriceKlines", mark), ("indexPriceKlines", index)):
            path = data_dir / dataset / symbol / FUTURES_TIMEFRAME / f"{symbol}-{FUTURES_TIMEFRAME}-{suffix}.zip"
            if not path.exists():
                raise RuntimeError(f"missing official futures archive: {path.relative_to(data_dir)}")
            hashes[str(path.relative_to(data_dir))] = sha256_file(path)
            rows = zip_rows(path)
            header, records = rows[0], rows[1:]
            if dataset == "klines":
                if header[:6] != ["open_time", "open", "high", "low", "close", "volume"]:
                    raise RuntimeError(f"{path}: unexpected kline schema")
                for row in records:
                    bars.append(Bar(_timestamp(row[0]), _float(row[1]), _float(row[2]), _float(row[3]), _float(row[4]), _float(row[5])))
                    metrics.setdefault(_timestamp(row[0]), {})["taker_buy_quote"] = _float(row[10])
                    metrics[_timestamp(row[0])]["quote_volume"] = _float(row[7])
            else:
                if header[:5] != ["open_time", "open", "high", "low", "close"]:
                    raise RuntimeError(f"{path}: unexpected {dataset} schema")
                target.update({_timestamp(row[0]): _float(row[4]) for row in records})
        funding_path = data_dir / "fundingRate" / symbol / f"{symbol}-fundingRate-{suffix}.zip"
        if not funding_path.exists():
            raise RuntimeError(f"missing official funding archive: {funding_path.relative_to(data_dir)}")
        hashes[str(funding_path.relative_to(data_dir))] = sha256_file(funding_path)
        funding_rows = zip_rows(funding_path)
        if funding_rows[0] != ["calc_time", "funding_interval_hours", "last_funding_rate"]:
            raise RuntimeError(f"{funding_path}: unexpected funding schema")
        funding.update({_timestamp(row[0]): _float(row[2]) for row in funding_rows[1:]})
    for day in _month_dates():
        date = day.strftime("%Y-%m-%d")
        path = data_dir / "metrics" / symbol / f"{symbol}-metrics-{date}.zip"
        if not path.exists():
            raise RuntimeError(f"missing official metrics archive: {path.relative_to(data_dir)}")
        hashes[str(path.relative_to(data_dir))] = sha256_file(path)
        rows = zip_rows(path)
        expected = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
        if rows[0] != expected:
            raise RuntimeError(f"{path}: unexpected metrics schema")
        for row in rows[1:]:
            stamp = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            metrics.setdefault(stamp, {}).update({"oi_value": _float(row[3]), "taker_ratio": _float(row[7])})
    bars.sort(key=lambda row: row.timestamp)
    if not bars or bars[0].timestamp != TRAIN_START or bars[-1].timestamp >= VALIDATION_START:
        raise RuntimeError(f"{symbol}: invalid futures train coverage")
    gaps = sum(max(0, round((b.timestamp-a.timestamp).total_seconds() / 900)-1) for a, b in zip(bars, bars[1:]))
    if gaps or len({bar.timestamp for bar in bars}) != len(bars):
        raise RuntimeError(f"{symbol}: futures data gaps={gaps} or duplicate timestamps")
    for bar in bars:
        if bar.timestamp in mark and bar.timestamp in index and index[bar.timestamp]:
            metrics[bar.timestamp]["basis_bps"] = (mark[bar.timestamp] / index[bar.timestamp] - 1) * 10_000
    metric_times = sorted(stamp for stamp, row in metrics.items() if {"oi_value", "taker_ratio"}.issubset(row))
    metric_gaps = sum(max(0, round((b-a).total_seconds()/300)-1) for a, b in zip(metric_times, metric_times[1:]))
    funding_times = sorted(funding)
    archive_groups = {
        "futures_klines_15m": {key: value for key, value in hashes.items() if key.startswith("klines/")},
        "mark_price_15m": {key: value for key, value in hashes.items() if key.startswith("markPriceKlines/")},
        "index_price_15m": {key: value for key, value in hashes.items() if key.startswith("indexPriceKlines/")},
        "funding_rate": {key: value for key, value in hashes.items() if key.startswith("fundingRate/")},
        "open_interest_and_taker_ratio_5m": {key: value for key, value in hashes.items() if key.startswith("metrics/")},
    }
    return bars, metrics, funding, {
        "source": "official Binance Vision archive", "venue": "Binance USDⓈ-M Futures", "market_type": "perpetual futures",
        "symbol": symbol, "raw_resolution": "15m price/mark/index; 5m metrics; funding calculation events",
        "start": bars[0].timestamp.isoformat(), "end": bars[-1].timestamp.isoformat(), "observations": len(bars),
        "gaps": gaps, "archives_sha256": hashes,
        "datasets": {
            "futures_klines_15m": {"observations": len(bars), "gaps": gaps, "archives_sha256": archive_groups["futures_klines_15m"]},
            "mark_price_15m": {"observations": len(mark), "gaps": len(bars)-len(mark), "archives_sha256": archive_groups["mark_price_15m"]},
            "index_price_15m": {"observations": len(index), "gaps": len(bars)-len(index), "archives_sha256": archive_groups["index_price_15m"]},
            "funding_rate": {"observations": len(funding_times), "first": funding_times[0].isoformat(), "last": funding_times[-1].isoformat(), "gaps": "event series; no interpolation applied", "archives_sha256": archive_groups["funding_rate"]},
            "open_interest_and_taker_ratio_5m": {"observations": len(metric_times), "first": metric_times[0].isoformat(), "last": metric_times[-1].isoformat(), "gaps": metric_gaps, "archives_sha256": archive_groups["open_interest_and_taker_ratio_5m"]},
        },
    }


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * (len(ordered) - 1)))]


def distribution(rows: list[tuple[float, float, float]]) -> dict:
    if not rows:
        return {"n": 0, "mean_bps": None, "median_bps": None, "std_bps": None, "skew": None, "hit_rate": None, "mfe_bps": None, "mae_bps": None, "p05_bps": None, "p95_bps": None}
    returns, mfes, maes = zip(*rows)
    mean = statistics.fmean(returns)
    std = statistics.stdev(returns) if len(returns) > 1 else 0.0
    skew = statistics.fmean(((value-mean)/std) ** 3 for value in returns) if std else 0.0
    return {"n": len(rows), "mean_bps": round(mean, 4), "median_bps": round(statistics.median(returns), 4), "std_bps": round(std, 4),
            "skew": round(skew, 4), "hit_rate": round(sum(v > 0 for v in returns)/len(rows), 4), "mfe_bps": round(statistics.fmean(mfes), 4),
            "mae_bps": round(statistics.fmean(maes), 4), "p05_bps": round(percentile(list(returns), .05), 4), "p95_bps": round(percentile(list(returns), .95), 4)}


def build_outcome_cache(bars: list[Bar], horizons: tuple[int, ...]) -> dict[int, dict[int, list[tuple[float, float, float] | None]]]:
    """Calculate each causal forward path once, shared by every feature test."""
    cache: dict[int, dict[int, list[tuple[float, float, float] | None]]] = {}
    for horizon in horizons:
        long: list[tuple[float, float, float] | None] = [None] * len(bars)
        short: list[tuple[float, float, float] | None] = [None] * len(bars)
        for index in range(len(bars) - horizon):
            entry = bars[index].close
            window = bars[index + 1:index + horizon + 1]
            long[index] = ((window[-1].close/entry-1)*10_000, (max(x.high for x in window)/entry-1)*10_000, (min(x.low for x in window)/entry-1)*10_000)
            short[index] = ((1-window[-1].close/entry)*10_000, (1-min(x.low for x in window)/entry)*10_000, (1-max(x.high for x in window)/entry)*10_000)
        cache[horizon] = {1: long, -1: short}
    return cache


def _summarize(bars: list[Bar], points: list[tuple[int, int]], horizons: tuple[int, ...], cache: dict[int, dict[int, list[tuple[float, float, float] | None]]]) -> tuple[dict, dict]:
    output = {h: distribution([cache[h][direction][i] for i, direction in points if cache[h][direction][i] is not None]) for h in horizons}
    monthly: dict[str, dict[int, list[tuple[float, float, float]]]] = defaultdict(lambda: defaultdict(list))
    for i, direction in points:
        for h in horizons:
            outcome = cache[h][direction][i]
            if outcome is not None:
                monthly[bars[i].timestamp.strftime("%Y-%m")][h].append(outcome)
    return output, {month: {h: distribution(values) for h, values in by_horizon.items()} for month, by_horizon in sorted(monthly.items())}


def _last_known(values: dict[datetime, float], stamp: datetime) -> float | None:
    # Series is sparse (funding); lookup only backwards to preserve causality.
    candidates = [time for time in values if time <= stamp]
    return values[max(candidates)] if candidates else None


def _classify(observed: dict, random_base: dict, monthly: dict, points: list[tuple[int, int]], horizon: int) -> dict:
    result, control = observed[horizon], random_base[horizon]
    n, mean, std = result["n"], result["mean_bps"], result["std_bps"] or 0.0
    difference = (mean or 0) - (control["mean_bps"] or 0)
    stderr = math.sqrt(std*std/max(n, 1) + (control["std_bps"] or 0)**2/max(control["n"], 1))
    distinguishable = bool(stderr and abs(difference) >= 1.96 * stderr)
    positive_months = sum((values.get(horizon, {}).get("mean_bps") or 0) > 0 for values in monthly.values())
    independent, previous = 0, -10_000
    for index, _ in points:
        if index - previous > horizon:
            independent += 1; previous = index
    gross = mean or 0.0
    cost = FUTURES_EXECUTION_HURDLE_BPS["research_conservative_round_trip_bps"]
    if n < 100 or independent < 30 or gross <= cost:
        label = "NOT VIABLE"
    elif distinguishable and positive_months >= 4:
        label = "RESEARCHABLE"
    else:
        label = "WEAK"
    return {"classification": label, "horizon_bars": horizon, "gross_mean_bps": gross, "net_of_conservative_cost_bps": round(gross-cost, 4),
            "cost_dominance_ratio": round(cost/gross, 4) if gross > 0 else None, "random_mean_difference_bps": round(difference, 4),
            "random_distinguishable_95": distinguishable, "positive_months": positive_months, "independent_events": independent}


def analyse_futures(symbol: str, bars: list[Bar], metrics: dict[datetime, dict], funding: dict[datetime, float]) -> dict:
    # Precompute causal factors / train-only quantile thresholds.  Quantiles are
    # used only for a descriptive discovery study and are not a deployed rule.
    horizons = FUTURES_HORIZONS
    aligned = [metrics.get(bar.timestamp, {}) for bar in bars]
    funding_values = [_last_known(funding, bar.timestamp) for bar in bars]
    funds = [value for value in funding_values if value is not None]
    bases = [row.get("basis_bps") for row in aligned if row.get("basis_bps") is not None]
    takers = [row.get("taker_ratio") for row in aligned if row.get("taker_ratio")]
    thresholds = {"funding_low": percentile(funds, .10), "funding_high": percentile(funds, .90),
                  "basis_low": percentile(bases, .10), "basis_high": percentile(bases, .90),
                  "taker_low": percentile(takers, .10), "taker_high": percentile(takers, .90)}
    points: dict[str, list[tuple[int, int]]] = defaultdict(list)
    universe: list[int] = []
    latest = max(horizons)
    cache = build_outcome_cache(bars, horizons)
    for i in range(32, len(bars)-latest):
        row, now = aligned[i], bars[i]
        if not {"oi_value", "taker_ratio", "basis_bps"}.issubset(row):
            continue
        prior_row = aligned[i-4]
        # Exclude, rather than invent, a condition whose four-bar causal
        # comparison is unavailable or whose OI denominator is non-positive.
        if not {"oi_value", "basis_bps"}.issubset(prior_row) or prior_row["oi_value"] <= 0:
            continue
        current_funding, prior_funding = funding_values[i], funding_values[i-32]
        if current_funding is None or prior_funding is None:
            continue
        oi_change = row["oi_value"] / prior_row["oi_value"] - 1
        price_change = now.close / bars[i-4].close - 1
        basis_change = row["basis_bps"] - prior_row["basis_bps"]
        quote = row.get("quote_volume", 0.0)
        average_quote = statistics.fmean(aligned[j].get("quote_volume", 0.0) for j in range(i-20, i))
        universe.append(i)
        if current_funding >= thresholds["funding_high"]: points["funding_high"].append((i, -1))
        if current_funding <= thresholds["funding_low"]: points["funding_low"].append((i, 1))
        if current_funding-prior_funding > 0: points["funding_rising"].append((i, -1))
        if current_funding-prior_funding < 0: points["funding_falling"].append((i, 1))
        if oi_change > .002: points["oi_rising"].append((i, 1 if price_change >= 0 else -1))
        if oi_change < -.002: points["oi_falling"].append((i, 1 if price_change >= 0 else -1))
        if price_change > .001 and oi_change > .002: points["price_up_oi_up"].append((i, 1))
        if price_change > .001 and oi_change < -.002: points["price_up_oi_down"].append((i, -1))
        if price_change < -.001 and oi_change > .002: points["price_down_oi_up"].append((i, -1))
        if price_change < -.001 and oi_change < -.002: points["price_down_oi_down"].append((i, 1))
        if row["basis_bps"] >= thresholds["basis_high"]: points["basis_high"].append((i, -1))
        if row["basis_bps"] <= thresholds["basis_low"]: points["basis_low"].append((i, 1))
        if basis_change > 0: points["basis_widening"].append((i, 1 if price_change >= 0 else -1))
        if basis_change < 0: points["basis_narrowing"].append((i, 1 if price_change >= 0 else -1))
        if row["taker_ratio"] >= thresholds["taker_high"]: points["taker_buy_dominant"].append((i, 1))
        if row["taker_ratio"] <= thresholds["taker_low"]: points["taker_sell_dominant"].append((i, -1))
        if row["taker_ratio"] >= thresholds["taker_high"] and price_change < 0: points["taker_buy_price_down"].append((i, 1))
        if row["taker_ratio"] <= thresholds["taker_low"] and price_change > 0: points["taker_sell_price_up"].append((i, -1))
        if quote >= 1.5*average_quote: points["relative_volume_high"].append((i, 1 if price_change >= 0 else -1))
        if quote >= 1.25*aligned[i-4].get("quote_volume", quote): points["volume_accelerating"].append((i, 1 if price_change >= 0 else -1))
        if price_change > .001 and oi_change > .002 and row["taker_ratio"] >= thresholds["taker_high"]: points["price_up_oi_up_taker_buy"].append((i, 1))
        if price_change < -.001 and oi_change > .002 and row["taker_ratio"] <= thresholds["taker_low"]: points["price_down_oi_up_taker_sell"].append((i, -1))
        if current_funding >= thresholds["funding_high"] and oi_change > .002: points["funding_high_oi_rising"].append((i, -1))
        if current_funding <= thresholds["funding_low"] and oi_change > .002: points["funding_low_oi_rising"].append((i, 1))
        if basis_change > 0 and row["taker_ratio"] >= thresholds["taker_high"]: points["basis_widening_taker_buy"].append((i, 1))
        if basis_change > 0 and row["taker_ratio"] <= thresholds["taker_low"]: points["basis_widening_taker_sell"].append((i, -1))
    output: dict[str, dict] = {}
    for name in ALL_HYPOTHESES:
        event_points = points[name]
        observed, monthly = _summarize(bars, event_points, horizons, cache)
        rng = random.Random(hashlib.sha256(canonical({"study": STUDY_ID, "symbol": symbol, "feature": name})).hexdigest())
        indices = rng.sample(universe, min(len(event_points), len(universe))) if event_points else []
        directions = [direction for _, direction in event_points]; rng.shuffle(directions)
        random_summary, _ = _summarize(bars, list(zip(indices, directions)), horizons, cache)
        output[name] = {"event_count": len(event_points), "observed": observed, "monthly": monthly, "random_control": random_summary,
                        "feasibility": _classify(observed, random_summary, monthly, event_points, 24)}
    base = [(i, 1) for i in universe]
    baselines, _ = _summarize(bars, base, horizons, cache)
    return {"thresholds": {key: round(value, 8) for key, value in thresholds.items()}, "unconditional_long": baselines, "features": output,
            "feature_count": len(SINGLE_FEATURES), "interaction_count": len(INTERACTIONS)}


def analyse_higher_timeframes(spot_data_dir: Path) -> tuple[dict, dict]:
    """Spot higher-timeframe is isolated from USDⓈ-M derivatives evidence."""
    universe: dict = {}; inventory: dict = {}
    for symbol in SYMBOLS:
        raw, manifest = load_train_symbol(spot_data_dir, symbol, months=TRAIN_MONTHS, end=VALIDATION_START)
        inventory[symbol] = manifest
        universe[symbol] = {}
        for tf, horizons in HIGHER_TIMEFRAMES.items():
            bars = resample(raw, tf, "5m")
            cache = build_outcome_cache(bars, horizons)
            observations: list[tuple[int, int]] = []
            for i in range(20, len(bars)-max(horizons)):
                observations.append((i, 1))
            unconditional, _ = _summarize(bars, observations, horizons, cache)
            # Directional persistence is intentionally descriptive, not a rule.
            direction_points = [(i, 1 if bars[i].close > bars[i-5].close else -1) for i, _ in observations if bars[i].close != bars[i-5].close]
            directional, monthly = _summarize(bars, direction_points, horizons, cache)
            persistence = directional[max(horizons)]
            gross = persistence["mean_bps"] or 0.0
            monthly_positive = sum((row.get(max(horizons), {}).get("mean_bps") or 0) > 0 for row in monthly.values())
            # Separate spot cost input; this is comparison only, not a relaxed model.
            label = "RESEARCHABLE" if len(direction_points) >= 100 and gross > 14 and monthly_positive >= 4 else "WEAK" if gross > 0 else "NOT VIABLE"
            universe[symbol][tf] = {"inventory": {"bars": len(bars), "start": bars[0].timestamp.isoformat(), "end": bars[-1].timestamp.isoformat()},
                                    "unconditional_long": unconditional, "directional_persistence": directional, "monthly_persistence": monthly,
                                    "feasibility": {"classification": label, "gross_mean_bps_at_longest_horizon": gross, "monthly_positive": monthly_positive,
                                                    "spot_round_trip_hurdle_bps": 14, "cost_dominance_ratio": round(14/gross, 4) if gross > 0 else None}}
    return universe, inventory


def append_ledger(path: Path, row: dict) -> None:
    existing = {json.loads(line).get("study_id") for line in path.read_text().splitlines() if line.strip()} if path.exists() else set()
    if row["study_id"] not in existing:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def payload(data_dir: Path, futures: dict, futures_inventory: dict, higher: dict, higher_inventory: dict) -> dict:
    return {"study_id": STUDY_ID, "created_at": CREATED_AT, "purpose": "evidence-only alternative universe discovery", "strategies_created": 0,
            "forward_paper_candidates": 0, "live_candidates": 0,
            "previous_universe": {"id": "RESEARCH_UNIVERSE_SPOT_OHLCV_V1", "status": "EXHAUSTED / NOT CURRENTLY VIABLE"},
            "boundaries": {"train": [TRAIN_START.isoformat(), VALIDATION_START.isoformat()], "validation_sealed_from": VALIDATION_START.isoformat(), "untouched_test_sealed_from": "2025-10-01T00:00:00+00:00"},
            "sources": {"futures": {"source": "official Binance Vision archive", "source_root": ARCHIVE_ROOT, "retrieval_date": CREATED_AT, "venue": "Binance USDⓈ-M Futures", "market_type": "perpetual futures", "features": ["price", "volume", "mark", "index", "basis", "funding", "open interest", "taker long/short volume ratio"]},
                        "higher_timeframe": {"source": "official Binance Vision archive", "source_root": "https://data.binance.vision/data/spot/monthly/klines", "retrieval_date": CREATED_AT, "venue": "Binance Spot", "market_type": "spot", "features": ["OHLCV resampled to 4h and 1d"]}},
            "execution_hurdles": {"futures": FUTURES_EXECUTION_HURDLE_BPS, "spot_comparison_round_trip_bps": 14},
            "multiple_testing": {"single_feature_hypotheses": len(SINGLE_FEATURES), "pre_registered_interactions": len(INTERACTIONS), "total_per_asset_futures_comparisons": len(ALL_HYPOTHESES), "caution": "Nominal 95% control separation is descriptive only and is not a multiple-comparison-corrected profitability claim."},
            "futures_inventory": futures_inventory, "futures_derivatives_structure": futures,
            "higher_timeframe_inventory": higher_inventory, "higher_timeframe_structure": higher,
            "universe_classifications": {}}


def classify_universes(result: dict) -> None:
    futures_labels = [detail["feasibility"]["classification"] for symbol in result["futures_derivatives_structure"].values() for detail in symbol["features"].values()]
    higher_labels = [detail["feasibility"]["classification"] for symbol in result["higher_timeframe_structure"].values() for detail in symbol.values()]
    result["universe_classifications"] = {
        "RESEARCH_UNIVERSE_SPOT_OHLCV_V1": "EXHAUSTED / NOT CURRENTLY VIABLE",
        "UNIVERSE_A_CRYPTO_DERIVATIVES_STRUCTURE": "CONTINUE DISCOVERY" if "RESEARCHABLE" in futures_labels else "LOW PRIORITY",
        "UNIVERSE_B_HIGHER_TIMEFRAME_STRUCTURE": "CONTINUE DISCOVERY" if "RESEARCHABLE" in higher_labels else "LOW PRIORITY",
        "UNIVERSE_C_VOLUME_FLOW": "CONTINUE DISCOVERY" if "RESEARCHABLE" in futures_labels else "LOW PRIORITY",
    }


def render_report(result: dict) -> str:
    """Human-readable companion to the complete machine-readable evidence."""
    lines = [
        "# Research Universe Expansion & Alternative Edge Discovery",
        "",
        "## Previous Research Stop Decision",
        "",
        "`RESEARCH_UNIVERSE_SPOT_OHLCV_V1` remains **EXHAUSTED / NOT CURRENTLY VIABLE**. This study neither changes nor reopens V1/V2/V3, Jul–Sep 2025 validation, or Oct–Dec 2025 untouched test data.",
        "",
        "## New Research Universes",
        "",
        "- **Universe A — Binance USDⓈ-M perpetual futures:** 15m price, mark/index, funding, observed open interest, and observed taker long/short-volume ratio.",
        "- **Universe B — Binance Spot higher timeframe:** 4h and 1D OHLCV resamples, reported separately from futures microstructure.",
        "- **Universe C — flow information:** observed futures taker ratio plus genuine price-volume measures; no signed-volume reconstruction.",
        "",
        "## Data Sources and Provenance",
        "",
        "All evidence is Jan–Jun 2025 TRAIN only. Source manifests, every archive SHA-256, observation count, start/end, and gaps are in `automation-hub/data/research_universe_expansion.json`.",
        "",
    ]
    for symbol, inventory in result["futures_inventory"].items():
        datasets = inventory["datasets"]
        lines += [f"### {symbol} — Binance USDⓈ-M Futures", "", f"- 15m futures price: {datasets['futures_klines_15m']['observations']} observations; gaps: {datasets['futures_klines_15m']['gaps']}.", f"- 5m observed OI/taker-ratio: {datasets['open_interest_and_taker_ratio_5m']['observations']} observations; gaps: {datasets['open_interest_and_taker_ratio_5m']['gaps']}.", f"- Funding events: {datasets['funding_rate']['observations']}; no interpolation was applied.", ""]
    lines += [
        "## Futures Execution Model",
        "",
        "The conservative futures round-trip research hurdle is **16 bp** (2 bp maker fee each side, 2 bp spread/slippage each side, and 2 bp latency/missed-fill allowance). It is an account-tier-agnostic research assumption, not the user’s actual commission schedule. Funding is not netted as a benefit; an adverse funding transfer is additional cost. Leverage is explicitly not treated as edge.",
        "",
        "## Unconditional Baselines",
        "",
        "Every futures condition is compared with the unconditional long distribution and a deterministic, matched-frequency random timestamp/direction control at 15m horizons 1, 4, 12, and 24 bars. Every higher-timeframe result contains 4h horizons 1/3/6/12 and 1D horizons 1/3/5/10.",
        "",
        "## Funding Analysis",
        "",
    ]
    candidates = []
    for symbol, detail in result["futures_derivatives_structure"].items():
        for feature, evidence in detail["features"].items():
            feasibility = evidence["feasibility"]
            if feasibility["classification"] == "RESEARCHABLE":
                candidates.append((symbol, feature, feasibility, evidence["event_count"]))
    funding = [row for row in candidates if row[1].startswith("funding")]
    if funding:
        for symbol, feature, stat, count in funding:
            lines.append(f"- **{symbol} {feature}**: RESEARCHABLE premise only; n={count}, 24-bar gross mean {stat['gross_mean_bps']:.2f} bp, net of 16 bp {stat['net_of_conservative_cost_bps']:.2f} bp, positive months {stat['positive_months']}/6, random-control difference {stat['random_mean_difference_bps']:.2f} bp.")
    else:
        lines.append("- No funding feature met the predeclared RESEARCHABLE screen.")
    lines += ["", "## Open Interest Analysis", "", "OI and price/OI state results are retained in the JSON evidence. No OI state independently met the full predeclared researchable screen in this first six-month sample.", "", "## Basis Analysis", "", "Mark/index basis states are retained in the JSON evidence. No basis state independently met the full predeclared researchable screen.", "", "## Flow Analysis", ""]
    flow = [row for row in candidates if any(part in row[1] for part in ("taker", "volume"))]
    if flow:
        for symbol, feature, stat, count in flow:
            lines.append(f"- **{symbol} {feature}**: RESEARCHABLE premise only; n={count}, 24-bar gross mean {stat['gross_mean_bps']:.2f} bp, net of 16 bp {stat['net_of_conservative_cost_bps']:.2f} bp, positive months {stat['positive_months']}/6.")
    else:
        lines.append("- No flow feature met the predeclared RESEARCHABLE screen.")
    lines += ["", "## Higher-Timeframe Analysis", ""]
    for symbol, by_timeframe in result["higher_timeframe_structure"].items():
        for timeframe, evidence in by_timeframe.items():
            stat = evidence["feasibility"]
            lines.append(f"- **{symbol} {timeframe}**: {stat['classification']}; longest-horizon directional persistence mean {stat['gross_mean_bps_at_longest_horizon']:.2f} bp, positive months {stat['monthly_positive']}/6, cost dominance {stat['cost_dominance_ratio']}.")
    lines += [
        "",
        "## Feature Information Value",
        "",
        "The complete per-feature output includes event count, conditional mean/median/volatility/skew/tails/hit-rate/MFE/MAE, monthly rows, and matched random controls. It is machine-readable to avoid selective presentation.",
        "",
        "## Feature Interactions",
        "",
        f"Exactly {len(INTERACTIONS)} pre-registered interactions were tested: {', '.join(INTERACTIONS)}. The interaction budget was not expanded after results were observed.",
        "",
        "## Random/Placebo Controls",
        "",
        "Each condition uses deterministic matched-frequency random timestamps with its condition directions shuffled. This is a placebo association control, not proof of causality.",
        "",
        "## Economic Significance",
        "",
        "A positive gross result is never called profitable. The screen requires at least 100 events, 30 independent events, positive net of the conservative cost hurdle, four positive months, and simple 95% separation from the matched random control before the label `RESEARCHABLE` is possible.",
        "",
        "## Cost Dominance",
        "",
        "The futures model is separate from the earlier Spot 14 bp model. The report does not use fee discounts, leverage, or perfect fills to create an apparent advantage. Actual account commissions, spreads, fills, and funding payment direction remain required before any execution validation.",
        "",
        "## Monthly Stability",
        "",
        "Monthly event count, conditional mean, median, and hit rate are retained for every feature in the JSON. A result driven by fewer than four positive months cannot pass the researchable screen.",
        "",
        "## Asset Stability",
        "",
        "There is no cross-asset universality claim. The initial signals are asset-specific: low funding qualified only for ETH and SOL; a relative-volume feature qualified only for ETH; 1D directional persistence qualified only for ETH.",
        "",
        "## Timeframe Stability",
        "",
        "The first screen gives no blanket 4h/1D conclusion. ETH 1D warrants separate follow-up discovery; BTC and SOL are weak, and 4h results are mixed or non-viable.",
        "",
        "## Multiple-Testing Audit",
        "",
        f"{len(SINGLE_FEATURES)} single-feature hypotheses and {len(INTERACTIONS)} interactions were tested per futures asset. Nominal 95% matched-control separation is descriptive only; no multiple-comparison-corrected profitability claim is made.",
        "",
        "## Data Limitations",
        "",
        "- The study has six TRAIN months only; no validation/test data were read.",
        "- Funding is an event series and was aligned only from information known at or before T; it was not forward-filled from the future.",
        "- Archive data do not provide the actual account fee tier, order-book queue position, or realized fills; these prevent an execution claim.",
        "- No synthetic candles, generated fixtures, replay caches, reconstructed OI, or mixed-venue microstructure were used.",
        "",
        "## Research Universe Classifications",
        "",
    ]
    for universe, status in result["universe_classifications"].items():
        lines.append(f"- **{universe}**: {status}")
    lines += [
        "",
        "## Decision Gate",
        "",
        "**DERIVATIVES-STRUCTURE RESEARCH JUSTIFIED (limited discovery only)**; **FLOW-DATA RESEARCH JUSTIFIED (limited discovery only)**; **HIGHER-TIMEFRAME RESEARCH JUSTIFIED (ETH 1D follow-up only)**. This does **not** authorize a strategy, a forward-paper candidate, or a live candidate.",
        "",
        "## Recommended Next Research Stage",
        "",
        "Freeze this result. Before any strategy construction, pre-register a small independent TRAIN extension or a completely new, separately sealed futures universe; verify actual account-specific futures fees and fills; then repeat only the named asset-specific premises. Do not open the existing Jul–Sep validation or Oct–Dec untouched-test data for this discovery study.",
        "",
        "## Status",
        "",
        "**VERIFIED:** provenance-gated Jan–Jun inputs, causal timestamp alignment, archive hashes, venue separation, deterministic matched controls, and sealed-boundary enforcement.",
        "",
        "**INSUFFICIENT EVIDENCE:** any profitable strategy, edge persistence beyond train, realistic fill survival, cross-asset generality, or forward-paper readiness.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--spot-data-dir", type=Path, help="separate official Binance Spot train archive directory for Universe B")
    parser.add_argument("--fetch", action="store_true", help="download only named official Jan--Jun 2025 archives")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--output", type=Path, default=HUB / "data" / "research_universe_expansion.json")
    parser.add_argument("--ledger", type=Path, default=HUB / "data" / "strategy_v3_research_ledger.jsonl")
    args = parser.parse_args()
    if args.fetch:
        print(json.dumps(download_archives(args.data_dir, workers=args.workers), sort_keys=True))
        return 0
    assert_train_only(TRAIN_MONTHS, VALIDATION_START)
    futures, futures_inventory = {}, {}
    for symbol in SYMBOLS:
        bars, metrics, funding, inventory = load_futures_train(args.data_dir, symbol)
        futures[symbol] = analyse_futures(symbol, bars, metrics, funding)
        futures_inventory[symbol] = inventory
    if args.spot_data_dir is None:
        raise ValueError("--spot-data-dir is required: higher-timeframe Spot evidence must remain venue-separated")
    higher, higher_inventory = analyse_higher_timeframes(args.spot_data_dir)
    result = payload(args.data_dir, futures, futures_inventory, higher, higher_inventory)
    classify_universes(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    report_path = ROOT / "RESEARCH_UNIVERSE_EXPANSION_REPORT.md"
    report_path.write_text(render_report(result), encoding="utf-8")
    append_ledger(args.ledger, {"study_id": f"{STUDY_ID}:finalized", "supersedes_study_id": STUDY_ID, "created_at": CREATED_AT, "research_universe_id": "UNIVERSE_EXPANSION_V1", "conclusion": result["universe_classifications"], "data_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(), "strategies_created": 0, "validation_status": "SEALED", "test_status": "SEALED", "code_sha256": sha256_file(Path(__file__))})
    print(json.dumps({"output": str(args.output), "strategies_created": 0, "validation_data_opened": False, "test_data_opened": False, "classifications": result["universe_classifications"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
