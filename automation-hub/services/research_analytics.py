"""Deterministic comparison and filter attribution for shadow observations."""
from __future__ import annotations

from collections import defaultdict

from services.shadow_research import ShadowResearchStore


MIN_VALIDATION_SAMPLE = 100


def metrics(rows: list[dict]) -> dict:
    closed = [row for row in rows if row.get("net_r") is not None]
    net = [float(row["net_r"]) for row in closed]
    gross = [float(row.get("gross_r") or 0) for row in closed]
    wins = [value for value in net if value > 0]
    losses = [value for value in net if value < 0]
    equity = peak = drawdown = 0.0
    for value in net:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    profit_factor = (sum(wins) / abs(sum(losses)) if losses else
                     (None if wins else 0.0))
    return {
        "sample_size": len(net),
        "expectancy_r": sum(net) / len(net) if net else 0.0,
        "gross_expectancy_r": sum(gross) / len(gross) if gross else 0.0,
        "profit_factor": profit_factor,
        "max_drawdown_r": drawdown,
        "wins": len(wins), "losses": len(losses),
        "net_r": sum(net), "gross_r": sum(gross),
    }


def validation_label(summary: dict, *, minimum_sample: int = MIN_VALIDATION_SAMPLE) -> str:
    if int(summary["sample_size"]) < int(minimum_sample):
        return "INSUFFICIENT_SAMPLE"
    if float(summary["gross_expectancy_r"]) > 0 >= float(summary["expectancy_r"]):
        return "NO NET EDGE"
    pf = summary.get("profit_factor")
    if float(summary["expectancy_r"]) <= 0 or (pf is not None and float(pf) < 1):
        return "HARMFUL"
    return "PROMISING"


def filter_verdict(before: dict, after: dict, *,
                   minimum_sample: int = MIN_VALIDATION_SAMPLE) -> str:
    if min(int(before["sample_size"]), int(after["sample_size"])) < int(minimum_sample):
        return "INSUFFICIENT_SAMPLE"
    delta = float(after["expectancy_r"]) - float(before["expectancy_r"])
    if abs(delta) < 0.01:
        return "NEUTRAL"
    return "HELPFUL" if delta > 0 else "HARMFUL"


class ResearchComparison:
    def __init__(self, store: ShadowResearchStore,
                 *, minimum_sample: int = MIN_VALIDATION_SAMPLE):
        self.store = store
        self.minimum_sample = int(minimum_sample)

    @staticmethod
    def _slices(rows: list[dict]) -> dict:
        dimensions: dict[str, dict[str, list[dict]]] = {
            "session": defaultdict(list),
            "liquidity_type": defaultdict(list),
            "htf_bias": defaultdict(list),
        }
        for row in rows:
            features = (row.get("context") or {}).get("features") or {}
            dimensions["session"][str(features.get("session") or "UNKNOWN")].append(row)
            htf = features.get("htf") or {}
            selected = htf.get("4h") or htf.get("1h") or {}
            dimensions["htf_bias"][str(selected.get("bias") or "UNAVAILABLE")].append(row)
            liquidity = features.get("liquidity") or []
            names = sorted({str(item.get("type")) for item in liquidity if item.get("type")}) or ["NONE"]
            for name in names:
                dimensions["liquidity_type"][name].append(row)
        return {dimension: [
            {"name": name, **metrics(items)} for name, items in sorted(groups.items())
        ] for dimension, groups in dimensions.items()}

    def report(self) -> dict:
        rows = self.store.measurements(limit=100_000)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get("strategy_id"))].append(row)
        comparisons = []
        for strategy_id, items in grouped.items():
            summary = metrics(items)
            slices = self._slices(items)
            closed_slices = [slice_row for values in slices.values() for slice_row in values
                             if slice_row["sample_size"]]
            positive = sum(row["expectancy_r"] > 0 for row in closed_slices)
            stability = positive / len(closed_slices) if closed_slices else 0.0
            comparisons.append({
                "strategy_id": strategy_id, **summary,
                "stability": stability,
                "validation_state": validation_label(
                    summary, minimum_sample=self.minimum_sample),
                "slices": slices,
            })
        comparisons.sort(key=lambda row: (
            -float(row["expectancy_r"]),
            -(float(row["profit_factor"]) if row["profit_factor"] is not None else 999),
            float(row["max_drawdown_r"]), -float(row["stability"]),
            -int(row["sample_size"]),
        ))
        for rank, row in enumerate(comparisons, 1):
            row["rank"] = rank

        by_strategy = {row["strategy_id"]: row for row in comparisons}
        pairs = (
            ("REAL_HTF", "SMC_A_SWEEP", "SMC_C_SWEEP_HTF"),
            ("DISPLACEMENT", "SMC_A_SWEEP", "SMC_D_SWEEP_DISPLACEMENT"),
            ("FRESH_LIQUIDITY", "SMC_A_SWEEP", "SMC_E_SWEEP_FRESH_LIQUIDITY"),
            ("HTF_WITH_SESSION_ATTRIBUTION", "SMC_B_SWEEP_SESSION", "SMC_F_SWEEP_HTF_SESSION"),
        )
        contributions = []
        for name, before_id, after_id in pairs:
            before_rows, after_rows = grouped.get(before_id, []), grouped.get(after_id, [])
            before, after = metrics(before_rows), metrics(after_rows)
            blocked = [row for row in after_rows
                       if row.get("blocker") not in {"SETUP_FOUND", "NONE"}
                       and row.get("net_r") is not None]
            contributions.append({
                "filter": name, "before_strategy": before_id,
                "after_strategy": after_id, "before": before, "after": after,
                "blocked_winners": sum(float(row["net_r"]) > 0 for row in blocked),
                "blocked_losers": sum(float(row["net_r"]) < 0 for row in blocked),
                "verdict": filter_verdict(
                    before, after, minimum_sample=self.minimum_sample),
            })
        return {
            "execution_class": "SHADOW", "real_execution_allowed": False,
            "minimum_validation_sample": self.minimum_sample,
            "rank_basis": ["expectancy_r", "profit_factor", "max_drawdown_r",
                           "stability", "sample_size"],
            "win_rate_used_for_ranking": False,
            "variants": comparisons, "filter_contributions": contributions,
            "best_positive_cost_adjusted_rule": next((row for row in comparisons
                if row["expectancy_r"] > 0), None),
            "allowed_validation_states": ["INSUFFICIENT_SAMPLE", "PROMISING",
                                          "NO NET EDGE", "HARMFUL"],
        }

