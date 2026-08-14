from scripts.edge_feasibility_audit import COST_SCENARIOS_BPS, feature_classification


def sample(mean, n=50, std=20):
    return {1: {"n": n, "mean_bps": mean, "std_bps": std},
            3: {"n": n, "mean_bps": mean, "std_bps": std},
            6: {"n": n, "mean_bps": mean, "std_bps": std},
            12: {"n": n, "mean_bps": mean, "std_bps": std},
            24: {"n": n, "mean_bps": mean, "std_bps": std}}


def test_current_execution_hurdle_is_conservative_14_bps_lower_bound():
    assert COST_SCENARIOS_BPS["current_realistic"] == 14.0
    assert COST_SCENARIOS_BPS["improved"] < COST_SCENARIOS_BPS["current_realistic"] < COST_SCENARIOS_BPS["worse"]


def test_below_cost_observation_is_never_researchable():
    observed, baseline = sample(10), sample(0)
    monthly = {f"2025-0{i}": sample(10) for i in range(1, 7)}
    result = feature_classification(observed, baseline, monthly, 20)
    assert result["classification"] == "NOT VIABLE"


def test_material_cross_random_move_can_be_researchable_but_not_approval():
    observed, baseline = sample(40, n=100, std=20), sample(0, n=100, std=20)
    monthly = {f"2025-0{i}": sample(40, n=30, std=20) for i in range(1, 7)}
    result = feature_classification(observed, baseline, monthly, 20)
    assert result["classification"] in {"RESEARCHABLE", "STRONG RESEARCH PREMISE"}
