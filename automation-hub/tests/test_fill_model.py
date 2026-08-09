"""Order fill models (#9): perfect default + realistic friction in the paper engine."""
import pytest

from services.fill_model import (PerfectFill, RealisticFill, from_name,
                                 normalize_fill_model)
from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine


def test_perfect_fill_is_a_noop():
    f = PerfectFill().apply("buy", 100.0, 2.0)
    assert f["price"] == 100.0 and f["size"] == 2.0 and f["rejected"] is False


def test_named_fill_models_are_canonical_and_environment_driven(monkeypatch):
    monkeypatch.setenv("HUB_FILL_SPREAD_PCT", "0.0012")
    monkeypatch.setenv("HUB_FILL_SLIPPAGE_PCT", "0.0008")
    monkeypatch.setenv("HUB_FILL_LATENCY_PCT", "0.0002")
    monkeypatch.setenv("HUB_FILL_TAKER_FEE_PCT", "0.0007")
    monkeypatch.setenv("HUB_FILL_MAKER_FEE_PCT", "0.0003")

    assert normalize_fill_model("realistic") == "RealisticFill"
    assert normalize_fill_model("perfect-fill") == "PerfectFill"
    realistic = from_name("RealisticFill")
    assert isinstance(realistic, RealisticFill)
    assert realistic.spread_pct == 0.0012
    assert realistic.slippage_pct == 0.0008
    assert realistic.latency_pct == 0.0002
    assert realistic.fee_pct() == 0.0007
    assert realistic.fee_pct(maker=True) == 0.0003


def test_unknown_named_fill_model_fails_closed():
    with pytest.raises(ValueError, match="fill_model must be one of"):
        from_name("free-money-fill")


def test_realistic_fill_moves_price_against_you():
    m = RealisticFill(spread_pct=0.001, slippage_pct=0.0005, latency_pct=0.0)
    buy = m.apply("buy", 100.0, 1.0)
    sell = m.apply("sell", 100.0, 1.0)
    assert buy["price"] > 100.0          # pay up to buy
    assert sell["price"] < 100.0         # sell into the bid
    assert buy["cost_pct"] > 0


def test_realistic_partial_and_reject():
    part = RealisticFill(partial_fill_prob=1.0, partial_fraction=0.5)
    assert part.apply("buy", 100, 2.0)["size"] == 1.0          # partial
    rej = RealisticFill(reject_prob=1.0)
    assert rej.apply("buy", 100, 1.0)["rejected"] is True
    # exits never reject/partial
    assert rej.apply("sell", 100, 1.0, allow_reject=False)["rejected"] is False


def test_execution_id_keeps_probabilistic_fill_stable_across_restart():
    first_worker = RealisticFill(partial_fill_prob=0.5, reject_prob=0.5, seed=41)
    restored_worker = RealisticFill(partial_fill_prob=0.5, reject_prob=0.5, seed=41)
    order_id = "auto:instance:candle:buy"
    assert first_worker.apply("buy", 100, 1, execution_id=order_id) == \
        restored_worker.apply("buy", 100, 1, execution_id=order_id)


def test_execution_ids_do_not_share_one_random_outcome():
    model = RealisticFill(partial_fill_prob=0.5, reject_prob=0.5, seed=41)
    outcomes = {
        tuple(sorted(model.apply("buy", 100, 1, execution_id=f"auto:{n}").items()))
        for n in range(20)
    }
    assert len(outcomes) > 1


def _engine(model=None):
    return PaperExecutionEngine(SqliteLedger(":memory:"), 10_000.0, fill_model=model)


def test_paper_engine_perfect_by_default_unchanged():
    eng = _engine()
    r = eng.open(symbol="BTCUSDT", side="BUY", size=1.0, entry=100.0, stop=95.0)
    assert r.action == "opened" and r.price == 100.0 and r.size == 1.0


def test_paper_engine_realistic_fills_cost_money():
    eng = _engine(RealisticFill(spread_pct=0.002, slippage_pct=0.0, latency_pct=0.0))
    r = eng.open(symbol="BTCUSDT", side="BUY", size=1.0, entry=100.0, stop=95.0)
    assert r.price > 100.0                                     # filled worse than requested
    c = eng.close(symbol="BTCUSDT", exit_price=100.0)
    assert c.action == "closed" and c.price < 100.0           # exit also crosses the spread
    assert c.pnl < 0                                          # round-trip friction -> a loss at flat price


def test_paper_engine_rejection():
    eng = _engine(RealisticFill(reject_prob=1.0))
    r = eng.open(symbol="ETHUSDT", side="SELL", size=1.0, entry=100.0, stop=105.0)
    assert r.action == "rejected" and r.size == 0.0
    assert eng.open_position("ETHUSDT") is None                # nothing opened


# ───────────────────────── endpoint ─────────────────────────
@pytest.fixture()
def client():
    pytest.importorskip("fastapi")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import webhook_api
    app = FastAPI(); app.include_router(webhook_api.router)
    return TestClient(app)


SECRET = "dev-webhook-secret"


def test_fill_model_endpoint(client):
    import webhook_api
    assert client.get("/execution/fill-model").json()["model"] in ("perfect", "realistic")
    assert client.post("/execution/fill-model", json={"model": "realistic"}).status_code == 401
    st = client.post("/execution/fill-model", json={"model": "realistic", "spread_pct": 0.001},
                     headers={"X-Webhook-Secret": SECRET}).json()
    assert st["model"] == "realistic"
    # reset to perfect so other tests using the shared engine are unaffected
    client.post("/execution/fill-model", json={"model": "perfect"}, headers={"X-Webhook-Secret": SECRET})


def test_trading_instance_options_default_to_realistic_but_keep_ideal_comparison(client):
    response = client.get("/instances/options")
    assert response.status_code == 200
    body = response.json()
    assert body["execution_defaults"]["fill_model"] == "RealisticFill"
    assert [row["key"] for row in body["fill_models"]] == [
        "RealisticFill", "UnifiedFees", "PerfectFill",
    ]
