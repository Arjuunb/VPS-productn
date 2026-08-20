from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from bot.types import Bar
from services.native_smc import (
    DealingRange,
    FairValueGap,
    LiquiditySweep,
    OrderBlock,
    PivotPoint,
    PriceAction,
    SMCConfig,
    SMCMarketStructureEngine,
    StructureEvent,
)
from services.smc_strategy_ladder import (
    ATR_LENGTH,
    ATR_STOP_MULTIPLIER,
    EVENT_AGE_BARS,
    LADDER_VERSION,
    TARGET_RR,
    candidate_configuration_hash,
    candidate_registry,
    evaluate_candidate,
    evaluate_ladder,
    manifest_payload,
    visual_state_verification_status,
)

UTC = timezone.utc


def bar(index: int, *, o: float = 100.0, h: float = 101.0, l: float = 99.0, c: float = 100.2) -> Bar:
    return Bar(datetime(2025, 3, 1, tzinfo=UTC) + timedelta(minutes=5 * index), o, h, l, c, 1_000.0 + index)


def seeded_engine() -> SMCMarketStructureEngine:
    """Only native objects are placed in the fixture; the ladder creates none."""
    engine = SMCMarketStructureEngine(SMCConfig("BTCUSDT", htf_minutes=5, require_volume_surge=False))
    for index in range(70):
        candle = bar(index, o=100 + index * .03, h=101 + index * .03, l=99 + index * .03, c=100.2 + index * .03)
        if index == 69:
            candle = bar(index, o=101.0, h=101.2, l=98.0, c=100.9)
        engine.process_closed_bar(candle)

    pivot_time, sweep_time, structure_time, poi_time, now = (engine.bars[index].timestamp for index in (63, 64, 65, 66, 69))
    pivot = PivotPoint("pivot-low", "low", 99.0, pivot_time, pivot_time, 63, "internal")
    sweep = LiquiditySweep("sweep-low", "BTCUSDT", "5m", "bullish", 99.0, sweep_time, 64)
    structure = StructureEvent("structure-bull", "BTCUSDT", "5m", "internal", "CHOCH", "bullish", 100.5, structure_time, structure_time, pivot.id, 101.0)
    fvg = FairValueGap("fvg-bull", "bullish", 100.5, 99.0, poi_time, (structure_time, structure_time, poi_time))
    ob = OrderBlock("ob-bull", "bullish", 100.5, 99.0, pivot.id, structure.id, poi_time)
    engine.pivots = {pivot.id: pivot}
    engine.events = {sweep.id: sweep, structure.id: structure}
    engine.fvgs = {fvg.id: fvg}
    engine.obs = {ob.id: ob}
    snapshot = engine.snapshots[now]
    engine.snapshots[now] = replace(
        snapshot,
        htf_bias=1,
        dealing_range=DealingRange(110.0, 90.0, 100.0, "discount"),
        session="london",
        price_action=PriceAction(True, False, .5, .2, 2.0),
    )
    engine.latest_snapshot = engine.snapshots[now]
    return engine


def trace(result, direction: str = "bullish"):
    return next(row for row in result.direction_traces if row.direction == direction)


def test_registry_is_complete_draft_blocked_and_non_executable():
    assert [row.strategy_id for row in candidate_registry()] == [
        "SMC_S1_PIVOT_REVERSAL", "SMC_S2_STRUCTURE", "SMC_S3_LIQUIDITY_STRUCTURE",
        "SMC_S4_FVG_RETEST", "SMC_S5_ORDER_BLOCK_RETEST", "SMC_S6_FULL_SMC",
    ]
    assert all("research" in row.strategy_id.lower() or row.strategy_id.startswith("SMC_S") for row in candidate_registry())
    payload = manifest_payload()
    assert payload["version"] == LADDER_VERSION
    assert payload["status"] == "DRAFT_PRE_VERIFICATION"
    assert payload["visual_state_verification"] == "VISUAL_STATE_VERIFICATION_PARTIAL"
    assert payload["blocked_by"] == "VISUAL_STATE_VERIFICATION"
    assert payload["freeze_allowed"] is False
    assert visual_state_verification_status() != "VISUAL_STATE_VERIFICATION_PASSED"
    assert payload["execution_allowed"] is False
    assert payload["performance_research"] == "NOT_RUN"
    assert all(row["execution_allowed"] is False for row in payload["candidates"])


