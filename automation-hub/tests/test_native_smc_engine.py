from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from bot.types import Bar
from services.native_smc import (
    EXECUTION_ALLOWED, FairValueGap, OrderBlock, PivotPoint, SMCConfig,
    SMCMarketStructureEngine, SMCSetup, SetupPhase, StructureEvent, VisualReview,
    VisualReviewLedger,
)

UTC = timezone.utc


def bar(i: int, *, o=100., h=101., l=99., c=100.2, v=1000.) -> Bar:
    return Bar(datetime(2025, 1, 1, tzinfo=UTC) + timedelta(minutes=5 * i), o, h, l, c, v)


def engine(**kwargs):
    return SMCMarketStructureEngine(SMCConfig("BTCUSDT", **kwargs))


def warm(e, n=60):
    for i in range(n): e.process_closed_bar(bar(i, o=100+i*.01, h=101+i*.01, l=99+i*.01, c=100.2+i*.01))


def test_closed_candles_are_idempotent_and_chronological():
    e = engine(); one = bar(0); first = e.process_closed_bar(one)
    assert e.process_closed_bar(one).id == first.id
    with pytest.raises(ValueError): e.process_closed_bar(bar(-1))


def test_completed_htf_bucket_excludes_current_forming_bucket():
    e = engine(htf_minutes=10)
    for i in range(2): e.process_closed_bar(bar(i))
    assert not e.htf_closed
    e.process_closed_bar(bar(2))
    assert len(e.htf_closed) == 1 and e.htf_closed[0].timestamp == bar(0).timestamp


def test_pivot_occurrence_and_confirmation_are_distinct():
    e = engine(internal_pivot_length=2)
    e.process_closed_bar(bar(0, h=110)); e.process_closed_bar(bar(1, h=105)); e.process_closed_bar(bar(2, h=103))
    pivot = e.internal_high
    assert pivot and pivot.occurred_at == bar(0).timestamp and pivot.confirmed_at == bar(2).timestamp


def test_structure_event_is_exactly_once_per_pivot():
    e = engine(internal_pivot_length=1); warm(e)
    p = PivotPoint("p", "high", 99., bar(5).timestamp, bar(8).timestamp, 5)
    e.protected_internal_high = p; e.internal_bias = -1
    current = bar(60, c=110, h=111, l=109); e.bars.append(current)
    first = e._break_structure(current, 60); second = e._break_structure(current, 60)
    assert len(first) == 1 and not second and first[0].event_type == "CHOCH"


def test_sweep_and_price_action_are_closed_bar_objects():
    e = engine(sweep_lookback=3)
    for i in range(3): e.bars.append(bar(i, h=105, l=95, c=100))
    current = bar(3, o=101, h=106, l=99, c=104)
    e.bars.append(current)
    assert e._detect_sweep(current, 3).direction == "bearish"
    action = e._price_action(bar(4, o=100, h=101, l=94, c=101))
    assert action.bullish_rejection


def test_fvg_creation_and_mitigation_have_stable_lifecycle():
    e = engine(require_volume_surge=False)
    for i in range(20): e.bars.append(bar(i, h=101, l=99, c=100))
    e.bars.append(bar(20, h=103, l=100, c=102))
    current = bar(21, h=105, l=102, c=104); e.bars.append(current)
    gap = e._detect_fvg(current, 21)
    assert gap and gap.direction == "bullish" and gap.active
    e._mitigate_zones(bar(22, h=103, l=98, c=99))
    assert gap.mitigated and not gap.active and gap.mitigation_at == bar(22).timestamp


def test_order_block_mitigation_and_chart_object_share_the_same_id():
    e = engine(); ob = OrderBlock("ob-1", "bearish", 110, 108, "pivot", "break", bar(0).timestamp); e.obs[ob.id] = ob
    e._mitigate_zones(bar(1, h=111, l=109, c=110))
    assert ob.mitigated and not e.chart_objects()


def test_dealing_range_and_session_are_deterministic():
    e = engine(); e.bars = [bar(0, h=110, l=90), bar(1, h=108, l=95)]
    dealing = e._dealing_range(109)
    assert dealing.high == 110 and dealing.low == 90 and dealing.area == "premium"


def test_setup_sequence_rejects_out_of_order_fvg():
    e = engine(); setup = SMCSetup("setup", "bullish", bar(0).timestamp, 0, SetupPhase.LIQUIDITY_SWEPT, sweep_id="sweep")
    e.setups[setup.id] = setup
    fvg = FairValueGap("fvg", "bullish", 105, 103, bar(1).timestamp, (bar(0).timestamp, bar(0).timestamp, bar(1).timestamp)); e.fvgs[fvg.id] = fvg
    e._advance_setups(bar(1), 1, None, [], fvg, e._price_action(bar(1)), e._dealing_range(100))
    assert setup.phase == SetupPhase.LIQUIDITY_SWEPT


def test_setup_expiry_and_execution_isolation():
    e = engine(setup_expiry_bars=1); setup = SMCSetup("setup", "bullish", bar(0).timestamp, 0, SetupPhase.LIQUIDITY_SWEPT); e.setups[setup.id] = setup
    e._advance_setups(bar(2), 2, None, [], None, e._price_action(bar(2)), e._dealing_range(100))
    assert setup.phase == SetupPhase.EXPIRED and EXECUTION_ALLOWED is False


def test_checkpoint_restore_has_identical_state_and_no_duplicate_events():
    e = engine(); warm(e, 12); restored = SMCMarketStructureEngine.restore_checkpoint(e.checkpoint())
    assert restored.latest_snapshot.id == e.latest_snapshot.id
    assert restored.process_closed_bar(bar(11)).id == e.latest_snapshot.id


