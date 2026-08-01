"""Non-blocking bridge from closed paper-engine bars to V2 shadow records."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Sequence

from .confidence import ConfidenceComposer
from .contracts import DecisionAction, MarketSnapshot, RiskVerdict
from .persistence import ShadowDecisionStore
from .risk import RiskBridge
from .shadow import ShadowEvidenceRunner
from .strategy import proposal_from_signal


class CoreV2ShadowObserver:
    """Observe existing strategy cycles without changing their control flow."""

    def __init__(self, store: ShadowDecisionStore, runner: ShadowEvidenceRunner | None = None) -> None:
        self._store = store
        self._runner = runner or ShadowEvidenceRunner()

    def observe(self, *, symbol: str, timeframe: str, bars: Sequence[Any],
                as_of: datetime, source: str = "paper-engine", signal: Any = None,
                strategy_id: str = "legacy", strategy_version: str = "legacy-adapter-1",
                risk_context: Any = None, risk_engine: Any = None) -> dict[str, Any]:
        """Persist evidence from already-closed bars; no order can result."""
        if not bars:
            raise ValueError("cannot observe a cycle with no closed bars")
        snapshot = MarketSnapshot(
            snapshot_id=f"cycle_{symbol}_{timeframe}_{as_of.isoformat()}",
            symbol=symbol,
            as_of=as_of.astimezone(timezone.utc),
            bars_by_timeframe={timeframe: tuple(bars)},
            source=source,
            metadata={"observation": "automatic paper-engine shadow cycle"},
        )
        evaluation = self._runner.evaluate(snapshot)
        if signal is not None:
            try:
                proposal = proposal_from_signal(
                    signal, strategy_id=strategy_id, strategy_version=strategy_version,
                    timeframe=timeframe, evidence_ids=(snapshot.snapshot_id,))
                trend = evaluation.by_engine().get("trend")
                confidence = ConfidenceComposer().compose(
                    strategy_confidence=getattr(signal, "confidence", None),
                    trade_quality=None,
                    mtf_trend=trend.confidence if trend is not None else None,
                )
                risk = RiskBridge(risk_engine).assess(proposal, risk_context)
                if risk.verdict is RiskVerdict.VETO:
                    action, rationale = DecisionAction.IGNORE, (f"Risk veto: {risk.reason}",)
                elif confidence.score < 50:
                    action, rationale = DecisionAction.WAIT, (f"Confidence {confidence.score}/100 below shadow threshold 50.",)
                else:
                    action = DecisionAction.BUY if proposal.direction.value == "long" else DecisionAction.SELL
                    rationale = (f"Shadow proposal passes risk with confidence {confidence.score}/100.",)
                evaluation = replace(evaluation, action=action, proposal=proposal,
                                     confidence_assessment=confidence, risk_assessment=risk,
                                     rationale=rationale)
            except ValueError as exc:
                evaluation = replace(evaluation, action=DecisionAction.IGNORE,
                                     rationale=(f"Invalid strategy proposal: {exc}",))
        return self._store.record(symbol=symbol, evaluation=evaluation)
