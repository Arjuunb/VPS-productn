from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from services.native_price_action import (
    NativePriceActionEngine,
    PriceActionConfig,
    PriceZone,
    ResearchTrade,
    RESEARCH_ID,
    STRATEGIES,
)


def bar(index: int, open_: float, high: float, low: float, close: float, volume: float = 1_000) -> Bar:
    return Bar(datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=5 * index),
               open_, high, low, close, volume)


def test_swing_confirmation_preserves_occurrence_and_confirmation_without_lookahead():
    engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT", swing_left=2, swing_right=2))
    rows = [
        bar(0, 100, 101, 99, 100), bar(1, 100, 103, 99.5, 102),
        bar(2, 102, 110, 101, 109), bar(3, 109, 109.5, 103, 104),
        bar(4, 104, 107, 102, 106),
    ]
    for row in rows[:4]:
        engine.process_closed_bar(row)
    assert not engine.swings
    engine.process_closed_bar(rows[4])
    high = next(s for s in engine.swings.values() if s.kind == "high")
    assert high.occurred_at == rows[2].timestamp
    assert high.confirmed_at == rows[4].timestamp + timedelta(minutes=5)
    state_at_prior = engine.visual_state(candle_at=rows[3].timestamp)
    assert state_at_prior["swings"] == []


def test_idempotent_duplicate_and_out_of_order_rejected():
    engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT"))
    first = bar(0, 100, 102, 99, 101)
    assert engine.process_closed_bar(first) == engine.process_closed_bar(first)
    with pytest.raises(ValueError, match="chronological"):
        engine.process_closed_bar(bar(-1, 100, 101, 99, 100))


def test_volume_does_not_change_native_price_action_decisions():
    prices = [
        (100, 101, 99, 100), (100, 104, 99, 103), (103, 110, 102, 109),
        (109, 109.5, 104, 105), (105, 107, 103, 106), (106, 109, 104, 108),
        (108, 111, 107, 110), (110, 110.5, 105, 106), (106, 108, 103, 104),
    ]
    engines = []
    for multiplier in (1, 99_999):
        engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT", swing_left=2, swing_right=2))
        engine.ingest_closed_bars(bar(i, *row, volume=(i + 1) * multiplier) for i, row in enumerate(prices))
        engines.append(engine)
    left, right = engines
    assert [(s.kind, s.price, s.label) for s in left.swings.values()] == [
        (s.kind, s.price, s.label) for s in right.swings.values()
    ]
    assert [(e.event_type, e.direction, e.level) for e in left.events.values()] == [
        (e.event_type, e.direction, e.level) for e in right.events.values()
    ]
    assert left.latest_snapshot.strategy_traces == right.latest_snapshot.strategy_traces


def test_four_strategies_are_independent_and_research_boundary_is_explicit():
    engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT"))
    engine.ingest_closed_bars([bar(i, 100 + i, 102 + i, 99 + i, 101 + i) for i in range(10)])
    state = engine.visual_state()
    assert state["research_id"] == RESEARCH_ID
    assert state["execution_allowed"] is False
    assert state["paper_execution_allowed"] is True
    assert state["volume_signal_input"] is False
    assert {trace.strategy_id for trace in engine.latest_snapshot.strategy_traces} == set(STRATEGIES)
    assert len(engine.latest_snapshot.strategy_traces) == len(STRATEGIES) * 2


def test_confirmed_zone_rejection_can_create_signal_only_proposal():
    engine = NativePriceActionEngine(PriceActionConfig(
        "BTCUSDT", swing_left=2, swing_right=2, wick_body_ratio=1.2,
    ))
    seed = [
        bar(0, 103, 105, 102, 104), bar(1, 104, 106, 101, 102),
        bar(2, 102, 104, 95, 97), bar(3, 97, 103, 96, 102),
        bar(4, 102, 105, 99, 104),
    ]
    engine.ingest_closed_bars(seed)
    support = next(z for z in engine.zones.values() if z.role == "support")
    reject = bar(5, support.high, support.high + 2, support.low, support.high + 1.8)
    snapshot = engine.process_closed_bar(reject)
    assert any(engine.events[event_id].event_type == "zone_rejection" for event_id in snapshot.event_ids)
    proposal = next((engine.proposals[p] for p in snapshot.proposal_ids
                     if engine.proposals[p].strategy_id == "PA1_SR_REJECTION"), None)
    assert proposal is not None
    assert proposal.execution_allowed is False
    assert proposal.paper_execution_allowed is True
    assert proposal.status == "SIGNAL_ONLY"
    assert proposal.rr_ratio == 2.5
    assert proposal.stop < proposal.entry < proposal.target
    research_trade = next(row for row in engine.research_trades.values() if row.proposal_id == proposal.id)
    assert research_trade.status == "PENDING"
    engine.process_closed_bar(bar(6, proposal.entry, proposal.entry + 1, proposal.stop - 1, proposal.stop))
    assert research_trade.status == "LOST"
    assert research_trade.outcome == "STOP"
    assert research_trade.costs_r > 0
    assert research_trade.net_r < 0
    metrics = engine.visual_state()["metrics"]
    assert metrics["closed"] == 1
    assert metrics["losses"] == 1
    assert metrics["net_r"] < 0


