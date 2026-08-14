#!/usr/bin/env python3
"""Append immutable selection verdict events without rewriting experiment rows."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    development = json.loads(args.development.read_text(encoding="utf-8"))
    selected = {row["experiment_id"] for row in development["selected"]}
    events = []
    for strategy, rows in development["experiments"].items():
        for row in rows:
            failed = sorted(name for name, passed in row["selection_gates"].items() if not passed)
            events.append({
                "event_id": f"selection-{row['experiment_id']}",
                "experiment_id": row["experiment_id"],
                "recorded_at": development["generated_at"],
                "strategy": strategy,
                "status": "FROZEN FOR TEST" if row["experiment_id"] in selected else "REJECTED",
                "failed_gates": failed,
                "test_status": "NOT_OPENED",
            })
    serialized = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                         for row in events)
    if args.output.exists() and args.output.read_text(encoding="utf-8") != serialized:
        raise RuntimeError("immutable verdict ledger conflict")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists():
        args.output.write_text(serialized, encoding="utf-8")
    print(json.dumps({"events": len(events), "frozen": len(selected), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
