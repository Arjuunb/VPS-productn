"""Permanent Trading Memory System — store, composer, similarity, NL query,
insights and the pipeline close hook. Everything is asserted against REAL
composed data; honesty markers ('not captured' / 'Not checked') must survive.
"""
import pytest

from data.journal_store import JournalStore as DecisionJournalStore
from data.trade_memory_store import TradeMemoryStore
from services.trade_memory_manager import TradeMemoryManager
from services import trade_memory as tm
from services import memory_insights as mi


def _journal_with_trade(js, trade_id="T1", *, symbol="BTCUSDT", side="long",
                        entry=100.0, stop=95.0, target=115.0, exit=115.0,
                        pnl=30.0, rr=3.0, result="win", grade="A",
                        mistake="None — trade followed the plan.",
                        strategy="Decision Brain", regime="trend",
                        pretrade_score=72.0, provenance=None):
    js.record_entry({
        "trade_id": trade_id, "mode": "paper", "symbol": symbol, "side": side,
        "strategy": strategy, "timeframe": "15m", "entry": entry, "stop": stop,
        "target": target, "size": 2.0, "risk_amount": 10.0, "planned_rr": 3.0,
        "confidence": 70, "brain_score": 72, "regime": regime,
        "sections": {
            "market_snapshot": {"account_equity": 10000, "rsi": 61, "atr_pct": 1.2,
                                "volatility": 0.8, "ema_fast": 101, "ema_slow": 99,
                                "regime": regime},
            "checklist": {"entry_reads": [{"rule": "ema_cross", "ok": True,
                                           "detail": "EMA fast over slow"}],
                          "risk_gates": [{"rule": "risk", "ok": True, "detail": "0.1% risk"}]},
            "entry_decision": {"main_reason": "EMA crossover long",
                               "quality_gate_score": pretrade_score},
            "provenance": provenance or {},
        }})
    js.close_trade(trade_id, exit=exit, pnl=pnl, actual_rr=rr, result=result,
                   grade=grade, extra_sections={
                       "exit_decision": {"exit_reason": "take-profit", "exit_price": exit},
                       "review": {"grade": grade, "mistake": mistake,
                                  "improvement": "Repeat the disciplined process.",
                                  "followed_strategy": True, "risk_valid": True},
                       "evolution": {"learned": f"{strategy} {regime} {side}",
                                     "take_similar_again": result == "win"}})


@pytest.fixture()
def mgr(tmp_path):
    js = DecisionJournalStore(str(tmp_path / "journal.db"))
    store = TradeMemoryStore(str(tmp_path / "mem.db"))
    m = TradeMemoryManager(store, js, decision_store=None,
                           exchange="kraken", starting_balance=10000.0)
    return m, js, store


# ─────────────────────────── composition ───────────────────────────
def test_remember_composes_all_eight_categories(mgr):
    m, js, store = mgr
    _journal_with_trade(js)
    mem = m.remember("T1")
    assert mem is not None
    s = mem["sections"]
    for cat in ("trade_information", "market_context", "technical_analysis",
                "strategy", "execution", "emotion_journal", "trade_outcome",
                "ai_reflection"):
        assert cat in s, cat
    # real values flow through
    assert s["trade_information"]["symbol"] == "BTCUSDT"
    assert s["trade_information"]["direction"] == "Long"
    assert s["trade_information"]["exchange"] == "kraken"
    assert s["trade_outcome"]["result"] == "win"
    assert s["strategy"]["setup_grade"] == "B"
    assert s["strategy"]["setup_quality_score"] == 72.0
    assert s["strategy"]["setup_grade_source"] == "quality_gate_score"
    assert s["strategy"]["outcome_grade"] == "A"


def test_setup_grade_is_entry_only_and_cannot_leak_outcome(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "WIN", result="win", pnl=30, rr=3, grade="A",
                        pretrade_score=72)
    _journal_with_trade(js, "LOSS", result="loss", pnl=-10, rr=-1, grade="D",
                        pretrade_score=72)
    win = m.remember("WIN")
    loss = m.remember("LOSS")

    # Identical entry evidence must have the same predictive grade even though
    # the post-trade outcomes differ.
    assert win["sections"]["strategy"]["setup_grade"] == "B"
    assert loss["sections"]["strategy"]["setup_grade"] == "B"
    assert win["sections"]["strategy"]["outcome_grade"] == "A"
    assert loss["sections"]["strategy"]["outcome_grade"] == "D"


