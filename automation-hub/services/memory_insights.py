"""Nightly pattern recognition + data-driven coaching over the trade memory.

Every number here is computed from the REAL stored trades — no invented
percentages. Coaching statements are sample-gated: a claim like "you perform
27% better during the London session" is only emitted when the underlying
buckets are large enough to mean anything; below the bar we downgrade to an
"early signal" or stay silent. This mirrors the evolution-memory staging used
elsewhere (early-signal < 10, evidence ≥ 30).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from services.performance import _risk_adjusted

EARLY_SIGNAL_MAX = 10      # below this a bucket is an early signal only
EVIDENCE_MIN = 30          # at/above this a bucket is strong evidence
_MIN_BUCKET = 5            # never report a breakdown row below this many trades


def _bucket_stats(rows: list[dict]) -> dict:
    """Win rate / net-R expectancy / average PnL for memory rows."""
    n = len(rows)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "expectancy": 0.0,
                "expectancy_r": 0.0, "expectancy_pnl": 0.0,
                "avg_rr": 0.0, "pnl": 0.0}
    wins = sum(1 for r in rows if (r.get("pnl") or 0) > 0)
    pnl = sum((r.get("pnl") or 0) for r in rows)
    rrs = [_net_r(r) for r in rows]
    rrs = [value for value in rrs if value is not None]
    expectancy_r = round(sum(rrs) / len(rrs), 3) if rrs else 0.0
    return {
        "trades": n,
        "win_rate": round(100 * wins / n, 1),
        # ``expectancy`` remains the public ranking field but is now correctly
        # measured in R. Dollar expectancy is explicit and separate.
        "expectancy": expectancy_r,
        "expectancy_r": expectancy_r,
        "expectancy_pnl": round(pnl / n, 3),
        "avg_rr": expectancy_r,
        "pnl": round(pnl, 2),
    }


def _net_r(row: dict) -> float | None:
    """Net realized R, including modeled fees when risk was captured."""
    try:
        risk = float(row.get("risk_amount") or 0.0)
        pnl = row.get("pnl")
        if risk > 0 and pnl is not None:
            return float(pnl) / risk
        actual = row.get("actual_rr")
        return float(actual) if actual is not None else None
    except (TypeError, ValueError):
        return None


def _group(rows: list[dict], key: str) -> list[dict]:
    buckets: dict = defaultdict(list)
    for r in rows:
        k = r.get(key)
        if k:
            buckets[k].append(r)
    out = [{key: k, **_bucket_stats(v)} for k, v in buckets.items()]
    out.sort(key=lambda d: d["expectancy"], reverse=True)
    return out


def _entry_setup_grade(row: dict) -> str | None:
    """Return only a grade proven to have been captured before the outcome.

    Legacy memories labelled the outcome-derived review grade as setup_grade.
    They intentionally remain unscored until the explicit memory rebuild
    recomposes them from the authoritative decision journal.
    """
    strategy = ((row.get("sections") or {}).get("strategy") or {})
    if strategy.get("setup_grade_basis") != "pretrade_quality_v1":
        return None
    grade = str(strategy.get("setup_grade") or "").strip()
    return grade if grade in {"A", "B", "C", "D", "F"} else None


def _group_setup_quality(rows: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grade = _entry_setup_grade(row)
        if grade:
            buckets[grade].append(row)
    out = [{"grade": grade, **_bucket_stats(bucket)}
           for grade, bucket in buckets.items()]
    out.sort(key=lambda item: item["expectancy"], reverse=True)
    return out


def _evidence_provenance(rows: list[dict]) -> dict:
    modes: dict[str, int] = defaultdict(int)
    fills: dict[str, int] = defaultdict(int)
    forward_realistic = 0
    for row in rows:
        sections = row.get("sections") or {}
        info = sections.get("trade_information") or {}
        execution = sections.get("execution") or {}
        mode = str(info.get("market_data_mode") or "unknown")
        fill = str(execution.get("fill_model") or "unknown")
        modes[mode] += 1
        fills[fill] += 1
        if mode == "paper_forward" and fill == "RealisticFill":
            forward_realistic += 1
    return {
        "paper_forward_realistic": forward_realistic,
        "other_or_unknown": len(rows) - forward_realistic,
        "market_data_modes": dict(sorted(modes.items())),
        "fill_models": dict(sorted(fills.items())),
        "live_readiness_note": (
            "Only paper_forward + RealisticFill trades are suitable for live-readiness evidence; "
            "all other trades remain visible but are not equivalent to executable forward results."
        ),
    }


def _is_live_ready(row: dict) -> bool:
    sections = row.get("sections") or {}
    info = sections.get("trade_information") or {}
    execution = sections.get("execution") or {}
    return (info.get("market_data_mode") == "paper_forward"
            and execution.get("fill_model") == "RealisticFill")


def build_review(rows: list[dict], starting_balance: float = 10000.0) -> dict:
    """Full pattern-recognition report over a set of closed-trade memories."""
    rows = [r for r in rows if r.get("result") in ("win", "loss", "breakeven")]
    n = len(rows)
    live_ready_rows = [row for row in rows if _is_live_ready(row)]
    live_ready_n = len(live_ready_rows)
    overall = _bucket_stats(rows)
    rrs = [_net_r(row) for row in rows]
    rrs = [value for value in rrs if value is not None]
    risk_adj = _risk_adjusted(rrs)

    # equity / drawdown from the memory pnl sequence (chronological)
    chrono = sorted(rows, key=lambda r: r.get("closed_at") or "")
    equity = peak = starting_balance
    max_dd = 0.0
    for r in chrono:
        equity += (r.get("pnl") or 0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    by_symbol = [b for b in _group(rows, "symbol") if b["trades"] >= 1]
    by_strategy = _group(rows, "strategy")
    by_strategy_live_ready = _group(live_ready_rows, "strategy")
    by_session = [b for b in _group(rows, "session") if b["trades"] >= 1]
    by_weekday = _group(rows, "weekday")
    by_setup = _group_setup_quality(live_ready_rows)
    scored_setups = sum(item["trades"] for item in by_setup)

    durations = [r.get("duration_s") for r in rows if r.get("duration_s") is not None]
    avg_hold = round(sum(durations) / len(durations), 0) if durations else None

    best_setup = max(by_setup, key=lambda b: b["expectancy"], default=None)
    worst_setup = min(by_setup, key=lambda b: b["expectancy"], default=None)
    best_session = max((b for b in by_session if b["trades"] >= _MIN_BUCKET),
                       key=lambda b: b["expectancy"], default=None)
    worst_session = min((b for b in by_session if b["trades"] >= _MIN_BUCKET),
                        key=lambda b: b["expectancy"], default=None)

    return {
        "sample": n,
        "overall": overall,
        "risk_adjusted": risk_adj,
        "sharpe_ratio": risk_adj["sharpe_ratio"],
        "sortino_ratio": risk_adj["sortino_ratio"],
        "max_drawdown_abs": round(max_dd, 2),
        "avg_hold_seconds": avg_hold,
        "by_symbol": by_symbol,
        "by_strategy": by_strategy,
        "by_strategy_live_ready": by_strategy_live_ready,
        "live_ready_overall": _bucket_stats(live_ready_rows),
        "by_session": by_session,
        "by_weekday": by_weekday,
        "by_setup_grade": by_setup,
        "setup_quality_coverage": {
            "scored": scored_setups,
            "unscored": live_ready_n - scored_setups,
            "eligible": live_ready_n,
            "pct": round(100 * scored_setups / live_ready_n, 1) if live_ready_n else 0.0,
            "basis": "entry-time quality score over paper_forward + RealisticFill trades only",
        },
        "evidence_provenance": _evidence_provenance(rows),
        "best_setup": best_setup,
        "worst_setup": worst_setup,
        "best_session": best_session,
        "worst_session": worst_session,
        "mistakes": _mistake_library(rows),
        "winning_patterns": [b for b in by_setup if b["expectancy"] > 0 and b["trades"] >= _MIN_BUCKET],
        "coaching": coaching_insights(live_ready_rows),
        "evidence_note": (f"{n} closed trades. Performance is observational, not a promise of future returns. "
                          "Setup-quality analysis excludes post-trade outcomes. "
                          + (f"{live_ready_n} are eligible forward-realistic observations. "
                             + ("Live-readiness sample is meaningful (30+)." if live_ready_n >= EVIDENCE_MIN
                                else "Live-readiness sample is early; do not treat it as proof."
                                if live_ready_n < EARLY_SIGNAL_MAX else
                                "Live-readiness evidence is still building."))),
    }


def _mistake_library(rows: list[dict]) -> list[dict]:
    """Aggregate the recorded mistakes into a frequency-ranked library."""
    counts: dict = defaultdict(lambda: {"count": 0, "loss": 0.0, "examples": []})
    for r in rows:
        m = ((r.get("sections", {}) or {}).get("trade_outcome", {}) or {}).get("mistakes")
        if not m or str(m).startswith("None"):
            continue
        c = counts[m]
        c["count"] += 1
        c["loss"] += min(0.0, r.get("pnl") or 0)
        if len(c["examples"]) < 3:
            c["examples"].append(r.get("trade_id"))
    lib = [{"mistake": k, "count": v["count"], "loss_attributed": round(v["loss"], 2),
            "examples": v["examples"], "repeated": v["count"] >= 2}
           for k, v in counts.items()]
    lib.sort(key=lambda d: d["count"], reverse=True)
    return lib


def coaching_insights(rows: list[dict]) -> list[dict]:
    """Data-driven coaching, sample-gated. Each statement carries the real
    numbers behind it and a confidence stage; nothing is emitted on thin data
    that could read as a fabricated edge."""
    out: list[dict] = []
    n = len(rows)
    if n < _MIN_BUCKET:
        return [{"statement": f"Only {n} closed trades so far — keep trading to unlock "
                              "session/weekday/setup coaching (needs at least "
                              f"{_MIN_BUCKET}).", "stage": "insufficient-data", "metric": None}]

    overall_exp = _bucket_stats(rows)["expectancy"]

    # Session edge vs overall
    for b in _group(rows, "session"):
        if b["trades"] < _MIN_BUCKET:
            continue
        delta = b["expectancy"] - overall_exp
        if abs(delta) < 1e-9 or overall_exp == 0:
            continue
        pct = round(100 * delta / abs(overall_exp)) if overall_exp else None
        if pct is not None and abs(pct) >= 15:
            better = delta > 0
            out.append({
                "statement": (f"You perform {abs(pct)}% {'better' if better else 'worse'} during the "
                              f"{b['session']} session ({b['expectancy']:+.3f}R vs {overall_exp:+.3f}R "
                              f"overall, {b['trades']} trades)."),
                "stage": _stage(b["trades"]),
                "metric": {"session": b["session"], **b},
            })

    # Weekday weakness
    for b in _group(rows, "weekday"):
        if b["trades"] < _MIN_BUCKET:
            continue
        if b["win_rate"] <= 35 and b["expectancy"] < 0:
            out.append({
                "statement": (f"{b['weekday']}s are a weak spot: {b['win_rate']}% win rate over "
                              f"{b['trades']} trades ({b['expectancy']:+.3f}R). Consider trading lighter "
                              f"or sitting out until the pattern reverses."),
                "stage": _stage(b["trades"]),
                "metric": {"weekday": b["weekday"], **b},
            })

    # Best pre-trade setup grade. Outcome-derived review grades are never used
    # here, which prevents hindsight leakage into coaching.
    grades = [b for b in _group_setup_quality(rows) if b["trades"] >= _MIN_BUCKET]
    top = max(grades, key=lambda b: b["expectancy"], default=None)
    if top and top["expectancy"] > 0:
        out.append({
            "statement": (f"Your pre-trade '{top['grade']}' quality setups currently lead: {top['win_rate']}% win, "
                          f"{top['expectancy']:+.3f}R over {top['trades']} trades. Prioritise them."),
            "stage": _stage(top["trades"]),
            "metric": {"grade": top["grade"], **top},
        })

    if not out:
        out.append({"statement": "No statistically meaningful edge or weakness yet — "
                                 "results are within normal variance across buckets.",
                    "stage": "no-signal", "metric": None})
    return out


def _stage(trades: int) -> str:
    if trades >= EVIDENCE_MIN:
        return "evidence"
    if trades >= EARLY_SIGNAL_MAX:
        return "building"
    return "early-signal"
