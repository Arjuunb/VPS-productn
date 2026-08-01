"""Additive, authenticated V2 shadow-decision endpoints.

The app's existing authentication middleware protects this router.  Evaluation
accepts supplied research snapshots only and has no dependency on an executor.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from core_engine.api import snapshot_from_payload
from core_engine.persistence import ShadowDecisionStore
from core_engine.shadow import ShadowEvidenceRunner


def create_router(store: ShadowDecisionStore) -> APIRouter:
    router = APIRouter(prefix="/api/v2", tags=["Core Engine V2"])
    runner = ShadowEvidenceRunner()

    @router.post("/decisions/evaluate")
    def evaluate_shadow_decision(body: dict[str, Any] = Body(...)):
        """Validate, evaluate and persist a shadow-only market snapshot."""
        try:
            snapshot = snapshot_from_payload(body)
            evaluation = runner.evaluate(snapshot)
            return store.record(symbol=snapshot.symbol, evaluation=evaluation)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/decisions/latest")
    def latest_shadow_decisions(limit: int = Query(50, ge=1, le=200),
                                symbol: Optional[str] = None):
        return {"mode": "shadow", "execution_enabled": False,
                "decisions": store.latest(limit=limit, symbol=symbol)}

    @router.get("/decisions/{decision_id}")
    def get_shadow_decision(decision_id: str):
        decision = store.get(decision_id)
        if decision is None:
            raise HTTPException(status_code=404, detail="V2 shadow decision not found")
        return decision

    @router.get("/health/engines")
    def engine_health():
        return store.summary()

    @router.get("/metrics/decision")
    def decision_metrics():
        summary = store.summary()
        return {"mode": summary["mode"], "execution_enabled": False,
                "total_decisions": summary["total_decisions"],
                "engine_status_counts": summary["engine_status_counts"]}

    return router
