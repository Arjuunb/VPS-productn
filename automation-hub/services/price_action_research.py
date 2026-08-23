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


def _stable(payload: object, prefix: str) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:20]}"


def _dataset_version(datasets: dict[tuple[str, str], list[Bar]]) -> str:
    material = []
    for (symbol, timeframe), rows in sorted(datasets.items()):
        material.append({"symbol": symbol, "timeframe": timeframe, "count": len(rows),
                         "first": rows[0].timestamp if rows else None,
                         "last": rows[-1].timestamp if rows else None,
                         "ohlc_hash": hashlib.sha256("|".join(
                             f"{row.timestamp.isoformat()}:{row.open}:{row.high}:{row.low}:{row.close}"
                             for row in rows).encode()).hexdigest()})
    return _stable(material, "dataset")


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
    return {
        "trade_count": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": len(wins) / len(closed) * 100 if closed else 0,
        "average_win_r": sum(wins) / len(wins) if wins else 0,
        "average_loss_r": sum(losses) / len(losses) if losses else 0,
        "expectancy_r": sum(rs) / len(rs) if rs else 0,
        "median_r": sorted(rs)[len(rs) // 2] if rs else 0,
        "profit_factor": gross_profit / gross_loss if gross_loss else (None if gross_profit else 0),
        "maximum_drawdown_r": _max_drawdown(rs), "longest_losing_streak": _longest_losing_streak(rs),
        "gross_r": gross_edge, "net_r": sum(rs), "fees_r": total_costs,
        "slippage_r": sum(float(row.get("slippage_r") or 0) for row in closed),
        "funding_r": sum(float(row.get("funding_r") or 0) for row in closed),
        "costs_r": total_costs,
        "gross_edge_consumed_pct": total_costs / gross_edge * 100 if gross_edge > 0 else None,
        "unfilled": unfilled, "cancelled": cancelled, "rejected": rejected,
    }


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


class PriceActionExperimentRunner:
    def __init__(self, store: PriceActionExperimentStore, *, source_path: str | Path | None = None):
        self.store = store
        source = Path(source_path or Path(__file__).with_name("native_price_action.py"))
        self.code_version = "code-" + hashlib.sha256(source.read_bytes()).hexdigest()[:20]

    @staticmethod
    def _trade_rows(engine: NativePriceActionEngine, symbol: str, timeframe: str,
                    partition_for) -> list[dict]:
        rows = []
        for trade in engine.research_trades.values():
            setup = engine.setups.get(trade.setup_id)
            event = engine.events.get(setup.trigger_event_id) if setup and setup.trigger_event_id else None
            zone = engine.zones.get(setup.zone_id) if setup and setup.zone_id else None
            snapshot = engine.snapshots.get(trade.created_at)
            fill = float(trade.fill_price or trade.requested_entry)
            risk = max(abs(fill - trade.stop), 1e-12)
            slippage = abs(fill - trade.requested_entry) / risk
            rows.append({**asdict(trade), "symbol": symbol, "timeframe": timeframe,
                         "year": trade.created_at.year, "partition": partition_for(trade.created_at),
                         "regime": snapshot.structure_bias if snapshot else "unknown",
                         "trigger": event.pattern or event.event_type if event else "unknown",
                         "zone_touch_count": zone.touch_count if zone else 0,
                         "entry_model": trade.entry_model, "stop_model": trade.stop_model,
                         "slippage_r": slippage, "funding_r": 0.0})
        return rows

    def run(self, datasets: dict[tuple[str, str], list[Bar]], config: PriceActionConfig,
            *, partitions: tuple[float, float] = (.6, .8), walk_forward_folds: int = 4,
            cost_multipliers: Iterable[float] = (1.0, 1.5, 2.0), save: bool = True) -> dict:
        if not datasets:
            raise ValueError("at least one verified dataset is required")
        dataset_version = _dataset_version(datasets)
        frozen = {**asdict(config), "symbols_timeframes": sorted([list(key) for key in datasets]),
                  "partitions": partitions, "walk_forward_folds": walk_forward_folds,
                  "cost_multipliers": list(cost_multipliers)}
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
            local_config = replace(config, symbol=symbol, timeframe=timeframe)
            engine = NativePriceActionEngine(local_config)
            engine.ingest_closed_bars(bars)
            all_rows.extend(self._trade_rows(engine, symbol, timeframe, partition_for))
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
            adjusted = [{**row, "net_r": float(row["gross_r"] or 0) -
                          float(row["costs_r"] or 0) * float(multiplier)} for row in closed]
            sensitivity[str(multiplier)] = _metrics(adjusted)
        result = {
            "experiment_id": experiment_id, "research_id": "PRICE_ACTION_NATIVE_V1_RESEARCH",
            "dataset_version": dataset_version, "code_version": self.code_version,
            "configuration": frozen, "partition_boundaries": partition_boundaries,
            "assumptions": {"source_data": dataset_version, "cost_model": {
                "commission_bps": config.commission_bps, "slippage_bps": config.slippage_bps},
                "symbols": sorted({key[0] for key in datasets}),
                "timeframes": sorted({key[1] for key in datasets}),
                "date_partitions": partition_boundaries,
                "fill_model": "conservative_ohlc_adverse_first", "ambiguity": "stop_first",
                "risk_per_trade_pct": .5, "target_r": config.rr_ratio},
            "metrics": _metrics(closed, rejected=rejected, unfilled=unfilled),
            "by_partition": by_partition, "walk_forward": folds,
            "cost_sensitivity": sensitivity,
            "segments": {key: _slice(closed, key) for key in
                         ("strategy_id", "symbol", "timeframe", "year", "regime", "trigger",
                          "zone_touch_count", "entry_model", "stop_model")},
            "trades": closed, "unfilled_trades": [row for row in all_rows if row["status"] == "EXPIRED"],
            "verdict": "RESEARCH_ONLY_NO_PROFITABILITY_CLAIM",
            "real_execution_allowed": False,
        }
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

    def rerun(self, experiment_id: str, datasets: dict[tuple[str, str], list[Bar]]) -> dict:
        prior = self.store.get(experiment_id)
        config_keys = set(PriceActionConfig.__dataclass_fields__)
        config = PriceActionConfig(**{key: value for key, value in prior["configuration"].items() if key in config_keys})
        rerun = self.run(datasets, config,
                         partitions=tuple(prior["configuration"]["partitions"]),
                         walk_forward_folds=prior["configuration"]["walk_forward_folds"],
                         cost_multipliers=prior["configuration"]["cost_multipliers"], save=False)
        return {"matches": rerun == prior, "expected_experiment_id": experiment_id,
                "actual_experiment_id": rerun["experiment_id"], "result": rerun}


def controlled_pa_smc_report(pa: dict, smc: dict) -> dict:
    required = ("source_data", "symbols", "timeframes", "date_partitions", "cost_model",
                "fill_model", "ambiguity", "risk_per_trade_pct")
    pa_assumptions, smc_assumptions = pa.get("assumptions", {}), smc.get("assumptions", {})
    mismatches = {key: {"pa": pa_assumptions.get(key), "smc": smc_assumptions.get(key)}
                  for key in required if pa_assumptions.get(key) != smc_assumptions.get(key)}
    if mismatches:
        raise ValueError(f"PA/SMC comparison refused because assumptions differ: {mismatches}")
    keys = ("expectancy_r", "profit_factor", "maximum_drawdown_r", "trade_count", "net_r",
            "gross_edge_consumed_pct")
    return {"controlled": True, "mixed_strategy": False, "assumptions": pa_assumptions,
            "price_action": {key: pa.get("metrics", {}).get(key) for key in keys},
            "smc": {key: smc.get("metrics", {}).get(key) for key in keys},
            "qualitative": {
                "stability": {"pa": pa.get("stability"), "smc": smc.get("stability")},
                "cost_sensitivity": {"pa": pa.get("cost_sensitivity"), "smc": smc.get("cost_sensitivity")},
                "parameter_sensitivity": {"pa": pa.get("parameter_sensitivity"), "smc": smc.get("parameter_sensitivity")},
                "explainability": {"pa": "native OHLC location/event/confirmation trace",
                                   "smc": "native SMC context/sweep/structure/POI trace"},
                "implementation_complexity": {"pa": pa.get("implementation_complexity", "lower"),
                                              "smc": smc.get("implementation_complexity", "higher")}},
            "verdict": "descriptive research comparison only; no profitability claim"}
