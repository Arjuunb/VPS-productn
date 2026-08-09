"""Position sizing — wraps the engine's risk-based sizing.

Risk a fixed % of equity per trade based on stop distance, reusing the tested
``bot.risk.RiskManager`` logic via a thin functional entry point.
"""
from __future__ import annotations

from database.models import RiskRules
from tradexa.risk.position_sizing import (
    DYNAMIC_CURRENT_EQUITY_PERCENT, InstrumentMetadata, PositionSizingRequest,
    PositionSizingService,
)


def size_position(equity: float, entry: float, stop: float,
                  rules: RiskRules) -> float:
    """Return position quantity, capped by per-trade risk and max notional."""
    result = PositionSizingService.calculate(PositionSizingRequest(
        mode=DYNAMIC_CURRENT_EQUITY_PERCENT, entry_price=entry,
        stop_price=stop, starting_equity=equity,
        current_realized_equity=equity,
        risk_per_trade_pct=rules.risk_per_trade_pct,
        profit_reinvestment=True,
        instrument=InstrumentMetadata(maximum_notional=equity * 0.25),
    ))
    qty = result.quantity if result.approved else 0.0
    return max(qty, 0.0)
