"""Paper-trading validation readiness (Phase 8).

Turns REAL stored paper trades + skip log + Safety Center state into a single
"is this ready for a human live-trading review?" verdict. It never unlocks live
trading — live stays hard-locked. Eligibility is deliberately multi-factor so a
single good-looking metric can never carry it:

  eligible = enough sample size  AND  a proven edge  AND  safety guards active

This is deliberately a deployment policy, not a profitability guarantee. It
requires forward paper trading on live market data, a non-ideal execution model,
and stability across chronological windows before a human may even *review* a
future live-trading proposal. The hard live lock remains independent and on.
"""
from __future__ import annotations

MIN_REVIEW = 60       # earliest human review; never an automatic promotion
MIN_EVIDENCE = 100    # stronger evidence threshold
MIN_PROFIT_FACTOR = 1.15
MAX_DRAWDOWN_PCT = 10.0
STABILITY_WINDOWS = 3


def _profit_factor(trades: list[dict]) -> float:
    wins = sum(float(t.get("pnl") or 0.0) for t in trades if float(t.get("pnl") or 0.0) > 0)
    losses = -sum(float(t.get("pnl") or 0.0) for t in trades if float(t.get("pnl") or 0.0) < 0)
    if losses == 0:
        return 99.0 if wins > 0 else 0.0
    return wins / losses


def _stability_windows(trades: list[dict], windows: int = STABILITY_WINDOWS) -> dict:
    """Grade chronological paper-trade windows without tuning on their outcome.

    A strategy that earns only in one short episode is not robust enough for a
    real-money review. Windows are contiguous and equal-sized, so no future
    result leaks into an earlier window.
    """
    rows = [t for t in trades if t.get("pnl") is not None]
    if len(rows) < windows:
        return {"available": False, "passed": False, "windows": []}
    base, remainder = divmod(len(rows), windows)
    start = 0
    out = []
    for i in range(windows):
        size = base + (1 if i < remainder else 0)
        bucket = rows[start:start + size]
        start += size
        net = sum(float(t.get("pnl") or 0.0) for t in bucket)
        pf = _profit_factor(bucket)
        out.append({"index": i + 1, "trades": len(bucket), "net_pnl": round(net, 2),
                    "profit_factor": round(pf, 2), "passed": net > 0 and pf >= 1.0})
    # The newest regime must be positive and no more than one window can fail.
    return {"available": True, "passed": sum(x["passed"] for x in out) >= windows - 1 and out[-1]["passed"],
            "windows": out}


def build_paper_validation(
    *,
    perf: dict,
    avg_rr: float,
    per_symbol: list[dict],            # [{name, net_pnl}]
    per_strategy: list[dict],          # [{name, net_r}]
    skipped_total: int,
    skipped_by_category: list[dict],   # [{category, count}]
    readiness: dict,                   # from services.safety_gate.build_live_readiness
    closed_trades: list[dict] | None = None,
    forward_data: bool = False,
    execution_model: str = "perfect",
    min_review: int = MIN_REVIEW,
    min_evidence: int = MIN_EVIDENCE,
    min_profit_factor: float = MIN_PROFIT_FACTOR,
    max_drawdown_pct: float = MAX_DRAWDOWN_PCT,
) -> dict:
    n = int(perf.get("trades", 0) or 0)
    pf = float(perf.get("profit_factor", 0) or 0)
    exp = float(perf.get("expectancy", 0) or 0)

    sample_ok = n >= min_review
    edge_ok = n > 0 and pf >= min_profit_factor and exp > 0
    drawdown_ok = float(perf.get("max_drawdown_pct", 0) or 0) <= max_drawdown_pct
    execution_ok = execution_model not in ("", "perfect", "ideal")
    stability = _stability_windows(closed_trades or [])
    stability_ok = stability["passed"] if n >= min_evidence else False
    # only the guards a paper->live review depends on (a real live broker
    # connection is checked separately at go-live, not during paper validation)
    reqs = {r["key"]: r["passed"] for r in readiness.get("requirements", [])}
    safety_keys = ("max_daily_loss", "max_drawdown", "decision_logging",
                   "emergency_stop_tested")
    safety_ok = all(reqs.get(k, False) for k in safety_keys)

    eligible = (n >= min_evidence and edge_ok and drawdown_ok and safety_ok
                and forward_data and execution_ok and stability_ok)
    if not sample_ok:
        stage = "insufficient-sample"
    elif n < min_evidence:
        stage = "early-review"
    elif eligible:
        stage = "ready-for-review (evidence)"
    else:
        stage = "not-eligible"

    reasons = []
    if not sample_ok:
        reasons.append(f"Need ≥ {min_review} closed paper trades (have {n}).")
    elif n < min_evidence:
        reasons.append(f"Early paper sample only — need ≥ {min_evidence} closed trades for a live review (have {n}).")
    if not edge_ok:
        reasons.append(f"Edge not proven yet (need profit factor ≥ {min_profit_factor:.2f} and positive expectancy).")
    if not drawdown_ok:
        reasons.append(f"Drawdown exceeds the review cap ({perf.get('max_drawdown_pct', 0):.2f}% > {max_drawdown_pct:.2f}%).")
    if not forward_data:
        reasons.append("Need forward paper trading on a connected live market-data feed; replay/synthetic results do not qualify.")
    if not execution_ok:
        reasons.append("Use a non-ideal paper fill model with fees/slippage before evaluating a live review.")
    if n >= min_evidence and not stability_ok:
        reasons.append("Chronological paper windows are not stable enough; the newest window and at least two of three windows must be profitable.")
    if not safety_ok:
        missing = [k for k in safety_keys if not reqs.get(k, False)]
        reasons.append("Safety guards incomplete: " + ", ".join(missing) + ".")
    if eligible:
        reasons.append("Sample size, edge and safety guards all met — a human live review may begin.")

    def _pick(rows, key):
        rows = [r for r in rows if r.get(key) is not None]
        if not rows:
            return None, None
        return (max(rows, key=lambda r: r[key]), min(rows, key=lambda r: r[key]))

    best_sym, worst_sym = _pick(per_symbol, "net_pnl")
    best_strat, worst_strat = _pick(per_strategy, "net_r")

    return {
        "sample_size": n,
        "min_review": min_review,
        "min_evidence": min_evidence,
        "criteria": {
            "minimum_profit_factor": min_profit_factor,
            "max_drawdown_pct": max_drawdown_pct,
            "forward_data": forward_data,
            "execution_model": execution_model,
        },
        "stability": stability,
        "metrics": {
            "win_rate": perf.get("win_rate", 0.0),
            "profit_factor": pf,
            "expectancy": exp,
            "max_drawdown_pct": perf.get("max_drawdown_pct", 0.0),
            "avg_rr": round(avg_rr, 2),
            "sharpe_ratio": perf.get("sharpe_ratio", 0.0),
            "sortino_ratio": perf.get("sortino_ratio", 0.0),
        },
        "best_symbol": best_sym, "worst_symbol": worst_sym,
        "best_strategy": best_strat, "worst_strategy": worst_strat,
        "skipped_total": skipped_total,
        "skipped_by_category": skipped_by_category,
        "safety": {
            "live_allowed": readiness.get("live_allowed", False),
            "hard_locked": readiness.get("hard_locked", True),
            "passed": readiness.get("passed", 0),
            "total": readiness.get("total", 0),
        },
        "live_review": {
            "eligible": eligible,
            "stage": stage,
            "reasons": reasons,
            "note": ("Live trading stays LOCKED regardless of this verdict. "
                     "This is human-review eligibility only — it never auto-enables "
                     "real-money trading."),
        },
    }
