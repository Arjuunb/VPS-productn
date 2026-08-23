from copy import deepcopy
from datetime import datetime, timedelta, timezone

from bot.types import Bar
from data.market_data_v2 import MarketDataService, TF_MS
from services.native_price_action import PriceActionConfig
from services.price_action_reference_study import (
    REFERENCE_TIMEFRAMES, REFERENCE_UNIVERSE, run_reference_study,
)
from services.price_action_research import (
    PriceActionExperimentRunner, PriceActionExperimentStore,
    _cost_adjusted_rows, controlled_pa_smc_report, evaluate_research_quality,
)
from services.research_funding import (
    AVAILABLE, DISABLED, PARTIAL, UNAVAILABLE, HistoricalFundingSeries,
)
from services.smc_research_adapter import FrozenSMCNormalizationAdapter


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bars(count, timeframe="1h"):
    step = timedelta(milliseconds=TF_MS[timeframe])
    return [Bar(NOW + step * index, 100 + index % 6, 103 + index % 6,
                97 + index % 6, 101 + index % 6, 1000) for index in range(count)]


def assumptions():
    return {"source_data": "same-dataset", "symbols": ["BTCUSDT"],
            "timeframes": ["1h"], "date_partitions": {"same": True},
            "cost_model": {"commission_bps": 4, "spread_bps": 2,
                           "slippage_bps": 3, "funding": "complete"},
            "fill_model": "conservative_ohlc_adverse_first",
            "ambiguity": "stop_first", "risk_per_trade_pct": .5,
            "exit_assumptions": "fixed_2.5R_or_rejection_stop",
            "reporting_metrics_version": "normalized-r-v2",
            "funding_data_complete": True}


def complete_smc_record():
    return {"id": "smc-1", "setup_id": "setup-1", "symbol": "BTCUSDT",
            "timeframe": "1h", "signal_at": NOW.isoformat(), "direction": "bullish",
            "entry": 100, "stop": 98, "target": 105, "fill_price": 100,
            "filled_at": NOW.isoformat(), "exit_price": 105,
            "closed_at": (NOW + timedelta(hours=3)).isoformat(), "gross_r": 2.5,
            "commission_r": .04, "spread_r": .01, "slippage_r": .02,
            "funding_r": -.005, "status": "WON", "regime": "bullish",
            "partition": "untouched_oos"}


def test_historical_funding_long_short_missing_partial_and_disabled_are_explicit():
    records = [{"funding_time": (NOW + timedelta(hours=8 * index)).isoformat(),
                "funding_rate": .0001, "mark_price": 100,
                "provider": "public", "source_quality": "verified"}
               for index in range(4)]
    series = HistoricalFundingSeries.build(
        "BTCUSDT", records, requested_start=NOW,
        requested_end=NOW + timedelta(hours=24))
    assert series.state == AVAILABLE
    long = series.effect_r(direction="bullish", entry_price=100, risk_distance=2,
                           opened_at=NOW, closed_at=NOW + timedelta(hours=24))
    short = series.effect_r(direction="bearish", entry_price=100, risk_distance=2,
                            opened_at=NOW, closed_at=NOW + timedelta(hours=24))
    assert long["complete"] and long["funding_r"] < 0
    assert short["complete"] and short["funding_r"] > 0

    partial = HistoricalFundingSeries.build(
        "BTCUSDT", records[:1], requested_start=NOW,
        requested_end=NOW + timedelta(days=3))
    missing = HistoricalFundingSeries.build(
        "BTCUSDT", [], requested_start=NOW, requested_end=NOW + timedelta(days=1))
    disabled = HistoricalFundingSeries.build(
        "BTCUSDT", [], requested_start=NOW, requested_end=NOW + timedelta(days=1),
        intentionally_disabled=True)
    assert partial.state == PARTIAL
    assert missing.state == UNAVAILABLE and "not treated as zero" in missing.warnings[0]
    assert disabled.state == DISABLED


def test_funding_cache_paginates_deduplicates_and_survives_restart(tmp_path):
    calls = []

    def provider(url, params):
        calls.append((url, dict(params)))
        assert url.endswith("/fapi/v1/fundingRate")
        return [{"fundingTime": 0, "fundingRate": ".0001", "markPrice": "100"},
                {"fundingTime": 8 * 3_600_000, "fundingRate": "-.0002", "markPrice": "101"}]

    root = tmp_path / "market"
    service = MarketDataService(root, request_json=provider)
    first = service.download_usdm_funding_history(
        "BTCUSDT", start_ms=0, end_ms=8 * 3_600_000)
    assert first["valid"] == 2 and first["coverage"]["state"] == AVAILABLE
    restarted = MarketDataService(root, request_json=provider)
    second = restarted.download_usdm_funding_history(
        "BTCUSDT", start_ms=0, end_ms=8 * 3_600_000)
    assert second["inserted"] == 0
    history = restarted.funding_history("BTCUSDT", start_ms=0, end_ms=8 * 3_600_000)
    assert len(history) == 2 and history[0]["funding_time"].endswith("+00:00")


