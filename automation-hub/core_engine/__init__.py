"""Typed, shadow-mode building blocks for the Core Trading Engine V2.

Nothing in this package routes an order.  It is deliberately introduced beside
the established paper-trading path so evidence can be compared before V2 is
allowed to influence a decision.
"""

from .contracts import (
    DecisionAction,
    Evidence,
    EvidenceStatus,
    MarketSnapshot,
    RiskAssessment,
    RiskCheck,
    RiskVerdict,
    ShadowEvaluation,
    StrategyProposal,
    TradeDirection,
)
from .confidence import ConfidenceAssessment, ConfidenceComposer
from .persistence import ShadowDecisionStore
from .observer import CoreV2ShadowObserver
from .risk import RiskBridge
from .shadow import ShadowEvidenceRunner
from .strategy import proposal_from_signal

__all__ = [
    "DecisionAction",
    "Evidence",
    "EvidenceStatus",
    "MarketSnapshot",
    "RiskAssessment",
    "RiskCheck",
    "RiskVerdict",
    "ShadowEvaluation",
    "StrategyProposal",
    "TradeDirection",
    "ConfidenceAssessment",
    "ConfidenceComposer",
    "ShadowDecisionStore",
    "CoreV2ShadowObserver",
    "RiskBridge",
    "ShadowEvidenceRunner",
    "proposal_from_signal",
]
