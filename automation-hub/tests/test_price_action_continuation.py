import asyncio
from datetime import datetime, timedelta, timezone

from bot.types import Bar
from services.native_price_action import NativePriceActionEngine, PriceActionConfig, PriceZone
from services.price_action_lab import PaperExecutionConfig, PriceActionPaperAccount
from services.price_action_research import PriceActionExperimentRunner, PriceActionExperimentStore, controlled_pa_smc_report
from services.price_action_stream import PriceActionPublicStream


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def bar(index, open_=100, high=102, low=98, close=101, volume=10_000):
    return Bar(NOW + timedelta(minutes=5 * index), open_, high, low, close, volume)


RULES = {"tick_size": .1, "quantity_step": .001, "min_quantity": .001,
         "max_quantity": 1000, "min_notional": 5}


def visual(proposal=True, count=10):
    setup = {"id": "setup-1", "zone_id": "zone-1", "strategy_id": "PA1_SR_REJECTION",
             "direction": "bullish", "phase": "ENTRY_READY"}
    trade = {"id": "proposal-1", "setup_id": "setup-1", "strategy_id": "PA1_SR_REJECTION",
             "direction": "bullish", "entry": 105.0, "stop": 99.0, "target": 120.0,
             "risk_distance": 6.0, "rr_ratio": 2.5, "valid_until_index": count + 2}
    return {"candles": [{}] * count, "setups": [setup], "proposals": [trade] if proposal else [],
            "metrics": {"closed": 0}}


def test_automatic_paper_lifecycle_sizes_rounds_protects_and_stops_first(tmp_path):
    account = PriceActionPaperAccount(tmp_path / "auto.db")
    account.configure(execution_config=PaperExecutionConfig(operating_mode="automatic", risk_pct=.5))
    first = account.synchronize_strategy(visual(), contract_rules=RULES,
                                         candle=bar(1), feed_reliable=True)
    assert first["created"][0]["accepted"] is True
    order = account.state()["orders"][0]
    assert order["quantity"] == 8.333
    assert account.state()["order_metadata"][0]["config"]["execution_mode"] == "PAPER"

    account.synchronize_strategy(visual(count=11), contract_rules=RULES,
                                 candle=bar(2, 104, 106, 103, 105), feed_reliable=True)
    position = account.state()["positions"][0]
    assert position["stop_loss"] == 99.0
    assert position["take_profit"] == 120.0

    # Both stop and target occur inside this candle; protective handling is adverse-first.
    account.synchronize_strategy(visual(count=12), contract_rules=RULES,
                                 candle=bar(3, 105, 125, 98, 110), feed_reliable=True)
    state = account.state()
    assert state["positions"] == []
    assert state["order_metadata"][0]["status"] == "COMPLETED"
    assert state["account"]["realized_pnl"] < 0


def test_signals_manual_approval_duplicate_and_unreliable_feed_are_explicit(tmp_path):
    account = PriceActionPaperAccount(tmp_path / "modes.db")
    signal = account.synchronize_strategy(visual(), contract_rules=RULES,
                                          candle=bar(1), feed_reliable=True)
    assert signal["created"] == []
    assert account.state()["candidates"][0]["status"] == "SIGNAL_ONLY"

    account.reset()
    account.configure(execution_config=PaperExecutionConfig(operating_mode="manual_approval"))
    account.synchronize_strategy(visual(), contract_rules=RULES, candle=bar(1), feed_reliable=True)
    assert account.state()["candidates"][0]["status"] == "PENDING_APPROVAL"
    assert account.approve_candidate("proposal-1")["accepted"] is True

    account.reset()
    account.configure(execution_config=PaperExecutionConfig(operating_mode="automatic"))
    account.synchronize_strategy(visual(), contract_rules=RULES, candle=bar(1), feed_reliable=False)
    rejected = account.state()["candidates"][0]
    assert rejected["status"] == "REJECTED"
    assert "unreliable" in rejected["payload"]["reason"]


