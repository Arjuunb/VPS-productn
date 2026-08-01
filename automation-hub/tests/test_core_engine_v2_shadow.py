"""Contract tests for the non-executing Core Engine V2 shadow foundation."""
from datetime import datetime, timedelta, timezone

import pytest

from bot.data.synthetic import generate_bars
from bot.types import Signal, SignalType
from core_engine import (ConfidenceComposer, Evidence, EvidenceStatus, MarketSnapshot,
                         RiskBridge, RiskVerdict, ShadowEvidenceRunner, TradeDirection,
                         proposal_from_signal)
from core_engine.api import snapshot_from_payload
from core_engine.persistence import ShadowDecisionStore
from core_engine.observer import CoreV2ShadowObserver
from tradexa.risk import (AccountState, Direction, MarketConditions, PIPELINE_PARITY,
                          RiskContext, RiskEngine, TradeProposal as RiskProposal)


NOW = datetime(2025, 1, 2, 12, 0, tzinfo=timezone.utc)


def _snapshot(**overrides):
    # The synthetic fixture deliberately has no weekly generator capability;
    # MTF analysis only needs an honest supplied sequence for its weekly slot.
    bars = {tf: generate_bars(n=140, timeframe=("1d" if tf == "1w" else tf), seed=i + 10)
            for i, tf in enumerate(("1w", "1d", "4h", "1h", "15m", "5m"))}
    params = {
        "snapshot_id": "snapshot-1",
        "symbol": "BTCUSDT",
        "as_of": NOW,
        "bars_by_timeframe": bars,
        "events": (),
        "event_calendar_connected": True,
        "event_fetched_at": NOW - timedelta(seconds=10),
        "source": "test",
    }
    params.update(overrides)
    return MarketSnapshot(**params)


def test_contracts_reject_naive_time_and_keep_top_level_maps_immutable():
    with pytest.raises(ValueError, match="timezone-aware"):
        MarketSnapshot("id", "BTCUSDT", datetime(2025, 1, 1), {})
    evidence = Evidence("test", "1", EvidenceStatus.PASS, NOW, facts={"a": 1})
    with pytest.raises(TypeError):
        evidence.facts["a"] = 2


def test_shadow_runner_produces_only_non_executable_evidence():
    result = ShadowEvidenceRunner().evaluate(_snapshot())
    assert result.execution_eligible is False
    assert result.action.value == "WAIT"
    assert set(result.by_engine()) == {
        "market_context", "trend", "trend_1h", "liquidity", "volume", "volatility",
        "news_event", "session",
    }
    assert all(item.source_ids == ("snapshot-1",) for item in result.evidence)


def test_shadow_runner_marks_missing_mtf_as_unavailable_without_faking_it():
    result = ShadowEvidenceRunner().evaluate(_snapshot(bars_by_timeframe={"5m": generate_bars(60, "5m", 1)}))
    trend = result.by_engine()["trend"]
    assert trend.status is EvidenceStatus.UNAVAILABLE
    assert "Missing required MTF bars" in trend.blockers[0]


def test_shadow_runner_vetoes_blackout_but_cannot_execute():
    result = ShadowEvidenceRunner().evaluate(_snapshot(events=(
        {"name": "CPI", "impact": "high", "time": (NOW + timedelta(minutes=10)).isoformat()},
    )))
    event = result.by_engine()["news_event"]
    assert event.status is EvidenceStatus.VETO
    assert result.execution_eligible is False


def test_shadow_runner_marks_stale_or_unconnected_calendar_unavailable():
    stale = ShadowEvidenceRunner().evaluate(_snapshot(event_fetched_at=NOW - timedelta(minutes=6)))
    disconnected = ShadowEvidenceRunner().evaluate(_snapshot(event_calendar_connected=False))
    assert stale.by_engine()["news_event"].status is EvidenceStatus.UNAVAILABLE
    assert disconnected.by_engine()["news_event"].status is EvidenceStatus.UNAVAILABLE


def test_phase4_context_evidence_is_explicit_about_available_and_missing_data():
    result = ShadowEvidenceRunner().evaluate(_snapshot(metadata={"bid": 99.9, "ask": 100.1}))
    hourly = result.by_engine()["trend_1h"]
    volume = result.by_engine()["volume"]
    volatility = result.by_engine()["volatility"]
    liquidity = result.by_engine()["liquidity"]
    session = result.by_engine()["session"]
    assert hourly.status is not EvidenceStatus.UNAVAILABLE
    assert volume.facts["order_flow_delta"] is None
    assert "not order-flow delta" in volume.reasons[1]
    assert volatility.facts["spread_status"] == "observed"
    assert liquidity.facts["criteria"].startswith("equal levels")
    assert session.facts["schedule"].startswith("08:00")


