#!/usr/bin/env python3
"""Train-only market/execution feasibility audit; it creates no strategy."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import sys
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
from scripts.strategy_v3_market_study import (  # noqa: E402
    SYMBOLS, TRAIN_MONTHS, TRAIN_START, VALIDATION_START, load_train_symbol,
)

UTC = timezone.utc
TIMEFRAMES = ("5m", "15m", "1h")
HORIZONS = (1, 3, 6, 12, 24)
CREATED_AT = "2026-08-15T00:00:00+00:00"
# Existing V1/V2/V3 realistic limit-entry execution: 4bp maker entry fee plus
# 4bp fee and 6bp adverse exit cost = 14bp direct lower-bound round trip cost.
COST_SCENARIOS_BPS = {"improved": 10.0, "current_realistic": 14.0, "worse": 20.0}
FEATURES = (
    "recent_direction", "high_efficiency", "low_efficiency", "atr_expansion",
    "atr_contraction", "pullback_depth", "body_strength", "structural_distance",
    "trend_plus_pullback", "compression_plus_expansion",
)


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * (len(ordered) - 1)))]


def distribution(values: list[tuple[float, float, float]]) -> dict:
    """(signed return, MFE, MAE), expressed in basis points."""
    if not values:
        return {"n": 0, "mean_bps": None, "median_bps": None, "std_bps": None,
                "hit_rate": None, "mfe_bps": None, "mae_bps": None, "p05_bps": None, "p95_bps": None}
    returns, mfes, maes = zip(*values)
    return {"n": len(returns), "mean_bps": round(statistics.fmean(returns), 4),
            "median_bps": round(statistics.median(returns), 4),
            "std_bps": round(statistics.stdev(returns), 4) if len(returns) > 1 else 0.0,
            "hit_rate": round(sum(value > 0 for value in returns) / len(returns), 4),
            "mfe_bps": round(statistics.fmean(mfes), 4), "mae_bps": round(statistics.fmean(maes), 4),
            "p05_bps": round(percentile(list(returns), .05), 4), "p95_bps": round(percentile(list(returns), .95), 4)}


def feature_classification(event: dict, random_base: dict, monthly: dict, independent_events: int) -> dict:
    """Predeclared feasibility, never a profitability or trade approval label."""
    observed, baseline = event[6], random_base[6]
    gross = observed["mean_bps"]
    if not observed["n"] or gross is None:
        return {"classification": "NOT VIABLE", "reason": "no observations"}
    n, std = observed["n"], observed["std_bps"] or 0.0
    bstd, bn = baseline["std_bps"] or 0.0, baseline["n"] or 1
    difference = gross - (baseline["mean_bps"] or 0.0)
    stderr = math.sqrt((std * std / n) + (bstd * bstd / bn)) if n else float("inf")
    distinguishable = bool(stderr and abs(difference) >= 1.96 * stderr)
    positive_months = sum((row[6]["mean_bps"] or 0) > 0 for row in monthly.values())
    net = gross - COST_SCENARIOS_BPS["current_realistic"]
    cost_ratio = COST_SCENARIOS_BPS["current_realistic"] / gross if gross > 0 else None
    if n < 30 or gross <= 0 or net <= 0:
        label = "NOT VIABLE"
    elif distinguishable and positive_months >= 4 and independent_events >= 12 and cost_ratio is not None and cost_ratio < .50:
        label = "STRONG RESEARCH PREMISE" if cost_ratio < .25 else "RESEARCHABLE"
    else:
        label = "WEAK"
    return {"classification": label, "gross_minus_current_cost_bps": round(net, 4),
            "cost_dominance_ratio": round(cost_ratio, 4) if cost_ratio is not None else None,
            "random_mean_difference_bps": round(difference, 4), "random_distinguishable_95": distinguishable,
            "positive_months": positive_months, "independent_events": independent_events,
            "signal_to_noise": round(gross / std, 4) if std else None}


def _atr_series(bars: list[Bar], period: int) -> list[float | None]:
    trs = [0.0]
    for prior, current in zip(bars, bars[1:]):
        trs.append(max(current.high-current.low, abs(current.high-prior.close), abs(current.low-prior.close)))
    sums = [0.0]
    for value in trs:
        sums.append(sums[-1] + value)
    return [None if index < period else (sums[index + 1] - sums[index - period + 1]) / period
            for index in range(len(bars))]


def _event_outcome(bars: list[Bar], index: int, direction: int, horizon: int) -> tuple[float, float, float]:
    entry = bars[index].close
    future = bars[index + 1:index + horizon + 1]
    if direction > 0:
        ret = (future[-1].close / entry - 1) * 10_000
        mfe = (max(row.high for row in future) / entry - 1) * 10_000
        mae = (min(row.low for row in future) / entry - 1) * 10_000
    else:
        ret = (1 - future[-1].close / entry) * 10_000
        mfe = (1 - min(row.low for row in future) / entry) * 10_000
        mae = (1 - max(row.high for row in future) / entry) * 10_000
    return ret, mfe, mae


def analyse_series(symbol: str, timeframe: str, bars: list[Bar]) -> dict:
    atr10, atr20, atr50 = _atr_series(bars, 10), _atr_series(bars, 20), _atr_series(bars, 50)
    closes = [bar.close for bar in bars]
    close_prefix, path_prefix = [0.0], [0.0]
    for index, close in enumerate(closes):
        close_prefix.append(close_prefix[-1] + close)
        path_prefix.append(path_prefix[-1] + (abs(close - closes[index - 1]) if index else 0.0))
    # Pre-compute future extrema once.  Every event family then reads the same
    # causal forward window in O(1), rather than re-scanning it per feature.
    future = {}
    for horizon in HORIZONS:
        closes_at, highs_at, lows_at = [None] * len(bars), [None] * len(bars), [None] * len(bars)
        for index in range(len(bars) - horizon):
            window = bars[index + 1:index + horizon + 1]
            closes_at[index] = window[-1].close
            highs_at[index] = max(row.high for row in window)
            lows_at[index] = min(row.low for row in window)
        future[horizon] = (closes_at, highs_at, lows_at)
    observations: dict[str, list[tuple[int, int]]] = defaultdict(list)
    universe: list[int] = []
    state_observations: dict[str, list[tuple[int, int]]] = defaultdict(list)
    latest = max(HORIZONS)
    for index in range(60, len(bars) - latest):
        current = bars[index]
        a10, a20, a50 = atr10[index], atr20[index], atr50[index]
        if not a10 or not a20 or not a50:
            continue
        net20 = current.close - closes[index-20]
        direction = 1 if net20 > 0 else -1 if net20 < 0 else 0
        if not direction:
            continue
        path = path_prefix[index + 1] - path_prefix[index - 19]
        er = abs(net20) / path if path else 0.0
        atr_ratio = a10 / a50
        body = current.close - current.open
        pullback = current.close - bars[index-3].close
        mean20 = (close_prefix[index + 1] - close_prefix[index - 19]) / 20
        structural = current.close - mean20
        volatility = "high" if atr_ratio >= 1.25 else "low" if atr_ratio <= .80 else "normal"
        regime = "trend" if er >= .45 else "range"
        universe.append(index)
        state_observations[f"{regime}_{volatility}"].append((index, direction))
        observations["recent_direction"].append((index, direction))
        if er >= .45:
            observations["high_efficiency"].append((index, direction))
        if er <= .15:
            observations["low_efficiency"].append((index, direction))
        if atr_ratio >= 1.25:
            observations["atr_expansion"].append((index, direction))
        if atr_ratio <= .80:
            observations["atr_contraction"].append((index, direction))
        if direction * pullback <= -.5 * a20:
            observations["pullback_depth"].append((index, direction))
        if abs(body) >= .35 * a20:
            observations["body_strength"].append((index, 1 if body > 0 else -1))
        if abs(structural) >= 1.5 * a20:
            observations["structural_distance"].append((index, 1 if structural > 0 else -1))
        if er >= .45 and direction * pullback <= -.5 * a20:
            observations["trend_plus_pullback"].append((index, direction))
        prior_ratios = [atr10[index-offset] / atr50[index-offset] for offset in range(1, 9)
                        if atr10[index-offset] and atr50[index-offset]]
        if atr_ratio >= 1.25 and prior_ratios and min(prior_ratios) <= .80 and abs(body) >= .15 * a20:
            observations["compression_plus_expansion"].append((index, 1 if body > 0 else -1))

    def outcome(index: int, direction: int, horizon: int) -> tuple[float, float, float]:
        close, high, low = future[horizon][0][index], future[horizon][1][index], future[horizon][2][index]
        entry = closes[index]
        if direction > 0:
            return ((close / entry - 1) * 10_000, (high / entry - 1) * 10_000, (low / entry - 1) * 10_000)
        return ((1 - close / entry) * 10_000, (1 - low / entry) * 10_000, (1 - high / entry) * 10_000)

    def summarize(points: list[tuple[int, int]]) -> tuple[dict, dict]:
        outcomes = {horizon: [outcome(index, direction, horizon) for index, direction in points]
                    for horizon in HORIZONS}
        monthly = defaultdict(lambda: defaultdict(list))
        for index, direction in points:
            for horizon in HORIZONS:
                monthly[bars[index].timestamp.strftime("%Y-%m")][horizon].append(outcome(index, direction, horizon))
        return ({horizon: distribution(rows) for horizon, rows in outcomes.items()},
                {month: {h: distribution(rows) for h, rows in values.items()} for month, values in sorted(monthly.items())})

    base_points = [(index, 1 if bars[index].close > bars[index-20].close else -1) for index in universe]
    output = {"inventory": {"symbol": symbol, "timeframe": timeframe, "bars": len(bars),
                            "first": bars[0].timestamp.isoformat(), "last": bars[-1].timestamp.isoformat()},
              "unconditional_directional": summarize(base_points)[0], "states": {}, "features": {}}
    for state, points in sorted(state_observations.items()):
        output["states"][state] = summarize(points)[0]
    for feature in FEATURES:
        points = observations.get(feature, [])
        observed, monthly = summarize(points)
        rng = random.Random(sha(canonical({"symbol": symbol, "timeframe": timeframe, "feature": feature})))
        sample_indices = rng.sample(universe, min(len(points), len(universe))) if points else []
        signs = [direction for _, direction in points]
        rng.shuffle(signs)
        random_points = list(zip(sample_indices, signs))
        random_summary, _ = summarize(random_points)
        independent = 0; last = -10_000
        for index, _ in points:
            if index - last > 24:
                independent += 1; last = index
        feasibility = feature_classification(observed, random_summary, monthly, independent)
        feasibility["cost_adjusted_bps"] = {name: {horizon: round((observed[horizon]["mean_bps"] or 0) - cost, 4)
                                               for horizon in HORIZONS} for name, cost in COST_SCENARIOS_BPS.items()}
        output["features"][feature] = {"event_count": len(points), "events_per_month": round(len(points)/6, 2),
                                        "cluster_ratio": round(len(points)/independent, 3) if independent else None,
                                        "observed": observed, "random_baseline": random_summary,
                                        "monthly": monthly, "feasibility": feasibility}
    return output


def append_ledger(path: Path, row: dict) -> None:
    existing = {json.loads(line).get("experiment_id") for line in path.read_text().splitlines() if line.strip()} if path.exists() else set()
    if row["experiment_id"] in existing:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HUB / "data" / "edge_feasibility_audit.json")
    parser.add_argument("--ledger", type=Path, default=HUB / "data" / "strategy_v3_research_ledger.jsonl")
    parser.add_argument("--symbol", choices=SYMBOLS)
    parser.add_argument("--timeframe", choices=TIMEFRAMES)
    parser.add_argument("--combine-from", type=Path, nargs="*")
    args = parser.parse_args()
    if args.combine_from:
        parts = [json.loads(path.read_text()) for path in args.combine_from]
        inventory = {symbol: values for part in parts for symbol, values in part["inventory"].items()}
        study = {}
        for part in parts:
            for symbol, frames in part["study"].items():
                study.setdefault(symbol, {}).update(frames)
        payload = _payload(inventory, study)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append_ledger(args.ledger, _ledger_row(args.output))
        print(json.dumps({"output": str(args.output), "combined_symbols": sorted(study), "test_data_opened": False}, indent=2))
        return 0
    inventory, study = {}, {}
    for symbol in ((args.symbol,) if args.symbol else SYMBOLS):
        raw, inventory[symbol] = load_train_symbol(args.data_dir, symbol, months=TRAIN_MONTHS, end=VALIDATION_START)
        series = {"5m": raw, "15m": resample(raw, "15m", "5m"), "1h": resample(raw, "1h", "5m")}
        if args.timeframe:
            series = {args.timeframe: series[args.timeframe]}
        study[symbol] = {timeframe: analyse_series(symbol, timeframe, rows) for timeframe, rows in series.items()}
    payload = _payload(inventory, study)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if not args.symbol:
        append_ledger(args.ledger, _ledger_row(args.output))
    print(json.dumps({"output": str(args.output), "strategies_created": 0,
                      "validation_data_opened": False, "test_data_opened": False}, indent=2))
    return 0


def _payload(inventory: dict, study: dict) -> dict:
    return {"created_at": CREATED_AT, "purpose": "evidence-only market and execution feasibility audit",
            "strategies_created": 0, "validation_data_opened": False, "test_data_opened": False,
            "dataset_boundary": {"train": [TRAIN_START.isoformat(), VALIDATION_START.isoformat()],
                                 "validation_sealed_from": VALIDATION_START.isoformat(),
                                 "untouched_test_sealed_from": "2025-10-01T00:00:00+00:00"},
            "execution_hurdle": {"current": {"maker_entry_fee_bps": 4, "exit_fee_bps": 4,
                                                 "exit_spread_slippage_latency_bps": 6,
                                                 "direct_round_trip_lower_bound_bps": 14},
                                  "sensitivity_bps": COST_SCENARIOS_BPS,
                                  "limit_entry_note": "Missed limit fills and gap-through effects are not a negative cost; they are additional execution risk and are not treated as an advantage."},
            "inventory": inventory, "study": study}


def _ledger_row(output: Path) -> dict:
    return {"experiment_id": "edge-feasibility-v1-2025h1", "created_at": CREATED_AT,
            "classification": "EVIDENCE_ONLY", "dataset": "TRAIN Jan--Jun 2025 only",
            "evidence_sha256": sha(output.read_bytes()), "validation_status": "NOT_OPENED",
            "test_status": "NOT_OPENED", "strategies_created": 0}


if __name__ == "__main__":
    raise SystemExit(main())