def test_ended_session_can_resume_wallet_orders_and_positions(tmp_path):
    account = PriceActionPaperAccount(tmp_path / "sessions.db")
    old = account.session()["id"]
    account.broker.submit(symbol="BTCUSDT", side="buy", order_type="market", quantity=1)
    account.broker.process_candle("BTCUSDT", bar(1))
    account.end()
    account.start(symbol="ETHUSDT")
    assert account.state()["positions"] == []
    resumed = account.resume(old)
    assert resumed["session"]["id"] == old
    assert resumed["positions"][0]["symbol"] == "BTCUSDT"
    exported = account.export_session(old)
    assert exported["session"]["state"]["positions"]


def test_global_factory_reset_erases_price_action_session_history(tmp_path):
    account = PriceActionPaperAccount(tmp_path / "factory.db")
    old = account.session()["id"]
    account.record_external_event({"kind": "connection", "state": "CONNECTED"})
    account.reset()
    assert len(account.sessions()) == 2
    fresh = account.factory_reset()
    assert len(account.sessions()) == 1
    assert fresh["session"]["id"] != old
    assert fresh["activity"] == []


def test_public_stream_dedupes_closed_candles_and_marks_gaps_unreliable():
    history = [bar(0), bar(1)]
    stream = PriceActionPublicStream(lambda *_args, **_kwargs: history, stale_after_seconds=60)
    stream.bootstrap("BTCUSDT", "5m")
    stream._set_state("CONNECTED")
    stream.ingest_event({"data": {"e": "bookTicker", "s": "BTCUSDT", "b": "100", "a": "100.1"}})
    stream.ingest_event({"data": {"e": "markPriceUpdate", "p": "100.05", "r": ".0001", "T": 1767225600000}})
    closed = {"e": "kline", "k": {"t": int(bar(2).timestamp.timestamp() * 1000),
              "o": "100", "h": "102", "l": "98", "c": "101", "v": "1000", "x": True}}
    assert stream.ingest_event({"data": closed})["accepted"] is True
    assert stream.ingest_event({"data": closed})["duplicate"] is True
    gap = {"e": "kline", "k": {**closed["k"], "t": int(bar(4).timestamp.timestamp() * 1000)}}
    stream.ingest_event({"data": gap})
    status = stream.status()
    assert status["state"] == "DELAYED"
    assert status["new_entries_paused"] is True
    assert status["duplicate_events"] >= 1


def test_public_stream_rest_reconciles_a_missing_closed_candle_once():
    history = [bar(0), bar(1), bar(2)]
    stream = PriceActionPublicStream(lambda *_args, **_kwargs: history, stale_after_seconds=60)
    stream.bootstrap("BTCUSDT", "5m")
    stream._bars.remove(history[1])
    stream.missing_candles += 1
    stream.last_update = datetime.now(timezone.utc)
    stream._set_state("DELAYED", "controlled test gap")

    assert asyncio.run(stream.reconcile()) == 1
    assert [row.timestamp for row in stream.snapshot()["closed_bars"]] == [
        row.timestamp for row in history]
    assert stream.status()["reliable"] is True
    assert asyncio.run(stream.reconcile()) == 0


def test_pattern_combinations_are_metadata_and_generic_rejection_has_no_pattern_gate():
    engine = NativePriceActionEngine(PriceActionConfig(symbol="BTCUSDT", swing_left=1, swing_right=1))
    mother = bar(0, 100, 110, 90, 105)
    inside_pin = bar(1, 104, 108, 96, 107)
    engine.process_closed_bar(mother)
    snapshot = engine.process_closed_bar(inside_pin)
    names = {row["name"] for row in snapshot.patterns}
    assert {"inside_bar", "inside_pin_bar", "mother_bar_reference"}.issubset(names)

    fakey_engine = NativePriceActionEngine(PriceActionConfig(symbol="BTCUSDT", swing_left=1, swing_right=1))
    rows = [mother, bar(1, 104, 108, 95, 100), bar(2, 98, 103, 88, 96)]
    snapshots = fakey_engine.ingest_closed_bars(rows)
    assert any("fakey" in row["name"] for row in snapshots[-1].patterns)


