from dataclasses import replace

from services.native_smc import PivotPoint
from services.smc_strategy_v1 import ENTRY_MODELS, _trade_plan, evaluate, manifest, strategy_models
from tests.test_smc_strategy_ladder import seeded_engine


def test_manifest_is_versioned_paper_only_and_keeps_parked_models_inert():
    payload = manifest()
    assert payload["version"] == "SMC_SOURCE_V1.0.0-paper-draft"
    assert payload["paper_only"] is True
    assert payload["execution_allowed"] is False
    assert payload["structure_policy"]["htf_bos"] == "FULL_BODY_CLOSE_REQUIRED"
    assert [row.status for row in ENTRY_MODELS] == ["ACTIVE", "PARKED", "PARKED"]
    assert len(payload["configuration_hash"]) == 64
    registry = strategy_models()
    assert registry["real_execution_allowed"] is False
    assert [row["status"] for row in registry["models"]] == ["ACTIVE", "PARKED", "PARKED"]


def test_sweep_reversal_prefers_causal_order_block_and_builds_split_targets():
    engine = seeded_engine()
    result = evaluate(engine)
    assert result["state"] == "ENTRY_READY"
    assert result["selected_candidate_id"] == "SMC_S5_ORDER_BLOCK_RETEST"
    assert result["proposal"]["execution_allowed"] is False
    plan = result["trade_plan"]
    assert plan["target_1_fraction"] == 0.5
    assert plan["target_2_fraction"] == 0.5
    assert plan["target_2_r"] >= 3.0
    assert plan["risk_percent"] == 0.5
    assert plan["paper_only"] is True and plan["execution_allowed"] is False
    assert result["proposal_id"] == result["proposal"]["id"]
    assert result["setup_id"] == result["proposal"]["setup_id"]
    assert result["native_object_ids"] == ["sweep-low", "structure-bull", "ob-bull"]
    assert [row["status"] for row in result["ordered_condition_results"][:2]] == ["PASS", "PASS"]
    assert result["missing_conditions"] == []


def test_trade_plan_keeps_scale_out_and_runner_targets_strictly_ordered():
    proposal = {"entry": 100.0, "stop": 90.0, "direction": "bullish"}
    expected = {
        115.0: (115.0, 130.0),
        130.0: (120.0, 130.0),
        140.0: (120.0, 140.0),
    }
    for external_price, targets in expected.items():
        engine = seeded_engine()
        at = engine.bars[-1].timestamp
        pivot = PivotPoint(
            f"external-{external_price}", "high", external_price,
            at, at, len(engine.bars) - 1, "external")
        engine.pivots = {pivot.id: pivot}

        plan = _trade_plan(engine, proposal)

        assert (plan["target_1"], plan["target_2"]) == targets
        assert plan["target_1"] < plan["target_2"]
        assert plan["target_1_r"] < plan["target_2_r"]


def test_sweep_reversal_falls_back_to_causal_fvg():
    engine = seeded_engine()
    engine.obs = {}
    result = evaluate(engine)
    assert result["state"] == "ENTRY_READY"
    assert result["selected_candidate_id"] == "SMC_S4_FVG_RETEST"


def test_parked_models_cannot_emit_proposals():
    engine = seeded_engine()
    for model_id in ("SMC_M2_BOS_CONTINUATION", "SMC_M3_DISPLACEMENT_FVG"):
        result = evaluate(engine, model_id)
        assert result["state"] == "PARKED"
        assert result["proposal"] is None
        assert result["trade_plan"] is None


def test_missing_confirmation_stays_watching():
    engine = seeded_engine()
    now = engine.bars[-1].timestamp
    engine.snapshots[now] = replace(engine.snapshots[now], price_action=replace(
        engine.snapshots[now].price_action, bullish_rejection=False))
    result = evaluate(engine)
    assert result["state"] == "WATCHING"
    assert result["proposal"] is None


def test_m1_fails_closed_when_native_htf_context_or_location_conflicts():
    engine = seeded_engine()
    now = engine.bars[-1].timestamp
    snapshot = engine.snapshots[now]
    engine.snapshots[now] = replace(snapshot, htf_bias=-1,
                                    dealing_range=replace(snapshot.dealing_range, area="premium"))
    result = evaluate(engine)
    assert result["state"] == "WATCHING"
    assert result["proposal"] is None
    assert result["trade_plan"] is None
    assert result["missing_conditions"] == ["Completed HTF direction", "Premium / discount location"]
    assert result["candidate_evaluations"][0]["state"] == "ENTRY_READY"
