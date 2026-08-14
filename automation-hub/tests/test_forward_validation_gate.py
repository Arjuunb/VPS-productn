from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.forward_validation import router
from services.forward_validation import (
    CANDIDATES,
    FORWARD_PAPER_ELIGIBLE,
    REJECTED,
    RESEARCH_ONLY,
    require_eligible,
    summary,
)


def test_frozen_registry_has_no_eligible_candidate():
    assert len(CANDIDATES) == 9
    assert {row.evidence_status for row in CANDIDATES} == {REJECTED, RESEARCH_ONLY}
    assert not [row for row in CANDIDATES if row.evidence_status == FORWARD_PAPER_ELIGIBLE]
    assert all(len(row.code_hash) == 64 for row in CANDIDATES)
    assert all(len(row.configuration_hash) == 64 for row in CANDIDATES)
    assert all(len(row.combined_hash) == 64 for row in CANDIDATES)


def test_summary_does_not_mix_ordinary_paper_records_with_evidence():
    payload = summary()
    assert payload["stage_status"] == "BLOCKED_NO_ELIGIBLE_CANDIDATES"
    assert payload["active_experiments"] == []
    assert payload["forward_evidence"]["trades"] == 0
    assert "excluded" in payload["forward_evidence"]["note"].lower()


def test_rejected_and_research_candidates_cannot_start():
    for row in CANDIDATES:
        try:
            require_eligible(row.candidate_id)
        except RuntimeError as exc:
            assert row.evidence_status in str(exc)
        else:  # pragma: no cover - a regression would be safety-critical
            raise AssertionError(f"Ineligible candidate started: {row.candidate_id}")


def test_api_reports_status_and_refuses_experiment_creation():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    status = client.get("/forward-validation")
    assert status.status_code == 200
    assert status.json()["candidate_counts"] == {
        REJECTED: 6,
        RESEARCH_ONLY: 3,
        FORWARD_PAPER_ELIGIBLE: 0,
    }

    response = client.post(
        "/forward-validation/experiments",
        json={"candidate_id": CANDIDATES[0].candidate_id},
    )
    assert response.status_code == 409
    assert CANDIDATES[0].evidence_status in response.json()["detail"]


def test_unknown_candidate_is_not_implicitly_created():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post(
        "/forward-validation/experiments",
        json={"candidate_id": "not-a-frozen-candidate"},
    )
    assert response.status_code == 404
