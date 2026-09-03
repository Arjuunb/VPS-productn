"""Read-only API for partitioned PA/SMC shadow research."""
from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter(prefix="/research/observatory", tags=["research-observatory"])


def _runtime():
    import webhook_api
    return webhook_api.research_observer


@router.get("/status")
def status():
    return _runtime().status()


@router.get("/registry")
def registry():
    return _runtime().variants.registry


@router.get("/measurements")
def measurements(limit: int = Query(500, ge=1, le=5000)):
    return {
        "execution_class": "SHADOW", "real_execution_allowed": False,
        "rows": _runtime().store.measurements(limit=limit),
    }


@router.get("/comparison")
def comparison():
    from services.research_analytics import ResearchComparison
    return ResearchComparison(_runtime().store).report()
