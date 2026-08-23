"""Predeclared PA1–PA4 reference study and isolated research ladder."""
from __future__ import annotations

import hashlib
import json
import copy
from dataclasses import asdict, replace
from pathlib import Path

from bot.types import Bar
from services.native_price_action import PriceActionConfig
from services.price_action_research import (
    PriceActionExperimentRunner, controlled_pa_smc_report, evaluate_research_quality,
)
from services.research_funding import HistoricalFundingSeries


REFERENCE_UNIVERSE = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
REFERENCE_TIMEFRAMES = ("15m", "1h", "4h")
# Declared before results are observed. These are conservative simulation
# assumptions, not claims of reconstructed historical order books.
REFERENCE_EXECUTION_COSTS = {
    "BTCUSDT": {"commission_bps": 4.0, "spread_bps": 1.0, "slippage_bps": 2.0},
    "ETHUSDT": {"commission_bps": 4.0, "spread_bps": 1.2, "slippage_bps": 2.5},
    "BNBUSDT": {"commission_bps": 4.0, "spread_bps": 1.5, "slippage_bps": 3.0},
    "SOLUSDT": {"commission_bps": 4.0, "spread_bps": 2.0, "slippage_bps": 4.0},
    "XRPUSDT": {"commission_bps": 4.0, "spread_bps": 2.0, "slippage_bps": 4.0},
}
SELECTION_METHOD = (
    "Predeclared common configuration and isolated ladder across every symbol/timeframe; "
    "no per-asset tuning and no untouched-OOS results used for selection."
)


def _study_code_sha256() -> str:
    """Pin the orchestration/ladder definition as part of every study identity."""
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def baseline_config() -> PriceActionConfig:
    return PriceActionConfig(
        symbol=REFERENCE_UNIVERSE[0], timeframe=REFERENCE_TIMEFRAMES[0],
        swing_left=3, swing_right=3, trigger_filter="generic_rejection",
        confusion_candles=3, entry_expiry_bars=3, entry_model="confirmation",
        stop_model="rejection_extreme", rr_ratio=2.5,
        commission_bps=4.0, spread_bps=2.0, slippage_bps=3.0,
        first_touch_only=False, zone_timeframe_scope="same_timeframe",
    )


def _ladder(base: PriceActionConfig) -> list[tuple[str, PriceActionConfig, str]]:
    return [
        ("01_baseline", base, "PA1-PA4 baseline: generic rejection and up to three confusion candles"),
        ("02_pin_bar_only", replace(base, trigger_filter="pin_bar_only"),
         "generic rejection versus directional pin-bar-only"),
        ("03_immediate_confirmation", replace(base, confusion_candles=0),
         "immediate confirmation versus up to three confusion candles"),
        ("04_structural_zone_stop", replace(base, stop_model="structural_zone"),
         "rejection-extreme versus structural-zone stop"),
        ("05_close_entry", replace(base, entry_model="close"),
         "confirmation entry versus next-available close entry"),
        ("06_retracement_50", replace(base, entry_model="retracement_50"),
         "confirmation entry versus 50% rejection-candle retracement"),
        ("07_first_touch", replace(base, first_touch_only=True),
         "all eligible touches versus first zone touch only"),
        ("08_higher_timeframe_zones", replace(base, zone_timeframe_scope="higher_timeframe"),
         "same-timeframe versus completed higher-timeframe zones"),
    ]


