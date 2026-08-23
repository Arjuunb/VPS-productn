"""Frozen research-only SMC strategy ladder after native visual verification.

This module deliberately *consumes* :mod:`services.native_smc` objects.  It
does not calculate pivots, breaks, liquidity, gaps, order blocks, or dealing
ranges itself, and it has no execution or position-management capability.

The candidate definitions are frozen as V1.0.0-research.  Their original draft
fingerprints are preserved byte-for-byte through promotion; this module still
cannot authorize performance research or execution.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from bot.data.indicators import atr
from services.native_smc import (
    FairValueGap,
    LiquiditySweep,
    OrderBlock,
    PivotPoint,
    SMCMarketStructureEngine,
    StructureEvent,
)


EXECUTION_ALLOWED = False
RESEARCH_FAMILY = "SMC_NATIVE_V1_RESEARCH"
LADDER_ID = "SMC_STRATEGY_LADDER_V1"
LADDER_VERSION = "SMC_STRATEGY_LADDER_V1.0.0-research"
RESEARCH_STATUS = "PASSED"
DEFINITION_STATUS = "PASSED"
CANDIDATE_FINGERPRINT_VERSION = "SMC_STRATEGY_LADDER_DRAFT_PRE_VERIFICATION"
VISUAL_STATE_VERIFICATION_REQUIRED = "VISUAL_STATE_VERIFICATION_PASSED"
_VISUAL_STATE_MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data" / "native_smc_visual_verification_final.json"

# These are pre-registered event lifetimes, not tuned values.  They preserve
# the existing native ten-bar setup expiry and the reference's eight-bar CHoCH
# and five-bar FVG-entry memories for every candidate that needs them.
EVENT_AGE_BARS = {
    "pivot": 10,
    "sweep": 10,
    "structure": 8,
    "poi": 5,
    "retest": 0,
}
ATR_LENGTH = 14
ATR_STOP_MULTIPLIER = 1.5
TARGET_RR = 2.5

Direction = Literal["bullish", "bearish"]
ConditionStatus = Literal["PASS", "MISSING", "NOT_REQUIRED", "INVALIDATED", "EXPIRED"]


@dataclass(frozen=True)
class CandidateDefinition:
    strategy_id: str
    short_label: str
    description: str
    required_objects: tuple[str, ...]
    long_sequence: tuple[str, ...]
    short_sequence: tuple[str, ...]
    invalidation: str
    expiry: str
    entry_semantics: str
    stop_logic: str
    target_logic: str
    poi_policy: str
    requires_htf: bool = False
    requires_location: bool = False
    requires_session: bool = False


@dataclass(frozen=True)
class TraceCondition:
    key: str
    label: str
    status: ConditionStatus
    detail: str
    object_id: str | None = None
    bars_since: int | None = None


@dataclass(frozen=True)
class DirectionTrace:
    direction: Direction
    state: str
    conditions: tuple[TraceCondition, ...]
    missing_conditions: tuple[str, ...]
    invalidation_reason: str | None
    next_required_event: str
    supporting_object_ids: tuple[str, ...]
    event_ages: dict[str, int | None]
    setup_id: str | None
    proposal: "SMCStrategyProposal | None"


@dataclass(frozen=True)
class SMCStrategyProposal:
    """A deterministic draft research proposal. It is never an order."""

    id: str
    strategy_id: str
    setup_id: str
    symbol: str
    timeframe: str
    direction: Direction
    entry: float
    stop: float
    target: float
    rr_ratio: float
    signal_timestamp: datetime
    supporting_object_ids: tuple[str, ...]
    reasoning_trace: tuple[TraceCondition, ...]
    execution_allowed: bool = False


@dataclass(frozen=True)
class CandidateEvaluation:
    strategy_id: str
    version: str
    research_status: str
    execution_allowed: bool
    source_hash: str
    configuration_hash: str
    selected_direction: Direction | None
    state: str
    conflict: bool
    next_required_event: str
    direction_traces: tuple[DirectionTrace, ...]
    selected_trace: DirectionTrace | None
    sample_destruction: dict[str, int]


SMC_STRATEGY_LADDER: tuple[CandidateDefinition, ...] = (
    CandidateDefinition(
        "SMC_S1_PIVOT_REVERSAL", "S1 Pivot", "Confirmed native pivot followed by same-side rejection.",
        ("PivotPoint", "PriceAction", "SMCMarketSnapshot"),
        ("confirmed swing/internal low", "bullish rejection", "ENTRY_READY"),
        ("confirmed swing/internal high", "bearish rejection", "ENTRY_READY"),
        "opposite native structure after pivot confirmation", "pivot age > 10 bars",
        "enter at the confirming closed-bar close", "signal-bar low/high minus/plus 1.5 ATR", "2.5R from entry", "NOT_REQUIRED",
    ),
    CandidateDefinition(
        "SMC_S2_STRUCTURE", "S2 Structure", "Pivot reversal with a fresh same-side native BOS or CHoCH.",
        ("PivotPoint", "StructureEvent", "PriceAction", "SMCMarketSnapshot"),
        ("confirmed pivot low", "bullish BOS/CHOCH after pivot", "bullish rejection", "ENTRY_READY"),
        ("confirmed pivot high", "bearish BOS/CHOCH after pivot", "bearish rejection", "ENTRY_READY"),
        "opposite native structure after the qualifying shift", "pivot > 10 bars or structure > 8 bars",
        "enter at the confirming closed-bar close", "signal-bar low/high minus/plus 1.5 ATR", "2.5R from entry", "NOT_REQUIRED",
    ),
    CandidateDefinition(
        "SMC_S3_LIQUIDITY_STRUCTURE", "S3 Liquidity", "Native liquidity reference, sweep, then ordered structure shift.",
        ("PivotPoint", "LiquiditySweep", "StructureEvent", "PriceAction", "SMCMarketSnapshot"),
        ("low-side reference", "sell-side sweep", "bullish BOS/CHOCH after sweep", "bullish rejection", "ENTRY_READY"),
        ("high-side reference", "buy-side sweep", "bearish BOS/CHOCH after sweep", "bearish rejection", "ENTRY_READY"),
        "opposite native structure after the qualifying sweep", "sweep > 10 bars or structure > 8 bars",
        "enter at the confirming closed-bar close", "signal-bar low/high minus/plus 1.5 ATR", "2.5R from entry", "NOT_REQUIRED",
    ),
    CandidateDefinition(
        "SMC_S4_FVG_RETEST", "S4 FVG", "Ordered liquidity/structure sequence followed by an exact native FVG retest.",
        ("LiquiditySweep", "StructureEvent", "FairValueGap", "PriceAction", "SMCMarketSnapshot"),
        ("sell-side sweep", "bullish BOS/CHOCH", "bullish FVG after shift", "exact FVG retest", "bullish rejection", "ENTRY_READY"),
        ("buy-side sweep", "bearish BOS/CHOCH", "bearish FVG after shift", "exact FVG retest", "bearish rejection", "ENTRY_READY"),
        "opposite structure or POI mitigation before a qualifying retest", "sweep > 10, shift > 8, or FVG > 5 bars",
        "enter at the confirming retest-bar close", "signal-bar low/high minus/plus 1.5 ATR", "2.5R from entry", "FVG_ONLY",
    ),
    CandidateDefinition(
        "SMC_S5_ORDER_BLOCK_RETEST", "S5 Order Block", "Ordered liquidity/structure sequence followed by a same-shift native OB retest.",
        ("LiquiditySweep", "StructureEvent", "OrderBlock", "PriceAction", "SMCMarketSnapshot"),
        ("sell-side sweep", "bullish BOS/CHOCH", "same-shift bullish OB", "exact OB retest", "bullish rejection", "ENTRY_READY"),
        ("buy-side sweep", "bearish BOS/CHOCH", "same-shift bearish OB", "exact OB retest", "bearish rejection", "ENTRY_READY"),
        "opposite structure or OB mitigation before a qualifying retest", "sweep > 10, shift > 8, or OB > 5 bars",
        "enter at the confirming retest-bar close", "signal-bar low/high minus/plus 1.5 ATR", "2.5R from entry", "OB_ONLY",
    ),
    CandidateDefinition(
        "SMC_S6_FULL_SMC", "S6 Full SMC", "HTF/location-gated ordered SMC sequence with one validated native POI.",
        ("LiquiditySweep", "StructureEvent", "FairValueGap", "OrderBlock", "DealingRange", "PriceAction", "SMCMarketSnapshot"),
        ("HTF bullish", "discount", "sell-side sweep", "bullish BOS/CHOCH", "bullish FVG OR OB", "exact POI retest", "bullish rejection", "London/New York", "ENTRY_READY"),
        ("HTF bearish", "premium", "buy-side sweep", "bearish BOS/CHOCH", "bearish FVG OR OB", "exact POI retest", "bearish rejection", "London/New York", "ENTRY_READY"),
        "HTF/location reversal, opposite structure, or POI mitigation before qualifying retest", "sweep > 10, shift > 8, POI > 5 bars",
        "enter at the confirming retest-bar close", "signal-bar low/high minus/plus 1.5 ATR", "2.5R from entry", "FVG_OR_OB",
        requires_htf=True, requires_location=True, requires_session=True,
    ),
)

_CANDIDATES = {candidate.strategy_id: candidate for candidate in SMC_STRATEGY_LADDER}


def candidate_registry() -> tuple[CandidateDefinition, ...]:
    """Return frozen research definitions; this is never a production registry."""
    return SMC_STRATEGY_LADDER


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, default=lambda row: asdict(row) if hasattr(row, "__dataclass_fields__") else str(row), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def native_engine_source_hash() -> str:
    return hashlib.sha256((Path(__file__).with_name("native_smc.py")).read_bytes()).hexdigest()


def visual_state_verification_status() -> str:
    """Read the authoritative visual-state gate without opening market data.

    A missing or malformed status fails closed so a future release cannot be
    accidentally frozen around unverified native SMC objects.
    """
    try:
        payload = json.loads(_VISUAL_STATE_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "VISUAL_STATE_VERIFICATION_UNKNOWN"
    return str(payload.get("status") or "VISUAL_STATE_VERIFICATION_UNKNOWN")


def candidate_configuration_hash(candidate: CandidateDefinition) -> str:
    return _canonical_hash(
        {
            # Promotion cannot change the six already-reviewed candidate
            # fingerprints. The release version is recorded separately.
            "version": CANDIDATE_FINGERPRINT_VERSION,
            "event_age_bars": EVENT_AGE_BARS,
            "shared_trade_mechanics": {
                "atr_length": ATR_LENGTH,
                "atr_stop_multiplier": ATR_STOP_MULTIPLIER,
                "target_rr": TARGET_RR,
            },
            "candidate": asdict(candidate),
        }
    )


def manifest_payload() -> dict:
    """Machine-readable frozen definition with no performance metrics."""
    return {
        "research_id": RESEARCH_FAMILY,
        "ladder_id": LADDER_ID,
        "version": LADDER_VERSION,
        "status": DEFINITION_STATUS,
        "visual_state_verification": visual_state_verification_status(),
        "blocked_by": None,
        "freeze_allowed": True,
        "execution_allowed": False,
        "performance_research": "NOT_RUN",
        "event_age_bars": EVENT_AGE_BARS,
        "entry_semantics": "Confirmed closed-bar close; signal generation is separate from later fill simulation.",
        "direction_conflict_policy": "REJECT_BOTH_IF_SIMULTANEOUS_ENTRY_READY",
        "position_overlap_policy": "ONE_OPEN_RESEARCH_POSITION_PER_STRATEGY_SYMBOL; suppress same/opposite entries until the later simulator closes it.",
        "shared_trade_mechanics": {"atr_length": ATR_LENGTH, "atr_stop_multiplier": ATR_STOP_MULTIPLIER, "target_rr": TARGET_RR, "risk_model": "PENDING_COMMON_RESEARCH_RISK_MODEL", "fill_model": "PENDING_COMMON_REALISTIC_FILL_MODEL", "transaction_cost_model": "PENDING_COMMON_COST_MODEL"},
        "native_engine_source": "services/native_smc.py",
        "native_engine_source_hash": native_engine_source_hash(),
        "candidates": [{**asdict(candidate), "research_status": RESEARCH_STATUS, "execution_allowed": False, "configuration_hash": candidate_configuration_hash(candidate), "source_hash": native_engine_source_hash()} for candidate in SMC_STRATEGY_LADDER],
    }


def _bar_index(engine: SMCMarketStructureEngine, timestamp: datetime) -> int | None:
    for index in range(len(engine.bars) - 1, -1, -1):
        if engine.bars[index].timestamp == timestamp:
            return index
    return None


def _age(engine: SMCMarketStructureEngine, now_index: int, timestamp: datetime | None) -> int | None:
    if timestamp is None:
        return None
    index = _bar_index(engine, timestamp)
    return None if index is None else max(0, now_index - index)


def _visible_at(created_at: datetime, mitigated_at: datetime | None, now: datetime) -> bool:
    return created_at <= now and (mitigated_at is None or mitigated_at > now)


def _trace(key: str, label: str, ok: bool | None, detail: str, object_id: str | None = None, bars_since: int | None = None) -> TraceCondition:
    return TraceCondition(key, label, "PASS" if ok is True else "NOT_REQUIRED" if ok is None else "MISSING", detail, object_id, bars_since)


def _expired(key: str, label: str, detail: str, object_id: str | None, bars_since: int | None) -> TraceCondition:
    return TraceCondition(key, label, "EXPIRED", detail, object_id, bars_since)


def _latest(rows: list, timestamp: datetime, key) -> object | None:
    eligible = [row for row in rows if key(row) <= timestamp]
    return max(eligible, key=key) if eligible else None


def _opposite_after(events: list[StructureEvent], direction: Direction, after: datetime, now: datetime) -> StructureEvent | None:
    opposite = "bearish" if direction == "bullish" else "bullish"
    return _latest([row for row in events if row.direction == opposite and after < row.confirmed_at <= now], now, lambda row: row.confirmed_at)


def _matching_pivot(engine: SMCMarketStructureEngine, direction: Direction, before: datetime, now_index: int, *, require_near_level: float | None = None) -> PivotPoint | None:
    kind = "low" if direction == "bullish" else "high"
    pivots = [row for row in engine.pivots.values() if row.kind == kind and row.confirmed_at <= before]
    if require_near_level is not None:
        current_atr = atr(engine.bars[:now_index + 1], ATR_LENGTH)
        buffer = current_atr * engine.config.poi_atr_buffer_mult
        pivots = [row for row in pivots if abs(row.price - require_near_level) <= buffer]
    return _latest(pivots, before, lambda row: row.confirmed_at)


def _matching_sweep(engine: SMCMarketStructureEngine, direction: Direction, now: datetime) -> LiquiditySweep | None:
    return _latest([row for row in engine.events.values() if isinstance(row, LiquiditySweep) and row.direction == direction and row.timestamp <= now], now, lambda row: row.timestamp)


def _matching_structure(engine: SMCMarketStructureEngine, direction: Direction, after: datetime, now: datetime) -> StructureEvent | None:
    rows = [row for row in engine.events.values() if isinstance(row, StructureEvent) and row.direction == direction and row.event_type in {"BOS", "CHOCH"} and after < row.confirmed_at <= now]
    return _latest(rows, now, lambda row: row.confirmed_at)


def _matching_fvg(engine: SMCMarketStructureEngine, direction: Direction, after: datetime, now: datetime) -> FairValueGap | None:
    rows = [row for row in engine.fvgs.values() if row.direction == direction and after < row.created_at <= now]
    return _latest(rows, now, lambda row: row.created_at)


def _matching_ob(engine: SMCMarketStructureEngine, direction: Direction, structure: StructureEvent, now: datetime) -> OrderBlock | None:
    rows = [row for row in engine.obs.values() if row.direction == direction and row.source_structure_id == structure.id and row.created_at <= now]
    return _latest(rows, now, lambda row: row.created_at)


def _touches(bar, zone: FairValueGap | OrderBlock) -> bool:
    low = zone.bottom if isinstance(zone, FairValueGap) else zone.low
    high = zone.top if isinstance(zone, FairValueGap) else zone.high
    return bar.low <= high and bar.high >= low


def _active_at(zone: FairValueGap | OrderBlock, now: datetime) -> bool:
    return _visible_at(zone.created_at, zone.mitigation_at, now)


def _setup_id(candidate: CandidateDefinition, engine: SMCMarketStructureEngine, direction: Direction, anchor: datetime | None) -> str | None:
    if anchor is None:
        return None
    return f"{candidate.strategy_id}-{engine.config.symbol}-{engine.config.timeframe.upper()}-{direction.upper()}-{anchor.strftime('%Y%m%dT%H%M%S')}"


def _proposal(candidate: CandidateDefinition, engine: SMCMarketStructureEngine, direction: Direction, setup_id: str, now_index: int, objects: tuple[str, ...], conditions: tuple[TraceCondition, ...]) -> SMCStrategyProposal | None:
    current_atr = atr(engine.bars[:now_index + 1], ATR_LENGTH)
    if current_atr <= 0:
        return None
    bar = engine.bars[now_index]
    stop = bar.low - current_atr * ATR_STOP_MULTIPLIER if direction == "bullish" else bar.high + current_atr * ATR_STOP_MULTIPLIER
    risk = abs(bar.close - stop)
    if risk <= 0:
        return None
    target = bar.close + risk * TARGET_RR if direction == "bullish" else bar.close - risk * TARGET_RR
    # The setup identity is anchored at the causal native sequence. A later
    # simulator can de-duplicate this deterministic ID, so re-evaluation
    # cannot turn later candles into duplicate proposals.
    proposal_id = hashlib.sha256(f"{setup_id}|proposal".encode()).hexdigest()[:20]
    return SMCStrategyProposal(
        id=f"smc-proposal-{proposal_id}", strategy_id=candidate.strategy_id, setup_id=setup_id,
        symbol=engine.config.symbol, timeframe=engine.config.timeframe, direction=direction,
        entry=bar.close, stop=stop, target=target, rr_ratio=TARGET_RR,
        signal_timestamp=bar.timestamp, supporting_object_ids=objects, reasoning_trace=conditions,
        execution_allowed=False,
    )


def _direction_trace(candidate: CandidateDefinition, engine: SMCMarketStructureEngine, direction: Direction, now_index: int) -> DirectionTrace:
    """Evaluate one direction from existing native objects only at one closed bar."""
    now_bar = engine.bars[now_index]
    now = now_bar.timestamp
    # Price action is a native snapshot object. An incomplete checkpoint must
    # remain incomplete rather than be calculated with an alternate pattern.
    snapshot = engine.snapshots.get(now)
    rejection = snapshot.price_action if snapshot is not None else None
    required_rejection = (rejection.bullish_rejection if direction == "bullish" else rejection.bearish_rejection) if rejection else False
    pivot_label = "Pivot low" if direction == "bullish" else "Pivot high"
    sweep_label = "Sell-side sweep" if direction == "bullish" else "Buy-side sweep"
    shift_label = "Bullish BOS / CHoCH" if direction == "bullish" else "Bearish BOS / CHoCH"
    poi_label = "Bullish POI" if direction == "bullish" else "Bearish POI"
    traces: list[TraceCondition] = []
    objects: list[str] = []
    ages: dict[str, int | None] = {"pivot": None, "sweep": None, "structure": None, "poi": None, "retest": None}

    # Only S1/S2/S3 explicitly require a pivot. S4-S6 start from their native
    # sweep object and must not silently add a locally inferred prerequisite.
    requires_pivot = candidate.strategy_id in {"SMC_S1_PIVOT_REVERSAL", "SMC_S2_STRUCTURE", "SMC_S3_LIQUIDITY_STRUCTURE"}
    pivot = _matching_pivot(engine, direction, now, now_index) if requires_pivot else None
    if pivot:
        ages["pivot"] = _age(engine, now_index, pivot.confirmed_at)
        objects.append(pivot.id)
    pivot_fresh = pivot is not None and ages["pivot"] is not None and ages["pivot"] <= EVENT_AGE_BARS["pivot"]
    if not requires_pivot:
        traces.append(_trace("pivot", pivot_label, None, "not required by this candidate"))
    elif pivot and not pivot_fresh:
        traces.append(_expired("pivot", pivot_label, "confirmed pivot exceeded the frozen 10-bar lifetime", pivot.id, ages["pivot"]))
    else:
        traces.append(_trace("pivot", pivot_label, pivot_fresh, "native confirmed pivot" if pivot_fresh else "awaiting matching native confirmed pivot", pivot.id if pivot else None, ages["pivot"]))

    needs_structure = candidate.strategy_id != "SMC_S1_PIVOT_REVERSAL"
    sweep: LiquiditySweep | None = None
    structure: StructureEvent | None = None
    if candidate.strategy_id in {"SMC_S3_LIQUIDITY_STRUCTURE", "SMC_S4_FVG_RETEST", "SMC_S5_ORDER_BLOCK_RETEST", "SMC_S6_FULL_SMC"}:
        sweep = _matching_sweep(engine, direction, now)
        if sweep:
            ages["sweep"] = _age(engine, now_index, sweep.timestamp)
            linked_pivot = _matching_pivot(engine, direction, sweep.timestamp, now_index, require_near_level=sweep.level) if candidate.strategy_id == "SMC_S3_LIQUIDITY_STRUCTURE" else None
            if linked_pivot:
                pivot = linked_pivot
                ages["pivot"] = _age(engine, now_index, pivot.confirmed_at)
                if pivot.id not in objects:
                    objects.append(pivot.id)
        sweep_fresh = sweep is not None and ages["sweep"] is not None and ages["sweep"] <= EVENT_AGE_BARS["sweep"]
        if candidate.strategy_id == "SMC_S3_LIQUIDITY_STRUCTURE":
            reference_ok = pivot is not None and pivot.confirmed_at <= sweep.timestamp if sweep else False
            traces[0] = _trace("pivot", "Liquidity reference" if sweep else pivot_label, reference_ok, "native pivot matched to native sweep level" if reference_ok else "awaiting native pivot reference at the sweep level", pivot.id if reference_ok and pivot else None, ages["pivot"])
        if sweep and not sweep_fresh:
            traces.append(_expired("sweep", sweep_label, "native sweep exceeded the frozen 10-bar lifetime", sweep.id, ages["sweep"]))
        else:
            traces.append(_trace("sweep", sweep_label, sweep_fresh, "native liquidity sweep" if sweep_fresh else "awaiting ordered native liquidity sweep", sweep.id if sweep else None, ages["sweep"]))
        if sweep_fresh:
            objects.append(sweep.id)
            structure = _matching_structure(engine, direction, sweep.timestamp, now)
    elif needs_structure:
        if pivot_fresh:
            structure = _matching_structure(engine, direction, pivot.confirmed_at, now)

    if needs_structure:
        structure_age = _age(engine, now_index, structure.confirmed_at) if structure else None
        ages["structure"] = structure_age
        structure_fresh = structure is not None and structure_age is not None and structure_age <= EVENT_AGE_BARS["structure"]
        if structure and not structure_fresh:
            traces.append(_expired("structure", shift_label, "native structure shift exceeded the frozen 8-bar lifetime", structure.id, structure_age))
        else:
            traces.append(_trace("structure", shift_label, structure_fresh, "native structure is after the prior sequence event" if structure_fresh else "awaiting fresh same-side native BOS or CHoCH", structure.id if structure else None, structure_age))
        if structure_fresh:
            objects.append(structure.id)

    poi: FairValueGap | OrderBlock | None = None
    requires_poi = candidate.poi_policy != "NOT_REQUIRED"
    if requires_poi and structure is not None:
        fvg = _matching_fvg(engine, direction, structure.confirmed_at, now)
        ob = _matching_ob(engine, direction, structure, now)
        if candidate.poi_policy == "FVG_ONLY":
            poi = fvg
        elif candidate.poi_policy == "OB_ONLY":
            poi = ob
        else:  # Frozen S6 interpretation: one exact directional native POI is enough.
            options = [item for item in (fvg, ob) if item is not None]
            poi = max(options, key=lambda row: row.created_at) if options else None
        poi_age = _age(engine, now_index, poi.created_at) if poi else None
        ages["poi"] = poi_age
        poi_fresh = poi is not None and poi_age is not None and poi_age <= EVENT_AGE_BARS["poi"]
        label = "Bullish FVG" if candidate.poi_policy == "FVG_ONLY" and direction == "bullish" else "Bearish FVG" if candidate.poi_policy == "FVG_ONLY" else "Bullish OB" if candidate.poi_policy == "OB_ONLY" and direction == "bullish" else "Bearish OB" if candidate.poi_policy == "OB_ONLY" else poi_label
        if poi and not poi_fresh:
            traces.append(_expired("poi", label, "native POI exceeded the frozen 5-bar lifetime", poi.id, poi_age))
        elif poi and not _active_at(poi, now):
            traces.append(TraceCondition("poi", label, "INVALIDATED", "native POI was mitigated before this bar", poi.id, poi_age))
        else:
            traces.append(_trace("poi", label, poi_fresh, "native POI created after qualifying structure" if poi_fresh else "awaiting a same-sequence native POI", poi.id if poi else None, poi_age))
        if poi_fresh and poi and _active_at(poi, now):
            objects.append(poi.id)
    else:
        traces.append(_trace("poi", poi_label, None if not requires_poi else False, "not required by this candidate" if not requires_poi else "awaiting qualifying native structure before POI", None, None))

    retest_ok = False
    if requires_poi and poi and _active_at(poi, now) and poi.created_at < now:
        retest_ok = _touches(now_bar, poi)
        ages["retest"] = 0 if retest_ok else None
    traces.append(_trace("retest", "Exact POI retest", retest_ok if requires_poi else None, "current closed bar overlaps the exact native POI" if retest_ok else "awaiting exact retest of the linked native POI" if requires_poi else "not required by this candidate", poi.id if retest_ok and poi else None, ages["retest"]))

    if candidate.requires_htf:
        snapshot = engine.snapshots.get(now)
        htf_ok = snapshot is not None and snapshot.htf_bias == (1 if direction == "bullish" else -1)
        traces.insert(0, _trace("htf", "HTF bias", htf_ok, "completed HTF native bias aligned" if htf_ok else "awaiting aligned completed HTF native bias"))
    if candidate.requires_location:
        snapshot = engine.snapshots.get(now)
        expected_area = "discount" if direction == "bullish" else "premium"
        location_ok = snapshot is not None and snapshot.dealing_range.area == expected_area
        insertion = 1 if candidate.requires_htf else 0
        traces.insert(insertion, _trace("location", expected_area.title(), location_ok, "native dealing range location aligned" if location_ok else f"awaiting native {expected_area} location"))
    if candidate.requires_session:
        snapshot = engine.snapshots.get(now)
        session_ok = snapshot is not None and snapshot.session != "inactive"
        traces.append(_trace("session", "London / New York session", session_ok, "native session is active" if session_ok else "awaiting a native active session"))

    rejection_required = required_rejection and (not requires_poi or retest_ok)
    traces.append(_trace("rejection", "Bullish rejection" if direction == "bullish" else "Bearish rejection", rejection_required, "native price-action rejection on the qualifying closed bar" if rejection_required else "awaiting same-bar native rejection", None, 0 if required_rejection else None))

    # A candidate is invalidated only by factual native objects, never by a
    # reinterpreted candle pattern.  Expired statuses take priority over a
    # possible current rejection so stale pieces cannot be recombined.
    anchor = (sweep.timestamp if sweep else structure.confirmed_at if structure else pivot.confirmed_at if pivot else None)
    opposite = _opposite_after([row for row in engine.events.values() if isinstance(row, StructureEvent)], direction, anchor, now) if anchor else None
    invalidated = next((row for row in traces if row.status in {"INVALIDATED", "EXPIRED"}), None)
    if opposite:
        invalidated = TraceCondition("invalidation", "Opposite native structure", "INVALIDATED", "opposite native structure occurred after this setup sequence", opposite.id, _age(engine, now_index, opposite.confirmed_at))
        traces.append(invalidated)
    statuses = {row.key: row.status for row in traces}
    required_keys = [row.key for row in traces if row.status != "NOT_REQUIRED"]
    all_pass = not invalidated and all(statuses[key] == "PASS" for key in required_keys)
    setup_id = _setup_id(candidate, engine, direction, anchor)
    object_ids = tuple(dict.fromkeys(objects))
    if invalidated:
        state = invalidated.status
        next_event = "Start a new ordered native sequence"
    elif all_pass:
        state = "ENTRY_READY"
        next_event = "Research proposal recorded; execution remains disabled"
    else:
        first_missing = next((row for row in traces if row.status == "MISSING"), None)
        state = f"WAITING_{first_missing.key.upper()}" if first_missing else "WAITING_SEQUENCE"
        next_event = first_missing.detail if first_missing else "Await the next required native event"
    proposal = _proposal(candidate, engine, direction, setup_id, now_index, object_ids, tuple(traces)) if all_pass and setup_id else None
    return DirectionTrace(
        direction=direction, state=state, conditions=tuple(traces),
        missing_conditions=tuple(row.label for row in traces if row.status == "MISSING"),
        invalidation_reason=invalidated.detail if invalidated else None,
        next_required_event=next_event, supporting_object_ids=object_ids, event_ages=ages,
        setup_id=setup_id, proposal=proposal,
    )


def evaluate_candidate(engine: SMCMarketStructureEngine, strategy_id: str, *, candle_at: datetime | None = None) -> CandidateEvaluation:
    """Return a visual/research trace without modifying ``engine``.

    ``candle_at`` supports checkpoint review.  It cannot use objects confirmed
    after the chosen closed bar, which prevents future leakage in visual work.
    """
    if strategy_id not in _CANDIDATES:
        raise ValueError(f"Unknown native SMC ladder candidate: {strategy_id}")
    candidate = _CANDIDATES[strategy_id]
    if not engine.bars:
        return CandidateEvaluation(strategy_id, LADDER_VERSION, RESEARCH_STATUS, False, native_engine_source_hash(), candidate_configuration_hash(candidate), None, "WAITING_CANDLES", False, "Await confirmed native candles", (), None, {})
    now_index = _bar_index(engine, candle_at) if candle_at is not None else len(engine.bars) - 1
    if now_index is None:
        raise ValueError("candle_at is not a native closed candle")
    traces = tuple(_direction_trace(candidate, engine, direction, now_index) for direction in ("bullish", "bearish"))
    ready = [row for row in traces if row.state == "ENTRY_READY"]
    conflict = len(ready) > 1
    if conflict:
        selected = None
        state = "CONFLICT"
        next_event = "Both directions are ENTRY_READY; frozen policy rejects both"
    else:
        rank = {"ENTRY_READY": 100, "WAITING_REJECTION": 90, "WAITING_RETEST": 80, "WAITING_POI": 70, "WAITING_STRUCTURE": 60, "WAITING_SWEEP": 50, "WAITING_PIVOT": 40, "WAITING_HTF": 30, "WAITING_LOCATION": 20, "WAITING_SESSION": 10}
        selected = max(traces, key=lambda row: (rank.get(row.state, 0), row.setup_id or ""))
        state = selected.state
        next_event = selected.next_required_event
    counter_keys = ("candles", "htf", "location", "pivot", "sweep", "structure", "poi", "retest", "rejection", "entry_ready")
    counters = {key: 0 for key in counter_keys}
    counters["candles"] = 1
    for trace in traces:
        for condition in trace.conditions:
            if condition.key in counters and condition.status == "PASS":
                counters[condition.key] += 1
        if trace.state == "ENTRY_READY":
            counters["entry_ready"] += 1
    return CandidateEvaluation(strategy_id, LADDER_VERSION, RESEARCH_STATUS, False, native_engine_source_hash(), candidate_configuration_hash(candidate), selected.direction if selected else None, state, conflict, next_event, traces, selected, counters)


def evaluate_ladder(engine: SMCMarketStructureEngine, *, candle_at: datetime | None = None) -> dict:
    """Public research payload with no performance field and no execution path."""
    evaluations = [evaluate_candidate(engine, candidate.strategy_id, candle_at=candle_at) for candidate in SMC_STRATEGY_LADDER]
    return {
        "research_id": RESEARCH_FAMILY,
        "ladder_id": LADDER_ID,
        "version": LADDER_VERSION,
        "research_only": True,
        "execution_allowed": False,
        "performance_research": "NOT_RUN",
        "definitions_frozen": True,
        "direction_conflict_policy": "REJECT_BOTH_IF_SIMULTANEOUS_ENTRY_READY",
        "event_age_bars": EVENT_AGE_BARS,
        "candidates": [asdict(row) for row in evaluations],
    }
