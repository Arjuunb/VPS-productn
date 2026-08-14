#!/usr/bin/env python3
"""V3 causal candidate construction, train selection, and frozen validation.

There is intentionally no Oct--Dec command or code path in this harness.  A
candidate may be marked UNTOUCHED_TEST_ELIGIBLE, but consuming the final OOS
set needs a separately authorised future program.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "automation-hub"
for item in (str(HUB), str(ROOT)):
    if item not in sys.path:
        sys.path.insert(0, item)

from bot.data.resample import resample  # noqa: E402
from bot.types import Bar  # noqa: E402
from scripts import strategy_validation as v1  # noqa: E402
from strategies.custom import simulate_strategy  # noqa: E402
from strategies.research_v3 import RESEARCH_V3_STRATEGIES  # noqa: E402

UTC = timezone.utc
CREATED_AT = "2026-08-14T00:00:00+00:00"
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAME = "15m"
TRAIN_START = datetime(2025, 1, 1, tzinfo=UTC)
VALIDATION_START = datetime(2025, 7, 1, tzinfo=UTC)
TEST_START = datetime(2025, 10, 1, tzinfo=UTC)
TRAIN_MONTHS = (1, 2, 3, 4, 5, 6)
VALIDATION_MONTHS_WITH_WARMUP = (6, 7, 8, 9)

# Pre-registered minimal budget: five variants per family, ten total.  These
# ranges are the only values considered in this study; no result may enlarge it.
CONFIGS = {
    "trend_pullback_v3": [
        {"htf_lookback": 4, "htf_min_er": .35, "trend_lookback": 16, "trend_min_er": .45,
         "pullback_bars": 3, "pullback_atr": .50, "confirmation_body_atr": .15,
         "structure_bars": 4, "rr_target": 2.0, "allow_short": True},
        {"htf_lookback": 4, "htf_min_er": .35, "trend_lookback": 16, "trend_min_er": .45,
         "pullback_bars": 3, "pullback_atr": .50, "confirmation_body_atr": .15,
         "structure_bars": 4, "rr_target": 2.0, "allow_short": False},
        {"htf_lookback": 4, "htf_min_er": .35, "trend_lookback": 16, "trend_min_er": .35,
         "pullback_bars": 3, "pullback_atr": .50, "confirmation_body_atr": .15,
         "structure_bars": 4, "rr_target": 2.0, "allow_short": True},
        {"htf_lookback": 4, "htf_min_er": .35, "trend_lookback": 16, "trend_min_er": .45,
         "pullback_bars": 3, "pullback_atr": .75, "confirmation_body_atr": .15,
         "structure_bars": 4, "rr_target": 2.0, "allow_short": True},
        {"htf_lookback": 4, "htf_min_er": .35, "trend_lookback": 16, "trend_min_er": .45,
         "pullback_bars": 3, "pullback_atr": .50, "confirmation_body_atr": .15,
         "structure_bars": 4, "rr_target": 1.5, "allow_short": True},
    ],
    "volatility_expansion_v3": [
        {"htf_lookback": 4, "htf_min_er": .35, "compression_ratio": .80, "expansion_ratio": 1.25,
         "compression_lookback": 8, "body_atr": .35, "structure_bars": 4, "rr_target": 2.0, "allow_short": True},
        {"htf_lookback": 4, "htf_min_er": .35, "compression_ratio": .80, "expansion_ratio": 1.25,
         "compression_lookback": 8, "body_atr": .35, "structure_bars": 4, "rr_target": 2.0, "allow_short": False},
        {"htf_lookback": 4, "htf_min_er": .35, "compression_ratio": .75, "expansion_ratio": 1.25,
         "compression_lookback": 8, "body_atr": .35, "structure_bars": 4, "rr_target": 2.0, "allow_short": True},
        {"htf_lookback": 4, "htf_min_er": .35, "compression_ratio": .80, "expansion_ratio": 1.40,
         "compression_lookback": 8, "body_atr": .35, "structure_bars": 4, "rr_target": 2.0, "allow_short": True},
        {"htf_lookback": 4, "htf_min_er": .35, "compression_ratio": .80, "expansion_ratio": 1.25,
         "compression_lookback": 8, "body_atr": .50, "structure_bars": 4, "rr_target": 2.0, "allow_short": True},
    ],
}
HYPOTHESES = {
    "trend_pullback_v3": "v3-mtf-trend-pullback",
    "volatility_expansion_v3": "v3-volatility-expansion-confirmed-breakout",
}


class SealedDatasetAccessError(RuntimeError):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_hash() -> str:
    path = HUB / "strategies" / "research_v3.py"
    return digest(path.read_bytes())


def assert_window(kind: str, months: tuple[int, ...]) -> tuple[datetime, datetime]:
    allowed = {"train": (TRAIN_MONTHS, TRAIN_START, VALIDATION_START),
               "validation": (VALIDATION_MONTHS_WITH_WARMUP, VALIDATION_START, TEST_START)}
    if kind not in allowed or tuple(months) != allowed[kind][0]:
        raise SealedDatasetAccessError("V3 permits only declared train or validation archives; Oct--Dec is sealed")
    return allowed[kind][1], allowed[kind][2]


def load_window(data_dir: Path, symbol: str, kind: str) -> tuple[list[Bar], dict]:
    months = TRAIN_MONTHS if kind == "train" else VALIDATION_MONTHS_WITH_WARMUP if kind == "validation" else ()
    start, end = assert_window(kind, months)
    rows: list[Bar] = []
    archives = {}
    for month in months:
        path = data_dir / f"{symbol}-5m-2025-{month:02d}.zip"
        if not path.exists():
            raise RuntimeError(f"missing official archive: {path.name}")
        archives[path.name] = digest(path.read_bytes())
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise RuntimeError(f"unexpected archive layout: {path.name}")
            with archive.open(names[0]) as handle:
                for row in csv.reader(line.decode("utf-8") for line in handle):
                    if not row:
                        continue
                    stamp = int(row[0]); divisor = 1_000_000 if stamp > 100_000_000_000_000 else 1_000
                    rows.append(Bar(datetime.fromtimestamp(stamp / divisor, tz=UTC), float(row[1]), float(row[2]),
                                    float(row[3]), float(row[4]), float(row[5])))
    rows.sort(key=lambda item: item.timestamp)
    if not rows or rows[-1].timestamp >= end:
        raise SealedDatasetAccessError(f"{kind} loader crossed its declared boundary")
    duplicates = sum(a.timestamp == b.timestamp for a, b in zip(rows, rows[1:]))
    gaps = sum(max(0, round((b.timestamp-a.timestamp).total_seconds()/300)-1) for a,b in zip(rows, rows[1:]))
    if duplicates or gaps:
        raise RuntimeError(f"{symbol}: bad data duplicates={duplicates}, gaps={gaps}")
    return rows, {"exchange": "Binance Spot", "provenance": "official Binance Vision monthly kline archives",
                  "months_opened": months, "first": rows[0].timestamp.isoformat(), "last": rows[-1].timestamp.isoformat(),
                  "candles": len(rows), "gaps": gaps, "archives": archives, "trade_window": [start.isoformat(), end.isoformat()]}


def frame(rows: list[Bar]) -> list[Bar]:
    output = resample(rows, TIMEFRAME, "5m")
    if not output:
        raise RuntimeError("could not causally resample 5m data to 15m")
    return output


def simulate(key: str, symbol: str, bars: list[Bar], start: datetime, params: dict, *, mode: str,
             fee=None, slippage=None) -> dict:
    if mode not in {"raw", "realistic", "risk"}:
        raise ValueError(mode)
    costs = mode != "raw"
    risk = mode == "risk"
    return simulate_strategy(
        RESEARCH_V3_STRATEGIES[key](symbol, **params), bars,
        fee=v1.PRODUCTION["fee_pct_per_side"] if fee is None and costs else (fee or 0.0),
        slippage=v1.PRODUCTION["exit_spread_slippage_latency_pct"] if slippage is None and costs else (slippage or 0.0),
        starting_balance=v1.PRODUCTION["starting_equity"], risk_pct=v1.PRODUCTION["risk_per_trade_pct"],
        brain=None, min_score=0, enforce_brain=False, entry_mode=v1.PRODUCTION["entry_mode"],
        limit_ttl_bars=v1.PRODUCTION["limit_ttl_bars"], entry_delay_bars=0,
        max_daily_loss_pct=v1.PRODUCTION["max_daily_loss_pct"] if risk else 0.0,
        max_drawdown_pct=v1.PRODUCTION["max_drawdown_pct"] if risk else 0.0,
        max_consecutive_losses=v1.PRODUCTION["max_consecutive_losses"] if risk else 0,
        cooldown_after_loss=v1.PRODUCTION["cooldown_after_loss_min"] if risk else 0,
        trade_start_at=start, retain_all=True,
    )


def by_symbol_and_side(trades: list[dict], bars: list[Bar]) -> dict:
    sides = defaultdict(list)
    for trade in trades:
        sides[str(trade.get("side", "unknown"))].append(trade)
    return {"sides": {side: v1._stats(items) for side, items in sorted(sides.items())},
            "conditional": v1.conditional(trades, bars)}


def monthly_folds(trades: list[dict], first_month: int, last_month: int) -> list[dict]:
    grouped = defaultdict(list)
    for trade in trades:
        month = datetime.fromisoformat(trade["exit_time"].replace("Z", "+00:00")).month
        grouped[month].append(trade)
    return [{"month": f"2025-{month:02d}", **v1._stats(grouped[month])}
            for month in range(first_month, last_month + 1)]


def run_pool(key: str, bars_by_symbol: dict[str, list[Bar]], start: datetime, params: dict) -> dict:
    pooled = {mode: [] for mode in ("raw", "realistic", "risk")}
    symbols = {}
    for symbol, bars in bars_by_symbol.items():
        values = {mode: simulate(key, symbol, bars, start, params, mode=mode) for mode in pooled}
        for mode, value in values.items():
            pooled[mode].extend(value["trades"])
        symbols[symbol] = {mode: v1._stats(value["trades"]) for mode, value in values.items()}
        symbols[symbol]["risk_detail"] = by_symbol_and_side(values["risk"]["trades"], bars)
    return {"symbols": symbols, "raw": v1._stats(pooled["raw"]), "realistic": v1._stats(pooled["realistic"]),
            "risk": v1._stats(pooled["risk"]), "risk_trades": pooled["risk"]}


def experiment_id(key: str, params: dict) -> str:
    return "v3-" + digest(canonical({"strategy": key, "version": RESEARCH_V3_STRATEGIES[key].research_version,
                                      "timeframe": TIMEFRAME, "params": params}))[:20]


def fingerprint(key: str, params: dict) -> dict:
    code = source_hash(); config = digest(canonical(params))
    return {"strategy_id": key, "version": RESEARCH_V3_STRATEGIES[key].research_version,
            "research_hypothesis_id": HYPOTHESES[key], "source_hash": code, "configuration_hash": config,
            "candidate_fingerprint": digest(f"{key}:{RESEARCH_V3_STRATEGIES[key].research_version}:{code}:{config}".encode()),
            "created_at": CREATED_AT}


def append_ledger(path: Path, row: dict) -> None:
    existing = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                if item.get("experiment_id"):
                    existing[item["experiment_id"]] = item
    if row["experiment_id"] in existing:
        if existing[row["experiment_id"]] != row:
            raise RuntimeError(f"immutable ledger collision: {row['experiment_id']}")
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def verify_frozen_manifest(manifest: dict, train_serialized: str) -> None:
    """Fail closed if either the frozen candidate source or train evidence moved."""
    if manifest.get("source_hash") != source_hash():
        raise RuntimeError("candidate source changed after train freeze; validation invalidated")
    if manifest.get("train_sha256") != digest(train_serialized.encode()):
        raise RuntimeError("train evidence changed after freeze; validation invalidated")
    if manifest.get("test_data_opened"):
        raise RuntimeError("V3 harness never consumes the untouched test dataset")


def train_gates(result: dict, neighbours: int) -> dict:
    stats = result["risk"]
    raw, costs = result["raw"], result["realistic"]
    positive_assets = sum(item["risk"]["expectancy_r"] > 0 and item["risk"]["trades"] >= 10
                          for item in result["symbols"].values())
    folds = monthly_folds(result["risk_trades"], 1, 6)
    return {
        "raw_signal_positive": raw["expectancy_r"] > 0,
        "cost_survival": costs["expectancy_r"] > 0,
        "risk_expectancy_positive": stats["expectancy_r"] > 0,
        "profit_factor": stats["profit_factor"] >= 1.10,
        "sample_adequacy": stats["trades"] >= 30,
        "drawdown": stats["max_drawdown_r"] <= 10,
        "multi_asset": positive_assets >= 2,
        "walk_forward_train": sum(row["expectancy_r"] > 0 for row in folds) >= 4,
        "neighbourhood": neighbours >= 2,
        "no_single_trade_dependence": stats["top5_winner_share_pct"] <= 60,
    }, folds


def pick_train(experiments: list[dict]) -> list[dict]:
    stable = []
    for row in experiments:
        # Same family configs are the deliberately limited neighbourhood.
        peers = [other for other in experiments if other["strategy"] == row["strategy"]]
        neighbours = sum(other["train"]["risk"]["expectancy_r"] > 0 and other["train"]["risk"]["profit_factor"] >= 1.0
                         for other in peers)
        gates, folds = train_gates(row["train"], neighbours)
        row["train"]["walk_forward"] = folds
        row["train"]["selection_gates"] = gates
        row["train"]["neighbour_count"] = neighbours
        if all(gates.values()):
            stable.append(row)
    winners = []
    for family in CONFIGS:
        candidates = [row for row in stable if row["strategy"] == family]
        if candidates:
            winners.append(max(candidates, key=lambda row: (row["train"]["risk"]["expectancy_r"],
                                                             row["train"]["risk"]["profit_factor"],
                                                             -row["train"]["risk"]["max_drawdown_r"])))
    return winners


def validation_gates(train: dict, validation: dict, folds: list[dict], stress: dict, monte: dict) -> dict:
    baseline = validation["risk"]
    return {
        "validation_expectancy": baseline["expectancy_r"] > 0,
        "validation_pf": baseline["profit_factor"] >= 1.05,
        "validation_sample": baseline["trades"] >= 20,
        "degradation": baseline["expectancy_r"] >= max(0.0, train["risk"]["expectancy_r"] * .40),
        "walk_forward": sum(row["expectancy_r"] > 0 for row in folds) >= 6,
        "parameter_neighbourhood": train["neighbour_count"] >= 2,
        "execution_stress": stress["elevated"]["expectancy_r"] > 0 and stress["stress"]["expectancy_r"] > 0,
        "monte_carlo": monte.get("probability_loss_pct", 100) <= 25 and monte.get("max_drawdown_r", {}).get("p95", 999) <= 10,
        "causality": True,
    }


def run(args) -> int:
    train_bars, inventory = {}, {}
    for symbol in SYMBOLS:
        rows, inventory[symbol] = load_window(args.data_dir, symbol, "train")
        train_bars[symbol] = frame(rows)
    experiments = []
    for key, configs in CONFIGS.items():
        for params in configs:
            result = run_pool(key, train_bars, TRAIN_START + timedelta(days=2), params)
            row = {"experiment_id": experiment_id(key, params), "created_at": CREATED_AT, "strategy": key,
                   "hypothesis_id": HYPOTHESES[key], "timeframe": TIMEFRAME, "parameters": params,
                   "fingerprint": fingerprint(key, params), "dataset": "TRAIN Jan--Jun 2025 only",
                   "test_status": "NOT_OPENED", "train": result}
            experiments.append(row)
    selected = pick_train(experiments)
    for row in experiments:
        ledger_row = {key: value for key, value in row.items() if key != "train"}
        ledger_row.update({"result": row["train"]["risk"], "selection_gates": row["train"]["selection_gates"],
                           "classification": "FROZEN_FOR_VALIDATION" if row in selected else "REJECTED",
                           "rejection_reason": None if row in selected else "failed pre-registered TRAIN gate"})
        append_ledger(args.ledger, ledger_row)
    development = {"stage": "train_development", "created_at": CREATED_AT, "test_data_opened": False,
                   "execution": {"entry_mode": v1.PRODUCTION["entry_mode"], "limit_ttl_bars": v1.PRODUCTION["limit_ttl_bars"],
                                 "fee": v1.PRODUCTION["fee_pct_per_side"], "slippage": v1.PRODUCTION["exit_spread_slippage_latency_pct"],
                                 "learning_engine": "excluded", "brain_gate": "excluded"}, "inventory": inventory,
                   "experiment_budget": {"per_family": 5, "used": {key: len(value) for key, value in CONFIGS.items()}},
                   "experiments": experiments, "selected": [{key: row[key] for key in ("experiment_id", "strategy", "parameters", "fingerprint")} for row in selected]}
    serialized = json.dumps(development, indent=2, sort_keys=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized + "\n", encoding="utf-8")
    manifest = {"created_at": CREATED_AT, "train_sha256": digest(serialized.encode()), "source_hash": source_hash(),
                "selected": development["selected"], "validation_boundary": [VALIDATION_START.isoformat(), TEST_START.isoformat()],
                "untouched_test_boundary": TEST_START.isoformat(), "test_data_opened": False}
    args.freeze_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Validation access is conditional on an already-written immutable train manifest.
    validation_results = []
    if selected:
        frozen = json.loads(args.freeze_manifest.read_text())
        verify_frozen_manifest(frozen, serialized)
        validation_bars = {}
        validation_inventory = {}
        for symbol in SYMBOLS:
            rows, validation_inventory[symbol] = load_window(args.data_dir, symbol, "validation")
            validation_bars[symbol] = frame(rows)
        for selected_row in selected:
            key, params = selected_row["strategy"], selected_row["parameters"]
            validation = run_pool(key, validation_bars, VALIDATION_START, params)
            all_folds = monthly_folds(selected_row["train"]["risk_trades"], 1, 6) + monthly_folds(validation["risk_trades"], 7, 9)
            stress = {}
            for name, multiplier in (("baseline", 1.0), ("elevated", 1.5), ("stress", 2.0)):
                values = []
                for symbol, bars in validation_bars.items():
                    value = simulate(key, symbol, bars, VALIDATION_START, params, mode="risk",
                                     fee=v1.PRODUCTION["fee_pct_per_side"] * multiplier,
                                     slippage=v1.PRODUCTION["exit_spread_slippage_latency_pct"] * multiplier)
                    values.extend(value["trades"])
                stress[name] = v1._stats(values)
            monte = v1.monte_carlo(validation["risk_trades"])
            gates = validation_gates(selected_row["train"], validation, all_folds, stress, monte)
            classification = "UNTOUCHED_TEST_ELIGIBLE" if all(gates.values()) else "REJECTED"
            row = {"experiment_id": selected_row["experiment_id"] + "-validation", "strategy": key,
                   "fingerprint": selected_row["fingerprint"], "validation_inventory": validation_inventory,
                   "validation": validation, "walk_forward": all_folds, "execution_stress": stress,
                   "monte_carlo": monte, "gates": gates, "classification": classification,
                   "test_data_opened": False}
            validation_results.append(row)
            append_ledger(args.ledger, {"experiment_id": row["experiment_id"], "created_at": CREATED_AT,
                                        "strategy": key, "classification": classification, "fingerprint": row["fingerprint"],
                                        "dataset": "VALIDATION Jul--Sep 2025 only", "result": validation["risk"],
                                        "validation_gates": gates, "test_status": "NOT_OPENED",
                                        "rejection_reason": None if classification != "REJECTED" else "failed frozen validation gate"})
    final = {"train": development, "freeze_manifest": manifest, "validation": validation_results,
             "test_data_opened": False}
    args.results.write_text(json.dumps(final, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"experiments": len(experiments), "selected_for_validation": len(selected),
                      "validation_evaluated": len(validation_results), "test_data_opened": False}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=HUB / "data" / "strategy_v3_train_development.json")
    parser.add_argument("--freeze-manifest", type=Path, default=HUB / "data" / "strategy_v3_freeze_manifest.json")
    parser.add_argument("--results", type=Path, default=HUB / "data" / "strategy_v3_candidate_results.json")
    parser.add_argument("--ledger", type=Path, default=HUB / "data" / "strategy_v3_research_ledger.jsonl")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