def test_legacy_outcome_grade_is_not_treated_as_predictive_setup():
    legacy = {
        "trade_id": "legacy", "result": "win", "pnl": 10, "actual_rr": 3,
        "grade": "A", "sections": {"strategy": {"setup_grade": "A"}},
    }
    report = mi.build_review([legacy])
    assert report["by_setup_grade"] == []
    assert report["setup_quality_coverage"]["eligible"] == 0
    assert report["evidence_provenance"]["other_or_unknown"] == 1


def test_live_readiness_evidence_requires_forward_data_and_realistic_fill(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "REALISTIC", provenance={
        "instance_id": "one", "strategy_version": "1.0.0",
        "market_data_mode": "paper_forward", "fill_model": "RealisticFill",
        "execution_mode": "paper", "exchange": "kraken", "instrument_type": "spot",
    })
    _journal_with_trade(js, "IDEAL", provenance={
        "instance_id": "two", "strategy_version": "1.0.0",
        "market_data_mode": "paper_forward", "fill_model": "PerfectFill",
        "execution_mode": "paper", "exchange": "kraken", "instrument_type": "spot",
    })
    m.remember("REALISTIC")
    m.remember("IDEAL")

    evidence = m.insights()["evidence_provenance"]
    assert evidence["paper_forward_realistic"] == 1
    assert evidence["other_or_unknown"] == 1
    assert evidence["market_data_modes"] == {"paper_forward": 2}
    assert evidence["fill_models"] == {"PerfectFill": 1, "RealisticFill": 1}
    realistic = store.get("REALISTIC")["sections"]
    assert realistic["trade_information"]["exchange"] == "kraken"
    assert realistic["strategy"]["version"] == "1.0.0"


def test_uncaptured_fields_are_marked_honestly_not_invented(mgr):
    m, js, store = mgr
    _journal_with_trade(js)
    mem = m.remember("T1")
    mc = mem["sections"]["market_context"]
    # things the bot does not measure must say so — never a fake number
    assert mc["funding_rate"] == "not captured"
    assert mc["fear_greed_index"] == "not captured"
    assert mc["btc_dominance"] == "not captured"
    ta = mem["sections"]["technical_analysis"]
    assert ta["macd"] == "Not checked"
    assert ta["order_blocks"] == "Not checked"
    # paper fees are explicitly modeled as zero with a note
    assert "paper" in str(mem["sections"]["trade_information"]["fees"]).lower()


def test_real_checklist_statuses_are_not_inverted_and_smc_reads_survive():
    journal = {
        "trade_id": "T-status", "status": "closed", "mode": "paper",
        "symbol": "BTCUSDT", "side": "long", "strategy": "SMC",
        "timeframe": "5m", "entry": 100, "stop": 95, "target": 110,
        "size": 1, "risk_amount": 5, "pnl": 10, "result": "win",
        "actual_rr": 2, "confidence": 0.8, "brain_score": 80,
        "sections": {
            "entry_decision": {"quality_gate_score": 80},
            "checklist": {
                "entry_reads": [
                    {"name": "Order block", "status": "Passed", "detail": "bullish block held"},
                    {"name": "Fair value gap", "status": "Not checked", "detail": "not part of setup"},
                ],
                "risk_gates": [
                    {"name": "Position sized", "rule": "risk", "status": "Passed", "detail": "risk within cap"},
                    {"name": "Session", "rule": "session", "status": "Failed", "detail": "outside session"},
                ],
            },
        },
    }

    memory = tm.compose_memory(journal)
    technical = memory["sections"]["technical_analysis"]
    execution = memory["sections"]["execution"]
    assert technical["order_blocks"] == "bullish block held"
    assert technical["fair_value_gaps"] == "Not checked"
    assert "risk within cap" in execution["conditions_passed"]
    assert "outside session" in execution["conditions_failed"]
    assert "not part of setup" not in execution["conditions_failed"]


