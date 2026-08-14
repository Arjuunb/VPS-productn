#!/usr/bin/env python3
"""Blind, append-only Research Strategy V2 evaluation.

``develop`` reads January through September 2025 only.  ``test`` is a separate
command that requires an immutable freeze manifest before it may open October
through December.  There is deliberately no command that develops and tests in
one process.
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

from bot.data.resample import resample  # noqa: E402
from bot.types import Bar  # noqa: E402
from scripts import strategy_validation as v1  # noqa: E402
from strategies.brain import TradeBrain  # noqa: E402
from strategies.custom import simulate_strategy  # noqa: E402
from strategies.research_v2 import RESEARCH_STRATEGIES  # noqa: E402

UTC = timezone.utc
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = ("5m", "15m", "1h")
TRAIN_START = datetime(2025, 1, 1, tzinfo=UTC)
VALIDATION_START = datetime(2025, 7, 1, tzinfo=UTC)
TEST_START = datetime(2025, 10, 1, tzinfo=UTC)
END = datetime(2026, 1, 1, tzinfo=UTC)

HYPOTHESES = {
    "supertrend_v2": {
        "problem": "V1 losses concentrated in low-volatility, low-efficiency flips and were cost-sensitive.",
        "change": "Require a causal efficiency-ratio floor and minimum ATR percentage at each trend flip.",
        "mechanism": "Suppress false transitions whose expected movement cannot cover realistic execution friction.",
        "failure": "Reject if validation expectancy/PF, parameter neighbourhood, walk-forward or cost stress fails.",
    },
    "donchian_v2": {
        "problem": "V1 was negative on every untouched asset and near break-even before costs on ETH/SOL.",
        "change": "Require close penetration beyond the channel plus causal channel width and volume confirmation.",
        "mechanism": "Select breakouts with observable expansion/follow-through instead of every marginal channel breach.",
        "failure": "Retire the family if confirmed breakouts still lack stable validation edge or adequate sample.",
    },
    "brain_v2": {
        "problem": "V1 varied by asset, shorts were weak, and isolated BTC strength failed cost/latency stress.",
        "change": "Require directional efficiency; separately evaluate explainable bidirectional and long-only policies.",
        "mechanism": "Prevent noisy low-persistence votes and measure directional asymmetry without black-box features.",
        "failure": "Reject if the result is asset-fragile, sample-poor, cost-sensitive or unstable across folds.",
    },
}

CONFIGS = {
    "supertrend_v2": [
        {"period": 10, "mult": 3.0, "min_er": value, "min_atr_pct": .003, "rr_target": 2.5}
        for value in (.20, .25, .30)
    ],
    "donchian_v2": [
        {"channel": 30, "min_volume_ratio": value, "min_penetration_atr": .10,
         "min_channel_width_pct": .004, "rr_target": 2.5}
        for value in (.90, 1.00, 1.10)
    ],
    "brain_v2": [
        {"min_er": value, "allow_short": True, "conviction_threshold": .56,
         "rr_target": 3.0, "max_history": 320}
        for value in (.15, .20, .25)
    ] + [{"min_er": .20, "allow_short": False, "conviction_threshold": .56,
          "rr_target": 3.0, "max_history": 320}],
}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def source_hash() -> str:
    paths = [
        HUB / "strategies" / "research_v2.py",
        HUB / "strategies" / "base_strategy.py",
        HUB / "strategies" / "brain_strategy.py",
        HUB / "strategies" / "custom.py",
        ROOT / "bot" / "tradecore" / "trade_manager.py",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_months(data_dir: Path, symbol: str, months: tuple[int, ...]) -> tuple[list[Bar], dict]:
    bars: list[Bar] = []
    archives = {}
    for month in months:
        path = data_dir / f"{symbol}-5m-2025-{month:02d}.zip"
        if not path.exists():
            raise RuntimeError(f"missing official archive: {path.name}")
        archives[path.name] = v1._sha256(path)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise RuntimeError(f"{path.name}: expected one CSV member")
            with archive.open(names[0]) as raw:
                for row in csv.reader(line.decode("utf-8") for line in raw):
                    if not row:
                        continue
                    stamp = int(row[0]); divisor = 1_000_000 if stamp > 100_000_000_000_000 else 1_000
                    bars.append(Bar(datetime.fromtimestamp(stamp / divisor, tz=UTC),
                                    float(row[1]), float(row[2]), float(row[3]),
                                    float(row[4]), float(row[5])))
    bars.sort(key=lambda row: row.timestamp)
    duplicates = sum(a.timestamp == b.timestamp for a, b in zip(bars, bars[1:]))
    missing = sum(max(0, int((b.timestamp - a.timestamp).total_seconds() / 300) - 1)
                  for a, b in zip(bars, bars[1:]))
    invalid = sum(not (bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high)
                  for bar in bars)
    if duplicates or missing or invalid:
        raise RuntimeError(f"{symbol}: integrity failed duplicates={duplicates} gaps={missing} invalid={invalid}")
    return bars, {"exchange": "Binance Spot", "symbol": symbol, "timeframe": "5m",
                  "first": bars[0].timestamp.isoformat(), "last": bars[-1].timestamp.isoformat(),
                  "candles": len(bars), "duplicates": duplicates, "missing_intervals": missing,
                  "invalid_ohlc": invalid, "archives": archives}


def framed(raw: dict[str, list[Bar]]) -> dict[tuple[str, str], list[Bar]]:
    output = {}
    for symbol, bars in raw.items():
        output[(symbol, "5m")] = bars
        output[(symbol, "15m")] = resample(bars, "15m", "5m")
        output[(symbol, "1h")] = resample(bars, "1h", "5m")
    return output


def bounded(series: list[Bar], start: datetime, end: datetime, warmup=600):
    first = next(index for index, bar in enumerate(series) if bar.timestamp >= start)
    last = next((index for index, bar in enumerate(series) if bar.timestamp >= end), len(series))
    return series[max(0, first - warmup):last], start


def simulate_candidate(key: str, symbol: str, bars: list[Bar], start: datetime,
                       params: dict, *, costs=True, risk=True, gate=True, delay=0):
    strategy = RESEARCH_STRATEGIES[key](symbol, **params)
    return simulate_strategy(
        strategy, bars,
        fee=v1.PRODUCTION["fee_pct_per_side"] if costs else 0.0,
        slippage=v1.PRODUCTION["exit_spread_slippage_latency_pct"] if costs else 0.0,
        starting_balance=v1.PRODUCTION["starting_equity"],
        risk_pct=v1.PRODUCTION["risk_per_trade_pct"],
        brain=TradeBrain() if gate else None,
        min_score=v1.PRODUCTION["min_quality_score"] if gate else 0,
        enforce_brain=gate,
        entry_mode="limit", limit_ttl_bars=3, entry_delay_bars=delay,
        max_daily_loss_pct=v1.PRODUCTION["max_daily_loss_pct"] if risk else 0.0,
        max_drawdown_pct=v1.PRODUCTION["max_drawdown_pct"] if risk else 0.0,
        max_consecutive_losses=v1.PRODUCTION["max_consecutive_losses"] if risk else 0,
        cooldown_after_loss=v1.PRODUCTION["cooldown_after_loss_min"] if risk else 0,
        trade_start_at=start, retain_all=True,
    )


def monthly_folds(trades: list[dict]) -> list[dict]:
    grouped = defaultdict(list)
    for trade in trades:
        stamp = datetime.fromisoformat(trade["exit_time"].replace("Z", "+00:00"))
        if 4 <= stamp.month <= 9:
            grouped[f"2025-{stamp.month:02d}"].append(trade)
    return [{"month": f"2025-{month:02d}", **v1._stats(grouped[f"2025-{month:02d}"])}
            for month in range(4, 10)]


def experiment_id(key: str, timeframe: str, params: dict) -> str:
    return "rv2-" + sha256_bytes(canonical({"strategy": key, "timeframe": timeframe,
                                              "params": params}))[:20]


def append_ledger(path: Path, row: dict) -> None:
    existing = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                current = json.loads(line); existing[current["experiment_id"]] = current
    old = existing.get(row["experiment_id"])
    if old is not None:
        if old != row:
            raise RuntimeError(f"immutable research ledger conflict: {row['experiment_id']}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def develop(args) -> int:
    # Critical blindness invariant: no October/November/December file is opened.
    raw, inventory = {}, {}
    for symbol in SYMBOLS:
        raw[symbol], inventory[symbol] = load_months(args.data_dir, symbol, tuple(range(1, 10)))
        if raw[symbol][-1].timestamp >= TEST_START:
            raise RuntimeError("development data crossed the untouched-test boundary")
    datasets = framed(raw)
    code_hash = source_hash()
    output = {"stage": "development", "test_data_opened": False,
              "generated_at": datetime.now(UTC).isoformat(), "source_hash": code_hash,
              "data_inventory": inventory, "hypotheses": HYPOTHESES,
              "execution": v1.PRODUCTION, "experiments": {}, "selected": []}

    for key, configs in CONFIGS.items():
        output["experiments"][key] = []
        for timeframe in TIMEFRAMES:
            for params in configs:
                train_trades, validation_trades = [], []
                symbols = {}
                for symbol in SYMBOLS:
                    train_bars, train_start = bounded(datasets[(symbol, timeframe)], TRAIN_START, VALIDATION_START)
                    val_bars, val_start = bounded(datasets[(symbol, timeframe)], VALIDATION_START, TEST_START)
                    train = simulate_candidate(key, symbol, train_bars, train_start, params)
                    validation = simulate_candidate(key, symbol, val_bars, val_start, params)
                    train_trades.extend(train["trades"]); validation_trades.extend(validation["trades"])
                    symbols[symbol] = {"train": v1._stats(train["trades"]),
                                       "validation": v1._stats(validation["trades"])}
                row = {"experiment_id": experiment_id(key, timeframe, params),
                       "strategy": key, "version": RESEARCH_STRATEGIES[key].research_version,
                       "hypothesis": HYPOTHESES[key], "parameters": params,
                       "timeframe": timeframe, "symbols": symbols,
                       "train_window": [TRAIN_START.isoformat(), VALIDATION_START.isoformat()],
                       "validation_window": [VALIDATION_START.isoformat(), TEST_START.isoformat()],
                       "test_status": "NOT_OPENED", "source_hash": code_hash,
                       "configuration_hash": sha256_bytes(canonical(params)),
                       "train": v1._stats(train_trades),
                       "validation": v1._stats(validation_trades),
                       "walk_forward": monthly_folds(train_trades + validation_trades),
                       "monte_carlo_development": v1.monte_carlo(train_trades + validation_trades),
                       "verdict": "RESEARCH ONLY"}
                output["experiments"][key].append(row)
                append_ledger(args.ledger, row)

    # Predeclared selection gates; no ranking can override a failed gate.
    for key, rows in output["experiments"].items():
        by_timeframe = defaultdict(list)
        for row in rows:
            by_timeframe[row["timeframe"]].append(row)
        candidates = []
        for row in rows:
            train, validation = row["train"], row["validation"]
            stable_neighbours = sum(
                peer["validation"]["expectancy_r"] > 0 and peer["validation"]["profit_factor"] >= 1.0
                for peer in by_timeframe[row["timeframe"]]
            )
            positive_symbols = sum(value["validation"]["expectancy_r"] > 0
                                   for value in row["symbols"].values())
            positive_folds = sum(fold["net_r"] > 0 for fold in row["walk_forward"])
            gates = {
                "train_non_negative": train["expectancy_r"] >= 0,
                "validation_positive": validation["expectancy_r"] > 0,
                "validation_pf": validation["profit_factor"] >= 1.10,
                "validation_sample": validation["trades"] >= 20,
                "multi_asset": positive_symbols >= 2,
                "parameter_neighbourhood": stable_neighbours >= 2,
                "walk_forward": positive_folds >= 3,
            }
            row["selection_gates"] = gates
            if all(gates.values()):
                candidates.append(row)
        if candidates:
            chosen = max(candidates, key=lambda row: (
                row["validation"]["expectancy_r"], -row["validation"]["max_drawdown_r"]))
            output["selected"].append({"experiment_id": chosen["experiment_id"],
                                       "strategy": key, "timeframe": chosen["timeframe"],
                                       "parameters": chosen["parameters"]})

    manifest = {"created_at": datetime.now(UTC).isoformat(), "source_hash": code_hash,
                "development_sha256": None, "selected": output["selected"],
                "test_boundary": TEST_START.isoformat(), "test_opened": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(output, indent=2, sort_keys=True)
    args.output.write_text(serialized, encoding="utf-8")
    manifest["development_sha256"] = sha256_bytes(serialized.encode())
    args.freeze_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"stage": "development", "experiments": sum(len(v) for v in output["experiments"].values()),
                      "selected_for_test": len(output["selected"]), "test_data_opened": False,
                      "output": str(args.output)}, indent=2))
    return 0


def test(args) -> int:
    manifest = json.loads(args.freeze_manifest.read_text(encoding="utf-8"))
    if manifest.get("test_opened"):
        raise RuntimeError("untouched test already opened for this freeze manifest")
    if source_hash() != manifest["source_hash"]:
        raise RuntimeError("research source changed after freeze")
    development = args.development_output.read_bytes()
    if sha256_bytes(development) != manifest["development_sha256"]:
        raise RuntimeError("development evidence changed after freeze")
    if not manifest["selected"]:
        print(json.dumps({"stage": "test", "status": "BLOCKED_NO_FROZEN_CANDIDATE",
                          "test_data_opened": False}, indent=2))
        return 2

    raw, inventory = {}, {}
    for symbol in SYMBOLS:
        raw[symbol], inventory[symbol] = load_months(args.data_dir, symbol, (9, 10, 11, 12))
    datasets = framed(raw)
    results = []
    for frozen in manifest["selected"]:
        trades, symbols = [], {}
        for symbol in SYMBOLS:
            bars, start = bounded(datasets[(symbol, frozen["timeframe"])], TEST_START, END)
            result = simulate_candidate(frozen["strategy"], symbol, bars, start, frozen["parameters"])
            trades.extend(result["trades"]); symbols[symbol] = v1._stats(result["trades"])
        stats = v1._stats(trades)
        eligible = (stats["trades"] >= 30 and stats["expectancy_r"] > 0
                    and stats["profit_factor"] >= 1.2 and stats["max_drawdown_r"] <= 10)
        results.append({**frozen, "symbols": symbols, "test": stats,
                        "monte_carlo": v1.monte_carlo(trades),
                        "verdict": "FORWARD PAPER ELIGIBLE" if eligible else "REJECTED"})
    payload = {"stage": "untouched_test", "opened_at": datetime.now(UTC).isoformat(),
               "data_inventory": inventory, "source_hash": manifest["source_hash"],
               "results": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    manifest["test_opened"] = True
    manifest["test_opened_at"] = payload["opened_at"]
    manifest["test_output_sha256"] = sha256_bytes(args.output.read_bytes())
    args.freeze_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"stage": "test", "candidates": len(results),
                      "eligible": sum(row["verdict"] == "FORWARD PAPER ELIGIBLE" for row in results)}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="stage", required=True)
    develop_parser = sub.add_parser("develop")
    develop_parser.add_argument("--data-dir", required=True, type=Path)
    develop_parser.add_argument("--output", required=True, type=Path)
    develop_parser.add_argument("--ledger", required=True, type=Path)
    develop_parser.add_argument("--freeze-manifest", required=True, type=Path)
    test_parser = sub.add_parser("test")
    test_parser.add_argument("--data-dir", required=True, type=Path)
    test_parser.add_argument("--development-output", required=True, type=Path)
    test_parser.add_argument("--freeze-manifest", required=True, type=Path)
    test_parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    return develop(args) if args.stage == "develop" else test(args)


if __name__ == "__main__":
    raise SystemExit(main())
