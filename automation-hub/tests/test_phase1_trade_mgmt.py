"""Phase 1 upgrades: shared trade management (break-even / scale-out /
trailing — OFF by default, by measurement), paper-engine partial closes, and
Kelly-capped adaptive risk sizing."""
import json
from datetime import datetime, timezone

from bot.types import Bar
from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine
from services.trade_manager import ManagedTrade, TradeManager


def _bar(close, high=None, low=None):
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return Bar(ts, close, high if high is not None else close,
               low if low is not None else close, close, 1.0)


# ─────────────────────────── TradeManager (pure logic) ───────────────────────────
def test_disabled_manager_matches_plain_stop_target():
    mgr = TradeManager()  # defaults: everything off
    t = ManagedTrade(side="long", entry=100, stop=95, target=115, risk=5)
    assert mgr.on_bar(t, high=104, low=99).exit_price is None   # in range: nothing
    assert t.stop == 95                                          # never moved
    a = mgr.on_bar(t, high=104, low=94.9)
    assert a.exit_price == 95 and a.exit_reason == "stop"
    t2 = ManagedTrade(side="long", entry=100, stop=95, target=115, risk=5)
    a2 = mgr.on_bar(t2, high=115.2, low=101)
    assert a2.exit_price == 115 and a2.exit_reason == "target"


def test_break_even_locks_out_the_loss():
    mgr = TradeManager(be_at_r=1.0)
    t = ManagedTrade(side="long", entry=100, stop=95, target=115, risk=5)
    mgr.on_bar(t, high=105.5, low=100)          # +1.1R seen -> stop to entry
    assert t.be and t.stop == 100
    a = mgr.on_bar(t, high=101, low=99.5)       # retrace: exit at entry, not -1R
    assert a.exit_price == 100


def test_scale_out_and_blended_r():
    mgr = TradeManager(scale_at_r=1.5, scale_frac=0.5)
    t = ManagedTrade(side="short", entry=100, stop=105, target=85, risk=5)
    a = mgr.on_bar(t, high=100, low=92)         # -1.6R favorable -> partial at 92.5
    assert t.scaled and a.partial_price == 92.5
    # runner reaches the 3R target: blended R = 0.5*1.5 + 0.5*3.0
    r = mgr.r_multiple(t, exit_price=85, partial_price=92.5)
    assert abs(r - 2.25) < 1e-9


def test_trailing_arms_only_after_threshold():
    mgr = TradeManager(trail_r=1.0, trail_after_r=2.0)
    t = ManagedTrade(side="long", entry=100, stop=95, target=115, risk=5)
    mgr.on_bar(t, high=107, low=101)            # +1.4R: trail not armed yet
    assert t.stop == 95
    mgr.on_bar(t, high=111, low=104)            # +2.2R: armed -> stop = 111 - 5
    assert t.stop == 106
    a = mgr.on_bar(t, high=107, low=105.9)      # pullback takes the trailed stop
    assert a.exit_price == 106 and a.exit_reason == "stop"


def test_stop_checked_before_it_moves():
    # pessimistic rule: a bar that would hit both old stop and BE level exits at
    # the OLD stop — the manager never moves the stop on the exit bar first
    mgr = TradeManager(be_at_r=0.5)
    t = ManagedTrade(side="long", entry=100, stop=95, target=115, risk=5)
    a = mgr.on_bar(t, high=104, low=94)
    assert a.exit_price == 95


# ─────────────────────────── paper engine partial close ───────────────────────────
def test_paper_reduce_realizes_partial_pnl():
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led, starting_balance=10_000)
    paper.open(symbol="BTCUSDT", side="BUY", size=2.0, entry=100, stop=95)
    fill = paper.reduce(symbol="BTCUSDT", exit_price=110, fraction=0.5)
    assert fill.action == "reduced" and fill.size == 1.0 and fill.pnl == 10.0
    pos = paper.open_position("BTCUSDT")
    assert pos is not None and pos["size"] == 1.0 and pos["entry"] == 100
    assert paper.realized_pnl() == 10.0
    # closing the remainder settles the rest
    paper.close(symbol="BTCUSDT", exit_price=110)
    assert paper.open_position("BTCUSDT") is None
    assert paper.realized_pnl() == 20.0


def test_paper_reduce_noop_on_bad_input():
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led, starting_balance=10_000)
    assert paper.reduce(symbol="BTCUSDT", exit_price=100, fraction=0.5).action == "noop"
    paper.open(symbol="BTCUSDT", side="BUY", size=1.0, entry=100, stop=95)
    assert paper.reduce(symbol="BTCUSDT", exit_price=100, fraction=1.5).action == "noop"


