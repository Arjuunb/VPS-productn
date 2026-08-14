"""Read-only evidence status and the forward-validation eligibility gate."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.forward_validation import (
    ForwardValidationEligibilityError,
    candidate,
    require_eligible,
    summary,
)


router = APIRouter(prefix="/forward-validation", tags=["forward-validation"])


class ExperimentRequest(BaseModel):
    candidate_id: str


@router.get("")
def forward_validation_summary():
    return summary()


@router.get("/candidates/{candidate_id}")
def forward_validation_candidate(candidate_id: str):
    row = candidate(candidate_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown frozen validation candidate")
    return row.public()


@router.post("/experiments", status_code=201)
def create_forward_validation_experiment(body: ExperimentRequest):
    """Refuse all current candidates before any experiment state is created."""

    try:
        row = require_eligible(body.candidate_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Unknown frozen validation candidate") from None
    except ForwardValidationEligibilityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None

    # This branch is deliberately unreachable with the frozen 2026-08-13
    # registry.  A later qualifying version needs its own reviewed persistence
    # migration and instrumentation before the endpoint may create evidence.
    raise HTTPException(
        status_code=501,
        detail=f"Eligible candidate {row.candidate_id} requires reviewed experiment storage",
    )
