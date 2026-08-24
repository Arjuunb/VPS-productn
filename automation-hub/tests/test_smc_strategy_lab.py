from datetime import datetime, timedelta, timezone
import json

import pytest

from bot.types import Bar
from services.price_action_lab import PriceActionPaperAccount
from services.smc_strategy_lab import SMCPaperAccount, SMCPaperConfig, SMCStrategyLabRuntime
from services.smc_strategy_v1 import evaluate
from services.native_smc_live_visual import reconcile_market_state
from tests.test_smc_strategy_ladder import seeded_engine


RULES = {
    "tick_size": 0.1, "quantity_step": 0.001, "min_quantity": 0.001,
    "max_quantity": 100.0, "min_notional": 5.0,
}


def test_smc_account_is_durable_and_isolated_from_price_action(tmp_path):
    smc_path, pa_path = tmp_path / "smc.db", tmp_path / "pa.db"
    smc = SMCPaperAccount(smc_path)
    pa = PriceActionPaperAccount(pa_path)
    assert smc.path != pa.path
    assert smc.state()["account_scope"] == "SMC_STRATEGY_LAB_ONLY"
    assert pa.state()["account_scope"] == "PRICE_ACTION_VISUAL_LAB_ONLY"

    pa_order = pa.broker.submit(symbol="BTCUSDT", side="buy", order_type="limit",
                                quantity=0.1, limit_price=90)
    smc.reset("RESET SMC PAPER")
    assert pa.broker.order(pa_order["id"])["status"] == "open"
    reopened = SMCPaperAccount(smc_path)
    assert reopened.state()["paper_only"] is True
    assert reopened.state()["real_execution_allowed"] is False


