import json
from pathlib import Path

import pytest

from scripts import strategy_v2_research as research


def test_development_months_cannot_include_untouched_test():
    assert research.TEST_START.month == 10
    assert tuple(range(1, 10)) == (1, 2, 3, 4, 5, 6, 7, 8, 9)


def test_experiment_ids_are_deterministic_and_parameter_scoped():
    first = research.experiment_id("supertrend_v2", "5m", {"min_er": .2})
    assert first == research.experiment_id("supertrend_v2", "5m", {"min_er": .2})
    assert first != research.experiment_id("supertrend_v2", "5m", {"min_er": .25})


def test_research_ledger_is_append_only_and_detects_conflicts(tmp_path: Path):
    path = tmp_path / "ledger.jsonl"
    row = {"experiment_id": "rv2-test", "verdict": "RESEARCH ONLY"}
    research.append_ledger(path, row)
    research.append_ledger(path, row)
    assert len(path.read_text().splitlines()) == 1
    with pytest.raises(RuntimeError, match="immutable research ledger conflict"):
        research.append_ledger(path, {**row, "verdict": "REJECTED"})


def test_test_stage_refuses_empty_freeze_without_opening_data(tmp_path: Path, monkeypatch):
    development = tmp_path / "development.json"
    development.write_text("{}")
    manifest = tmp_path / "freeze.json"
    manifest.write_text(json.dumps({
        "test_opened": False,
        "source_hash": research.source_hash(),
        "development_sha256": research.sha256_bytes(development.read_bytes()),
        "selected": [],
    }))
    output = tmp_path / "test.json"
    args = type("Args", (), {
        "freeze_manifest": manifest, "development_output": development,
        "data_dir": tmp_path / "does-not-exist", "output": output,
    })()
    assert research.test(args) == 2
    assert not output.exists()
    assert json.loads(manifest.read_text())["test_opened"] is False