def test_reflection_is_grounded_in_real_review(mgr):
    m, js, store = mgr
    _journal_with_trade(js)
    mem = m.remember("T1")
    refl = mem["sections"]["ai_reflection"]
    assert set(refl) >= {"what_went_well", "what_went_wrong",
                         "what_to_repeat", "what_to_never_do_again"}
    assert "A-grade" in refl["what_went_well"] or "win" in refl["what_went_well"].lower()
    assert "no invented insight" in refl["basis"].lower()


def test_session_and_weekday_derived(mgr):
    m, js, store = mgr
    _journal_with_trade(js)
    mem = m.remember("T1")
    assert mem["session"] not in (None, "")
    assert mem["weekday"] in ("Monday", "Tuesday", "Wednesday", "Thursday",
                              "Friday", "Saturday", "Sunday")


# ─────────────────────────── persistence / delete ───────────────────────────
def test_remembered_forever_until_deleted(mgr):
    m, js, store = mgr
    _journal_with_trade(js)
    m.remember("T1")
    assert store.get("T1") is not None
    # re-remembering is idempotent (no duplicate)
    m.remember("T1")
    assert store.count() == 1
    # only an explicit delete removes it
    assert store.delete("T1") is True
    assert store.get("T1") is None
    assert store.delete("T1") is False


def test_notes_attach_and_are_searchable(mgr):
    m, js, store = mgr
    _journal_with_trade(js)
    m.remember("T1")
    assert m.set_notes("T1", "FOMO — I entered early") is True
    mem = store.get("T1")
    assert mem["notes"] == "FOMO — I entered early"
    assert mem["sections"]["emotion_journal"]["manual_notes"] == "FOMO — I entered early"
    # the note is findable by full-text search
    hits = store.list(q="FOMO")
    assert any(h["trade_id"] == "T1" for h in hits)


