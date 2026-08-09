"""Deterministic position sizing shared by paper trading and backtests.

The service accepts realized equity explicitly. It never reads mark-to-market
equity, a database, or strategy state, preventing unrealized profit from
increasing the next trade's risk budget.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional

FIXED_QUANTITY = "fixed_quantity"
FIXED_STARTING_EQUITY_PERCENT = "fixed_starting_equity_percent"
DYNAMIC_CURRENT_EQUITY_PERCENT = "dynamic_current_equity_percent"
SIZING_ENGINE_VERSION = "v2"
SIZING_MODES = (FIXED_QUANTITY, FIXED_STARTING_EQUITY_PERCENT, DYNAMIC_CURRENT_EQUITY_PERCENT)


def normalize_sizing_mode(value: object) -> str:
    """Map legacy instance values without silently enabling compounding."""
    raw = str(value or "").strip().lower()
    return {
        "fixed": FIXED_QUANTITY,
        "fixed_position": FIXED_QUANTITY,
        "auto": FIXED_STARTING_EQUITY_PERCENT,
        "fixed_starting_equity_pct": FIXED_STARTING_EQUITY_PERCENT,
        "dynamic": DYNAMIC_CURRENT_EQUITY_PERCENT,
        "dynamic_current_equity_pct": DYNAMIC_CURRENT_EQUITY_PERCENT,
    }.get(raw, raw or FIXED_STARTING_EQUITY_PERCENT)


@dataclass(frozen=True)
class InstrumentMetadata:
    contract_multiplier: float = 1.0
    quantity_step: float = 0.0
    minimum_quantity: float = 0.0
    maximum_quantity: Optional[float] = None
    minimum_stop_distance: float = 0.0
    minimum_notional: float = 0.0
    maximum_notional: Optional[float] = None
    available_margin: Optional[float] = None
    leverage: float = 1.0


@dataclass(frozen=True)
class PositionSizingRequest:
    mode: str
    entry_price: float
    stop_price: float
    starting_equity: float
    current_realized_equity: float
    risk_per_trade_pct: float
    fixed_quantity: float = 0.0
    profit_reinvestment: bool = False
    maximum_risk_amount: Optional[float] = None
    minimum_equity: Optional[float] = None
    instrument: InstrumentMetadata = InstrumentMetadata()


@dataclass(frozen=True)
class PositionSizingResult:
    approved: bool
    quantity: float
    risk_basis: float
    risk_amount: float
    stop_distance: float
    mode: str
    reason: str = ""
    sizing_engine_version: str = SIZING_ENGINE_VERSION


class PositionSizingService:
    """Calculate entry quantity under one explicit, persisted sizing policy."""

    @staticmethod
    def risk_basis(*, mode: str, starting_equity: float,
                   current_realized_equity: float,
                   profit_reinvestment: bool) -> float:
        canonical = normalize_sizing_mode(mode)
        if canonical == FIXED_STARTING_EQUITY_PERCENT:
            return float(starting_equity)
        if canonical == DYNAMIC_CURRENT_EQUITY_PERCENT:
            # Reinvestment off freezes upside but losses still reduce risk.
            return (float(current_realized_equity) if profit_reinvestment
                    else min(float(starting_equity), float(current_realized_equity)))
        return float(current_realized_equity)

    @classmethod
    def risk_budget(cls, *, mode: str, starting_equity: float,
                    current_realized_equity: float, risk_per_trade_pct: float,
                    profit_reinvestment: bool,
                    maximum_risk_amount: Optional[float] = None) -> tuple[float, float]:
        basis = cls.risk_basis(mode=mode, starting_equity=starting_equity,
                               current_realized_equity=current_realized_equity,
                               profit_reinvestment=profit_reinvestment)
        amount = max(0.0, basis * float(risk_per_trade_pct))
        if maximum_risk_amount is not None and maximum_risk_amount > 0:
            amount = min(amount, float(maximum_risk_amount))
        return basis, amount

    @staticmethod
    def _round_down(quantity: float, step: float) -> float:
        if step <= 0:
            return quantity
        q, s = Decimal(str(quantity)), Decimal(str(step))
        return float((q / s).to_integral_value(rounding=ROUND_DOWN) * s)

    @classmethod
    def calculate(cls, request: PositionSizingRequest) -> PositionSizingResult:
        mode = normalize_sizing_mode(request.mode)
        if mode not in SIZING_MODES:
            return PositionSizingResult(False, 0, 0, 0, 0, mode, "unsupported sizing mode")
        current, starting = float(request.current_realized_equity), float(request.starting_equity)
        if starting <= 0 or current <= 0:
            return PositionSizingResult(False, 0, current, 0, 0, mode, "equity must be positive")
        if request.minimum_equity is not None and current < float(request.minimum_equity):
            return PositionSizingResult(False, 0, current, 0, 0, mode, "instance equity floor reached")
        entry, stop = float(request.entry_price), float(request.stop_price)
        distance = abs(entry - stop)
        if entry <= 0 or stop <= 0 or distance <= max(0.0, float(request.instrument.minimum_stop_distance)):
            return PositionSizingResult(False, 0, current, 0, distance, mode, "stop distance is missing or too small")
        multiplier = float(request.instrument.contract_multiplier)
        if multiplier <= 0:
            return PositionSizingResult(False, 0, current, 0, distance, mode, "invalid contract multiplier")
        basis, budget = cls.risk_budget(mode=mode, starting_equity=starting,
                                        current_realized_equity=current,
                                        risk_per_trade_pct=request.risk_per_trade_pct,
                                        profit_reinvestment=request.profit_reinvestment,
                                        maximum_risk_amount=request.maximum_risk_amount)
        if mode == FIXED_QUANTITY:
            quantity = float(request.fixed_quantity)
            budget = quantity * distance * multiplier
            if request.maximum_risk_amount is not None and request.maximum_risk_amount > 0 and budget > float(request.maximum_risk_amount) + 1e-12:
                return PositionSizingResult(False, 0, basis, budget, distance, mode, "fixed quantity exceeds maximum risk amount")
        else:
            if not 0 < float(request.risk_per_trade_pct) <= 1 or budget <= 0:
                return PositionSizingResult(False, 0, basis, budget, distance, mode, "risk percentage must be positive")
            quantity = budget / (distance * multiplier)
        quantity = cls._round_down(quantity, float(request.instrument.quantity_step))
        if quantity <= 0 or quantity < float(request.instrument.minimum_quantity):
            return PositionSizingResult(False, 0, basis, budget, distance, mode, "calculated quantity is below the minimum")
        if request.instrument.maximum_quantity is not None and quantity > float(request.instrument.maximum_quantity):
            return PositionSizingResult(False, 0, basis, budget, distance, mode, "calculated quantity exceeds the maximum")
        notional = quantity * entry * multiplier
        if notional < float(request.instrument.minimum_notional):
            return PositionSizingResult(False, 0, basis, budget, distance, mode, "calculated notional is below the minimum")
        if request.instrument.maximum_notional is not None and notional > float(request.instrument.maximum_notional):
            return PositionSizingResult(False, 0, basis, budget, distance, mode, "calculated notional exceeds the maximum")
        required_margin = notional / max(1.0, float(request.instrument.leverage))
        if request.instrument.available_margin is not None and required_margin > float(request.instrument.available_margin):
            return PositionSizingResult(False, 0, basis, budget, distance, mode, "insufficient available capital")
        actual_risk = quantity * distance * multiplier
        return PositionSizingResult(True, quantity, basis, actual_risk, distance, mode)
