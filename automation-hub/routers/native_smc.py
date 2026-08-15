"""Read-only observability surface for the native SMC research model."""
import hashlib
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from services.native_smc import VisualReview, VisualReviewLedger, research_engine
from services.native_smc_visual_verification import deterministic_review_sample

router = APIRouter(prefix="/research/smc", tags=["research-smc"])
reviews = VisualReviewLedger()


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
    return research_engine(symbol, timeframe).visual_state(candle_at=_at_timestamp(at), candle_window=window)


@router.get("/chart")
def chart(symbol: str = "BTCUSDT", timeframe: str = "5m", at: str | None = None, window: int = 400):
    """Chart contract made only from native engine objects and raw candles."""
    return research_engine(symbol, timeframe).visual_state(candle_at=_at_timestamp(at), candle_window=window)


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
