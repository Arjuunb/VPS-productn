"""Adapters that expose existing analysis as immutable V2 shadow evidence.

This module intentionally *does not* import or call ``AutoStrategyEngine`` or
``SignalPipeline``.  Callers may compare its result with the legacy decision,
but it cannot submit, approve, size, or execute a trade.
"""
from __future__ import annotations

from datetime import timezone
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from .contracts import (
    DecisionAction,
    Evidence,
    EvidenceStatus,
    MarketSnapshot,
    ShadowEvaluation,
)

_VERSION = "core-v2-shadow-1"
_MTF = ("1w", "1d", "4h", "15m", "5m")
_SESSION_ZONES = (
    ("Sydney", "Australia/Sydney"),
    ("Tokyo", "Asia/Tokyo"),
    ("London", "Europe/London"),
    ("New York", "America/New_York"),
)


def _unavailable(engine: str, snapshot: MarketSnapshot, reason: str) -> Evidence:
    return Evidence(
        engine=engine,
        version=_VERSION,
        status=EvidenceStatus.UNAVAILABLE,
        as_of=snapshot.as_of,
        blockers=(reason,),
        source_ids=(snapshot.snapshot_id,),
    )


def _analysis_input(snapshot: MarketSnapshot) -> tuple[str, Sequence[Any], Optional[dict[str, Any]]]:
    """Run the existing deterministic analysis once for all dependent engines."""
    from services.market_analysis import analyze

    bars = snapshot.bars_by_timeframe.get("5m")
    timeframe = "5m"
    if not bars:
        timeframe, bars = next(iter(snapshot.bars_by_timeframe.items()), ("", ()))
    if not bars:
        return timeframe, (), None
    result = analyze(bars)
    return timeframe, bars, result


def _market_analysis(snapshot: MarketSnapshot, timeframe: str, bars: Sequence[Any],
                     result: Optional[dict[str, Any]]) -> Evidence:
    """Adapt the existing deterministic one-timeframe market analysis."""
    if result is None:
        return _unavailable("market_context", snapshot, "No bars were supplied for market analysis.")
    if not result.get("available"):
        return Evidence(
            engine="market_context",
            version=_VERSION,
            status=EvidenceStatus.UNAVAILABLE,
            as_of=snapshot.as_of,
            facts={"timeframe": timeframe, "bar_count": len(bars)},
            blockers=(str(result.get("note", "Market analysis unavailable.")),),
            source_ids=(snapshot.snapshot_id,),
        )
    reasons = (
        f"{timeframe} bias: {result.get('bias', 'Neutral')}",
        f"Structure: {result.get('structure', {}).get('state', 'unknown')}",
        f"Volatility: {result.get('volatility', {}).get('label', 'unknown')}",
    )
    return Evidence(
        engine="market_context",
        version=_VERSION,
        status=EvidenceStatus.PASS,
        as_of=snapshot.as_of,
        facts={"timeframe": timeframe, **result},
        reasons=reasons,
        source_ids=(snapshot.snapshot_id,),
    )


def _trend(snapshot: MarketSnapshot) -> Evidence:
    """Adapt the established weekly/daily/4H/15M/5M causal MTF analysis."""
    from services.mtf_engine import analyze_layers

    missing = tuple(tf for tf in _MTF if not snapshot.bars_by_timeframe.get(tf))
    if missing:
        return Evidence(
            engine="trend",
            version=_VERSION,
            status=EvidenceStatus.UNAVAILABLE,
            as_of=snapshot.as_of,
            facts={"available_timeframes": tuple(sorted(snapshot.bars_by_timeframe))},
            blockers=(f"Missing required MTF bars: {', '.join(missing)}.",),
            source_ids=(snapshot.snapshot_id,),
        )
    decision = analyze_layers(*(snapshot.bars_by_timeframe[tf] for tf in _MTF))
    status = EvidenceStatus.PASS if decision.allowed else EvidenceStatus.FAIL
    blockers = tuple(decision.blockers)
    if not decision.allowed and not blockers:
        blockers = (decision.trigger_state,)
    return Evidence(
        engine="trend",
        version=_VERSION,
        status=status,
        as_of=snapshot.as_of,
        facts=decision.to_dict(),
        reasons=tuple(decision.reasons),
        blockers=blockers,
        confidence=float(decision.score),
        source_ids=(snapshot.snapshot_id,),
    )