def test_exact_target_and_management_survive_engine_reconstruction():
    """A restart restores the actual strategy target and lifecycle, never a
    guessed 3R replacement or a fresh zero-age management object."""
    from services.auto_engine import AutoStrategyEngine

    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led)
    paper.open(symbol="BTCUSDT", side="BUY", size=1.0, entry=100, stop=95,
               target=112.5)
    state = {
        "side": "long", "entry": 100.0, "stop": 100.0, "target": 112.5,
        "risk": 5.0, "be": True, "scaled": True, "best": 109.0,
        "age": 37, "mfe": 110.0, "mae": 97.0,
    }
    paper.update_management("BTCUSDT", stop=100.0, target=112.5,
                            management=state)

    restored = AutoStrategyEngine.__new__(AutoStrategyEngine)
    restored.paper = paper
    restored._managed = {}
    restored._targets = {}
    pos = paper.open_position("BTCUSDT")
    assert pos["target"] == 112.5
    assert json.loads(pos["management_json"])["age"] == 37

    mt = restored._adopt("BTCUSDT", pos)
    assert mt.target == 112.5
    assert mt.stop == 100.0
    assert mt.risk == 5.0
    assert mt.age == 37 and mt.be is True and mt.scaled is True
    assert mt.best == 109.0 and mt.mfe == 110.0 and mt.mae == 97.0


def test_partial_close_preserves_target_and_management_checkpoint():
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led)
    management = {
        "side": "long", "entry": 100.0, "stop": 98.0, "target": 112.5,
        "risk": 5.0, "be": True, "scaled": True, "best": 108.0,
        "age": 12, "mfe": 109.0, "mae": 97.0,
    }
    paper.open(symbol="BTCUSDT", side="BUY", size=2.0, entry=100, stop=98,
               target=112.5)
    paper.update_management("BTCUSDT", stop=98, target=112.5,
                            management=management)
    assert paper.reduce(symbol="BTCUSDT", exit_price=107.5, fraction=0.5).action == "reduced"
    pos = paper.open_position("BTCUSDT")
    assert pos["target"] == 112.5
    assert json.loads(pos["management_json"])["age"] == 12
    open_trade = next(t for t in paper.ledger.get_paper_trades()
                      if t["status"] == "open")
    assert open_trade["target"] == 112.5


# ─────────────────────────── Kelly-capped adaptive sizing ───────────────────────────
def _pipeline(paper, led):
    from services.controls import TradingControl
    from services.signal_pipeline import SignalPipeline
    return SignalPipeline(led, paper, TradingControl(), equity=10_000,
                          risk_per_trade_pct=0.01)


def _seed_trades(paper, rrs):
    for i, rr in enumerate(rrs):
        sym = f"S{i}USDT"
        paper.open(symbol=sym, side="BUY", size=1.0, entry=100, stop=99)
        paper.close(symbol=sym, exit_price=100 + rr)   # risk=1 -> rr == move


def test_kelly_factor_neutral_without_history():
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led)
    pipe = _pipeline(paper, led)
    assert pipe._kelly_factor() == 1.0                 # no trades -> untouched


def test_kelly_factor_scales_down_when_edge_is_gone():
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led)
    pipe = _pipeline(paper, led)
    _seed_trades(paper, [-1.0] * 15 + [2.0] * 5)       # 25% win at 2:1 -> negative Kelly
    assert pipe._kelly_factor() == 0.25
    led2 = SqliteLedger(":memory:")
    paper2 = PaperExecutionEngine(led2)
    pipe2 = _pipeline(paper2, led2)
    _seed_trades(paper2, [3.0] * 10 + [-1.0] * 10)     # 50% win at 3:1 -> healthy
    assert pipe2._kelly_factor() == 1.0


def test_kelly_factor_reduces_position_size_in_pipeline():
    from services.controls import TradingControl
    from services.signal_pipeline import SignalPipeline
    led = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(led)
    # equity throttle + streak scaler off so ONLY the Kelly factor is under test
    pipe = SignalPipeline(led, paper, TradingControl(), equity=10_000,
                          risk_per_trade_pct=0.01, equity_throttle=False)
    pipe.streak_risk_scaling = False
    _seed_trades(paper, [-1.0] * 20)                   # all losers -> quarter risk
    res = pipe.process({"alert_id": "k1", "symbol": "BTCUSDT", "side": "BUY",
                        "entry": 100.0, "stop": 95.0, "confidence": 1.0})
    assert res.accepted
    risk_step = next(s for s in res.steps if s.rule == "risk")
    assert "kelly 0.25" in risk_step.detail
    # quarter of the normal size: 10_000 * 1% * 0.25 / 5 = 5 units
    assert abs(res.fill["size"] - 5.0) < 0.2           # (fill model may nudge price)


# ───────────────────── simulator parity (managed default == legacy) ─────────────────────
def test_simulate_strategy_managed_matches_legacy_without_time_stop():
    # the manager's only measured-on default is the 150-bar time stop; with it
    # disabled, the managed path must be BIT-identical to plain stop/target
    from data.market_data import get_bars
    from strategies.brain_strategy import DecisionBrain
    from strategies.custom import simulate_strategy
    bars, _ = get_bars("ZZZUSDT", n=1500, timeframe="1h")
    a = simulate_strategy(DecisionBrain("ZZZUSDT"), bars, manage=True,
                          manager=TradeManager(max_hold_bars=0))
    b = simulate_strategy(DecisionBrain("ZZZUSDT"), bars, manage=False)
    assert a["net_r"] == b["net_r"] and a["total_trades"] == b["total_trades"]
