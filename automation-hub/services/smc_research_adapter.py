"""Read-only normalization of frozen Native SMC research records.

This adapter never imports an execution client and never mutates the frozen
SMC engine. Missing source facts remain ``None`` and are accompanied by a
conversion warning; they are never inferred from Price Action results.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime

from services.native_smc_visual_attestation import engine_fingerprints


_ASSUMPTIONS = (
    "source_data", "symbols", "timeframes", "date_partitions", "cost_model",
    "fill_model", "ambiguity", "risk_per_trade_pct", "exit_assumptions",
    "reporting_metrics_version", "funding_data_complete",
)


def _first(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return None


def _float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _metrics(rows: list[dict]) -> dict:
    closed = [row for row in rows if row.get("net_r") is not None]
    values = [float(row["net_r"]) for row in closed]
    wins, losses = [x for x in values if x > 0], [x for x in values if x < 0]
    equity = peak = drawdown = 0.0
    streak = longest = 0
    for value in values:
        equity += value; peak = max(peak, equity); drawdown = max(drawdown, peak - equity)
        streak = streak + 1 if value < 0 else 0; longest = max(longest, streak)
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    gross = sum(float(row.get("gross_r") or 0) for row in closed)
    costs = sum(float(row.get("costs_r") or 0) for row in closed)
    timestamps = []
    for row in closed:
        for key in ("filled_at", "signal_at", "closed_at"):
            value = row.get(key)
            if value:
                try:
                    timestamps.append(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
                except ValueError:
                    pass
    span_days = max((max(timestamps) - min(timestamps)).total_seconds() / 86_400,
                    1 / 24) if timestamps else None
    return {"trade_count": len(closed), "win_rate_pct": len(wins) / len(closed) * 100 if closed else 0,
            "expectancy_r": sum(values) / len(values) if values else 0,
            "profit_factor": gross_profit / gross_loss if gross_loss else (None if wins else 0),
            "maximum_drawdown_r": drawdown, "longest_losing_streak": longest,
            "gross_r": gross, "net_r": sum(values), "costs_r": costs,
            "gross_edge_consumed_pct": costs / gross * 100 if gross > 0 else None,
            "trade_frequency_per_day": len(closed) / span_days if span_days else 0,
            "normalized_trade_count": len(rows),
            "incomplete_trade_count": len(rows) - len(closed)}


class FrozenSMCNormalizationAdapter:
    """Map immutable SMC output to the shared normalized-R research contract."""

    def __init__(self):
        self.fingerprints = engine_fingerprints()

    def normalize(self, source: dict, *, assumptions: dict,
                  experiment_configuration: dict | None = None) -> dict:
        original = copy.deepcopy(source)
        records = list(source.get("trades") or source.get("records") or
                       source.get("proposals") or [])
        normalized, global_warnings = [], []
        for index, record in enumerate(records):
            row = dict(record)
            entry = _float(_first(row, "fill_price", "entry_price", "entry"))
            requested = _float(_first(row, "requested_entry", "entry"))
            stop = _float(_first(row, "stop", "stop_loss"))
            target = _float(_first(row, "target", "take_profit"))
            exit_price = _float(_first(row, "exit_price", "close_price"))
            direction = str(_first(row, "direction", "side") or "").lower()
            if direction in {"buy", "long"}: direction = "bullish"
            if direction in {"sell", "short"}: direction = "bearish"
            risk = abs(entry - stop) if entry is not None and stop is not None else None
            gross_r = _float(row.get("gross_r"))
            if gross_r is None and entry is not None and exit_price is not None and risk:
                gross_r = ((exit_price - entry) if direction == "bullish" else
                           (entry - exit_price)) / risk
            commission = _float(_first(row, "commission_r", "fees_r"))
            spread = _float(row.get("spread_r"))
            slippage = _float(row.get("slippage_r"))
            funding = _float(_first(row, "funding_pnl_r", "funding_r"))
            if funding is None and row.get("funding_cost_r") is not None:
                funding_cost = _float(row.get("funding_cost_r"))
                funding = -funding_cost if funding_cost is not None else None
            net_r = _float(row.get("net_r"))
            cost_fields_complete = all(value is not None for value in
                                       (commission, spread, slippage, funding))
            if net_r is None and gross_r is not None and cost_fields_complete:
                net_r = gross_r - commission - spread - slippage + funding
            costs_r = (commission + spread + slippage - funding
                       if cost_fields_complete else None)
            item = {
                "id": str(_first(row, "trade_id", "id") or f"smc-record-{index + 1}"),
                "source_record_id": _first(row, "trade_id", "id"),
                "strategy_id": _first(row, "strategy_id", "strategy") or "SMC_NATIVE_V1_RESEARCH",
                "setup_id": _first(row, "setup_id"),
                "symbol": _first(row, "symbol"), "timeframe": _first(row, "timeframe"),
                "signal_at": _timestamp(_first(row, "signal_at", "decision_at", "created_at")),
                "decision_at": _timestamp(_first(row, "decision_at", "signal_at", "created_at")),
                "direction": direction or None, "requested_entry": requested,
                "entry": entry, "stop": stop, "target": target,
                "filled_at": _timestamp(_first(row, "filled_at", "opened_at")),
                "exit_price": exit_price,
                "closed_at": _timestamp(_first(row, "closed_at", "exited_at")),
                "gross_r": gross_r, "net_r": net_r, "gross_pnl": _float(row.get("gross_pnl")),
                "net_pnl": _float(row.get("net_pnl")), "commission_r": commission,
                "spread_r": spread, "slippage_r": slippage, "funding_r": funding,
                "costs_r": costs_r, "status": _first(row, "status", "setup_status"),
                "reason": _first(row, "reason", "rejection_reason", "cancellation_reason"),
                "regime": _first(row, "regime", "market_regime"),
                "partition": _first(row, "partition", "dataset_partition"),
                "experiment_configuration": copy.deepcopy(experiment_configuration or
                                                            source.get("configuration") or {}),
            }
            required_trade = ("setup_id", "symbol", "timeframe", "signal_at", "direction",
                              "entry", "stop", "target", "status", "partition")
            unavailable = [key for key in required_trade if item.get(key) is None]
            if not cost_fields_complete:
                unavailable.extend(key for key, value in
                                   (("commission_r", commission), ("spread_r", spread),
                                    ("slippage_r", slippage), ("funding_r", funding))
                                   if value is None)
            item["unavailable_fields"] = sorted(set(unavailable))
            item["conversion_warnings"] = (["Source record lacks fields required for a complete normalized comparison."]
                                           if unavailable else [])
            item["source_record_sha256"] = hashlib.sha256(json.dumps(
                row, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
            normalized.append(item)
        missing_assumptions = [key for key in _ASSUMPTIONS if assumptions.get(key) is None]
        incomplete_records = sum(bool(row["unavailable_fields"]) for row in normalized)
        if not records:
            global_warnings.append("SMC source contains no result records; engine proposals alone cannot prove fills or outcomes.")
        if missing_assumptions:
            global_warnings.append("Execution assumptions are unavailable: " + ", ".join(missing_assumptions))
        if incomplete_records:
            global_warnings.append(f"{incomplete_records} SMC record(s) have unavailable normalized fields.")
        fair = bool(records) and not missing_assumptions and not incomplete_records
        source_hash = hashlib.sha256(json.dumps(original, sort_keys=True, default=str,
                                                separators=(",", ":")).encode()).hexdigest()
        return {
            "research_id": "SMC_NATIVE_V1_RESEARCH", "adapter_version": "smc-normalized-v1",
            "read_only": True, "mixed_strategy": False, "execution_allowed": False,
            "engine_fingerprints": self.fingerprints,
            "source_records_sha256": source_hash, "source_records": original,
            "assumptions": copy.deepcopy(assumptions),
            "configuration": copy.deepcopy(experiment_configuration or source.get("configuration") or {}),
            "records": normalized, "metrics": _metrics(normalized),
            "stability": copy.deepcopy(source.get("stability")),
            "cost_sensitivity": copy.deepcopy(source.get("cost_sensitivity")),
            "parameter_sensitivity": copy.deepcopy(source.get("parameter_sensitivity")),
            "oos_degradation_expectancy_r": source.get("oos_degradation_expectancy_r"),
            "implementation_complexity": source.get("implementation_complexity", "higher"),
            "normalization": {"status": "COMPLETE" if fair else "INCOMPLETE",
                              "fair_comparison_allowed": fair,
                              "missing_assumptions": missing_assumptions,
                              "incomplete_records": incomplete_records,
                              "warnings": global_warnings},
            "verdict": "READ_ONLY_NORMALIZATION_NO_PROFITABILITY_CLAIM",
        }
