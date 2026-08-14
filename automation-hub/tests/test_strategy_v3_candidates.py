from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from scripts.strategy_v3_candidates import (
    SealedDatasetAccessError,
    append_ledger,
    assert_window,
    fingerprint,
    verify_frozen_manifest,
)
from strategies.research_v3 import TrendPullbackV3Research


UTC = timezone.utc


def bar(index: int) -> Bar:
    price = 100.0 + index
    return Bar(datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=15 * index),
               price, price + 1, price - 1, price + .25, 1_000.0)


def test_higher_timeframe_context_uses_only_closed_groups():
    strategy = TrendPullbackV3Research("BTCUSDT")
    for index in range(3):
        strategy.on_bar(bar(index))
    assert strategy._hour_bars == []
    strategy.on_bar(bar(3))
    assert len(strategy._hour_bars) == 1
    assert strategy._hour_bars[0].close == bar(3).close


def test_v3_never_allows_untouched_months():
    with pytest.raises(SealedDatasetAccessError):
        assert_window("test", (10, 11, 12))
    with pytest.raises(SealedDatasetAccessError):
        assert_window("validation", (7, 8, 9))


def test_fingerprint_is_stable_and_configuration_specific():
    left = fingerprint("trend_pullback_v3", {"allow_short": True})
    right = fingerprint("trend_pullback_v3", {"allow_short": True})
    changed = fingerprint("trend_pullback_v3", {"allow_short": False})
    assert left == right
    assert left["candidate_fingerprint"] != changed["candidate_fingerprint"]


def test_freeze_integrity_rejects_changed_evidence():
    text = '{"frozen":true}'
    frozen = {"source_hash": fingerprint("trend_pullback_v3", {})["source_hash"],
              "train_sha256": __import__("hashlib").sha256(text.encode()).hexdigest(),
              "test_data_opened": False}
    verify_frozen_manifest(frozen, text)
    with pytest.raises(RuntimeError):
        verify_frozen_manifest(frozen, text + "changed")


def test_research_ledger_is_append_only(tmp_path):
    path = tmp_path / "ledger.jsonl"
    row = {"experiment_id": "immutable-id", "result": "REJECTED"}
    append_ledger(path, row)
    append_ledger(path, row)
    with pytest.raises(RuntimeError):
        append_ledger(path, {"experiment_id": "immutable-id", "result": "different"})