def run_reference_study(runner: PriceActionExperimentRunner,
                        datasets: dict[tuple[str, str], list[Bar]],
                        funding: dict[str, HistoricalFundingSeries], *,
                        save: bool = True, normalized_smc: dict | None = None) -> dict:
    expected = {(symbol, timeframe) for symbol in REFERENCE_UNIVERSE
                for timeframe in REFERENCE_TIMEFRAMES}
    missing = sorted(expected - set(datasets))
    if missing:
        raise ValueError(f"reference study datasets are incomplete: {missing}")
    steps = []
    for label, config, hypothesis in _ladder(baseline_config()):
        result = runner.run(
            datasets, config, save=False, funding_series=funding,
            execution_costs_by_symbol=REFERENCE_EXECUTION_COSTS,
            parameter_selection_method=SELECTION_METHOD,
            walk_forward_folds=4, cost_multipliers=(1.0, 1.5, 2.0),
        )
        if save:
            runner.store.save(result)
        steps.append({"label": label, "hypothesis": hypothesis, "result": result})
    baseline = copy.deepcopy(steps[0]["result"])
    baseline_oos = float(baseline.get("by_partition", {}).get(
        "untouched_oos", {}).get("expectancy_r") or 0)
    variants = []
    for step in steps[1:]:
        result = step["result"]
        oos = result.get("by_partition", {}).get("untouched_oos", {})
        variants.append({"ladder_step": step["label"],
                         "experiment_id": result["experiment_id"],
                         "oos_trade_count": oos.get("trade_count", 0),
                         "oos_expectancy_r": oos.get("expectancy_r"),
                         "expectancy_delta_from_baseline_r":
                             float(oos.get("expectancy_r") or 0) - baseline_oos})
    eligible = [row for row in variants if row["oos_trade_count"]]
    sensitivity = {
        "method": "predeclared isolated one-change-at-a-time ladder",
        "variants": variants,
        "positive_oos_fraction": (sum(float(row["oos_expectancy_r"] or 0) > 0 for row in eligible) /
                                  len(eligible) if eligible else None),
        "final_test_used_for_selection": False,
    }
    baseline["parameter_sensitivity"] = sensitivity
    baseline["quality_gates"] = evaluate_research_quality(baseline)
    study_material = {"baseline_experiment_id": baseline["experiment_id"],
                      "ladder_experiment_ids": [row["result"]["experiment_id"] for row in steps],
                      "dataset_version": baseline["dataset_version"],
                      "code_version": baseline["code_version"],
                      "study_code_sha256": _study_code_sha256(),
                      "normalized_smc_source": ((normalized_smc or {}).get("source_records_sha256")
                                                if normalized_smc else None)}
    study_id = "pa-reference-" + hashlib.sha256(json.dumps(
        study_material, sort_keys=True).encode()).hexdigest()[:20]
    if normalized_smc is None:
        comparison = {"status": "NOT_RUN", "fair_comparison_allowed": False,
                      "reason": "A normalized SMC result with identical datasets, partitions and costs was not supplied."}
    else:
        try:
            comparison = controlled_pa_smc_report(baseline, normalized_smc)
        except ValueError as exc:
            comparison = {"status": "INCOMPATIBLE", "fair_comparison_allowed": False,
                          "reason": str(exc)}
    artifact = {
        "artifact_id": study_id, "artifact_version": "pa-reference-study-v1",
        "created_at": max(rows[-1].timestamp for rows in datasets.values() if rows).isoformat(),
        "research_only": True, "real_execution_allowed": False,
        "universe": list(REFERENCE_UNIVERSE), "timeframes": list(REFERENCE_TIMEFRAMES),
        "selection_methodology": SELECTION_METHOD,
        "execution_costs_by_symbol": REFERENCE_EXECUTION_COSTS,
        "baseline": baseline,
        "ladder": [{"step": row["label"], "hypothesis": row["hypothesis"],
                    "experiment_id": row["result"]["experiment_id"],
                    "configuration": row["result"]["configuration"],
                    "metrics": row["result"]["metrics"],
                    "by_partition": row["result"]["by_partition"],
                    "quality_gates": row["result"]["quality_gates"]} for row in steps],
        "parameter_sensitivity": sensitivity,
        "per_strategy_metrics": baseline.get("segments", {}).get("strategy_id", {}),
        "oos_per_strategy_metrics": baseline.get("oos_segments", {}).get("strategy_id", {}),
        "funding_coverage": baseline["funding_coverage"],
        "data_quality_warnings": sorted({warning for row in baseline["funding_coverage"].values()
                                         for warning in row.get("warnings", [])}),
        "reproduction": {
            "command": ("python scripts/run_price_action_reference_study.py "
                        "--cache-dir <cache> --research-db <sqlite> --output <directory>"),
            "dataset_version": baseline["dataset_version"],
            "code_version": baseline["code_version"],
            "study_code_sha256": study_material["study_code_sha256"],
            "configuration": asdict(baseline_config()),
        },
        "pa_vs_smc_comparison": comparison,
        "operational_live_feed_validation": {
            "status": "NOT_ATTACHED",
            "reason": "Attach the output of validate_price_action_public_feed.py from the target VPS."},
        "software_validation": {"status": "SEPARATE_TEST_EVIDENCE_REQUIRED"},
        "verdict": "RESEARCH_EVIDENCE_ONLY_NO_FUTURE_PERFORMANCE_GUARANTEE",
    }
    canonical = json.loads(json.dumps(artifact, sort_keys=True, default=str))
    if save:
        return runner.store.save_artifact(canonical)
    return canonical


def export_reference_artifact(artifact: dict, directory: str | Path) -> dict:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / f"{artifact['artifact_id']}.json"
    markdown_path = target / f"{artifact['artifact_id']}.md"
    json_path.write_text(json.dumps(artifact, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    baseline = artifact["baseline"]
    lines = [
        f"# Price Action reference study `{artifact['artifact_id']}`", "",
        "Research-only simulation. Historical performance does not guarantee future results.", "",
        f"- Dataset: `{baseline['dataset_version']}`",
        f"- Code: `{baseline['code_version']}`",
        f"- Universe: {', '.join(artifact['universe'])}",
        f"- Timeframes: {', '.join(artifact['timeframes'])}",
        f"- Overall gate: `{baseline['quality_gates']['classification']}`", "",
        "## Partition metrics", "",
        "```json", json.dumps(baseline["by_partition"], sort_keys=True, indent=2), "```", "",
        "## Funding coverage", "", "```json",
        json.dumps(artifact["funding_coverage"], sort_keys=True, indent=2), "```", "",
        "## Research ladder", "", "```json",
        json.dumps(artifact["parameter_sensitivity"], sort_keys=True, indent=2), "```", "",
        "## Reproduction", "", f"`{artifact['reproduction']['command']}`", "",
    ]
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(markdown_path)}
