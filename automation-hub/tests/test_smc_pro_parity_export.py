"""Static safeguards for the temporary Pine instrumentation file."""
from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).parents[1]
REFERENCE = ROOT / "research_references" / "smc_pro_v2_reference.pine"
EXPORTER = ROOT / "research_references" / "instrumentation" / "smc_pro_v2_parity_export_only.pine"


def test_exporter_keeps_reference_inputs_and_strategy_declaration():
    reference, exporter = REFERENCE.read_text(), EXPORTER.read_text()
    inputs = lambda source: [line for line in source.splitlines() if "input." in line]
    assert inputs(exporter) == inputs(reference)
    assert next(line for line in exporter.splitlines() if line.startswith("strategy(")) == next(
        line for line in reference.splitlines() if line.startswith("strategy("))


def test_exporter_never_sends_the_reference_trade_webhooks():
    source = EXPORTER.read_text()
    assert "if sendTradexaAlerts" not in source
    assert source.count("if false // PARITY_EXPORT_ONLY") == 2
    assert "SMC_PRO_V2_PARITY_EXPORT_ONLY" in source
    assert "if barstate.isconfirmed and barstate.isrealtime" in source


def test_exporter_retains_reference_orders_but_disables_order_fill_alert_routing():
    reference, exporter = REFERENCE.read_text(), EXPORTER.read_text()
    for token in ('strategy.entry("PRO Long", strategy.long)',
                  'strategy.entry("PRO Short", strategy.short)',
                  'strategy.exit("Exit Long", "PRO Long", stop=tradeSl, limit=tradeTp)',
                  'strategy.exit("Exit Short", "PRO Short", stop=tradeSl, limit=tradeTp)'):
        assert exporter.count(token) == reference.count(token)


def test_exporter_has_complete_deterministic_record_contract():
    source = EXPORTER.read_text()
    fields = ("timestamp", "symbol", "timeframe", "close", "htf_bias",
              "swing_trend_bias", "internal_trend_bias", "sweep_high", "sweep_low",
              "internal_bullish_bos", "internal_bearish_bos",
              "internal_bullish_choch", "internal_bearish_choch",
              "swing_bullish_bos", "swing_bearish_bos",
              "swing_bullish_choch", "swing_bearish_choch",
              "bullish_fair_value_gap", "bearish_fair_value_gap",
              "near_bull_ob", "near_bear_ob", "bullish_pin_bar", "bearish_pin_bar",
              "recent_sweep_low", "recent_sweep_high", "recent_bull_choch",
              "recent_bear_choch", "recent_bull_fvg", "recent_bear_fvg",
              "long_condition", "short_condition", "trade_ready_condition",
              "context_score", "execution_score", "setup_quality", "execution_status",
              "strat_atr", "long_stop_loss", "long_take_profit",
              "short_stop_loss", "short_take_profit")
    for field in fields:
        assert f'"{field}"' in source


def test_immutable_reference_still_matches_its_recorded_fingerprint():
    assert hashlib.sha256(REFERENCE.read_bytes()).hexdigest() == (
        "95ec2874dd52abba0d26088d1fbce6208f73ed747a885b0dc0ca89fc0fb33e8c"
    )
