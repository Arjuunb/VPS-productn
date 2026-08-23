"""SQLite persistence for non-executing V2 shadow decisions.

This store is intentionally separate from the legacy decision table. A V2
record is evidence generated for comparison; it is not an accepted order and
must never be mistaken for one by existing dashboards or execution code.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .contracts import Evidence, ShadowEvaluation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence_dict(item: Evidence) -> dict[str, Any]:
    return {
        "engine": item.engine,
        "version": item.version,
        "status": item.status.value,
        "as_of": item.as_of.isoformat(),
        "facts": dict(item.facts),
        "reasons": list(item.reasons),
        "blockers": list(item.blockers),
        "confidence": item.confidence,
        "source_ids": list(item.source_ids),
    }


def _optional_decision_parts(evaluation: ShadowEvaluation) -> dict[str, Any]:
    proposal = evaluation.proposal
    confidence = evaluation.confidence_assessment
    risk = evaluation.risk_assessment
    return {
        "proposal": ({"strategy_id": proposal.strategy_id, "strategy_version": proposal.strategy_version,
                      "symbol": proposal.symbol, "timeframe": proposal.timeframe,
                      "direction": proposal.direction.value, "entry": proposal.entry,
                      "stop_loss": proposal.stop_loss, "take_profit": proposal.take_profit,
                      "invalidation": proposal.invalidation, "planned_rr": proposal.planned_rr,
                      "rationale": proposal.rationale, "evidence_ids": list(proposal.evidence_ids)}
                     if proposal is not None else None),
        "confidence": ({"score": confidence.score, "level": confidence.level,
                        "policy_version": confidence.policy_version,
                        "contributions": [vars(item) for item in confidence.contributions]}
                       if confidence is not None else None),
        "risk": ({"verdict": risk.verdict.value, "policy_name": risk.policy_name,
                  "reason": risk.reason, "primary_rule": risk.primary_rule,
                  "quantity": risk.quantity, "risk_amount": risk.risk_amount,
                  "risk_pct": risk.risk_pct,
                  "checks": [vars(item) for item in risk.checks]}
                 if risk is not None else None),
        "rationale": list(evaluation.rationale),
    }


class ShadowDecisionStore:
    """Durable, thread-safe storage for V2 shadow observations only."""

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS core_v2_shadow_decisions (
                   id TEXT PRIMARY KEY,
                   created_at TEXT NOT NULL,
                   snapshot_id TEXT NOT NULL,
                   symbol TEXT NOT NULL,
                   action TEXT NOT NULL,
                   execution_eligible INTEGER NOT NULL CHECK (execution_eligible = 0),
                   payload_json TEXT NOT NULL
               )"""
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_core_v2_shadow_ts "
            "ON core_v2_shadow_decisions(created_at DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_core_v2_shadow_symbol "
            "ON core_v2_shadow_decisions(symbol, created_at DESC)"
        )
        self._conn.commit()

    def record(self, *, symbol: str, evaluation: ShadowEvaluation) -> dict[str, Any]:
        """Persist an observation. The database constraint reinforces no-execution."""
        if evaluation.execution_eligible:
            raise ValueError("V2 shadow evaluations cannot be persisted as executable")
        record = {
            "id": f"v2_{uuid.uuid4().hex}",
            "created_at": _now(),
            "snapshot_id": evaluation.snapshot_id,
            "symbol": symbol.upper(),
            "action": evaluation.action.value,
            "execution_eligible": False,
            "evidence": [_evidence_dict(item) for item in evaluation.evidence],
            **_optional_decision_parts(evaluation),
        }
        with self._lock:
            self._conn.execute(
                """INSERT INTO core_v2_shadow_decisions
                   (id, created_at, snapshot_id, symbol, action, execution_eligible, payload_json)
                   VALUES (?, ?, ?, ?, ?, 0, ?)""",
                (record["id"], record["created_at"], record["snapshot_id"], record["symbol"],
                 record["action"], json.dumps(record, separators=(",", ":"), sort_keys=True)),
            )
            self._conn.commit()
        return record

    def get(self, decision_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM core_v2_shadow_decisions WHERE id = ?", (decision_id,)
            ).fetchone()
        return json.loads(row["payload_json"]) if row else None

    def latest(self, *, limit: int = 50, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        params: list[Any] = []
        sql = "SELECT payload_json FROM core_v2_shadow_decisions"
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def clear(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM core_v2_shadow_decisions")
            self._conn.commit()

    def summary(self) -> dict[str, Any]:
        """Dashboard-friendly counts and last-observed status per engine."""
        rows = self.latest(limit=200)
        engine_counts: dict[str, dict[str, int]] = {}
        for row in rows:
            for item in row.get("evidence", []):
                bucket = engine_counts.setdefault(item["engine"], {})
                status = item["status"]
                bucket[status] = bucket.get(status, 0) + 1
        with self._lock:
            total = int(self._conn.execute(
                "SELECT COUNT(*) AS count FROM core_v2_shadow_decisions"
            ).fetchone()["count"])
        return {
            "mode": "shadow",
            "execution_enabled": False,
            "total_decisions": total,
            "sampled_decisions": len(rows),
            "engine_status_counts": engine_counts,
        }