def test_multiple_timeframe_cache_keeps_independent_checksums(tmp_path):
    service = MarketDataService(tmp_path / "market", request_json=lambda *_: [])
    service.upsert("BTCUSDT", "1h", [(0, 100, 102, 99, 101, 1)], provider="test")
    service.upsert("BTCUSDT", "4h", [(0, 100, 104, 98, 103, 4)], provider="test")
    assert service.status("BTCUSDT", "1h")["available"] is True
    assert service.status("BTCUSDT", "4h")["available"] is True
    absent = service.status("BTCUSDT", "15m")
    assert absent["available"] is False and absent["quarantined_cache"] is None
    assert service.status("BTCUSDT", "1h")["available"] is True


def test_cost_stress_does_not_multiply_observed_historical_funding():
    stressed = _cost_adjusted_rows([{
        "gross_r": 1, "commission_r": .1, "spread_r": .1,
        "slippage_r": .1, "funding_r": .05,
    }], 2)[0]
    assert stressed["funding_r"] == .05
    assert stressed["costs_r"] == .55
    assert abs(stressed["net_r"] - .45) < 1e-12


def test_experiment_with_missing_funding_fails_cost_completeness_gate(tmp_path):
    runner = PriceActionExperimentRunner(PriceActionExperimentStore(tmp_path / "missing.db"))
    report = runner.run(
        {("BTCUSDT", "1h"): bars(60)},
        PriceActionConfig(symbol="BTCUSDT", timeframe="1h"), save=False)
    assert report["funding_coverage"]["BTCUSDT"]["state"] == UNAVAILABLE
    assert report["assumptions"]["funding_data_complete"] is False
    gate = next(row for row in report["quality_gates"]["gates"]
                if row["name"] == "complete_cost_data")
    assert gate["status"] == "FAIL"


def test_frozen_smc_adapter_preserves_source_and_refuses_incomplete_records():
    adapter = FrozenSMCNormalizationAdapter()
    source = {"trades": [complete_smc_record()]}
    original = deepcopy(source)
    normalized = adapter.normalize(source, assumptions=assumptions())
    assert source == original
    assert normalized["read_only"] and normalized["execution_allowed"] is False
    assert normalized["normalization"]["fair_comparison_allowed"] is True
    assert normalized["records"][0]["net_r"] < normalized["records"][0]["gross_r"]
    assert normalized["metrics"]["trade_frequency_per_day"] > 0

    incomplete = adapter.normalize({"proposals": [{"id": "proposal-only"}]},
                                   assumptions=assumptions())
    assert incomplete["normalization"]["fair_comparison_allowed"] is False
    assert incomplete["records"][0]["unavailable_fields"]


def test_controlled_comparison_has_full_metrics_and_never_mixes_strategies():
    smc = FrozenSMCNormalizationAdapter().normalize(
        {"trades": [complete_smc_record()]}, assumptions=assumptions())
    pa_metrics = {key: value for key, value in smc["metrics"].items()}
    pa_metrics["trade_frequency_per_day"] = 1
    pa = {"assumptions": assumptions(), "metrics": pa_metrics,
          "cost_sensitivity": {}, "parameter_sensitivity": {}}
    report = controlled_pa_smc_report(pa, smc)
    assert report["controlled"] is True and report["mixed_strategy"] is False
    assert {"trade_count", "win_rate_pct", "expectancy_r", "profit_factor",
            "maximum_drawdown_r", "longest_losing_streak", "gross_r", "net_r",
            "costs_r"}.issubset(report["price_action"])


def test_reference_ladder_is_predeclared_isolated_and_research_gates_fail_closed(tmp_path):
    datasets = {(symbol, timeframe): bars(60, timeframe)
                for symbol in REFERENCE_UNIVERSE for timeframe in REFERENCE_TIMEFRAMES}
    # Build explicit disabled series over the common deterministic fixture range.
    funding = {symbol: HistoricalFundingSeries.build(
        symbol, [], requested_start=NOW,
        requested_end=max(rows[-1].timestamp for (key, _), rows in datasets.items()
                          if key == symbol), intentionally_disabled=True)
        for symbol in REFERENCE_UNIVERSE}
    runner = PriceActionExperimentRunner(PriceActionExperimentStore(tmp_path / "study.db"))
    artifact = run_reference_study(runner, datasets, funding, save=True)
    assert len(artifact["ladder"]) == 8
    assert artifact["parameter_sensitivity"]["final_test_used_for_selection"] is False
    assert all(step["configuration"]["symbols_timeframes"] ==
               artifact["ladder"][0]["configuration"]["symbols_timeframes"]
               for step in artifact["ladder"])
    gates = evaluate_research_quality(artifact["baseline"])
    assert gates["classification"] in {
        "EVIDENCE_INSUFFICIENT_OR_FAILS_FILTERS", "EVIDENCE_PASSES_CONFIGURED_FILTERS"}
    assert gates["not_a_future_performance_guarantee"] is True
