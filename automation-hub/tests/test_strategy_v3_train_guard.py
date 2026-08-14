from datetime import datetime, timezone

import pytest

from scripts.strategy_v3_market_study import (
    TRAIN_MONTHS,
    VALIDATION_START,
    SealedDatasetAccessError,
    assert_train_only,
)


def test_v3_train_guard_accepts_only_january_to_june():
    assert_train_only(TRAIN_MONTHS, VALIDATION_START)


@pytest.mark.parametrize("months,end", [
    (tuple(range(1, 8)), VALIDATION_START),
    (TRAIN_MONTHS, datetime(2025, 10, 1, tzinfo=timezone.utc)),
])
def test_v3_train_guard_rejects_validation_and_untouched_test(months, end):
    with pytest.raises(SealedDatasetAccessError):
        assert_train_only(months, end)