def test_phase4_context_marks_missing_hourly_data_unavailable_and_off_hours_poor():
    no_hourly = _snapshot(bars_by_timeframe={"5m": generate_bars(60, "5m", seed=3)})
    off_hours = _snapshot(as_of=datetime(2025, 1, 4, 3, 0, tzinfo=timezone.utc))  # Saturday
    no_hourly_result = ShadowEvidenceRunner().evaluate(no_hourly)
    off_hours_result = ShadowEvidenceRunner().evaluate(off_hours)
    assert no_hourly_result.by_engine()["trend_1h"].status is EvidenceStatus.UNAVAILABLE
    assert off_hours_result.by_engine()["session"].status is EvidenceStatus.FAIL
    assert off_hours_result.by_engine()["session"].facts["liquidity_quality"] == "poor"


def test_strategy_proposal_adapts_existing_signal_without_changing_it():
    signal = Signal(timestamp=NOW, symbol="BTCUSDT", type=SignalType.LONG,
                    entry=100.0, stop_loss=95.0, take_profit=115.0,
                    reason="existing strategy rationale")
    proposal = proposal_from_signal(signal, strategy_id="brain", strategy_version="1.0",
                                    timeframe="5m", evidence_ids=("snapshot-1",))
    assert proposal.direction is TradeDirection.LONG
    assert proposal.planned_rr == 3.0
    assert "95" in proposal.invalidation
    assert proposal.rationale == signal.reason


def test_strategy_proposal_rejects_stop_on_the_wrong_side():
    signal = Signal(timestamp=NOW, symbol="BTCUSDT", type=SignalType.LONG,
                    entry=100.0, stop_loss=101.0, take_profit=110.0, reason="bad")
    with pytest.raises(ValueError, match="below entry"):
        proposal_from_signal(signal, strategy_id="brain", strategy_version="1.0", timeframe="5m")


def test_confidence_has_fixed_weights_and_missing_data_cannot_inflate_score():
    assessment = ConfidenceComposer().compose(
        strategy_confidence=0.8, trade_quality=70.0, mtf_trend=90.0)
    assert assessment.score == 78.5 and assessment.level == "high"
    missing = ConfidenceComposer().compose(
        strategy_confidence=0.8, trade_quality=None, mtf_trend=90.0)
    assert missing.score == 54.0 and missing.level == "medium"
    assert missing.by_name()["trade_quality"].contribution == 0.0


def test_confidence_rejects_bad_source_scores_and_bad_weight_sets():
    with pytest.raises(ValueError, match="sum to 100"):
        ConfidenceComposer(weights={"strategy_conviction": 99.0})
    with pytest.raises(ValueError, match="between 0 and 100"):
        ConfidenceComposer().compose(strategy_confidence=1.1, trade_quality=50, mtf_trend=50)


def _proposal():
    signal = Signal(timestamp=NOW, symbol="BTCUSDT", type=SignalType.LONG,
                    entry=100.0, stop_loss=95.0, take_profit=115.0, reason="test")
    return proposal_from_signal(signal, strategy_id="brain", strategy_version="1.0", timeframe="5m")


def _risk_context(*, halted=False):
    return RiskContext(
        proposal=RiskProposal(symbol="placeholder", direction=Direction.LONG,
                              entry=1, stop=0.5, confidence=0.8),
        account=AccountState(equity=10_000, starting_equity=10_000, cash=10_000),
        market=MarketConditions(cluster="crypto"), now=NOW,
        kill_switch_engaged=halted, kill_switch_reason="operator halt" if halted else "",
    )


def test_risk_bridge_assesses_the_v2_proposal_and_preserves_full_rule_trail():
    assessment = RiskBridge(RiskEngine(PIPELINE_PARITY)).assess(_proposal(), _risk_context())
    assert assessment.verdict is RiskVerdict.ALLOW
    assert assessment.quantity > 0 and assessment.policy_name == "pipeline_parity"
    assert assessment.checks and all(check.rule for check in assessment.checks)


