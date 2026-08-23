"""Reproducible Price Action experiments and controlled comparison reports."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from bot.types import Bar
from services.native_price_action import NativePriceActionEngine, PriceActionConfig
from services.research_funding import (
    DISABLED, HistoricalFundingSeries, unavailable_series,
)


def _stable(payload: object, prefix: str) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:20]}"


def _dataset_version(datasets: dict[tuple[str, str], list[Bar]],
                     funding: dict[str, HistoricalFundingSeries] | None = None) -> str:
    material = []
    for (symbol, timeframe), rows in sorted(datasets.items()):
        material.append({"symbol": symbol, "timeframe": timeframe, "count": len(rows),
                         "first": rows[0].timestamp if rows else None,
                         "last": rows[-1].timestamp if rows else None,
                         "ohlc_hash": hashlib.sha256("|".join(
                             f"{row.timestamp.isoformat()}:{row.open}:{row.high}:{row.low}:{row.close}"
                             for row in rows).encode()).hexdigest()})
    funding_material = [{"symbol": symbol, "dataset_id": series.dataset_id,
                         "state": series.state, "records": len(series.events)}
                        for symbol, series in sorted((funding or {}).items())]
    return _stable({"ohlc": material, "funding": funding_material}, "dataset")


def _max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _longest_losing_streak(values: list[float]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def _metrics(rows: list[dict], *, rejected: int = 0, cancelled: int = 0,
             unfilled: int = 0) -> dict:
    closed = [row for row in rows if row.get("net_r") is not None]
    rs = [float(row["net_r"]) for row in closed]
    gross_rs = [float(row.get("gross_r") or 0) for row in closed]
    costs = [float(row.get("costs_r") or 0) for row in closed]
    wins = [value for value in rs if value > 0]
    losses = [value for value in rs if value < 0]
    gross_profit, gross_loss = sum(wins), abs(sum(losses))
    gross_edge = sum(gross_rs)
    total_costs = sum(costs)
    complete_costs = sum(bool(row.get("funding_complete")) for row in closed)
    first = min((row.get("created_at") for row in closed), default=None)
    last = max((row.get("closed_at") or row.get("created_at") for row in closed), default=None)
    span_days = max((datetime.fromisoformat(str(last)).timestamp() -
                     datetime.fromisoformat(str(first)).timestamp()) / 86_400, 1 / 24) \
        if first and last else None
    return {
        "trade_count": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": len(wins) / len(closed) * 100 if closed else 0,
        "average_win_r": sum(wins) / len(wins) if wins else 0,
        "average_loss_r": sum(losses) / len(losses) if losses else 0,
        "expectancy_r": sum(rs) / len(rs) if rs else 0,
        "median_r": sorted(rs)[len(rs) // 2] if rs else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (None if gross_profit else 0),
        "maximum_drawdown_r": _max_drawdown(rs), "longest_losing_streak": _longest_losing_streak(rs),
        "gross_r": gross_edge, "net_r": sum(rs),
        "fees_r": sum(float(row.get("commission_r") or row.get("fees_r") or 0)
                      for row in closed),
        "spread_r": sum(float(row.get("spread_r") or 0) for row in closed),
        "slippage_r": sum(float(row.get("slippage_r") or 0) for row in closed),
        "funding_r": sum(float(row.get("funding_r") or 0) for row in closed),
        "costs_r": total_costs,
        "gross_edge_consumed_pct": total_costs / gross_edge * 100 if gross_edge > 0 else None,
        "unfilled": unfilled, "cancelled": cancelled, "rejected": rejected,
        "funding_complete_trades": complete_costs,
        "funding_incomplete_trades": len(closed) - complete_costs,
        "cost_data_complete": complete_costs == len(closed),
        "trade_frequency_per_day": len(closed) / span_days if span_days else 0,
    }


def _equity_and_drawdown(rows: list[dict]) -> list[dict]:
    equity = peak = 0.0
    result = []
    for index, row in enumerate(sorted(rows, key=lambda item: item.get("closed_at") or item["created_at"])):
        equity += float(row.get("net_r") or 0)
        peak = max(peak, equity)
        result.append({"index": index + 1, "timestamp": row.get("closed_at") or row["created_at"],
                       "equity_r": equity, "drawdown_r": peak - equity,
                       "trade_id": row.get("id")})
    return result


def _cost_adjusted_rows(rows: list[dict], multiplier: float) -> list[dict]:
    """Stress execution friction while leaving historical funding unchanged."""
    adjusted = []
    for row in rows:
        commission = float(row.get("commission_r") or row.get("fees_r") or 0) * multiplier
        spread = float(row.get("spread_r") or 0) * multiplier
        slippage = float(row.get("slippage_r") or 0) * multiplier
        funding = float(row.get("funding_r") or 0)
        costs = commission + spread + slippage - funding
        adjusted.append({**row, "commission_r": commission, "fees_r": commission,
                         "spread_r": spread, "slippage_r": slippage,
                         "costs_r": costs,
                         "net_r": float(row.get("gross_r") or 0) - costs})
    return adjusted


def _slice(rows: list[dict], key: str) -> dict:
    values: dict[str, list[dict]] = {}
    for row in rows:
        values.setdefault(str(row.get(key) or "unknown"), []).append(row)
    return {name: _metrics(group) for name, group in sorted(values.items())}


class PriceActionExperimentStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._db:
            self._db.executescript("""
              CREATE TABLE IF NOT EXISTS pa_experiments(
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                dataset_version TEXT NOT NULL, code_version TEXT NOT NULL,
                config_json TEXT NOT NULL, result_json TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS pa_research_artifacts(
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                artifact_json TEXT NOT NULL);
            """)

    def save(self, result: dict) -> dict:
        existing = self._db.execute("SELECT * FROM pa_experiments WHERE id=?", (result["experiment_id"],)).fetchone()
        if existing:
            prior = json.loads(existing["result_json"])
            if prior != result:
                raise ValueError("immutable experiment ID already exists with different results")
            return prior
        with self._db:
            self._db.execute("INSERT INTO pa_experiments VALUES (?,?,?,?,?,?)",
                             (result["experiment_id"], datetime.now(timezone.utc).isoformat(),
                              result["dataset_version"], result["code_version"],
                              json.dumps(result["configuration"], sort_keys=True, default=str),
                              json.dumps(result, sort_keys=True, default=str)))
        return result

    def get(self, experiment_id: str) -> dict:
        row = self._db.execute("SELECT result_json FROM pa_experiments WHERE id=?", (experiment_id,)).fetchone()
        if not row:
            raise KeyError(experiment_id)
        return json.loads(row[0])

    def list(self) -> list[dict]:
        return [dict(row) for row in self._db.execute(
            "SELECT id,created_at,dataset_version,code_version FROM pa_experiments ORDER BY created_at DESC")]

    def clear(self) -> None:
        with self._db:
            self._db.execute("DELETE FROM pa_experiments")
            self._db.execute("DELETE FROM pa_research_artifacts")

    def save_artifact(self, artifact: dict) -> dict:
        existing = self._db.execute(
            "SELECT artifact_json FROM pa_research_artifacts WHERE id=?",
            (artifact["artifact_id"],)).fetchone()
        canonical = json.loads(json.dumps(artifact, sort_keys=True, default=str))
        if existing:
            prior = json.loads(existing[0])
            if prior != canonical:
                raise ValueError("immutable research artifact ID already exists with different content")
            return prior
        with self._db:
            self._db.execute("INSERT INTO pa_research_artifacts VALUES (?,?,?)",
                             (artifact["artifact_id"], datetime.now(timezone.utc).isoformat(),
                              json.dumps(canonical, sort_keys=True)))
        return canonical

    def get_artifact(self, artifact_id: str) -> dict:
        row = self._db.execute(
            "SELECT artifact_json FROM pa_research_artifacts WHERE id=?", (artifact_id,)).fetchone()
        if not row:
            raise KeyError(artifact_id)
        return json.loads(row[0])

    def list_artifacts(self) -> list[dict]:
        return [dict(row) for row in self._db.execute(
            "SELECT id,created_at FROM pa_research_artifacts ORDER BY created_at DESC")]


class PriceActionExperimentRunner:
    def __init__(self, store: PriceActionExperimentStore, *, source_path: str | Path | None = None):
        self.store = store
        sources = ([Path(source_path)] if source_path else
                   [Path(__file__).with_name("native_price_action.py"), Path(__file__),
                    Path(__file__).with_name("research_funding.py")])
        material = b"".join(path.name.encode() + b"\0" + path.read_bytes() for path in sources)
        self.code_version = "code-" + hashlib.sha256(material).hexdigest()[:20]

    @staticmethod
    def _trade_rows(engine: NativePriceActionEngine, symbol: str, timeframe: str,
                    partition_for, funding: HistoricalFundingSeries) -> list[dict]:
        rows = []
        for trade in engine.research_trades.values():
            setup = engine.setups.get(trade.setup_id)
            event = engine.events.get(setup.trigger_event_id) if setup and setup.trigger_event_id else None
            zone = engine.zones.get(setup.zone_id) if setup and setup.zone_id else None
            snapshot = engine.snapshots.get(trade.created_at)
            fill = float(trade.fill_price or trade.requested_entry)
            risk = max(abs(fill - trade.stop), 1e-12)
            raw_fill = float(trade.raw_fill_price or trade.requested_entry)
            raw_exit = float(trade.raw_exit_price or trade.exit_price or raw_fill)
            raw_gross = (((raw_exit - raw_fill) if trade.direction == "bullish" else
                          (raw_fill - raw_exit)) / risk if trade.closed_at is not None else None)
            execution_cost = (float(raw_gross) - float(trade.gross_r)
                              if raw_gross is not None and trade.gross_r is not None else 0.0)
            execution_bps = engine.config.slippage_bps + engine.config.spread_bps / 2
            slippage_share = engine.config.slippage_bps / execution_bps if execution_bps else 0
            slippage = execution_cost * slippage_share
            spread = execution_cost - slippage
            funding_effect = {"funding_r": 0.0, "complete": funding.state == DISABLED,
                              "events": [], "state": funding.state,
                              "warnings": list(funding.warnings)}
            if trade.filled_at is not None and trade.closed_at is not None:
                funding_effect = funding.effect_r(
                    direction=trade.direction, entry_price=fill, risk_distance=risk,
                    opened_at=trade.filled_at, closed_at=trade.closed_at)
            funding_r = float(funding_effect["funding_r"] or 0)
            commission_r = float(trade.costs_r or 0)
            costs_r = (execution_cost + commission_r - funding_r
                       if raw_gross is not None else None)
            net_r = (raw_gross - costs_r if raw_gross is not None and costs_r is not None else None)
            rows.append({**asdict(trade), "symbol": symbol, "timeframe": timeframe,
                         "year": trade.created_at.year, "partition": partition_for(trade.created_at),
                         "regime": snapshot.structure_bias if snapshot else "unknown",
                         "trigger": event.pattern or event.event_type if event else "unknown",
                         "zone_touch_count": zone.touch_count if zone else 0,
                         "entry_model": trade.entry_model, "stop_model": trade.stop_model,
                         "gross_r": raw_gross, "commission_r": commission_r,
                         "fees_r": commission_r, "spread_r": spread,
                         "slippage_r": slippage, "funding_r": funding_r,
                         "funding_complete": funding_effect["complete"],
                         "funding_data_state": funding_effect["state"],
                         "funding_events": funding_effect["events"],
                         "funding_warnings": funding_effect["warnings"],
                         "net_r": net_r, "costs_r": costs_r})
        return rows

    def run(self, datasets: dict[tuple[str, str], list[Bar]], config: PriceActionConfig,
            *, partitions: tuple[float, float] = (.6, .8), walk_forward_folds: int = 4,
            cost_multipliers: Iterable[float] = (1.0, 1.5, 2.0), save: bool = True,
            funding_series: dict[str, HistoricalFundingSeries] | None = None,
            funding_intentionally_disabled: bool = False,
            parameter_selection_method: str = "one shared configuration selected without final-test access",
            execution_costs_by_symbol: dict[str, dict[str, float]] | None = None) -> dict:
        if not datasets:
            raise ValueError("at least one verified dataset is required")
        ranges: dict[str, tuple[datetime, datetime]] = {}
        for (symbol, _timeframe), rows in datasets.items():
            if rows:
                prior = ranges.get(symbol)
                ranges[symbol] = (min(rows[0].timestamp, prior[0]) if prior else rows[0].timestamp,
                                  max(rows[-1].timestamp, prior[1]) if prior else rows[-1].timestamp)
        resolved_funding = {}
        for symbol, (start, end) in ranges.items():
            if funding_intentionally_disabled:
                resolved_funding[symbol] = HistoricalFundingSeries.build(
                    symbol, (), requested_start=start, requested_end=end,
                    intentionally_disabled=True)
            else:
                resolved_funding[symbol] = (funding_series or {}).get(symbol) or \
                    unavailable_series(symbol, start, end)
        dataset_version = _dataset_version(datasets, resolved_funding)
        frozen = {**asdict(config), "symbols_timeframes": sorted([list(key) for key in datasets]),
                  "partitions": partitions, "walk_forward_folds": walk_forward_folds,
                  "cost_multipliers": list(cost_multipliers),
                  "funding_intentionally_disabled": funding_intentionally_disabled,
                  "funding_dataset_ids": {key: row.dataset_id for key, row in resolved_funding.items()},
                  "execution_costs_by_symbol": execution_costs_by_symbol or {},
                  "parameter_selection_method": parameter_selection_method}
        experiment_id = _stable({"dataset": dataset_version, "code": self.code_version, "config": frozen}, "pa-exp")
        all_rows, rejected, unfilled = [], 0, 0
        partition_boundaries = {}
        for (symbol, timeframe), bars in sorted(datasets.items()):
            if len(bars) < 20:
                raise ValueError(f"{symbol} {timeframe} has insufficient candles")
            first, second = int(len(bars) * partitions[0]), int(len(bars) * partitions[1])
            validation_at, oos_at = bars[first].timestamp, bars[second].timestamp
            partition_boundaries[f"{symbol}:{timeframe}"] = {
                "development_end": validation_at.isoformat(), "validation_end": oos_at.isoformat(),
                "untouched_oos_end": bars[-1].timestamp.isoformat()}
            def partition_for(stamp, v=validation_at, o=oos_at):
                return "development" if stamp < v else "validation" if stamp < o else "untouched_oos"
            costs = (execution_costs_by_symbol or {}).get(symbol, {})
            local_config = replace(
                config, symbol=symbol, timeframe=timeframe,
                commission_bps=float(costs.get("commission_bps", config.commission_bps)),
                spread_bps=float(costs.get("spread_bps", config.spread_bps)),
                slippage_bps=float(costs.get("slippage_bps", config.slippage_bps)))
            engine = NativePriceActionEngine(local_config)
            engine.ingest_closed_bars(bars)
            all_rows.extend(self._trade_rows(
                engine, symbol, timeframe, partition_for, resolved_funding[symbol]))
            unfilled += sum(row.status == "EXPIRED" for row in engine.research_trades.values())
            rejected += sum(len(snapshot.strategy_traces) - len(snapshot.setup_ids)
                            for snapshot in engine.snapshots.values())
        closed = sorted([row for row in all_rows if row["status"] in {"WON", "LOST"}],
                        key=lambda row: row["created_at"])
        by_partition = _slice(closed, "partition")
        folds = []
        fold_count = max(1, min(int(walk_forward_folds), max(1, len(closed) - 1)))
        block = max(1, len(closed) // (fold_count + 1))
        for fold in range(fold_count):
            train_end = min(len(closed), (fold + 1) * block)
            test_end = len(closed) if fold == fold_count - 1 else min(len(closed), train_end + block)
            train_rows, test_rows = closed[:train_end], closed[train_end:test_end]
            folds.append({"fold": fold + 1, "role": "expanding_train_next_window_oos",
                          "train_start": train_rows[0]["created_at"] if train_rows else None,
                          "train_end": train_rows[-1]["created_at"] if train_rows else None,
                          "test_start": test_rows[0]["created_at"] if test_rows else None,
                          "test_end": test_rows[-1]["created_at"] if test_rows else None,
                          "train_metrics": _metrics(train_rows), "metrics": _metrics(test_rows)})
        sensitivity = {}
        for multiplier in frozen["cost_multipliers"]:
            adjusted = _cost_adjusted_rows(closed, float(multiplier))
            sensitivity[str(multiplier)] = _metrics(adjusted)
        funding_coverage = {
            symbol: {"state": series.state, "dataset_id": series.dataset_id,
                     "records": len(series.events), "requested_start": series.requested_start.isoformat(),
                     "requested_end": series.requested_end.isoformat(),
                     "first": series.events[0].funding_time.isoformat() if series.events else None,
                     "last": series.events[-1].funding_time.isoformat() if series.events else None,
                     "missing_ranges": [[left.isoformat(), right.isoformat()]
                                        for left, right in series.missing_ranges],
                     "warnings": list(series.warnings)}
            for symbol, series in resolved_funding.items()
        }
        oos_rows = [row for row in closed if row["partition"] == "untouched_oos"]
        development = by_partition.get("development", {})
        oos = by_partition.get("untouched_oos", {})
        degradation = ((float(oos.get("expectancy_r") or 0) -
                        float(development.get("expectancy_r") or 0))
                       if development and oos else None)
        result = {
            "experiment_id": experiment_id, "research_id": "PRICE_ACTION_NATIVE_V1_RESEARCH",
            "dataset_version": dataset_version, "code_version": self.code_version,
            "configuration": frozen, "partition_boundaries": partition_boundaries,
            "assumptions": {"source_data": dataset_version, "cost_model": {
                "commission_bps": config.commission_bps, "spread_bps": config.spread_bps,
                "slippage_bps": config.slippage_bps,
                "by_symbol": execution_costs_by_symbol or {},
                "spread_model": "configured full spread; adverse half-spread is applied on each fill",
                "funding": funding_coverage},
                "symbols": sorted({key[0] for key in datasets}),
                "timeframes": sorted({key[1] for key in datasets}),
                "date_partitions": partition_boundaries,
                "fill_model": "conservative_ohlc_adverse_first", "ambiguity": "stop_first",
                "risk_per_trade_pct": .5, "target_r": config.rr_ratio,
                "exit_assumptions": "fixed_2.5R_or_rejection_stop",
                "reporting_metrics_version": "normalized-r-v2",
                "funding_data_complete": all(row.state in {DISABLED, "HISTORICAL_FUNDING_AVAILABLE"}
                                             for row in resolved_funding.values())},
            "metrics": _metrics(closed, rejected=rejected, unfilled=unfilled),
            "by_partition": by_partition, "walk_forward": folds,
            "cost_sensitivity": sensitivity,
            "segments": {key: _slice(closed, key) for key in
                         ("strategy_id", "symbol", "timeframe", "year", "regime", "trigger",
                          "zone_touch_count", "entry_model", "stop_model")},
            "oos_segments": {key: _slice(oos_rows, key) for key in
                             ("strategy_id", "symbol", "timeframe", "year", "regime", "trigger")},
            "funding_coverage": funding_coverage,
            "funding_datasets": {
                symbol: {"symbol": symbol, "state": series.state,
                         "requested_start": series.requested_start.isoformat(),
                         "requested_end": series.requested_end.isoformat(),
                         "events": [asdict(event) for event in series.events]}
                for symbol, series in resolved_funding.items()},
            "oos_degradation_expectancy_r": degradation,
            "equity_drawdown_series": _equity_and_drawdown(closed),
            "trades": closed, "unfilled_trades": [row for row in all_rows if row["status"] == "EXPIRED"],
            "verdict": "RESEARCH_ONLY_NO_PROFITABILITY_CLAIM",
            "real_execution_allowed": False,
        }
        result["quality_gates"] = evaluate_research_quality(result)
        result = json.loads(json.dumps(result, sort_keys=True, default=str))
        return self.store.save(result) if save else result

    def sweep(self, datasets: dict[tuple[str, str], list[Bar]], configs: Iterable[PriceActionConfig]) -> dict:
        """Evaluate one shared grid across every asset; never tune per asset."""
        rows = [self.run(datasets, config, save=False) for config in configs]
        ranked = sorted(rows, key=lambda row: (
            row["by_partition"].get("development", {}).get("expectancy_r", 0),
            row["by_partition"].get("development", {}).get("trade_count", 0)), reverse=True)
        return {"selection_scope": "one shared configuration across all symbols/timeframes",
                "hidden_per_asset_optimization": False,
                "candidates": [{"experiment_id": row["experiment_id"],
                                "configuration": row["configuration"],
                                "development": row["by_partition"].get("development", {}),
                                "validation": row["by_partition"].get("validation", {}),
                                "untouched_oos": row["by_partition"].get("untouched_oos", {})}
                               for row in ranked],
                "selected_by_development_only": ranked[0]["experiment_id"] if ranked else None}

    def rerun(self, experiment_id: str, datasets: dict[tuple[str, str], list[Bar]],
              funding_series: dict[str, HistoricalFundingSeries] | None = None) -> dict:
        prior = self.store.get(experiment_id)
        config_keys = set(PriceActionConfig.__dataclass_fields__)
        config = PriceActionConfig(**{key: value for key, value in prior["configuration"].items() if key in config_keys})
        if funding_series is None:
            funding_series = {}
            for symbol, payload in prior.get("funding_datasets", {}).items():
                funding_series[symbol] = HistoricalFundingSeries.build(
                    symbol, payload.get("events", ()),
                    requested_start=payload["requested_start"],
                    requested_end=payload["requested_end"],
                    intentionally_disabled=payload.get("state") == DISABLED)
        rerun = self.run(datasets, config,
                         partitions=tuple(prior["configuration"]["partitions"]),
                         walk_forward_folds=prior["configuration"]["walk_forward_folds"],
                         cost_multipliers=prior["configuration"]["cost_multipliers"], save=False,
                         funding_series=funding_series,
                         funding_intentionally_disabled=prior["configuration"].get(
                             "funding_intentionally_disabled", False),
                         parameter_selection_method=prior["configuration"].get(
                             "parameter_selection_method",
                             "one shared configuration selected without final-test access"),
                         execution_costs_by_symbol=prior["configuration"].get(
                             "execution_costs_by_symbol") or None)
        return {"matches": rerun == prior, "expected_experiment_id": experiment_id,
                "actual_experiment_id": rerun["experiment_id"], "result": rerun}


def evaluate_research_quality(report: dict, *, minimum_oos_trades: int = 30,
                              maximum_drawdown_r: float = 20.0,
                              maximum_expectancy_degradation_r: float = .35) -> dict:
    """Fail-closed evidence filters. They never produce a profitability claim."""
    oos = report.get("by_partition", {}).get("untouched_oos", {})
    oos_symbols = report.get("oos_segments", {}).get("symbol", {})
    positive_symbols = sum(float(row.get("expectancy_r") or 0) > 0 and
                           int(row.get("trade_count") or 0) > 0 for row in oos_symbols.values())
    cost_rows = report.get("cost_sensitivity", {})
    high_cost = cost_rows.get("2.0") or cost_rows.get("2") or {}
    years = report.get("oos_segments", {}).get("year", {})
    year_counts = [int(row.get("trade_count") or 0) for row in years.values()]
    concentration = max(year_counts) / sum(year_counts) if sum(year_counts) else None
    degradation = report.get("oos_degradation_expectancy_r")
    parameter_sensitivity = report.get("parameter_sensitivity") or {}
    stable_fraction = parameter_sensitivity.get("positive_oos_fraction")

    def gate(name: str, passed: bool | None, evidence: object, requirement: str) -> dict:
        return {"name": name, "status": "PASS" if passed is True else
                "FAIL" if passed is False else "INSUFFICIENT", "evidence": evidence,
                "requirement": requirement}

    gates = [
        gate("meaningful_oos_trade_count",
             int(oos.get("trade_count") or 0) >= minimum_oos_trades,
             oos.get("trade_count", 0), f">= {minimum_oos_trades} untouched-OOS trades"),
        gate("positive_net_oos_expectancy",
             float(oos.get("expectancy_r") or 0) > 0 if oos.get("trade_count") else None,
             oos.get("expectancy_r"), "positive untouched-OOS net expectancy in R"),
        gate("acceptable_oos_drawdown",
             float(oos.get("maximum_drawdown_r") or 0) <= maximum_drawdown_r
             if oos.get("trade_count") else None,
             oos.get("maximum_drawdown_r"), f"<= {maximum_drawdown_r}R"),
        gate("multi_asset_support", positive_symbols >= 2 if oos_symbols else None,
             {"positive_symbols": positive_symbols, "symbols": len(oos_symbols)},
             "positive net OOS expectancy on at least two assets"),
        gate("increased_cost_resistance",
             float(high_cost.get("expectancy_r") or 0) > 0 if high_cost.get("trade_count") else None,
             high_cost.get("expectancy_r"), "positive expectancy at 2x configured costs"),
        gate("no_single_period_dependence",
             concentration <= .70 if concentration is not None and len(years) > 1 else None,
             {"largest_period_trade_share": concentration, "periods": len(years)},
             "no period supplies more than 70% of OOS trades"),
        gate("limited_oos_degradation",
             degradation >= -maximum_expectancy_degradation_r if degradation is not None else None,
             degradation, f"OOS expectancy degradation no worse than {maximum_expectancy_degradation_r}R"),
        gate("complete_cost_data", bool(report.get("assumptions", {}).get("funding_data_complete")),
             report.get("funding_coverage"), "funding available for every symbol or intentionally disabled"),
        gate("moderate_parameter_stability",
             stable_fraction >= .5 if stable_fraction is not None else None,
             parameter_sensitivity or None,
             "at least half of predeclared isolated variants retain positive OOS expectancy"),
    ]
    passed = all(row["status"] == "PASS" for row in gates)
    return {"all_passed": passed, "classification":
            "EVIDENCE_PASSES_CONFIGURED_FILTERS" if passed else
            "EVIDENCE_INSUFFICIENT_OR_FAILS_FILTERS",
            "not_a_future_performance_guarantee": True, "gates": gates}


def controlled_pa_smc_report(pa: dict, smc: dict) -> dict:
    required = ("source_data", "symbols", "timeframes", "date_partitions", "cost_model",
                "fill_model", "ambiguity", "risk_per_trade_pct", "exit_assumptions",
                "reporting_metrics_version", "funding_data_complete")
    pa_assumptions, smc_assumptions = pa.get("assumptions", {}), smc.get("assumptions", {})
    mismatches = {key: {"pa": pa_assumptions.get(key), "smc": smc_assumptions.get(key)}
                  for key in required if pa_assumptions.get(key) != smc_assumptions.get(key)}
    if mismatches:
        raise ValueError(f"PA/SMC comparison refused because assumptions differ: {mismatches}")
    missing = [key for key in required if pa_assumptions.get(key) is None]
    smc_normalization = smc.get("normalization", {})
    warnings = list(smc_normalization.get("warnings") or [])
    if missing:
        warnings.append("Comparison assumptions are unavailable: " + ", ".join(missing))
    if not pa_assumptions.get("funding_data_complete"):
        warnings.append("Historical funding coverage is incomplete; total-cost comparison is not fair-labelled.")
    fair = not missing and not warnings and smc_normalization.get("fair_comparison_allowed", True)
    keys = ("trade_count", "win_rate_pct", "expectancy_r", "profit_factor",
            "maximum_drawdown_r", "longest_losing_streak", "gross_r", "net_r", "costs_r",
            "gross_edge_consumed_pct", "trade_frequency_per_day")
    return {"controlled": fair, "fair_comparison_allowed": fair,
            "comparison_status": "COMPLETE" if fair else "INCOMPLETE",
            "mixed_strategy": False, "assumptions": pa_assumptions,
            "price_action": {key: pa.get("metrics", {}).get(key) for key in keys},
            "smc": {key: smc.get("metrics", {}).get(key) for key in keys},
            "qualitative": {
                "stability": {"pa": pa.get("stability"), "smc": smc.get("stability")},
                "cost_sensitivity": {"pa": pa.get("cost_sensitivity"), "smc": smc.get("cost_sensitivity")},
                "parameter_sensitivity": {"pa": pa.get("parameter_sensitivity"), "smc": smc.get("parameter_sensitivity")},
                "oos_degradation": {"pa": pa.get("oos_degradation_expectancy_r"),
                                    "smc": smc.get("oos_degradation_expectancy_r")},
                "explainability": {"pa": "native OHLC location/event/confirmation trace",
                                   "smc": "native SMC context/sweep/structure/POI trace"},
                "implementation_complexity": {"pa": pa.get("implementation_complexity", "lower"),
                                              "smc": smc.get("implementation_complexity", "higher")}},
            "warnings": warnings,
            "verdict": "descriptive research comparison only; no profitability claim"}