def test_checked_in_manifest_matches_the_draft_runtime_definition():
    manifest_path = Path(__file__).resolve().parents[1] / "data" / "smc_strategy_ladder_v1_manifest.json"
    checked_in = json.loads(manifest_path.read_text(encoding="utf-8"))
    runtime = manifest_payload()

    assert checked_in["version"] == runtime["version"]
    assert checked_in["status"] == runtime["status"]
    assert checked_in["visual_state_verification"] == runtime["visual_state_verification"]
    assert checked_in["blocked_by"] == runtime["blocked_by"]
    assert checked_in["freeze_allowed"] is False
    assert checked_in["native_engine_source_sha256"] == runtime["native_engine_source_hash"]
    assert checked_in["event_age_bars"] == EVENT_AGE_BARS
    assert checked_in["common_trade_mechanics"]["atr_length"] == ATR_LENGTH
    assert checked_in["common_trade_mechanics"]["stop"] == f"{ATR_STOP_MULTIPLIER} ATR beyond the signal-bar low/high"
    assert checked_in["common_trade_mechanics"]["target"] == f"{TARGET_RR}R"
    checked_in_hashes = {row["strategy_id"]: row["configuration_sha256"] for row in checked_in["candidates"]}
    assert checked_in_hashes == {
        candidate.strategy_id: candidate_configuration_hash(candidate)
        for candidate in candidate_registry()
    }


def test_s1_and_s2_use_native_pivot_then_native_structure_only():
    engine = seeded_engine()
    s1 = evaluate_candidate(engine, "SMC_S1_PIVOT_REVERSAL")
    s2 = evaluate_candidate(engine, "SMC_S2_STRUCTURE")
    assert trace(s1).state == "ENTRY_READY"
    assert trace(s2).state == "ENTRY_READY"
    assert "pivot-low" in trace(s2).supporting_object_ids
    assert "structure-bull" in trace(s2).supporting_object_ids


def test_s3_rejects_structure_that_precedes_the_native_sweep():
    engine = seeded_engine()
    original = engine.events["structure-bull"]
    before_sweep = engine.bars[62].timestamp
    engine.events[original.id] = replace(original, occurred_at=before_sweep, confirmed_at=before_sweep)
    result = evaluate_candidate(engine, "SMC_S3_LIQUIDITY_STRUCTURE")
    assert trace(result).state == "WAITING_STRUCTURE"


def test_s4_links_the_exact_native_fvg_and_current_retest():
    result = evaluate_candidate(seeded_engine(), "SMC_S4_FVG_RETEST")
    current = trace(result)
    assert current.state == "ENTRY_READY"
    assert "fvg-bull" in current.supporting_object_ids
    assert next(row for row in current.conditions if row.key == "retest").object_id == "fvg-bull"


def test_s5_links_the_same_shift_native_order_block_and_current_retest():
    result = evaluate_candidate(seeded_engine(), "SMC_S5_ORDER_BLOCK_RETEST")
    current = trace(result)
    assert current.state == "ENTRY_READY"
    assert "ob-bull" in current.supporting_object_ids
    assert next(row for row in current.conditions if row.key == "retest").object_id == "ob-bull"


def test_s6_uses_frozen_fvg_or_ob_interpretation_with_native_context():
    result = evaluate_candidate(seeded_engine(), "SMC_S6_FULL_SMC")
    current = trace(result)
    assert current.state == "ENTRY_READY"
    assert {"sweep-low", "structure-bull"}.issubset(current.supporting_object_ids)
    assert any(row.key == "htf" and row.status == "PASS" for row in current.conditions)
    assert any(row.key == "location" and row.status == "PASS" for row in current.conditions)
    assert any(row.key == "session" and row.status == "PASS" for row in current.conditions)


def test_expired_components_cannot_be_recombined_into_a_setup():
    engine = seeded_engine()
    old = engine.bars[30].timestamp
    engine.pivots = {"old-low": PivotPoint("old-low", "low", 99.0, old, old, 30, "internal")}
    result = evaluate_candidate(engine, "SMC_S1_PIVOT_REVERSAL")
    current = trace(result)
    assert current.state == "EXPIRED"
    assert current.event_ages["pivot"] > EVENT_AGE_BARS["pivot"]


def test_evaluation_is_read_only_and_proposal_id_is_deterministic():
    engine = seeded_engine()
    before = engine.checkpoint()
    first = evaluate_candidate(engine, "SMC_S4_FVG_RETEST")
    second = evaluate_candidate(engine, "SMC_S4_FVG_RETEST")
    assert engine.checkpoint() == before
    assert trace(first).proposal is not None
    assert trace(first).proposal.id == trace(second).proposal.id
    assert trace(first).proposal.execution_allowed is False
    public = evaluate_ladder(engine)
    assert public["execution_allowed"] is False
    assert len(public["candidates"]) == 6
