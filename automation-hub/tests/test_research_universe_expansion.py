from datetime import datetime, timezone

import pytest

from scripts.research_universe_expansion import (
    ALL_HYPOTHESES,
    FUTURES_EXECUTION_HURDLE_BPS,
    INTERACTIONS,
    SINGLE_FEATURES,
    SealedUniverseAccessError,
    archive_specs,
    assert_train_only,
    render_report,
)


def test_universe_expansion_is_train_only_before_any_archive_list_is_built():
    with pytest.raises(SealedUniverseAccessError):
        assert_train_only((1, 2, 3, 4, 5, 6), datetime(2025, 10, 1, tzinfo=timezone.utc))


def test_archive_manifest_never_names_validation_or_untouched_test_months():
    paths = [path for path, _ in archive_specs()]
    assert paths
    assert not any("2025-07" in path or "2025-08" in path or "2025-09" in path or "2025-10" in path for path in paths)
    assert any(path.startswith("metrics/BTCUSDT/") for path in paths)
    assert any(path.startswith("fundingRate/ETHUSDT/") for path in paths)


def test_interaction_budget_is_pre_registered_and_limited():
    assert len(INTERACTIONS) <= 10
    assert len(ALL_HYPOTHESES) == len(SINGLE_FEATURES) + len(INTERACTIONS)


def test_futures_hurdle_is_not_a_zero_cost_or_leverage_claim():
    assert FUTURES_EXECUTION_HURDLE_BPS["research_conservative_round_trip_bps"] > 0
    assert "funding" in FUTURES_EXECUTION_HURDLE_BPS


def test_report_keeps_research_and_sealed_boundaries_explicit():
    report = render_report({
        "futures_inventory": {}, "futures_derivatives_structure": {}, "higher_timeframe_structure": {},
        "universe_classifications": {"A": "LOW PRIORITY"},
    })
    assert "forward-paper candidate" in report
    assert "Oct–Dec 2025 untouched test" in report