def test_higher_timeframe_zone_uses_confirmation_time_for_age_expiry():
    engine = NativePriceActionEngine(PriceActionConfig(
        symbol="BTCUSDT", zone_expiry_bars=1, zone_timeframe_scope="higher_timeframe"))
    engine.bars = [bar(index) for index in range(4)]
    engine.zones["htf-zone"] = PriceZone(
        id="htf-zone", role="support", original_role="support", low=99, high=100,
        created_at=bar(0).timestamp, confirmed_at=bar(1).timestamp,
        source_swing_ids=["foreign-htf-swing"], timeframe_scope="higher_timeframe:4h")

    engine._expire_zones(3)

    assert engine.zones["htf-zone"].active is False
    assert engine.zones["htf-zone"].expiration_reason == "maximum_age"


def test_replay_and_live_completed_candles_are_deterministic():
    rows = [bar(i, 100 + (i % 6), 103 + (i % 6), 97 + (i % 6), 101 + (i % 6)) for i in range(80)]
    live = NativePriceActionEngine(PriceActionConfig(symbol="BTCUSDT", timeframe="5m"))
    replay = NativePriceActionEngine(PriceActionConfig(symbol="BTCUSDT", timeframe="5m"))
    live.ingest_closed_bars(rows)
    for row in rows:
        replay.process_closed_bar(row)
    left, right = live.visual_state(), replay.visual_state()
    assert left["snapshot"] == right["snapshot"]
    assert left["zones"] == right["zones"]
    assert left["proposals"] == right["proposals"]


def test_mark_price_liquidation_is_paper_only_and_estimated(tmp_path):
    account = PriceActionPaperAccount(tmp_path / "liquidation.db")
    account.set_leverage(10)
    account.broker.submit(symbol="BTCUSDT", side="buy", order_type="market", quantity=1)
    account.broker.process_candle("BTCUSDT", bar(1, 100, 101, 99, 100))
    boundary = account.state()["positions"][0]["estimated_liquidation_price"]
    result = account.broker.process_mark("BTCUSDT", boundary - .1)
    assert result["liquidated"] is True
    assert "estimate" in result["model"]
    assert account.state()["positions"] == []


def test_research_runs_are_deterministic_partitioned_and_comparison_is_guarded(tmp_path):
    rows = [bar(i, 100 + (i % 8), 103 + (i % 8), 97 + (i % 8), 101 + (i % 8)) for i in range(120)]
    runner = PriceActionExperimentRunner(PriceActionExperimentStore(tmp_path / "research.db"))
    config = PriceActionConfig(symbol="BTCUSDT", swing_left=2, swing_right=2)
    first = runner.run({("BTCUSDT", "5m"): rows}, config, walk_forward_folds=3)
    second = runner.run({("BTCUSDT", "5m"): rows}, config, walk_forward_folds=3)
    assert first["experiment_id"] == second["experiment_id"]
    assert set(first["by_partition"]) <= {"development", "validation", "untouched_oos"}
    assert len(first["walk_forward"]) >= 1
    pa = {"assumptions": {"source_data": "same", "symbols": ["BTCUSDT"], "timeframes": ["5m"],
          "date_partitions": {}, "cost_model": {}, "fill_model": "same", "ambiguity": "stop_first",
          "risk_per_trade_pct": .5}, "metrics": {"expectancy_r": 0}}
    smc = {**pa, "metrics": {"expectancy_r": -0.1}}
    assert controlled_pa_smc_report(pa, smc)["mixed_strategy"] is False
    smc = {**smc, "assumptions": {**smc["assumptions"], "fill_model": "different"}}
    try:
        controlled_pa_smc_report(pa, smc)
        assert False, "comparison should refuse mismatched assumptions"
    except ValueError:
        pass
