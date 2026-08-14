from datetime import datetime, timedelta, timezone

from bot.types import Bar
from bots.registry import STRATEGIES
from strategies.research_v2 import RESEARCH_STRATEGIES, efficiency_ratio


def _bars(count=80):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for index in range(count):
        price += 0.25 if index < 50 else -0.18
        rows.append(Bar(start + timedelta(minutes=5 * index), price - .1,
                        price + .4, price - .4, price, 1000 + index * 10))
    return rows


def test_research_v2_is_not_available_to_production_registry():
    assert not set(RESEARCH_STRATEGIES).intersection(STRATEGIES)
    assert all("research" in cls.research_version for cls in RESEARCH_STRATEGIES.values())


def test_efficiency_ratio_is_bounded_and_causal():
    bars = _bars()
    value = efficiency_ratio(bars, 30)
    assert 0 <= value <= 1
    mutated = list(bars)
    mutated[-1] = Bar(mutated[-1].timestamp, 1, 10000, 1, 10000, 1)
    assert efficiency_ratio(bars[:-1], 30) == efficiency_ratio(mutated[:-1], 30)


def test_every_research_strategy_runs_without_mutating_v1_classes():
    bars = _bars(400)
    for strategy in RESEARCH_STRATEGIES.values():
        instance = strategy("BTCUSDT")
        for bar in bars:
            instance.on_bar(bar)
        assert len(instance.bars) <= instance.max_history
