"""Canonical, PAPER-only lifecycle helpers shared by the independent labs."""
from __future__ import annotations

import hashlib
import json
from typing import Iterable


LAB_STATES = (
    "DISCONNECTED", "SYNCING", "READY", "WATCHING", "SIGNAL_FOUND",
    "RISK_REJECTED", "ORDER_SUBMITTED", "FILLED", "POSITION_OPEN",
    "EXITED", "ERROR",
)


def correlation_id(*, lab: str, session_id: str, strategy_id: str,
                   symbol: str, timeframe: str, candle_time: str) -> str:
    """Return one deterministic identity for a closed-candle decision chain."""
    raw = "|".join((lab, session_id, strategy_id, symbol.upper(), timeframe, candle_time))
    return f"{lab.lower()}-{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def decision_idempotency_key(*, strategy_id: str, symbol: str,
                             timeframe: str, candle_time: str) -> str:
    """The execution key excludes mutable UI/session presentation state."""
    raw = "|".join((strategy_id, symbol.upper(), timeframe, candle_time))
    return f"decision-{hashlib.sha256(raw.encode()).hexdigest()}"


def lifecycle_state(*, connection_state: str, reliable: bool,
                    has_position: bool = False, has_order: bool = False,
                    last_decision_state: str | None = None) -> str:
    """Project factual feed/execution evidence onto the canonical state machine."""
    connection = str(connection_state or "DISCONNECTED").upper()
    if connection == "DISCONNECTED":
        return "DISCONNECTED"
    if connection in {"ERROR", "DATA_ERROR"}:
        return "ERROR"
    if not reliable:
        return "SYNCING"
    if has_position:
        return "POSITION_OPEN"
    if has_order:
        return "ORDER_SUBMITTED"
    decision = str(last_decision_state or "").upper()
    aliases = {
        "REJECTED": "RISK_REJECTED", "DATA_PAUSED": "RISK_REJECTED",
        "SIGNAL_ONLY": "SIGNAL_FOUND", "PENDING_APPROVAL": "SIGNAL_FOUND",
        "APPROVED_AUTOMATIC": "SIGNAL_FOUND", "ORDER_CREATED": "ORDER_SUBMITTED",
        "ORDER_PENDING": "ORDER_SUBMITTED", "ENTERED": "POSITION_OPEN",
        "COMPLETED": "EXITED", "TARGET_HIT": "EXITED", "STOPPED": "EXITED",
        "WATCHING": "WATCHING", "READY": "READY",
    }
    return aliases.get(decision, "WATCHING" if last_decision_state else "READY")


def blockers(*, connection: dict, operating_mode: str, account: dict,
             strategy_valid: bool, positions: Iterable[dict],
             pending_orders: Iterable[dict] = (),
             risk_pct: float | None = None, max_risk_pct: float = 1.0) -> list[str]:
    """Return explicit execution blockers without granting execution authority."""
    rows: list[str] = []
    if not connection.get("reliable"):
        rows.append(str(connection.get("health_reason") or "market data is not synchronized"))
    if operating_mode != "automatic":
        rows.append("saved operating mode is not Automatic paper")
    if float(account.get("balance") or 0) <= 0:
        rows.append("virtual balance must be positive")
    if float(account.get("free_margin", account.get("available_margin", 0)) or 0) <= 0:
        rows.append("available paper margin must be positive")
    if not strategy_valid:
        rows.append("saved strategy or version is invalid")
    if risk_pct is None or not 0 < float(risk_pct) <= float(max_risk_pct):
        rows.append(f"saved risk percentage must be above 0% and no greater than {max_risk_pct:g}%")
    for position in positions:
        if position.get("protection_status") != "PROTECTED":
            rows.append(
                f"{position.get('symbol', 'position')} is LEGACY / UNPROTECTED and requires explicit repair or close"
            )
        else:
            rows.append(
                f"{position.get('symbol', 'position')} protected paper position is already open; new entries are blocked"
            )
    if any(not row.get("reduce_only") for row in pending_orders):
        rows.append("a pending paper entry order already exists; new entries are blocked")
    return list(dict.fromkeys(rows))


def paper_performance(*, fills: list[dict], account: dict,
                      realized_r_values: Iterable[float] = (),
                      orders: Iterable[dict] = ()) -> dict:
    """Calculate honest live-paper metrics from persisted exit evidence only.

    Reduce-only/protective fills identify an exit even when it closes exactly at
    break-even.  The aggregate net uses the account ledger so entry fees and
    funding are not accidentally omitted.
    """
    order_by_id = {str(row.get("id")): row for row in orders}
    exits = [row for row in fills if
             str(row.get("order_id") or "").startswith("protective-") or
             bool(order_by_id.get(str(row.get("order_id")), {}).get("reduce_only")) or
             abs(float(row.get("realized_pnl") or 0)) > 1e-12]
    net_values = [float(row.get("realized_pnl") or 0) - float(row.get("fee") or 0)
                  for row in exits]
    wins = [value for value in net_values if value > 0]
    losses = [value for value in net_values if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    rr = [float(value) for value in realized_r_values if value is not None]

    def longest(winning: bool) -> int:
        best = run = 0
        for value in reversed(net_values):
            matches = value > 0 if winning else value < 0
            run = run + 1 if matches else 0
            best = max(best, run)
        return best
    fees = float(account.get("fees_paid") or 0)
    funding = float(account.get("funding_paid") or 0)
    aggregate_net = float(account.get("realized_pnl") or 0) - fees - funding
    return {
        "scope": "LIVE_PAPER", "closed_trades": len(net_values),
        "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(net_values) if net_values else None,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "net_pnl": aggregate_net,
        "profit_factor": gross_profit / gross_loss if gross_loss else None,
        "expectancy": aggregate_net / len(net_values) if net_values else None,
        "average_win": gross_profit / len(wins) if wins else None,
        "average_loss": -gross_loss / len(losses) if losses else None,
        "maximum_consecutive_wins": longest(True),
        "maximum_consecutive_losses": longest(False),
        "maximum_drawdown": float(account.get("max_drawdown") or 0),
        "average_realized_rr": sum(rr) / len(rr) if rr else None,
        "fees": fees, "funding": funding,
        "slippage_model": "PaperBrokerV2 adverse spread plus configured slippage",
        "reconciled_completed_trades_only": True,
        "evidence_note": (
            "Closed-trade count follows persisted exit fills; staged scale-outs are separate exit legs. "
            "Aggregate net includes all account entry/exit fees and funding."
        ),
    }


def unavailable_performance(scope: str, reason: str) -> dict:
    return {"scope": scope, "available": False, "reason": reason,
            "combined_with_live_paper": False}


def journal_performance(scope: str, entries: Iterable[dict]) -> dict:
    """Summarize one immutable research partition without mixing scopes."""
    rows = list(entries)
    closed = [row for row in rows if (row.get("outcome") or {}).get("net_r") is not None]
    values = [float(row["outcome"]["net_r"]) for row in closed]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_loss = abs(sum(losses))
    return {
        "scope": scope, "available": bool(rows), "setups": len(rows),
        "closed_trades": len(closed), "wins": len(wins), "losses": len(losses),
        "win_rate": len(wins) / len(closed) if closed else None,
        "net_r": sum(values),
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
        "average_realized_rr": sum(values) / len(values) if values else None,
        "fees_and_slippage_included": all(
            (row.get("outcome") or {}).get("costs_r") is not None for row in closed),
        "combined_with_live_paper": False,
    }


def json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, default=str)
