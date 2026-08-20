"""Read-only observability surface for the native SMC research model."""
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from services.native_smc import VisualReview, VisualReviewLedger, research_engine
from services.native_smc_live_visual import (
    NativeSMCLiveDataUnavailable,
    live_visual_history,
    live_visual_state,
)
from services.native_smc_visual_verification import deterministic_review_sample
from services.smc_strategy_ladder import evaluate_ladder, manifest_payload

router = APIRouter(prefix="/research/smc", tags=["research-smc"])
reviews = VisualReviewLedger()
_REFERENCE_PINE_PATH = Path(__file__).resolve().parents[1] / "research_references" / "smc_pro_v2_reference.pine"


class VisualReviewInput(BaseModel):
    object_id: str = Field(min_length=1, max_length=160)
    component: str = Field(min_length=1, max_length=80)
    classification: Literal["CORRECT", "INCORRECT", "AMBIGUOUS"]
    expected_structure: str | None = Field(default=None, max_length=2000)
    actual_structure: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)
    screenshot_timestamp: str | None = Field(default=None, max_length=80)
    visible_range_start: str | None = Field(default=None, max_length=80)
    visible_range_end: str | None = Field(default=None, max_length=80)
    selected_candle_timestamp: str | None = Field(default=None, max_length=80)


def _at_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(422, "'at' must be an ISO-8601 timestamp") from exc


@router.get("/state")
def state(symbol: str = "BTCUSDT", timeframe: str = "5m", at: str | None = None, window: int = 400):
    selected_at = _at_timestamp(at)
    engine = research_engine(symbol, timeframe)
    payload = engine.visual_state(candle_at=selected_at, candle_window=window)
    payload["strategy_ladder"] = evaluate_ladder(engine, candle_at=selected_at)
    return payload


@router.get("/chart")
def chart(symbol: str = "BTCUSDT", timeframe: str = "5m", at: str | None = None, window: int = 400):
    """Chart contract made only from native engine objects and raw candles."""
    selected_at = _at_timestamp(at)
    engine = research_engine(symbol, timeframe)
    payload = engine.visual_state(candle_at=selected_at, candle_window=window)
    payload["strategy_ladder"] = evaluate_ladder(engine, candle_at=selected_at)
    return payload


@router.get("/strategy-ladder")
def strategy_ladder(symbol: str = "BTCUSDT", timeframe: str = "5m", at: str | None = None):
    """Frozen SMC research traces; it has no order or execution authority."""
    engine = research_engine(symbol, timeframe)
    return {
        "research_only": True,
        "execution_allowed": False,
        "definition": manifest_payload(),
        "evaluation": evaluate_ladder(engine, candle_at=_at_timestamp(at)),
    }


@router.get("/live-chart")
def live_chart(symbol: str = "BTCUSDT", timeframe: str = "5m", venue: str = "mexc_perpetual",
               window: int = 800, visible: int = 240):
    """Read-only live-exchange visualisation; never a trading data path."""
    try:
        return live_visual_state(symbol, timeframe, venue, limit=window, visible=visible)
    except (NativeSMCLiveDataUnavailable, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/live-history")
def live_history(symbol: str = "BTCUSDT", timeframe: str = "5m", venue: str = "mexc_perpetual",
                 before: str | None = None, limit: int = 400):
    """One paginated, closed-candle exchange page for Visual Lab browsing."""
    if before is None:
        raise HTTPException(422, "'before' is required for historical paging")
    parsed_before = _at_timestamp(before)
    # Guarded above; keeping this explicit makes the non-optional boundary of
    # the service contract clear without changing timestamp semantics.
    assert parsed_before is not None
    try:
        return live_visual_history(symbol, timeframe, venue, before=parsed_before, limit=limit)
    except (NativeSMCLiveDataUnavailable, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get("/pine-reference")
def pine_reference():
    """Return the immutable reference source for visual comparison only.

    The reference is deliberately not compiled, evaluated, or wired into the
    native model.  Its status remains a parity-audit artefact, never execution
    authority.
    """
    if not _REFERENCE_PINE_PATH.is_file():
        raise HTTPException(503, "Native SMC Pine reference is unavailable in this deployment")
    content = _REFERENCE_PINE_PATH.read_text(encoding="utf-8")
    return {
        "reference_id": "SMC_PRO_V2_REFERENCE",
        "status": "PARITY_AUDIT",
        "language": "pine",
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "execution_allowed": False,
        "notice": "Reference source only. It is not executed by TradeLogX and native SMC parity is not yet claimed.",
        "content": content,
    }


@router.get("/review-sample")
def review_sample(symbol: str = "BTCUSDT", timeframe: str = "5m"):
    """Fixed deterministic review list; classification can never select samples."""
    engine = research_engine(symbol, timeframe)
    return {
        "research_only": True,
        "execution_allowed": False,
        "sample": [asdict(row) for row in deterministic_review_sample(engine)],
    }


@router.get("/setups")
def setups(symbol: str = "BTCUSDT", timeframe: str = "5m"):
    engine = research_engine(symbol, timeframe)
    return {"research_only": True, "execution_allowed": False,
            "setups": engine.visual_state()["setups"]}


@router.get("/setups/{setup_id}")
def setup(setup_id: str, symbol: str = "BTCUSDT", timeframe: str = "5m"):
    engine = research_engine(symbol, timeframe)
    row = engine.setups.get(setup_id)
    if row is None:
        raise HTTPException(404, "Unknown native SMC research setup")
    # This is the engine's factual state history, not a generated explanation.
    return {"research_only": True, "execution_allowed": False, "setup": asdict(row)}


@router.get("/events")
def events(symbol: str = "BTCUSDT", timeframe: str = "5m"):
    engine = research_engine(symbol, timeframe)
    state = engine.visual_state()
    return {"research_only": True, "execution_allowed": False,
            "events": state["events"], "chart_objects": state["chart_objects"],
            "pivots": state["pivots"], "fair_value_gaps": state["fair_value_gaps"],
            "order_blocks": state["order_blocks"]}


@router.get("/reviews")
def list_reviews(symbol: str = "BTCUSDT", timeframe: str = "5m"):
    rows = [asdict(row) for row in reviews.records()
            if row.symbol == symbol.upper() and row.timeframe == timeframe]
    return {"research_only": True, "execution_allowed": False, "reviews": rows}


@router.post("/reviews")
def record_review(body: VisualReviewInput, symbol: str = "BTCUSDT", timeframe: str = "5m"):
    """Persist human interpretation evidence; this never modifies SMC rules."""
    engine = research_engine(symbol, timeframe)
    if body.object_id not in engine.known_object_ids():
        raise HTTPException(404, "The selected native SMC object is not present in this research state")
    now = datetime.now(timezone.utc)
    digest = hashlib.sha256(f"{engine.config.symbol}|{timeframe}|{body.object_id}|{body.component}|{now.isoformat()}".encode()).hexdigest()[:16]
    review = VisualReview(
        id=f"smc-review-{digest}", research_id="SMC_NATIVE_V1_RESEARCH",
        symbol=engine.config.symbol, timeframe=timeframe, object_id=body.object_id,
        component=body.component, classification=body.classification,
        expected_structure=body.expected_structure, actual_structure=body.actual_structure,
        reason=body.reason, screenshot_timestamp=body.screenshot_timestamp, created_at=now,
        notes=body.notes, visible_range_start=body.visible_range_start,
        visible_range_end=body.visible_range_end,
        selected_candle_timestamp=body.selected_candle_timestamp,
    )
    return {"research_only": True, "execution_allowed": False, "review": asdict(reviews.append(review))}
