"""Typed shadow adapter for the existing standalone risk engine.

The shared ``tradexa.risk.RiskEngine`` remains the sole policy calculator.
This module only replaces its proposal with the validated V2 proposal and
converts its complete result trail into immutable V2 contract types. It cannot
route an order or change any current pipeline setting.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from .contracts import RiskAssessment, RiskCheck, RiskVerdict, StrategyProposal

_VERSION = "core-v2-risk-1"


def _veto(*, policy_name: str, reason: str, rule: str) -> RiskAssessment:
    return RiskAssessment(
        verdict=RiskVerdict.VETO,
        policy_name=policy_name,
        primary_rule=rule,
        reason=reason,
        checks=(RiskCheck(rule=rule, passed=False, detail=reason),),
    )


class RiskBridge:
    """Make the current pure risk engine a mandatory V2 shadow authority."""

    def __init__(self, risk_engine: Optional[Any]) -> None:
        # ``None`` is accepted solely to represent an unavailable dependency in
        # a testable way. ``assess`` fails closed; it never returns an allow.
        self._risk_engine = risk_engine

    def assess(self, proposal: StrategyProposal, context: Any) -> RiskAssessment:
        """Evaluate the exact proposal that a future executor would receive.

        ``context`` is the existing ``tradexa.risk.RiskContext`` assembled from
        the real account, positions, controls and market state. Its embedded
        proposal is replaced to prevent callers accidentally assessing one
        order and later executing another.
        """
        if self._risk_engine is None:
            return _veto(policy_name="unavailable", rule="risk_policy_unavailable",
                         reason="Risk policy is unavailable — refusing the proposal.")
        try:
            from tradexa.risk import Direction, TradeProposal
            direction = Direction(proposal.direction.value)
            risk_proposal = TradeProposal(
                symbol=proposal.symbol, direction=direction, entry=proposal.entry,
                stop=proposal.stop_loss, target=proposal.take_profit,
                strategy_id=proposal.strategy_id,
            )
            # V2 confidence is introduced separately and does not yet change
            # legacy paper sizing. Preserve the existing risk-context conviction
            # when one was supplied, bounded by its risk model's own contract.
            existing_confidence = getattr(getattr(context, "proposal", None), "confidence", 1.0)
            risk_proposal = replace(risk_proposal, confidence=float(existing_confidence))
            evaluated_context = replace(context, proposal=risk_proposal)
            decision = self._risk_engine.evaluate(evaluated_context)
        except Exception as exc:  # noqa: BLE001 -- a missing risk dependency must fail closed
            return _veto(
                policy_name=getattr(getattr(self._risk_engine, "limits", None), "name", "unavailable"),
                rule="risk_policy_error",
                reason=f"Risk policy error ({type(exc).__name__}) — refusing the proposal.",
            )

        checks = tuple(
            RiskCheck(rule=str(check.rule), passed=bool(check.passed), detail=str(check.detail))
            for check in getattr(decision, "checks", ())
        )
        policy_name = str(getattr(decision, "limits_name", "") or
                          getattr(getattr(self._risk_engine, "limits", None), "name", "unknown"))
        if not bool(getattr(decision, "approved", False)):
            return RiskAssessment(
                verdict=RiskVerdict.VETO,
                policy_name=policy_name,
                primary_rule=getattr(decision, "rule", None),
                reason=str(getattr(decision, "reason", "Risk policy vetoed the proposal.")),
                checks=checks,
            )
        return RiskAssessment(
            verdict=RiskVerdict.ALLOW,
            policy_name=policy_name,
            reason=str(getattr(decision, "reason", "approved")),
            checks=checks,
            quantity=float(getattr(decision, "quantity", 0.0)),
            risk_amount=float(getattr(decision, "risk_amount", 0.0)),
            risk_pct=float(getattr(decision, "risk_pct", 0.0)),
        )
