"""Adapters that turn existing strategy signals into validated V2 proposals.

These helpers are deliberately non-executing.  They only validate and describe
the exact entry/stop/target already emitted by an existing strategy.
"""
from __future__ import annotations

from typing import Any, Iterable

from .contracts import StrategyProposal, TradeDirection


def proposal_from_signal(
    signal: Any,
    *,
    strategy_id: str,
    strategy_version: str,
    timeframe: str,
    evidence_ids: Iterable[str] = (),
) -> StrategyProposal:
    """Adapt a legacy ``bot.types.Signal`` without altering its contents.

    A legacy signal's stop is the concrete invalidation level.  We state that
    relationship rather than fabricate a separate technical invalidation rule.
    Invalid legacy signals fail validation before any later risk phase sees one.
    """
    signal_type = str(getattr(signal, "type", "")).lower()
    if signal_type.endswith("long"):
        direction = TradeDirection.LONG
    elif signal_type.endswith("short"):
        direction = TradeDirection.SHORT
    else:
        raise ValueError("signal type must be LONG or SHORT")
    entry = float(getattr(signal, "entry"))
    stop = float(getattr(signal, "stop_loss"))
    target = float(getattr(signal, "take_profit"))
    rr = abs(target - entry) / abs(entry - stop) if entry != stop else 0.0
    invalidation = (
        f"Price trades at or below {stop:.8g}." if direction is TradeDirection.LONG
        else f"Price trades at or above {stop:.8g}."
    )
    return StrategyProposal(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        symbol=str(getattr(signal, "symbol", "")),
        timeframe=timeframe,
        direction=direction,
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        invalidation=invalidation,
        planned_rr=rr,
        rationale=str(getattr(signal, "reason", "") or "Legacy strategy signal."),
        evidence_ids=tuple(evidence_ids),
    )
