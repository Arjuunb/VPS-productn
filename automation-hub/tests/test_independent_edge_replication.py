from datetime import datetime, timedelta, timezone

from pathlib import Path

import pytest

from scripts.independent_edge_replication import (
    HYPOTHESES, REPLICATION_END, REPLICATION_MONTHS, REPLICATION_START,
    ReplicationBoundaryError, append_ledger, archive_specs, assert_replication_only,
    fingerprints, last_known,
)


def test_replication_window_is_fixed_and_precedes_discovery():
    assert_replication_only()
    with pytest.raises(ReplicationBoundaryError):
        assert_replication_only(REPLICATION_START, datetime(2025, 7, 1, tzinfo=timezone.utc), REPLICATION_MONTHS)


def test_exactly_four_frozen_hypotheses_and_no_interaction_search():
    assert sorted(HYPOTHESES) == ["REP-H1-ETH-FUNDING-LOW", "REP-H2-SOL-FUNDING-LOW", "REP-H3-ETH-REL-VOLUME-HIGH", "REP-H4-ETH-1D-PERSISTENCE"]


def test_fingerprints_are_deterministic_and_complete():
    first, second = fingerprints(), fingerprints()
    assert first == second
    assert all("definition_hash" in value and "configuration_hash" in value for value in first.values())


def test_futures_hypotheses_use_only_known_or_prior_funding():
    for hid in ("REP-H1-ETH-FUNDING-LOW", "REP-H2-SOL-FUNDING-LOW"):
        assert "at or before" in HYPOTHESES[hid]["alignment"]


def test_daily_hypothesis_requires_completed_daily_candle():
    assert "completed daily candle" in HYPOTHESES["REP-H4-ETH-1D-PERSISTENCE"]["alignment"]


def test_archive_manifest_excludes_all_2025_discovery_validation_and_test_files():
    paths = [path for path, _ in archive_specs()]
    assert paths
    assert all("2024-" in path for path in paths)
    assert not any("2025-" in path for path in paths)


def test_funding_alignment_never_looks_ahead():
    event = REPLICATION_START + timedelta(hours=8)
    history = {event - timedelta(hours=2): -0.0001, event + timedelta(hours=2): 0.0002}
    assert last_known(history, event) == -0.0001


def test_ledger_is_append_only(tmp_path: Path):
    ledger = tmp_path / "ledger.jsonl"
    row = {"replication_record_id": "one", "verdict": "REPLICATION FAILED"}
    append_ledger(ledger, [row])
    first = ledger.read_text()
    append_ledger(ledger, [row])
    assert ledger.read_text() == first