def test_visual_replay_never_exposes_future_candles_or_objects():
    engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT", swing_left=2, swing_right=2))
    rows = [bar(i, 100 + i, 103 + i, 99 + i, 102 + i) for i in range(12)]
    engine.ingest_closed_bars(rows)
    state = engine.visual_state(candle_at=rows[6].timestamp)
    assert state["candles"][-1]["timestamp"] == rows[6].timestamp
    assert all(row["occurred_at"] <= rows[6].timestamp for row in state["events"])
    assert all(row["confirmed_at"] <= rows[6].timestamp for row in state["swings"])


def test_real_execution_cannot_be_enabled():
    with pytest.raises(ValueError, match="cannot enable live execution"):
        NativePriceActionEngine(PriceActionConfig("BTCUSDT", execution_allowed=True))


def seeded_resistance_engine() -> tuple[NativePriceActionEngine, PriceZone]:
    engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT"))
    first = bar(0, 99.5, 100.5, 99, 100)
    engine.process_closed_bar(first)
    zone = PriceZone("zone-test", "resistance", "resistance", 100, 101,
                     first.timestamp, first.timestamp, ["swing-test"])
    engine.zones[zone.id] = zone
    engine.process_closed_bar(bar(1, 100, 103, 99.5, 102))
    return engine, zone


def test_pa3_flip_retest_is_a_later_closed_candle_event():
    engine, zone = seeded_resistance_engine()
    assert zone.flipped and zone.role == "support"
    snapshot = engine.process_closed_bar(bar(2, 100.5, 102.5, 100.2, 102))
    assert any(engine.events[event_id].event_type == "flip_retest" for event_id in snapshot.event_ids)
    assert any(engine.proposals[row].strategy_id == "PA3_FLIP_RETEST" for row in snapshot.proposal_ids)


def test_pa4_false_break_reversal_reverts_failed_role_flip():
    engine, zone = seeded_resistance_engine()
    snapshot = engine.process_closed_bar(bar(2, 102, 102.4, 99, 99.5))
    assert any(engine.events[event_id].event_type == "false_break_reclaim" for event_id in snapshot.event_ids)
    assert any(engine.proposals[row].strategy_id == "PA4_FALSE_BREAK_REVERSAL" for row in snapshot.proposal_ids)
    assert zone.role == "resistance"
    assert zone.flipped is False


def test_historical_zone_projection_does_not_leak_a_future_flip():
    engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT"))
    first = bar(0, 99.5, 100.5, 99, 100)
    engine.process_closed_bar(first)
    zone = PriceZone("zone-test", "resistance", "resistance", 100, 101,
                     first.timestamp, first.timestamp, ["swing-test"])
    engine.zones[zone.id] = zone
    before_flip = engine.process_closed_bar(bar(1, 100, 100.8, 99.5, 100.4))
    engine.process_closed_bar(bar(2, 100.4, 103, 100, 102))
    historical = engine.visual_state(candle_at=before_flip.candle_open)
    historical_zone = next(row for row in historical["zones"] if row["id"] == zone.id)
    assert historical_zone["role"] == "resistance"
    assert historical_zone["flipped"] is False


def test_same_closed_bars_produce_historical_live_parity():
    rows = [bar(i, 100 + i, 103 + i, 99 + i, 102 + i) for i in range(18)]
    historical = NativePriceActionEngine(PriceActionConfig("BTCUSDT"))
    live = NativePriceActionEngine(PriceActionConfig("BTCUSDT"))
    historical.ingest_closed_bars(rows)
    for row in rows:
        live.process_closed_bar(row)
    assert historical.latest_snapshot == live.latest_snapshot
    assert historical.visual_state()["swings"] == live.visual_state()["swings"]
    assert historical.visual_state()["events"] == live.visual_state()["events"]


def test_unfilled_confirmation_order_expires_and_remains_reported():
    engine = NativePriceActionEngine(PriceActionConfig("BTCUSDT"))
    first = bar(0, 100, 101, 99, 100)
    engine.process_closed_bar(first)
    trade = ResearchTrade(
        id="trade-expire", proposal_id="proposal-expire", setup_id="setup-expire",
        strategy_id="PA1_SR_REJECTION", direction="bullish", status="PENDING",
        requested_entry=200, stop=190, target=225, created_at=first.timestamp,
        valid_until_index=1,
    )
    engine.research_trades[trade.id] = trade
    engine.process_closed_bar(bar(1, 100, 101, 99, 100))
    assert trade.status == "PENDING"
    engine.process_closed_bar(bar(2, 100, 101, 99, 100))
    assert trade.status == "EXPIRED"
    assert trade.outcome == "UNFILLED"
    assert engine.visual_state()["trades"][0]["reason"].startswith("confirmation order expired")
