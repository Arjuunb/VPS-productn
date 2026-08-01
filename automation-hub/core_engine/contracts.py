"""Immutable contracts for V2 analysis running in shadow mode.

The contracts deliberately accept the repository's existing ``Bar`` objects
without importing them.  This keeps domain analysis independent of a data
provider and lets the eventual snapshot assembler choose the appropriate
adapter.  These types are not yet part of the execution path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Optional, Sequence


class EvidenceStatus(str, Enum):
    """The honest state of a piece of market evidence."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"
    VETO = "VETO"


class DecisionAction(str, Enum):
    """V2 decision vocabulary; execution is intentionally separate."""

    BUY = "BUY"
    SELL = "SELL"
    WAIT = "WAIT"
    IGNORE = "IGNORE"


class TradeDirection(str, Enum):
    """Direction of a proposed trade, independent of an execution action."""

    LONG = "long"
    SHORT = "short"


class RiskVerdict(str, Enum):
    """The mandatory risk authority's outcome for a proposed entry."""

    ALLOW = "ALLOW"
    VETO = "VETO"


def _utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Prevent accidental mutation of a top-level contract payload."""
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class MarketSnapshot:
    """The complete, closed-candle input to one shadow evaluation.

    ``bars_by_timeframe`` is intentionally opaque to this module: adapters own
    bar construction, while evidence engines only require OHLCV-like objects.
    All timestamps must represent data known at or before ``as_of``.
    """

    snapshot_id: str
    symbol: str
    as_of: datetime
    bars_by_timeframe: Mapping[str, Sequence[Any]]
    events: Sequence[Mapping[str, Any]] = ()
    event_calendar_connected: Optional[bool] = None
    event_fetched_at: Optional[datetime] = None
    source: str = "unknown"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id is required")
        if not self.symbol.strip():
            raise ValueError("symbol is required")
        _utc(self.as_of, field_name="as_of")
        if self.event_fetched_at is not None:
            _utc(self.event_fetched_at, field_name="event_fetched_at")
        normalized = {str(tf).lower(): tuple(bars)
                      for tf, bars in self.bars_by_timeframe.items()}
        if any(not tf for tf in normalized):
            raise ValueError("timeframe names must not be empty")
        object.__setattr__(self, "bars_by_timeframe", _frozen_mapping(normalized))
        object.__setattr__(self, "events", tuple(dict(event) for event in self.events))
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True)
class Evidence:
    """A versioned, inspectable result from exactly one analysis engine."""

    engine: str
    version: str
    status: EvidenceStatus
    as_of: datetime
    facts: Mapping[str, Any] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    confidence: Optional[float] = None
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.engine.strip():
            raise ValueError("engine is required")
        if not self.version.strip():
            raise ValueError("version is required")
        _utc(self.as_of, field_name="as_of")
        if self.confidence is not None and not 0.0 <= self.confidence <= 100.0:
            raise ValueError("confidence must be between 0 and 100")
        object.__setattr__(self, "facts", _frozen_mapping(self.facts))
        object.__setattr__(self, "reasons", tuple(str(reason) for reason in self.reasons))
        object.__setattr__(self, "blockers", tuple(str(blocker) for blocker in self.blockers))
        object.__setattr__(self, "source_ids", tuple(str(source) for source in self.source_ids))


@dataclass(frozen=True)
class StrategyProposal:
    """A fully specified, but non-executable, strategy trade proposal.

    The proposal defines the same entry, stop and target that a future risk
    assessment would receive.  It intentionally carries no position size or
    execution permission; only the risk/execution phases can add those.
    """

    strategy_id: str
    strategy_version: str
    symbol: str
    timeframe: str
    direction: TradeDirection
    entry: float
    stop_loss: float
    take_profit: float
    invalidation: str
    planned_rr: float
    rationale: str
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (("strategy_id", self.strategy_id),
                            ("strategy_version", self.strategy_version),
                            ("symbol", self.symbol), ("timeframe", self.timeframe),
                            ("invalidation", self.invalidation), ("rationale", self.rationale)):
            if not str(value).strip():
                raise ValueError(f"{name} is required")
        if self.entry <= 0 or self.stop_loss <= 0 or self.take_profit <= 0:
            raise ValueError("entry, stop_loss and take_profit must be positive")
        if self.direction is TradeDirection.LONG:
            if self.stop_loss >= self.entry:
                raise ValueError("a long stop_loss must be below entry")
            if self.take_profit <= self.entry:
                raise ValueError("a long take_profit must be above entry")
        else:
            if self.stop_loss <= self.entry:
                raise ValueError("a short stop_loss must be above entry")
            if self.take_profit >= self.entry:
                raise ValueError("a short take_profit must be below entry")
        expected_rr = abs(self.take_profit - self.entry) / abs(self.entry - self.stop_loss)
        if self.planned_rr <= 0:
            raise ValueError("planned_rr must be positive")
        if abs(self.planned_rr - expected_rr) > 1e-6:
            raise ValueError("planned_rr must match entry, stop_loss and take_profit")
        object.__setattr__(self, "evidence_ids", tuple(str(item) for item in self.evidence_ids))


@dataclass(frozen=True)
class RiskCheck:
    """One rule result retained in a V2 risk audit trail."""

    rule: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class RiskAssessment:
    """Risk's non-bypassable verdict for a strategy proposal.

    ``ALLOW`` is still not an execution instruction. It says only that the
    supplied proposal and account context passed the stated policy. A missing
    or failed risk dependency is represented as ``VETO``.
    """

    verdict: RiskVerdict
    policy_name: str
    reason: str
    checks: tuple[RiskCheck, ...] = ()
    quantity: float = 0.0
    risk_amount: float = 0.0
    risk_pct: float = 0.0
    primary_rule: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.policy_name.strip():
            raise ValueError("policy_name is required")
        if not self.reason.strip():
            raise ValueError("reason is required")
        if self.verdict is RiskVerdict.VETO and (self.quantity != 0 or self.risk_amount != 0):
            raise ValueError("a veto must not carry executable size or risk")
        if self.verdict is RiskVerdict.ALLOW and self.quantity <= 0:
            raise ValueError("an allow assessment requires a positive quantity")
        if self.risk_amount < 0 or self.risk_pct < 0:
            raise ValueError("risk amounts must not be negative")
        object.__setattr__(self, "checks", tuple(self.checks))


@dataclass(frozen=True)
class ShadowEvaluation:
    """Stored/comparable V2 evidence; it carries no execution authority."""

    snapshot_id: str
    evidence: tuple[Evidence, ...]
    action: DecisionAction = DecisionAction.WAIT
    execution_eligible: bool = False
    proposal: Optional[StrategyProposal] = None
    confidence_assessment: Optional[Any] = None
    risk_assessment: Optional[RiskAssessment] = None
    rationale: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id is required")
        if self.execution_eligible:
            raise ValueError("shadow evaluations must never be execution eligible")
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "rationale", tuple(str(item) for item in self.rationale))

    def by_engine(self) -> Mapping[str, Evidence]:
        """Convenience read model; duplicate engine names are a programming error."""
        indexed = {item.engine: item for item in self.evidence}
        if len(indexed) != len(self.evidence):
            raise ValueError("shadow evaluation contains duplicate engine evidence")
        return MappingProxyType(indexed)
