#!/usr/bin/env python3
"""V1 component attribution using development data only (Jan-Sep 2025)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "automation-hub"
for item in (str(HUB), str(ROOT)):
    if item not in sys.path:
        sys.path.insert(0, item)

from scripts import strategy_v2_research as rv2  # noqa: E402
from scripts import strategy_validation as v1  # noqa: E402

UTC = timezone.utc


def pool(rows: list[dict]) -> dict:
    trades = sum(row["trades"] for row in rows)
    wins = sum(row["wins"] for row in rows)
    losses = sum(row["losses"] for row in rows)
    net = sum(row["net_r"] for row in rows)
    gross_profit = sum(row["average_win_r"] * row["wins"] for row in rows)
    gross_loss = -sum(row["average_loss_r"] * row["losses"] for row in rows)
    return {
        "trades": trades, "wins": wins, "losses": losses,
        "win_rate_pct": round(100 * wins / trades, 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss else (99.0 if gross_profit else 0.0),
        "net_r": round(net, 3),
        "expectancy_r": round(net / trades, 4) if trades else 0.0,
        "max_drawdown_r": max((row["max_drawdown_r"] for row in rows), default=0.0),
        "drawdown_basis": "worst individual symbol",
    }


def run(args) -> int:
    raw, inventory = {}, {}
    for symbol in rv2.SYMBOLS:
        raw[symbol], inventory[symbol] = rv2.load_months(
            args.data_dir, symbol, tuple(range(1, 10)))
        if raw[symbol][-1].timestamp >= rv2.TEST_START:
            raise RuntimeError("attribution crossed the untouched-test boundary")
        v1.DATASETS[(symbol, "5m")] = raw[symbol]

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": [rv2.TRAIN_START.isoformat(), rv2.TEST_START.isoformat()],
        "test_data_opened": False,
        "data_inventory": inventory,
        "strategies": {},
    }
    for key, strategy_class in v1.STRATEGIES.items():
        per_symbol = {}
        for symbol in rv2.SYMBOLS:
            params = dict(strategy_class(symbol).params)
            bars = raw[symbol]
            raw_result = v1.simulate(key, symbol, bars, params=params,
                                     gate=False, risk=False, costs=False)
            costs = v1.simulate(key, symbol, bars, params=params,
                                gate=False, risk=False, costs=True)
            risk = v1.simulate(key, symbol, bars, params=params,
                               gate=False, risk=True, costs=True)
            decision = v1.simulate(key, symbol, bars, params=params,
                                   gate=True, risk=True, costs=True)
            risk_learning = v1.learning_ab(risk["trades"], symbol)["enabled"]
            final_learning = v1.learning_ab(decision["trades"], symbol)["enabled"]
            rows = {
                "raw_signal": v1._stats(raw_result["trades"]),
                "after_execution_costs": v1._stats(costs["trades"]),
                "after_risk_engine": v1._stats(risk["trades"]),
                "after_learning": risk_learning,
                "after_decision_brain": v1._stats(decision["trades"]),
                "final_realised": final_learning,
            }
            per_symbol[symbol] = rows
        pooled = {
            stage: pool([per_symbol[symbol][stage] for symbol in rv2.SYMBOLS])
            for stage in next(iter(per_symbol.values()))
        }
        payload["strategies"][key] = {"pooled": pooled, "symbols": per_symbol}

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "test_data_opened": False,
                      "strategies": list(payload["strategies"])}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