def test_public_state_is_chart_and_decision_single_source_of_truth():
    e = engine(); e.process_closed_bar(bar(0)); public = e.public_state()
    assert public["execution_allowed"] is False and public["research_id"] == "SMC_NATIVE_V1_RESEARCH"


def test_proposed_trade_is_a_risk_plan_not_an_order():
    e = engine(); warm(e)
    setup = SMCSetup("setup", "bullish", bar(0).timestamp, 0, SetupPhase.ENTRY_READY)
    candidate = bar(60, o=101, h=103, l=100, c=102)
    e._propose(setup, candidate, "snapshot")
    proposal = next(iter(e.proposals.values()))
    assert proposal.stop < proposal.entry < proposal.target
    assert proposal.risk_status == "PENDING_RISK_ENGINE"
    assert proposal.position_size is None and proposal.execution_allowed is False


def test_research_configuration_cannot_enable_execution():
    with pytest.raises(ValueError, match="execution is permanently disabled"):
        SMCMarketStructureEngine(SMCConfig("BTCUSDT", execution_allowed=True))


def test_chart_markers_reference_the_same_structure_and_sweep_objects():
    e = engine()
    structure = StructureEvent("structure-1", "BTCUSDT", "5m", "internal", "CHOCH", "bullish", 101,
                               bar(0).timestamp, bar(1).timestamp, "pivot-1", 102)
    e.events[structure.id] = structure
    assert e.chart_objects()[0].source_id == structure.id


def test_authoritative_adapter_rejects_an_open_forming_candle():
    e = engine()
    now = bar(2).timestamp
    snapshots = e.ingest_authoritative_closed_bars([bar(0), bar(1), bar(2)], timeframe_seconds=300, now=now)
    assert len(snapshots) == 2
    assert e.bars[-1].timestamp == bar(1).timestamp


def test_completed_htf_reference_ema_is_exposed_without_forming_htf_data():
    e = engine(htf_minutes=5)
    for i in range(52):
        e.process_closed_bar(bar(i, c=100 + i))
    assert e.latest_snapshot.htf_ema is not None
    assert e.latest_snapshot.htf_completed_at == bar(50).timestamp


def test_visual_contract_keeps_mitigated_fvg_and_exact_chart_identity():
    e = engine()
    gap = FairValueGap("fvg-1", "bullish", 105, 103, bar(1).timestamp, (bar(0).timestamp, bar(0).timestamp, bar(1).timestamp))
    e.fvgs[gap.id] = gap
    e._mitigate_zones(bar(2, l=102))
    state = e.visual_state()
    assert state["fair_value_gaps"][0]["id"] == gap.id
    assert state["fair_value_gaps"][0]["mitigated"] is True


def test_visual_contract_preserves_pivot_confirmation_without_backdating():
    e = engine()
    pivot = PivotPoint("pivot-1", "high", 110, bar(2).timestamp, bar(5).timestamp, 2, "swing")
    e.pivots[pivot.id] = pivot
    row = e.visual_state()["pivots"][0]
    assert row["occurred_at"] == bar(2).timestamp
    assert row["confirmed_at"] == bar(5).timestamp
    assert e.chart_objects()[0].source_id == pivot.id


def test_setup_timeline_links_each_transition_to_the_engine_object():
    e = engine()
    setup = SMCSetup("setup", "bearish", bar(0).timestamp, 0, SetupPhase.IDLE)
    e._transition(setup, SetupPhase.LIQUIDITY_SWEPT, bar(1), "liquidity swept", "sweep-1")
    assert setup.transitions[0].object_id == "sweep-1"
    assert e.next_required_event(setup) == "Wait bearish CHoCH or BOS"


def test_visual_review_evidence_persists_without_changing_engine(tmp_path):
    ledger = VisualReviewLedger(tmp_path / "reviews.json")
    review = VisualReview("review-1", "SMC_NATIVE_V1_RESEARCH", "BTCUSDT", "5m", "pivot-1", "swing pivot",
                          "AMBIGUOUS", "higher high", "weak high", "insufficient zoom", "2025-01-01T00:10:00Z", bar(0).timestamp,
                          notes="compare alongside source chart", visible_range_start="2025-01-01T00:00:00Z",
                          visible_range_end="2025-01-01T01:00:00Z", selected_candle_timestamp="2025-01-01T00:10:00Z")
    ledger.append(review)
    restored = VisualReviewLedger(tmp_path / "reviews.json").records()
    assert restored == [review]
    assert EXECUTION_ALLOWED is False


def test_snapshot_ledger_preserves_closed_candle_context_without_changing_decisions():
    e = engine()
    first = e.process_closed_bar(bar(0))
    second = e.process_closed_bar(bar(1))
    state = e.visual_state(candle_at=first.candle_open, candle_window=20)
    assert e.snapshots[first.candle_open] == first
    assert e.snapshots[second.candle_open] == second
    assert state["selected_snapshot"]["id"] == first.id
    assert len(state["snapshot_ledger"]) == 2
    assert first.next_required_event == "Wait for confirmed HTF bias and liquidity sweep"


def test_verified_visual_checkpoint_loads_only_as_a_non_running_research_model(tmp_path, monkeypatch):
    from services import native_smc

    source = engine(); source.process_closed_bar(bar(0))
    checkpoint = tmp_path / "verified.checkpoint.json"
    checkpoint.write_text(__import__("json").dumps(source.checkpoint(), default=lambda value: value.isoformat()))
    monkeypatch.setenv("HUB_SMC_VISUAL_CHECKPOINT_PATH", str(checkpoint))
    native_smc._ENGINES.clear()
    restored = native_smc.research_engine("BTCUSDT", "5m")
    assert restored.latest_snapshot is not None
    assert restored.config.execution_allowed is False
    native_smc._ENGINES.clear()