def _trend_1h(snapshot: MarketSnapshot) -> Evidence:
    """Expose an honest first-class 1H trend layer for V2 shadow analysis."""
    from services.mtf_engine import _ema_bias

    bars = snapshot.bars_by_timeframe.get("1h")
    if not bars:
        return _unavailable("trend_1h", snapshot, "Missing required 1H bars.")
    direction, strength = _ema_bias([bar.close for bar in bars])
    if direction is None:
        return Evidence(
            engine="trend_1h", version=_VERSION, status=EvidenceStatus.UNAVAILABLE,
            as_of=snapshot.as_of, facts={"bar_count": len(bars)},
            blockers=("Insufficient 1H history for EMA trend analysis.",),
            source_ids=(snapshot.snapshot_id,),
        )
    label = {1: "Bullish", -1: "Bearish", 0: "Neutral"}[direction]
    status = EvidenceStatus.PASS if direction else EvidenceStatus.FAIL
    return Evidence(
        engine="trend_1h", version=_VERSION, status=status, as_of=snapshot.as_of,
        facts={"timeframe": "1h", "direction": label, "strength": strength,
               "bar_count": len(bars)},
        reasons=(f"1H EMA direction: {label}; efficiency strength {strength:.3f}.",),
        blockers=("1H trend is neutral.",) if not direction else (),
        confidence=round(strength * 100.0, 2), source_ids=(snapshot.snapshot_id,),
    )


def _liquidity(snapshot: MarketSnapshot, result: Optional[dict[str, Any]]) -> Evidence:
    """Surface the existing explicit equal-level and sweep criteria as evidence."""
    if not result or not result.get("available"):
        return _unavailable("liquidity", snapshot, "Liquidity analysis requires available market structure data.")
    liquidity = result.get("liquidity", {})
    equal_highs = tuple(liquidity.get("equal_highs") or ())
    equal_lows = tuple(liquidity.get("equal_lows") or ())
    sweep = str(liquidity.get("sweep") or "none detected")
    has_location = bool(equal_highs or equal_lows)
    has_sweep = sweep != "none detected"
    status = EvidenceStatus.PASS if (has_location or has_sweep) else EvidenceStatus.FAIL
    reasons = []
    if has_location:
        reasons.append("Resting-liquidity levels identified from equal pivot highs/lows.")
    if has_sweep:
        reasons.append(sweep)
    if not reasons:
        reasons.append("No equal-level liquidity or wick-through-and-close-back sweep detected.")
    return Evidence(
        engine="liquidity", version=_VERSION, status=status, as_of=snapshot.as_of,
        facts={"equal_highs": equal_highs, "equal_lows": equal_lows, "sweep": sweep,
               "criteria": "equal levels within 0.15 ATR; sweep = wick through then close back"},
        reasons=tuple(reasons),
        blockers=("No current liquidity setup under the declared criteria.",) if status is EvidenceStatus.FAIL else (),
        source_ids=(snapshot.snapshot_id,),
    )


def _volume(snapshot: MarketSnapshot, bars: Sequence[Any], result: Optional[dict[str, Any]]) -> Evidence:
    """Report observed relative volume and label unavailable order-flow honestly."""
    if not result or not result.get("available") or not bars:
        return _unavailable("volume", snapshot, "Volume analysis requires available OHLCV history.")
    volume = result.get("volume", {})
    ratio = volume.get("ratio_vs_20bar")
    if ratio is None:
        return _unavailable("volume", snapshot, "Relative volume cannot be calculated from supplied bars.")
    latest = bars[-1]
    pressure = ("buying" if latest.close > latest.open else "selling"
                if latest.close < latest.open else "neutral")
    participation = "high" if ratio > 1.1 else "low" if ratio < 0.9 else "normal"
    return Evidence(
        engine="volume", version=_VERSION, status=EvidenceStatus.PASS, as_of=snapshot.as_of,
        facts={"relative_volume_20bar": ratio, "participation": participation,
               "candle_pressure_proxy": pressure,
               "order_flow_delta": None,
               "delta_status": "unavailable: no order-flow feed supplied"},
        reasons=(f"Volume is {volume.get('label', 'unknown')} ({ratio}× the 20-bar average).",
                 f"Candle directional pressure proxy: {pressure}; this is not order-flow delta."),
        source_ids=(snapshot.snapshot_id,),
    )


def _volatility(snapshot: MarketSnapshot, result: Optional[dict[str, Any]]) -> Evidence:
    """Combine current ATR with supplied, never invented, execution metadata."""
    if not result or not result.get("available"):
        return _unavailable("volatility", snapshot, "Volatility analysis requires available market data.")
    volatility = result.get("volatility", {})
    atr = result.get("atr")
    atr_pct = volatility.get("atr_pct")
    if atr is None or atr_pct is None:
        return _unavailable("volatility", snapshot, "ATR is unavailable from supplied market data.")
    bid, ask = snapshot.metadata.get("bid"), snapshot.metadata.get("ask")
    try:
        bid, ask = float(bid), float(ask)
        spread_bps = round((ask - bid) / ((ask + bid) / 2.0) * 10_000, 4) if ask >= bid and bid > 0 else None
    except (TypeError, ValueError):
        spread_bps = None
    slippage = snapshot.metadata.get("estimated_slippage_bps")
    return Evidence(
        engine="volatility", version=_VERSION, status=EvidenceStatus.PASS, as_of=snapshot.as_of,
        facts={"atr": atr, "atr_pct": atr_pct, "regime": volatility.get("label"),
               "expected_range_price": atr, "trend_efficiency": result.get("trend", {}).get("strength"),
               "spread_bps": spread_bps, "estimated_slippage_bps": slippage,
               "spread_status": "observed" if spread_bps is not None else "unavailable",
               "slippage_status": "provider estimate" if slippage is not None else "unavailable"},
        reasons=(f"ATR is {atr_pct}% of price ({volatility.get('label', 'unknown')} volatility).",
                 "Expected one-period range equals current ATR; spread/slippage are only reported when supplied."),
        source_ids=(snapshot.snapshot_id,),
    )


