"""Strict request parsing and response shaping for additive V2 APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4

from bot.types import Bar

from .contracts import MarketSnapshot

_MAX_TIMEFRAMES = 8
_MAX_BARS_PER_TIMEFRAME = 5_000


def _timestamp(value: Any, *, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def _bar(raw: Mapping[str, Any], *, as_of: datetime) -> Bar:
    timestamp = _timestamp(raw.get("timestamp"), name="bar.timestamp")
    if timestamp > as_of:
        raise ValueError("bar.timestamp must not be after snapshot as_of")
    try:
        opening, high, low, close, volume = (
            float(raw["open"]), float(raw["high"]), float(raw["low"]),
            float(raw["close"]), float(raw["volume"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("each bar requires numeric open, high, low, close and volume") from exc
    if min(opening, high, low, close) <= 0 or volume < 0:
        raise ValueError("bar prices must be positive and volume must be non-negative")
    if high < max(opening, close) or low > min(opening, close) or high < low:
        raise ValueError("bar high/low must bound its open and close")
    return Bar(timestamp, opening, high, low, close, volume)


def snapshot_from_payload(payload: Mapping[str, Any]) -> MarketSnapshot:
    """Build a validated snapshot from a research/shadow API payload only."""
    symbol = str(payload.get("symbol", "")).upper().strip()
    if not symbol:
        raise ValueError("symbol is required")
    as_of = _timestamp(payload.get("as_of"), name="as_of")
    raw_timeframes = payload.get("bars_by_timeframe")
    if not isinstance(raw_timeframes, Mapping) or not raw_timeframes:
        raise ValueError("bars_by_timeframe must be a non-empty object")
    if len(raw_timeframes) > _MAX_TIMEFRAMES:
        raise ValueError(f"at most {_MAX_TIMEFRAMES} timeframes are accepted")
    bars_by_timeframe: dict[str, tuple[Bar, ...]] = {}
    for timeframe, raw_bars in raw_timeframes.items():
        name = str(timeframe).lower().strip()
        if not name or not isinstance(raw_bars, list) or not raw_bars:
            raise ValueError("each timeframe needs a non-empty bar array")
        if len(raw_bars) > _MAX_BARS_PER_TIMEFRAME:
            raise ValueError(f"at most {_MAX_BARS_PER_TIMEFRAME} bars per timeframe are accepted")
        bars = tuple(_bar(item, as_of=as_of) for item in raw_bars if isinstance(item, Mapping))
        if len(bars) != len(raw_bars):
            raise ValueError("each bar must be an object")
        if any(bars[index].timestamp >= bars[index + 1].timestamp for index in range(len(bars) - 1)):
            raise ValueError("bars must be strictly ordered by ascending timestamp")
        bars_by_timeframe[name] = bars
    events = payload.get("events", [])
    if not isinstance(events, list) or not all(isinstance(item, Mapping) for item in events):
        raise ValueError("events must be an array of objects")
    event_fetched_at = payload.get("event_fetched_at")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata must be an object")
    return MarketSnapshot(
        snapshot_id=str(payload.get("snapshot_id") or f"snap_{uuid4().hex}"),
        symbol=symbol,
        as_of=as_of,
        bars_by_timeframe=bars_by_timeframe,
        events=events,
        event_calendar_connected=payload.get("event_calendar_connected"),
        event_fetched_at=(_timestamp(event_fetched_at, name="event_fetched_at")
                          if event_fetched_at is not None else None),
        source=str(payload.get("source") or "api-shadow"),
        metadata=metadata,
    )