def test_risk_bridge_vetoes_halt_and_unavailable_policy_without_size():
    halted = RiskBridge(RiskEngine(PIPELINE_PARITY)).assess(_proposal(), _risk_context(halted=True))
    unavailable = RiskBridge(None).assess(_proposal(), _risk_context())
    assert halted.verdict is RiskVerdict.VETO and halted.primary_rule == "kill_switch"
    assert unavailable.verdict is RiskVerdict.VETO
    assert unavailable.primary_rule == "risk_policy_unavailable" and unavailable.quantity == 0


def _api_payload(**changes):
    start = NOW - timedelta(minutes=10)
    bars = []
    for index in range(3):
        price = 100.0 + index
        bars.append({"timestamp": (start + timedelta(minutes=5 * index)).isoformat(),
                     "open": price, "high": price + 1, "low": price - 1,
                     "close": price + 0.5, "volume": 1000 + index})
    payload = {"symbol": "btcusdt", "as_of": NOW.isoformat(),
               "bars_by_timeframe": {"5m": bars}, "event_calendar_connected": False}
    payload.update(changes)
    return payload


def test_api_snapshot_parser_rejects_future_or_malformed_bars_and_normalizes_symbol():
    snapshot = snapshot_from_payload(_api_payload())
    assert snapshot.symbol == "BTCUSDT" and len(snapshot.bars_by_timeframe["5m"]) == 3
    future = _api_payload()
    future["bars_by_timeframe"]["5m"][-1]["timestamp"] = (NOW + timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="must not be after"):
        snapshot_from_payload(future)
    malformed = _api_payload()
    malformed["bars_by_timeframe"]["5m"][0].pop("volume")
    with pytest.raises(ValueError, match="requires numeric"):
        snapshot_from_payload(malformed)


def test_shadow_store_persists_non_executable_evidence_and_summarizes_it(tmp_path):
    snapshot = _snapshot()
    evaluation = ShadowEvidenceRunner().evaluate(snapshot)
    store = ShadowDecisionStore(str(tmp_path / "v2.db"))
    saved = store.record(symbol=snapshot.symbol, evaluation=evaluation)
    assert saved["execution_eligible"] is False
    assert store.get(saved["id"])["snapshot_id"] == snapshot.snapshot_id
    assert store.latest(symbol="btcusdt")[0]["id"] == saved["id"]
    summary = store.summary()
    assert summary["mode"] == "shadow" and summary["total_decisions"] == 1
    assert "trend" in summary["engine_status_counts"]


def test_v2_router_evaluates_and_reads_shadow_records_only(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routers.core_v2 import create_router

    app = FastAPI()
    app.include_router(create_router(ShadowDecisionStore(str(tmp_path / "api.db"))))
    client = TestClient(app)
    created = client.post("/api/v2/decisions/evaluate", json=_api_payload()).json()
    assert created["action"] == "WAIT" and created["execution_eligible"] is False
    assert client.get(f"/api/v2/decisions/{created['id']}").status_code == 200
    assert client.get("/api/v2/health/engines").json()["execution_enabled"] is False


def test_automatic_observer_records_closed_paper_cycle_without_execution(tmp_path):
    store = ShadowDecisionStore(str(tmp_path / "observer.db"))
    bars = generate_bars(n=60, timeframe="1h", seed=42)
    record = CoreV2ShadowObserver(store).observe(
        symbol="BTCUSDT", timeframe="1h", bars=bars, as_of=bars[-1].timestamp,
    )
    assert record["execution_eligible"] is False and record["action"] == "WAIT"
    assert store.summary()["total_decisions"] == 1


def test_observer_links_signal_proposal_confidence_and_fail_closed_risk(tmp_path):
    store = ShadowDecisionStore(str(tmp_path / "linked.db"))
    bars = generate_bars(n=60, timeframe="1h", seed=41)
    signal = Signal(timestamp=bars[-1].timestamp, symbol="BTCUSDT", type=SignalType.LONG,
                    entry=100, stop_loss=95, take_profit=110, confidence=0.9, reason="test")
    record = CoreV2ShadowObserver(store).observe(
        symbol="BTCUSDT", timeframe="1h", bars=bars, as_of=bars[-1].timestamp,
        signal=signal, risk_context=None, risk_engine=None)
    assert record["proposal"]["direction"] == "long"
    assert record["confidence"] is not None
    assert record["risk"]["verdict"] == "VETO"
    assert record["action"] == "IGNORE" and record["execution_eligible"] is False