def test_manual_order_is_idempotent_and_protection_survives_fill(tmp_path):
    account = SMCPaperAccount(tmp_path / "smc.db")
    request = dict(symbol="BTCUSDT", side="long", order_type="market", rules=RULES,
                   reference_price=100, quantity=0.1, stop_loss=90,
                   target_1=120, target_2=130, idempotency_key="manual-1")
    first = account.submit_order(**request)
    duplicate = account.submit_order(**request)
    assert first["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert duplicate["order"]["id"] == first["order"]["id"]

    candle = Bar(datetime(2026, 8, 24, tzinfo=timezone.utc), 100, 105, 95, 102, 100)
    result = account.process_candle("BTCUSDT", candle)
    assert result["duplicate"] is False
    position = account.broker.positions()[0]
    assert position["stop_loss"] == 90
    assert position["take_profit"] == 130
    assert account.process_candle("BTCUSDT", candle)["duplicate"] is True


def test_risk_sizing_and_session_identity_guards(tmp_path):
    account = SMCPaperAccount(tmp_path / "smc.db")
    placed = account.submit_order(
        symbol="BTCUSDT", side="buy", order_type="limit", rules=RULES,
        reference_price=100, risk_pct=0.5, limit_price=100, stop_loss=90,
        target_1=120, target_2=130, idempotency_key="risk-1")
    assert placed["order"]["quantity"] == 5.0
    with pytest.raises(ValueError, match="cannot change"):
        account.configure(symbol="ETHUSDT")
    with pytest.raises(ValueError, match="parked"):
        account.configure(config=SMCPaperConfig(model_id="SMC_M2_BOS_CONTINUATION"))


def test_session_lifecycle_and_exact_reset_confirmation(tmp_path):
    account = SMCPaperAccount(tmp_path / "smc.db")
    original = account.session()["id"]
    with pytest.raises(ValueError, match="exactly match"):
        account.reset("reset")
    reset = account.reset("RESET SMC PAPER")
    assert reset["session"]["id"] != original
    assert reset["account"]["balance"] == 10_000
    assert any(row["id"] == original and row["end_reason"] == "paper_reset"
               for row in account.sessions())
    duplicated = account.duplicate(reset["session"]["id"])
    assert duplicated["session"]["id"] != reset["session"]["id"]
    ended = account.end()
    assert ended["status"] == "ended"
    resumed = account.resume(duplicated["session"]["id"])
    assert resumed["session"]["status"] == "active"


def test_signals_only_is_default_and_live_execution_is_impossible(tmp_path):
    state = SMCPaperAccount(tmp_path / "smc.db").state()
    assert state["session"]["operating_mode"] == "signals_only"
    assert state["execution_mode"] == "PAPER"
    assert state["real_execution_allowed"] is False
    assert not hasattr(SMCPaperAccount, "enable_live")


@pytest.mark.parametrize("mode,expected,orders", [
    ("signals_only", "SIGNAL_ONLY", 0),
    ("manual_approval", "PENDING_APPROVAL", 0),
    ("automatic", "APPROVED_AUTOMATIC", 1),
])
def test_strategy_candidate_obeys_saved_operating_mode(tmp_path, mode, expected, orders):
    account = SMCPaperAccount(tmp_path / f"{mode}.db")
    account.configure(config=SMCPaperConfig(operating_mode=mode))
    evaluation = evaluate(seeded_engine())
    result = account.synchronize_candidate(evaluation, rules=RULES,
                                           reference_price=evaluation["trade_plan"]["entry"],
                                           feed_reliable=True)
    assert result["candidate_status"] == expected
    assert len(account.broker.orders()) == orders
    assert account.journal()["journal"][0]["native_object_ids"]
    assert account.metrics()["detected_setups"] == 1
    if mode == "manual_approval":
        account.approve_candidate(evaluation["proposal_id"])
        assert len(account.broker.orders()) == 1


def test_unreliable_feed_cannot_create_strategy_order(tmp_path):
    account = SMCPaperAccount(tmp_path / "paused.db")
    account.configure(config=SMCPaperConfig(operating_mode="automatic"))
    evaluation = evaluate(seeded_engine())
    result = account.synchronize_candidate(evaluation, rules=RULES,
                                           reference_price=evaluation["trade_plan"]["entry"],
                                           feed_reliable=False)
    assert result["candidate_status"] == "DATA_PAUSED"
    assert account.broker.orders() == []


def test_strategy_fill_creates_half_size_t1_and_uses_stop_first_on_ambiguous_bar(tmp_path):
    account = SMCPaperAccount(tmp_path / "scale-out.db")
    account.configure(config=SMCPaperConfig(operating_mode="automatic"))
    evaluation = evaluate(seeded_engine())
    account.synchronize_candidate(evaluation, rules=RULES,
                                  reference_price=evaluation["trade_plan"]["entry"],
                                  feed_reliable=True)
    entry = evaluation["trade_plan"]["entry"]
    first = Bar(datetime(2026, 8, 24, 12, tzinfo=timezone.utc), entry, entry + 1,
                entry - 1, entry, 10_000)
    account.process_candle("BTCUSDT", first)
    position = account.broker.positions()[0]
    t1_meta = next(row for row in account.state()["order_metadata"]
                   if row["ownership"] == "strategy_target_1")
    t1_order = account.broker.order(t1_meta["order_id"])
    assert t1_order["quantity"] == pytest.approx(position["size"] * 0.5)
    assert position["stop_loss"] is not None and position["take_profit"] is not None

    ambiguous = Bar(datetime(2026, 8, 24, 12, 5, tzinfo=timezone.utc), entry,
                    float(t1_meta["target_1"]) + 1, float(position["stop_loss"]) - 1, entry, 10_000)
    account.process_candle("BTCUSDT", ambiguous)
    assert account.broker.order(t1_meta["order_id"])["status"] == "cancelled"
    assert account.broker.positions() == []
    assert any(row["kind"] == "intrabar_ambiguity_stop_first" for row in account.state()["activity"])


def test_runtime_reprocessing_same_closed_bar_cannot_duplicate_strategy_order(tmp_path, monkeypatch):
    account = SMCPaperAccount(tmp_path / "runtime.db")
    account.configure(config=SMCPaperConfig(operating_mode="automatic"))
    evaluation = evaluate(seeded_engine())
    price = evaluation["trade_plan"]["entry"]
    now = datetime.now(timezone.utc)
    candle_time = now - timedelta(minutes=5)
    candle = {"timestamp": candle_time.isoformat(), "open": price, "high": price + 1,
              "low": price - 1, "close": price, "volume": 1_000}
    monkeypatch.setattr("services.native_smc_live_visual.live_visual_state",
                        lambda *args, **kwargs: {"candles": [candle], "source_strategy": evaluation,
                                                "live_display": {"last_price": price},
                                                "data_provenance": {"last_closed_candle": candle_time.isoformat()}})

    class Market:
        def usdm_contract_rules(self, symbol): return RULES
        def public_usdm_quote(self, symbol):
            return {"bid": price, "ask": price + .01, "mark": price,
                    "provider_time": datetime.now(timezone.utc).isoformat(),
                    "funding_rate": 0.0001,
                    "last_funding_time": "2026-08-24T08:00:00+00:00",
                    "next_funding_time": "2026-08-24T16:00:00+00:00"}

    runtime = SMCStrategyLabRuntime(Market(), account, autostart=False)
    runtime.tick(); runtime.tick()
    strategy_orders = [row for row in account.state()["order_metadata"] if row["ownership"] == "strategy"]
    assert len(strategy_orders) == 1
    assert len(account.state()["funding_events"]) == 1


def test_market_reconciliation_fails_closed_and_accepts_fresh_matching_quote():
    now = datetime(2026, 8, 24, 12, 5, 5, tzinfo=timezone.utc)
    base = {"candles": [{"close": 100}], "live_display": {"last_price": 100},
            "data_provenance": {"last_closed_candle": "2026-08-24T12:00:00+00:00"}}
    stale = reconcile_market_state(json.loads(json.dumps(base)), None, timeframe="5m", now=now)
    assert stale["live_display"]["reliable"] is False
    assert stale["live_display"]["new_entries_paused"] is True
    fresh = reconcile_market_state(json.loads(json.dumps(base)), {
        "bid": 99.99, "ask": 100.01, "mark": 100,
        "provider_time": now.isoformat(), "funding_rate": 0.0001,
        "last_funding_time": "2026-08-24T08:00:00+00:00",
        "next_funding_time": "2026-08-24T16:00:00+00:00",
    }, timeframe="5m", now=now)
    assert fresh["live_display"]["connection_state"] == "SYNCHRONIZED"
    assert fresh["live_display"]["reliable"] is True


def test_funding_is_idempotent_and_journal_notes_are_append_only(tmp_path):
    account = SMCPaperAccount(tmp_path / "funding.db")
    account.submit_order(symbol="BTCUSDT", side="buy", order_type="market", rules=RULES,
                         reference_price=100, quantity=.1, stop_loss=90, target_1=110,
                         target_2=120, idempotency_key="funding-position")
    account.process_candle("BTCUSDT", Bar(datetime(2026, 8, 24, tzinfo=timezone.utc),
                                           100, 101, 99, 100, 100))
    first = account.apply_funding_once(symbol="BTCUSDT", funding_time="2026-08-24T08:00:00+00:00",
                                       rate=.0001, mark_price=100)
    second = account.apply_funding_once(symbol="BTCUSDT", funding_time="2026-08-24T08:00:00+00:00",
                                        rate=.0001, mark_price=100)
    assert first["applied"] is True
    assert second["reason"] == "funding event already processed"

    account.configure(config=SMCPaperConfig(operating_mode="signals_only"))
    evaluation = evaluate(seeded_engine())
    account.synchronize_candidate(evaluation, rules=RULES,
                                  reference_price=evaluation["trade_plan"]["entry"], feed_reliable=True)
    journal_id = account.journal()["journal"][0]["journal_id"]
    account.add_journal_note(journal_id, "reviewed without changing engine evidence")
    assert account.journal()["journal"][0]["notes"][0]["note"].startswith("reviewed")


def test_historical_replay_is_session_owned_and_hides_future_bars(tmp_path):
    bars = seeded_engine().bars
    account = SMCPaperAccount(tmp_path / "replay.db")
    account.start(mode="HISTORICAL", symbol="BTCUSDT", timeframe="5m",
                  config=SMCPaperConfig(operating_mode="signals_only"))

    class Market:
        def bars(self, symbol, timeframe, limit): return bars
        def usdm_contract_rules(self, symbol): return RULES

    runtime = SMCStrategyLabRuntime(Market(), account, autostart=False)
    first = runtime.replay_step(steps=5)
    second = runtime.replay_step(steps=3)
    assert first["cursor"] == 5
    assert second["cursor"] == 8
    assert second["session_id"] == account.session()["id"]
    assert second["future_candles_visible"] is False
    account.configure(mode="LIVE_PAPER")
    with pytest.raises(ValueError, match="HISTORICAL"):
        runtime.replay_step()


def test_completed_session_journal_survives_account_reset(tmp_path):
    account = SMCPaperAccount(tmp_path / "journal-history.db")
    evaluation = evaluate(seeded_engine())
    account.synchronize_candidate(evaluation, rules=RULES,
                                  reference_price=evaluation["trade_plan"]["entry"], feed_reliable=True)
    previous_session = account.session()["id"]
    previous_journal = account.journal(previous_session)["journal"]
    account.reset("RESET SMC PAPER")
    assert previous_journal
    assert account.journal(previous_session)["journal"][0]["proposal_id"] == evaluation["proposal_id"]
    assert any(row["session_id"] == previous_session for row in account.journal()["journal"])
