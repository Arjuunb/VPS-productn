import pytest

pytest.importorskip("fastapi")

from routers.native_smc import pine_reference


def test_pine_reference_is_read_only_research_material():
    payload = pine_reference()

    assert payload["reference_id"] == "SMC_PRO_V2_REFERENCE"
    assert payload["status"] == "PARITY_AUDIT"
    assert payload["execution_allowed"] is False
    assert len(payload["sha256"]) == 64
    assert "strategy(" in payload["content"]
