"""Smoke tests for the DecisionBrain backtest / walk-forward harness."""
from datetime import datetime, timedelta, timezone

from backtest import Metrics, performance_report, resample, run, walk_forward, _metrics
from data.market_data import get_bars
from bot.types import Bar


def test_performance_report_keys_and_sanity():
    # "ZZZUSDT" isn't a bundled sample -> deterministic synthetic of length n.
    bars, _ = get_bars("ZZZUSDT", n=2000, timeframe="1h")
    rep = performance_report(bars, train=800, test=400)
    for k in ("trades", "win_rate", "profit_factor", "expectancy_r",
              "max_drawdown_r", "longest_losing_streak", "sharpe", "cagr_pct"):
        assert k in rep
    assert rep["trades"] > 0
    assert 0 <= rep["win_rate"] <= 100
    assert rep["max_drawdown_r"] >= 0
    assert rep["longest_losing_streak"] >= 0


def test_run_produces_trades_and_metrics():
    bars, _ = get_bars("BTCUSDT", n=2000, timeframe="1h")
    rs = run(bars, threshold=0.4, rr=2.0)
    m = _metrics(rs)
    assert isinstance(m, Metrics)
    assert m.trades > 0
    assert m.profit_factor >= 0
    assert 0 <= m.win_rate <= 100


def test_resample_aggregates_bars():
    bars, _ = get_bars("BTCUSDT", n=160, timeframe="1h")
    r = resample(bars, 4)
    assert len(r) == 40
    # OHLC of an aggregated candle must be internally consistent
    assert r[0].high >= r[0].open and r[0].high >= r[0].close
    assert r[0].low <= r[0].open and r[0].low <= r[0].close


def test_resample_uses_epoch_boundary_and_drops_edge_fragments():
    start = datetime(2025, 1, 1, 0, 15, tzinfo=timezone.utc)
    bars = [Bar(start + timedelta(minutes=15 * i), i, i + 2, i - 1, i + 1, 10)
            for i in range(11)]
    hourly = resample(bars, 4)
    assert [row.timestamp.minute for row in hourly] == [0, 0]
    assert [row.timestamp.hour for row in hourly] == [1, 2]
    # 00:15..00:45 and the final 03:00 fragment are incomplete and excluded.
    assert len(hourly) == 2


def test_metrics_math():
    m = _metrics([2.0, -1.0, 2.0, -1.0])   # 2 wins (+2), 2 losses (-1)
    assert m.trades == 4
    assert m.win_rate == 50.0
    assert round(m.profit_factor, 2) == 2.0   # gross 4 / gross loss 2
    assert round(m.net_r, 2) == 2.0


def test_walk_forward_returns_oos_metrics():
    bars, _ = get_bars("BTCUSDT", n=1600, timeframe="1h")
    agg, folds = walk_forward(bars, train=600, test=300)
    assert isinstance(agg, Metrics)
    assert len(folds) >= 1


def test_supertrend_donchian_ensemble_strategies_run():
    bars, _ = get_bars("BTCUSDT", n=2000, timeframe="1h")
    for strat in ("supertrend", "donchian", "ensemble"):
        m = _metrics(run(bars, strategy=strat))
        assert isinstance(m, Metrics)
        assert m.trades > 0, f"{strat} produced no trades"