def _events(snapshot: MarketSnapshot, *, max_age_seconds: float) -> Evidence:
    """Expose event policy without treating an unconnected calendar as safe."""
    from services.econ_guard import evaluate

    if snapshot.event_calendar_connected is not True:
        return _unavailable("news_event", snapshot, "Economic calendar is not connected.")
    if snapshot.event_fetched_at is None:
        return _unavailable("news_event", snapshot, "Economic calendar freshness is unknown.")
    age = (snapshot.as_of - snapshot.event_fetched_at).total_seconds()
    if age < 0 or age > max_age_seconds:
        return _unavailable(
            "news_event", snapshot,
            f"Economic calendar is stale ({max(age, 0):.0f}s old; limit {max_age_seconds:.0f}s).",
        )
    result = evaluate(list(snapshot.events), now=snapshot.as_of)
    status = EvidenceStatus.VETO if result["halt_new_entries"] else EvidenceStatus.PASS
    reasons = tuple(result.get("actions") or [result.get("note", "No event restriction.")])
    return Evidence(
        engine="news_event",
        version=_VERSION,
        status=status,
        as_of=snapshot.as_of,
        facts={"calendar_age_seconds": round(age, 3), **result},
        reasons=reasons,
        blockers=reasons if status is EvidenceStatus.VETO else (),
        source_ids=(snapshot.snapshot_id,),
    )


def _session(snapshot: MarketSnapshot) -> Evidence:
    """Report named market sessions from IANA local times, including DST.

    A centre is active during its conventional 08:00–17:00 local weekday
    window. This is session context, not an exchange-specific trading calendar.
    """
    active: list[str] = []
    local_times: dict[str, str] = {}
    for name, zone_name in _SESSION_ZONES:
        local = snapshot.as_of.astimezone(ZoneInfo(zone_name))
        local_times[name] = local.isoformat()
        if local.weekday() < 5 and 8 <= local.hour < 17:
            active.append(name)
    label = " / ".join(active) if active else "Off-hours"
    liquidity_quality = "high" if len(active) >= 2 else "normal" if active else "poor"
    return Evidence(
        engine="session",
        version=_VERSION,
        status=EvidenceStatus.PASS if active else EvidenceStatus.FAIL,
        as_of=snapshot.as_of,
        facts={"utc_time": snapshot.as_of.astimezone(timezone.utc).isoformat(),
               "active_sessions": tuple(active), "label": label,
               "liquidity_quality": liquidity_quality, "local_times": local_times,
               "schedule": "08:00–17:00 local weekday per IANA zone"},
        reasons=(f"Active session context: {label}; liquidity quality {liquidity_quality}.",),
        blockers=("No named major session is active; liquidity quality is poor.",) if not active else (),
        source_ids=(snapshot.snapshot_id,),
    )


class ShadowEvidenceRunner:
    """Evaluate current analysis modules without modifying runtime behaviour."""

    def __init__(self, *, event_max_age_seconds: float = 300.0) -> None:
        if event_max_age_seconds <= 0:
            raise ValueError("event_max_age_seconds must be positive")
        self._event_max_age_seconds = float(event_max_age_seconds)

    def evaluate(self, snapshot: MarketSnapshot) -> ShadowEvaluation:
        """Return evidence only.  This function has no side effects or routing."""
        timeframe, bars, analysis = _analysis_input(snapshot)
        evidence = (
            _market_analysis(snapshot, timeframe, bars, analysis),
            _trend(snapshot),
            _trend_1h(snapshot),
            _liquidity(snapshot, analysis),
            _volume(snapshot, bars, analysis),
            _volatility(snapshot, analysis),
            _events(snapshot, max_age_seconds=self._event_max_age_seconds),
            _session(snapshot),
        )
        return ShadowEvaluation(
            snapshot_id=snapshot.snapshot_id,
            evidence=evidence,
            action=DecisionAction.WAIT,
            execution_eligible=False,
        )