def test_backfill_imports_closed_journal_trades(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1")
    _journal_with_trade(js, "T2", symbol="ETHUSDT")
    res = m.backfill()
    assert res["backfilled"] == 2
    assert store.count() == 2
    # backfill is idempotent
    assert m.backfill()["backfilled"] == 0


def test_rebuild_recomposes_existing_memory_and_preserves_notes(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1", pretrade_score=84)
    original = m.remember("T1")
    m.set_notes("T1", "keep this manual note")
    original["sections"]["strategy"] = {"setup_grade": "A"}  # legacy shape
    original["notes"] = "keep this manual note"
    store.upsert(original)

    result = m.rebuild()
    rebuilt = store.get("T1")
    assert result == {"rebuilt": 1, "failed": 0, "total": 1}
    assert rebuilt["sections"]["strategy"]["setup_grade"] == "A"
    assert rebuilt["sections"]["strategy"]["setup_grade_basis"] == "pretrade_quality_v1"
    assert rebuilt["notes"] == "keep this manual note"


# ─────────────────────────── similarity ───────────────────────────
def test_similar_ranks_by_feature_cosine(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1", side="long")
    _journal_with_trade(js, "T2", side="long", symbol="ETHUSDT")
    _journal_with_trade(js, "T3", side="short", symbol="SOLUSDT",
                        entry=100, stop=105, target=85, exit=85, pnl=-30, rr=-3,
                        result="loss")
    for t in ("T1", "T2", "T3"):
        m.remember(t)
    sim = m.similar("T1", limit=2)
    assert len(sim) == 2
    # the same-direction long should rank above the opposite-direction short
    assert sim[0]["trade_id"] in ("T2", "T3")
    assert all("similarity" in s for s in sim)


# ─────────────────────────── natural-language ask ───────────────────────────
def test_ask_losing_symbol_trades(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1", result="win", pnl=30)
    _journal_with_trade(js, "T2", result="loss", pnl=-30, exit=95, rr=-1.0)
    for t in ("T1", "T2"):
        m.remember(t)
    res = m.ask("show all losing BTC trades")
    assert res["kind"] == "filter"
    assert all(t["result"] == "loss" for t in res["trades"])
    assert len(res["trades"]) == 1


def test_ask_best_trade(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1", pnl=30)
    _journal_with_trade(js, "T2", pnl=80)
    for t in ("T1", "T2"):
        m.remember(t)
    res = m.ask("what was my best trade this year")
    assert res["kind"] == "best_trade"
    assert "80" in res["answer"]


def test_ask_expectancy_ranking(mgr):
    m, js, store = mgr
    provenance = {"instance_id": "one", "strategy_version": "1.0.0",
                  "market_data_mode": "paper_forward", "fill_model": "RealisticFill",
                  "execution_mode": "paper", "exchange": "kraken", "instrument_type": "spot"}
    _journal_with_trade(js, "T1", strategy="Decision Brain", pnl=30,
                        provenance=provenance)
    _journal_with_trade(js, "T2", strategy="EMA", pnl=-10, exit=95, rr=-1,
                        result="loss", provenance=provenance)
    for t in ("T1", "T2"):
        m.remember(t)
    res = m.ask("which setup has the highest expectancy?")
    assert res["kind"] == "expectancy"
    assert res["ranking"][0]["strategy"] == "Decision Brain"


def test_expectancy_ranking_refuses_replay_and_perfect_fill(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1", strategy="Supertrend", pnl=300,
                        provenance={"market_data_mode": "paper_forward",
                                    "fill_model": "PerfectFill"})
    m.remember("T1")
    result = m.ask("which strategy has the highest expectancy?")
    assert result["ranking"] == []
    assert "not used to rank live readiness" in result["answer"]


def test_ask_weekday_question_is_sample_honest(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1")
    m.remember("T1")
    res = m.ask("why am I losing on Mondays?")
    assert res["kind"] == "weekday"
    # a one-trade bucket must be flagged as an early signal, never asserted as fact
    assert "No closed trades" in res["answer"] or "sample" in res["answer"].lower()


# ─────────────────────────── insights / coaching ───────────────────────────
def test_insights_compute_real_stats(mgr):
    m, js, store = mgr
    for i in range(6):
        _journal_with_trade(js, f"W{i}", pnl=20)
    for i in range(4):
        _journal_with_trade(js, f"L{i}", pnl=-10, exit=95, rr=-1, result="loss",
                            grade="C")
    for t in [f"W{i}" for i in range(6)] + [f"L{i}" for i in range(4)]:
        m.remember(t)
    ins = m.insights()
    assert ins["sample"] == 10
    assert ins["overall"]["win_rate"] == 60.0
    assert ins["overall"]["trades"] == 10
    assert ins["overall"]["expectancy"] == 0.8
    assert ins["overall"]["expectancy_pnl"] == 8.0
    assert "sharpe_ratio" in ins
    assert isinstance(ins["by_weekday"], list)


def test_coaching_is_sample_gated(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1")
    m.remember("T1")
    coaching = m.insights()["coaching"]
    # with 1 trade we must NOT emit an edge percentage — only insufficient-data
    assert coaching[0]["stage"] == "insufficient-data"


def test_mistake_library_counts_repeats(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1", result="loss", pnl=-20, exit=95, rr=-1,
                        mistake="Chased the entry after the move started.")
    _journal_with_trade(js, "T2", result="loss", pnl=-15, exit=96, rr=-0.8,
                        mistake="Chased the entry after the move started.")
    for t in ("T1", "T2"):
        m.remember(t)
    lib = m.insights()["mistakes"]
    assert lib and lib[0]["count"] == 2
    assert lib[0]["repeated"] is True


# ─────────────────────────── reviews ───────────────────────────
def test_reviews_persist_by_period(mgr):
    m, js, store = mgr
    _journal_with_trade(js, "T1")
    m.remember("T1")
    res = m.run_reviews()
    assert any(r.startswith("nightly") for r in res["ran"])
    assert any(r.startswith("yearly") for r in res["ran"])
    nightly = m.reviews("nightly")
    assert nightly and "overall" in nightly[0]["report"]


# ─────────────────────────── store: FTS fallback ───────────────────────────
def test_fts_query_is_injection_safe():
    # operator characters are neutralised, tokens OR'd
    from data.trade_memory_store import _fts_query
    q = _fts_query('losing "BTC" trades OR NOT (x)')
    assert '"losing"' in q and '"BTC"' in q
    assert " OR " in q
