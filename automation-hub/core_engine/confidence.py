"""Fixed and explainable confidence composition for V2 shadow evaluations.

This module has no adaptive weights and no learned side effects.  A missing
input contributes zero to its declared weight, so unavailable data can never
raise confidence by causing the remaining inputs to be re-normalised.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Optional


_DEFAULT_WEIGHTS = {
    "strategy_conviction": 45.0,
    "trade_quality": 35.0,
    "mtf_trend": 20.0,
}


@dataclass(frozen=True)
class ConfidenceContribution:
    """One fixed-weight input to a confidence assessment."""

    name: str
    weight: float
    source_score: Optional[float]
    contribution: float
    reason: str


@dataclass(frozen=True)
class ConfidenceAssessment:
    """A 0–100 score and its unambiguous contribution breakdown."""

    score: float
    level: str
    contributions: tuple[ConfidenceContribution, ...]
    policy_version: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 100.0:
            raise ValueError("confidence score must be between 0 and 100")
        if self.level not in {"low", "medium", "high"}:
            raise ValueError("confidence level must be low, medium or high")

    def by_name(self) -> Mapping[str, ConfidenceContribution]:
        return MappingProxyType({item.name: item for item in self.contributions})


class ConfidenceComposer:
    """Compose fixed inputs without allowing missing data to inflate a score."""

    def __init__(self, *, weights: Mapping[str, float] = _DEFAULT_WEIGHTS,
                 policy_version: str = "core-v2-confidence-1") -> None:
        clean = {str(name): float(weight) for name, weight in weights.items()}
        if not clean or any(weight < 0 for weight in clean.values()):
            raise ValueError("confidence weights must be non-negative and non-empty")
        if abs(sum(clean.values()) - 100.0) > 1e-9:
            raise ValueError("confidence weights must sum to 100")
        if not policy_version.strip():
            raise ValueError("policy_version is required")
        self._weights = MappingProxyType(clean)
        self._policy_version = policy_version

    def compose(self, *, strategy_confidence: Optional[float],
                trade_quality: Optional[float], mtf_trend: Optional[float]) -> ConfidenceAssessment:
        """Build a deterministic assessment from real source scores.

        ``strategy_confidence`` is the legacy 0–1 signal conviction. The other
        inputs are 0–100 scores (TradeBrain and MTF respectively).  Values are
        range-checked instead of clipped so bad upstream data remains visible.
        """
        scores = {
            "strategy_conviction": None if strategy_confidence is None else strategy_confidence * 100.0,
            "trade_quality": trade_quality,
            "mtf_trend": mtf_trend,
        }
        contributions: list[ConfidenceContribution] = []
        total = 0.0
        for name, weight in self._weights.items():
            score = scores.get(name)
            if score is None:
                contribution, reason = 0.0, "Unavailable — contributes zero by policy."
            else:
                score = float(score)
                if not 0.0 <= score <= 100.0:
                    raise ValueError(f"{name} score must be between 0 and 100")
                contribution = weight * score / 100.0
                reason = f"Source score {score:.1f}/100 at fixed weight {weight:.1f}."
            total += contribution
            contributions.append(ConfidenceContribution(name, weight, score, contribution, reason))
        total = round(total, 2)
        level = "high" if total >= 75.0 else "medium" if total >= 50.0 else "low"
        return ConfidenceAssessment(total, level, tuple(contributions), self._policy_version)
