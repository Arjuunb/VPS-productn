"""Historical funding provenance and normalized R attribution for research.

No function in this module can fetch private account data or place an order.
It accepts public provider records, freezes their provenance, and refuses to
represent absent history as a known zero cost.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable


AVAILABLE = "HISTORICAL_FUNDING_AVAILABLE"
PARTIAL = "HISTORICAL_FUNDING_PARTIALLY_AVAILABLE"
UNAVAILABLE = "HISTORICAL_FUNDING_UNAVAILABLE"
DISABLED = "FUNDING_INTENTIONALLY_DISABLED"
_EXPECTED_INTERVAL = timedelta(hours=8)


def _utc(value) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class HistoricalFundingEvent:
    symbol: str
    funding_time: datetime
    funding_rate: float
    mark_price: float | None
    provider: str
    source_quality: str


@dataclass(frozen=True)
class HistoricalFundingSeries:
    symbol: str
    state: str
    requested_start: datetime
    requested_end: datetime
    events: tuple[HistoricalFundingEvent, ...]
    missing_ranges: tuple[tuple[datetime, datetime], ...]
    warnings: tuple[str, ...]
    dataset_id: str

    @classmethod
    def build(cls, symbol: str, records: Iterable[dict], *, requested_start,
              requested_end, intentionally_disabled: bool = False,
              provider: str = "Binance USD-M public funding history") -> "HistoricalFundingSeries":
        start, end = _utc(requested_start), _utc(requested_end)
        if end < start:
            raise ValueError("funding coverage end precedes start")
        key = symbol.upper()
        if intentionally_disabled:
            material = {"symbol": key, "state": DISABLED,
                        "requested_start": start.isoformat(), "requested_end": end.isoformat()}
            digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:20]
            return cls(key, DISABLED, start, end, (), (), (), f"funding-{digest}")
        deduped: dict[datetime, HistoricalFundingEvent] = {}
        rejected = 0
        for raw in records:
            try:
                stamp = _utc(raw.get("funding_time") or raw.get("fundingTime") or
                             raw.get("funding_time_ms"))
                rate = float(raw.get("funding_rate") if raw.get("funding_rate") is not None
                             else raw["fundingRate"])
                raw_mark = raw.get("mark_price") if "mark_price" in raw else raw.get("markPrice")
                mark = None if raw_mark in (None, "") else float(raw_mark)
                if not math.isfinite(rate) or (mark is not None and
                                                (not math.isfinite(mark) or mark <= 0)):
                    raise ValueError("invalid funding values")
            except (KeyError, TypeError, ValueError, OverflowError):
                rejected += 1
                continue
            if start <= stamp <= end:
                deduped[stamp] = HistoricalFundingEvent(
                    key, stamp, rate, mark, str(raw.get("provider") or provider),
                    str(raw.get("source_quality") or "verified_public_provider"))
        events = tuple(deduped[stamp] for stamp in sorted(deduped))
        missing: list[tuple[datetime, datetime]] = []
        for left, right in zip(events, events[1:]):
            if right.funding_time - left.funding_time > _EXPECTED_INTERVAL * 1.5:
                missing.append((left.funding_time, right.funding_time))
        if not events:
            state = UNAVAILABLE
        else:
            starts_late = events[0].funding_time > start + _EXPECTED_INTERVAL
            ends_early = events[-1].funding_time < end - _EXPECTED_INTERVAL
            state = PARTIAL if starts_late or ends_early or missing else AVAILABLE
        warnings = []
        if state == UNAVAILABLE:
            warnings.append("Historical funding is unavailable; it is unknown and is not treated as zero.")
        elif state == PARTIAL:
            warnings.append("Historical funding covers only part of the requested dataset interval.")
        if rejected:
            warnings.append(f"Rejected {rejected} malformed historical funding record(s).")
        if any(event.mark_price is None for event in events):
            warnings.append("Provider mark price is missing for some events; disclosed entry-price notional fallback is used.")
        material = {"symbol": key, "state": state, "start": start.isoformat(),
                    "end": end.isoformat(), "events": [asdict(event) for event in events],
                    "missing": missing}
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=str,
                                           separators=(",", ":")).encode()).hexdigest()[:20]
        return cls(key, state, start, end, events, tuple(missing), tuple(warnings),
                   f"funding-{digest}")

    def covers(self, opened_at: datetime, closed_at: datetime) -> bool:
        opened, closed = _utc(opened_at), _utc(closed_at)
        if self.state == DISABLED:
            return True
        if self.state == UNAVAILABLE or not self.events:
            return False
        if opened < self.requested_start or closed > self.requested_end:
            return False
        first, last = self.events[0].funding_time, self.events[-1].funding_time
        if opened < first - _EXPECTED_INTERVAL or closed > last + _EXPECTED_INTERVAL:
            return False
        return not any(left < closed and right > opened for left, right in self.missing_ranges)

    def effect_r(self, *, direction: str, entry_price: float, risk_distance: float,
                 opened_at: datetime, closed_at: datetime) -> dict:
        if risk_distance <= 0 or entry_price <= 0:
            raise ValueError("positive entry price and risk distance are required")
        opened, closed = _utc(opened_at), _utc(closed_at)
        if self.state == DISABLED:
            return {"funding_r": 0.0, "complete": True, "events": [],
                    "state": self.state, "warnings": []}
        applied = [event for event in self.events if opened <= event.funding_time <= closed]
        sign = 1 if direction in {"bullish", "long", "buy"} else -1
        funding_r = 0.0
        warnings = []
        rows = []
        for event in applied:
            notional_price = event.mark_price or entry_price
            if event.mark_price is None:
                warnings.append(f"{event.funding_time.isoformat()}: entry price used as disclosed notional fallback")
            # Positive rates: longs pay and shorts receive. Negative rates invert.
            contribution = -sign * event.funding_rate * notional_price / risk_distance
            funding_r += contribution
            rows.append({"funding_time": event.funding_time.isoformat(),
                         "funding_rate": event.funding_rate,
                         "notional_price": notional_price,
                         "funding_r": contribution, "provider": event.provider})
        complete = self.covers(opened, closed)
        if not complete:
            warnings.append("Funding coverage is incomplete for this holding interval; net result is provisional.")
        return {"funding_r": funding_r, "complete": complete, "events": rows,
                "state": self.state, "warnings": sorted(set(warnings))}


def unavailable_series(symbol: str, start: datetime, end: datetime) -> HistoricalFundingSeries:
    return HistoricalFundingSeries.build(symbol, (), requested_start=start, requested_end=end)
