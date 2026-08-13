#!/usr/bin/env python3
"""Reproducible strategy validation on externally supplied real OHLCV archives.

This command never downloads data and never mutates a strategy. It consumes the
official Binance Vision monthly kline ZIP layout, fingerprints all inputs and
the exact production strategy source, then writes a machine-readable evidence
bundle used by STRATEGY_VALIDATION_REPORT.md.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import math
import random
import statistics
import sys
import zipfile
from copy import copy
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "automation-hub"
for item in (str(HUB), str(ROOT)):
    if item not in sys.path:
        sys.path.insert(0, item)

from bot.data.resample import resample  # noqa: E402
from bot.types import Bar  # noqa: E402
from services.learning import LearningBook  # noqa: E402
from strategies.brain import TradeBrain  # noqa: E402
from strategies.brain_strategy import DecisionBrain  # noqa: E402
from strategies.builtin_versions import builtin_strategy_version  # noqa: E402
from strategies.custom import simulate_strategy  # noqa: E402
from strategies.donchian_strategy import DonchianStrategy  # noqa: E402
from strategies.supertrend_strategy import SupertrendStrategy  # noqa: E402

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
TIMEFRAMES = ("5m", "15m")
STRATEGIES = {
    "supertrend": SupertrendStrategy,
    "donchian": DonchianStrategy,
    "brain": DecisionBrain,
}
UTC = timezone.utc
TRAIN_START = datetime(2025, 1, 1, tzinfo=UTC)
VALIDATION_START = datetime(2025, 7, 1, tzinfo=UTC)
TEST_START = datetime(2025, 10, 1, tzinfo=UTC)
END = datetime(2026, 1, 1, tzinfo=UTC)

PRODUCTION = {
    "timeframe": "5m",
    "entry_mode": "limit",
    "limit_ttl_bars": 3,
    "min_quality_score": 60,
    "starting_equity": 1_000.0,
    "risk_per_trade_pct": 0.005,
    "max_daily_loss_pct": 0.01,
    "max_drawdown_pct": 0.10,
    "max_consecutive_losses": 3,
    "cooldown_after_loss_min": 60,
    "fee_pct_per_side": 0.0004,
    "exit_spread_slippage_latency_pct": 0.0006,
    "fill_model": "RealisticFill",
}
DATASETS: dict[tuple[str, str], list[Bar]] = {}
SIGNAL_CACHE: dict[tuple, dict[datetime, object]] = {}


class SignalReplayStrategy:
    """Replay one immutable causal signal stream through multiple A/B paths."""

    def __init__(self, symbol: str, signals: dict[datetime, object]):
        self.symbol, self.signals, self.bars = symbol, signals, []

    def on_bar(self, bar: Bar):
        self.bars.append(bar)
        if len(self.bars) > 600:
            del self.bars[:-600]
        signal = self.signals.get(bar.timestamp)
        return copy(signal) if signal is not None else None


def cached_strategy(key: str, symbol: str, timeframe: str, params: dict | None,
                    source_bars: list[Bar] | None = None):
    frozen = json.dumps(params or {}, sort_keys=True, separators=(",", ":"))
    source = (source_bars if params and source_bars is not None
              else DATASETS[(symbol, timeframe)])
    window = (source[0].timestamp.isoformat(), source[-1].timestamp.isoformat(), len(source))
    cache_key = (key, symbol, timeframe, frozen, window)
    if cache_key not in SIGNAL_CACHE:
        strategy = STRATEGIES[key](symbol, **(params or {}))
        stream = {}
        for bar in source:
            signal = strategy.on_bar(bar)
            if signal is not None:
                stream[bar.timestamp] = copy(signal)
        SIGNAL_CACHE[cache_key] = stream
    return SignalReplayStrategy(symbol, SIGNAL_CACHE[cache_key])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def load_symbol(data_dir: Path, symbol: str) -> tuple[list[Bar], dict]:
    files = [Path(p) for p in sorted(glob.glob(str(data_dir / f"{symbol}-5m-2025-*.zip")))]
    if len(files) != 12:
        raise RuntimeError(f"{symbol}: expected 12 monthly archives, found {len(files)}")
    bars: list[Bar] = []
    archive_hashes = {}
    for path in files:
        archive_hashes[path.name] = _sha256(path)
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != 1:
                raise RuntimeError(f"{path.name}: expected one CSV member")
            with archive.open(names[0]) as raw:
                rows = csv.reader(line.decode("utf-8") for line in raw)
                for row in rows:
                    if not row:
                        continue
                    stamp = int(row[0])
                    divisor = 1_000_000 if stamp > 100_000_000_000_000 else 1_000
                    bars.append(Bar(
                        datetime.fromtimestamp(stamp / divisor, tz=UTC),
                        float(row[1]), float(row[2]), float(row[3]),
                        float(row[4]), float(row[5]),
                    ))
    bars.sort(key=lambda bar: bar.timestamp)
    expected = 300
    duplicates = sum(a.timestamp == b.timestamp for a, b in zip(bars, bars[1:]))
    gaps = [int((b.timestamp - a.timestamp).total_seconds() / expected) - 1
            for a, b in zip(bars, bars[1:])
            if (b.timestamp - a.timestamp).total_seconds() > expected]
    invalid = sum(not (bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high)
                  for bar in bars)
    if duplicates or gaps or invalid or bars[0].timestamp != TRAIN_START or bars[-1].timestamp != datetime(2025, 12, 31, 23, 55, tzinfo=UTC):
        raise RuntimeError(
            f"{symbol}: integrity failed duplicates={duplicates} gaps={sum(gaps)} invalid={invalid} "
            f"coverage={bars[0].timestamp.isoformat()}..{bars[-1].timestamp.isoformat()}")
    return bars, {
        "exchange": "Binance Spot",
        "symbol": symbol,
        "timeframe": "5m",
        "bars": len(bars), "first": bars[0].timestamp.isoformat(),
        "last": bars[-1].timestamp.isoformat(), "duplicates": duplicates,
        "missing_intervals": sum(gaps), "invalid_ohlc": invalid,
        "provenance": "official Binance Vision monthly kline archives",
        "archives": archive_hashes,
    }


def resampled_integrity(symbol: str, bars: list[Bar], timeframe: str,
                        expected_seconds: int) -> dict:
    """Inventory a causally aggregated series without treating it as a new source."""
    duplicates = sum(a.timestamp == b.timestamp for a, b in zip(bars, bars[1:]))
    gaps = [int((b.timestamp - a.timestamp).total_seconds() / expected_seconds) - 1
            for a, b in zip(bars, bars[1:])
            if (b.timestamp - a.timestamp).total_seconds() > expected_seconds]
    invalid = sum(not (bar.low <= min(bar.open, bar.close)
                       <= max(bar.open, bar.close) <= bar.high) for bar in bars)
    return {
        "exchange": "Binance Spot",
        "symbol": symbol,
        "timeframe": timeframe,
        "bars": len(bars),
        "first": bars[0].timestamp.isoformat(),
        "last": bars[-1].timestamp.isoformat(),
        "duplicates": duplicates,
        "missing_intervals": sum(gaps),
        "invalid_ohlc": invalid,
        "provenance": "causal OHLCV aggregation of verified official Binance Vision 5m bars",
        "independently_downloaded": False,
    }


def strategy_fingerprint(key: str) -> dict:
    files = [
        HUB / "strategies" / f"{key}_strategy.py",
        HUB / "strategies" / "base_strategy.py",
        HUB / "strategies" / "brain.py",
        HUB / "strategies" / "custom.py",
        ROOT / "bot" / "tradecore" / "trade_manager.py",
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    instance = STRATEGIES[key]("BTCUSDT")
    code_hash = digest.hexdigest()
    config = dict(instance.params)
    config_hash = hashlib.sha256(
        json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "strategy_id": key,
        "strategy_version": builtin_strategy_version(key),
        "code_hash": code_hash,
        "config_hash": config_hash,
        "combined_hash": hashlib.sha256(f"{code_hash}:{config_hash}".encode()).hexdigest(),
        "class": f"{type(instance).__module__}.{type(instance).__name__}",
        "parameters": config,
        "source_files": [path.relative_to(ROOT).as_posix() for path in files],
    }


def slice_with_warmup(bars: list[Bar], start: datetime, end: datetime,
                      warmup: int = 600) -> tuple[list[Bar], datetime]:
    first = next(i for i, bar in enumerate(bars) if bar.timestamp >= start)
    last = next((i for i, bar in enumerate(bars) if bar.timestamp >= end), len(bars))
    return bars[max(0, first - warmup):last], start


def simulate(key: str, symbol: str, bars: list[Bar], *, trade_start_at=None,
             timeframe="5m", gate=True, risk=True, costs=True,
             delay=0, params=None, learning=False, enforce_brain=True,
             risk_overrides=None) -> dict:
    strategy = cached_strategy(key, symbol, timeframe, params, bars)
    risk_values = {
        "max_daily_loss_pct": PRODUCTION["max_daily_loss_pct"] if risk else 0.0,
        "max_drawdown_pct": PRODUCTION["max_drawdown_pct"] if risk else 0.0,
        "max_consecutive_losses": PRODUCTION["max_consecutive_losses"] if risk else 0,
        "cooldown_after_loss": PRODUCTION["cooldown_after_loss_min"] if risk else 0,
    }
    risk_values.update(risk_overrides or {})
    result = simulate_strategy(
        strategy, bars,
        fee=PRODUCTION["fee_pct_per_side"] if costs else 0.0,
        slippage=PRODUCTION["exit_spread_slippage_latency_pct"] if costs else 0.0,
        starting_balance=PRODUCTION["starting_equity"],
        risk_pct=PRODUCTION["risk_per_trade_pct"],
        brain=TradeBrain() if gate else None,
        min_score=PRODUCTION["min_quality_score"] if gate else 0,
        enforce_brain=enforce_brain,
        entry_mode=PRODUCTION["entry_mode"],
        limit_ttl_bars=PRODUCTION["limit_ttl_bars"],
        entry_delay_bars=delay,
        max_daily_loss_pct=risk_values["max_daily_loss_pct"],
        max_drawdown_pct=risk_values["max_drawdown_pct"],
        max_consecutive_losses=risk_values["max_consecutive_losses"],
        cooldown_after_loss=risk_values["cooldown_after_loss"],
        apply_confidence_sizing=learning,
        apply_streak_sizing=learning,
        trade_start_at=trade_start_at,
        retain_all=True,
    )
    result["timeframe"] = timeframe
    return result


def _max_drawdown(rs: list[float]) -> float:
    eq = peak = worst = 0.0
    for value in rs:
        eq += value
        peak = max(peak, eq)
        worst = max(worst, peak - eq)
    return worst


def _stats(trades: list[dict], field="r") -> dict:
    rs = [float(t.get(field, t.get("r", 0.0)) or 0.0) for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gp, gl = sum(wins), -sum(losses)
    mean = statistics.fmean(rs) if rs else 0.0
    sd = statistics.stdev(rs) if len(rs) > 1 else 0.0
    downside = [min(0.0, value) for value in rs]
    downside_deviation = math.sqrt(statistics.fmean(value * value for value in downside)) if downside else 0.0
    sorted_rs = sorted(rs)
    q = lambda p: sorted_rs[min(len(sorted_rs) - 1, int(p * len(sorted_rs)))] if sorted_rs else 0.0
    top5 = sum(sorted(wins, reverse=True)[:5])
    longest_win = longest_loss = current_win = current_loss = 0
    for value in rs:
        current_win = current_win + 1 if value > 0 else 0
        current_loss = current_loss + 1 if value < 0 else 0
        longest_win = max(longest_win, current_win)
        longest_loss = max(longest_loss, current_loss)
    drawdown = _max_drawdown(rs)
    return {
        "trades": len(rs), "wins": len(wins), "losses": len(losses),
        "breakeven": len(rs) - len(wins) - len(losses),
        "win_rate_pct": round(100 * len(wins) / len(rs), 2) if rs else 0.0,
        "profit_factor": round(gp / gl, 3) if gl else (99.0 if gp else 0.0),
        "net_r": round(sum(rs), 3), "expectancy_r": round(mean, 4),
        "net_return_pct_at_production_risk": round(
            sum(rs) * PRODUCTION["risk_per_trade_pct"] * 100, 4),
        "expectancy_pct_at_production_risk": round(mean * PRODUCTION["risk_per_trade_pct"] * 100, 4),
        "expectancy_usd_at_starting_equity": round(mean * PRODUCTION["risk_per_trade_pct"] * PRODUCTION["starting_equity"], 2),
        "average_win_r": round(statistics.fmean(wins), 4) if wins else 0.0,
        "average_loss_r": round(statistics.fmean(losses), 4) if losses else 0.0,
        "median_r": round(statistics.median(rs), 4) if rs else 0.0,
        "stdev_r": round(sd, 4), "p05_r": round(q(.05), 4),
        "p95_r": round(q(.95), 4), "max_drawdown_r": round(drawdown, 3),
        "longest_win_streak": longest_win, "longest_loss_streak": longest_loss,
        "recovery_factor": round(sum(rs) / drawdown, 3) if drawdown else 0.0,
        "sharpe_per_trade": round(mean / sd * math.sqrt(len(rs)), 3) if sd else 0.0,
        "sortino_per_trade": round(
            mean / downside_deviation * math.sqrt(len(rs)), 3
        ) if downside_deviation else 0.0,
        "top5_winner_share_pct": round(100 * top5 / gp, 2) if gp else 0.0,
        "avg_mfe_r": round(statistics.fmean(t.get("mfe_r", 0.0) for t in trades), 3) if trades else 0.0,
        "avg_mae_r": round(statistics.fmean(t.get("mae_r", 0.0) for t in trades), 3) if trades else 0.0,
        "avg_cost_r": round(statistics.fmean(t.get("cost_r", 0.0) for t in trades), 4) if trades else 0.0,
    }


def _session(ts: str) -> str:
    hour = _stamp(ts).hour
    if hour < 8:
        return "Asia"
    if hour < 13:
        return "London"
    if hour < 17:
        return "London-New York overlap"
    if hour < 21:
        return "New York"
    return "Late UTC"


def _regime_at(bars: list[Bar], index: int) -> str:
    if index < 31:
        return "insufficient"
    segment = bars[index - 30:index + 1]
    closes = [bar.close for bar in segment]
    net = closes[-1] - closes[0]
    path = sum(abs(b - a) for a, b in zip(closes, closes[1:])) or 1e-12
    er = abs(net) / path
    trs = [max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
           for prev, cur in zip(segment[-15:-1], segment[-14:])]
    atr_pct = (sum(trs) / len(trs)) / closes[-1] if trs and closes[-1] else 0.0
    if er >= .55:
        return "strong uptrend" if net > 0 else "strong downtrend"
    if er >= .30:
        return "weak uptrend" if net > 0 else "weak downtrend"
    # Fixed thresholds are predeclared and causal; unlike full-sample quantiles,
    # they cannot leak future volatility into an earlier classification.
    if atr_pct >= .012:
        return "high volatility"
    if atr_pct <= .003:
        return "low volatility"
    return "ranging"


def conditional(trades: list[dict], bars: list[Bar]) -> dict:
    by_time = {bar.timestamp.isoformat(): i for i, bar in enumerate(bars)}
    buckets = {"side": defaultdict(list), "session": defaultdict(list),
               "regime": defaultdict(list), "exit_reason": defaultdict(list)}
    for trade in trades:
        buckets["side"][trade["side"]].append(trade)
        buckets["session"][_session(trade["entry_time"])].append(trade)
        idx = by_time.get(trade["entry_time"], 0)
        buckets["regime"][_regime_at(bars, idx)].append(trade)
        buckets["exit_reason"][trade["exit_reason"]].append(trade)
    return {kind: {name: _stats(rows) for name, rows in sorted(groups.items())}
            for kind, groups in buckets.items()}


def monte_carlo(trades: list[dict], runs=5_000, seed=20250812) -> dict:
    rs = [float(t["r"]) for t in trades]
    if not rs:
        return {"runs": runs, "trades": 0, "available": False}
    rnd = random.Random(seed)
    nets, dds = [], []
    thresholds = (5, 10, 20)
    breached = Counter()
    for _ in range(runs):
        sampled = [rs[rnd.randrange(len(rs))] for _ in rs]
        net, dd = sum(sampled), _max_drawdown(sampled)
        nets.append(net); dds.append(dd)
        for threshold in thresholds:
            if dd >= threshold:
                breached[threshold] += 1
    nets.sort(); dds.sort()
    pct = lambda seq, p: seq[min(len(seq) - 1, int(p * len(seq)))]
    return {
        "available": True, "runs": runs, "seed": seed, "trades": len(rs),
        "net_r": {"p05": round(pct(nets, .05), 2), "median": round(pct(nets, .5), 2),
                  "p95": round(pct(nets, .95), 2)},
        "max_drawdown_r": {"median": round(pct(dds, .5), 2),
                           "p95": round(pct(dds, .95), 2)},
        "probability_loss_pct": round(100 * sum(value < 0 for value in nets) / runs, 2),
        "drawdown_probability_pct": {str(t): round(100 * breached[t] / runs, 2)
                                      for t in thresholds},
    }


def learning_ab(trades: list[dict], symbol: str) -> dict:
    """Run the real LearningBook causally over one immutable trade sequence."""
    book = LearningBook()
    history, disabled, enabled = [], [], []
    active_counts = Counter()
    for index, trade in enumerate(trades):
        confidence = float(trade.get("signal_confidence") or 1.0)
        regime = str(trade.get("signal_regime") or "")
        adjustment = book.soft_adjustment(
            symbol=symbol, regime=regime, confidence=confidence,
            minutes_since_loss=None)
        side_mult = book.side_multiplier(str(trade.get("side") or ""))
        boost = book.boost_multiplier(regime=regime, confidence=confidence)
        weight = max(0.5, min(1.25, adjustment["multiplier"] * side_mult * boost))
        for rule in adjustment["active_rules"]:
            active_counts[rule] += 1
        disabled.append({**trade, "weighted_r": float(trade["r"])})
        enabled.append({**trade, "weighted_r": float(trade["r"]) * weight})
        alert = f"validation-{index}"
        history.append({
            "symbol": symbol, "side": trade.get("side"), "status": "closed",
            "pnl": float(trade["r"]) * weight,
            "rr": float(trade["r"]) * weight, "alert_id": alert,
            "opened_at": trade.get("entry_time"), "closed_at": trade.get("exit_time"),
        })
        events = {row["alert_id"]: {
            "confidence": float(src.get("signal_confidence") or 1.0),
            "regime": str(src.get("signal_regime") or ""),
        } for row, src in zip(history, trades[:len(history)])}
        book.update(list(reversed(history)), events, now=_stamp(trade["exit_time"]))
    return {
        "disabled": _stats(disabled, "weighted_r"),
        "enabled": _stats(enabled, "weighted_r"),
        "trades_rejected": 0,
        "reason": "Production LearningBook is a bounded soft sizing input; it never vetoes trades.",
        "active_rule_applications": dict(active_counts),
        "final_active_adjustments": sorted(book.adjustments),
    }


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    lm, rm = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((a - lm) * (b - rm) for a, b in zip(left, right))
    ld = math.sqrt(sum((a - lm) ** 2 for a in left))
    rd = math.sqrt(sum((b - rm) ** 2 for b in right))
    return round(numerator / (ld * rd), 4) if ld and rd else None


def strategy_correlation(trade_sets: dict[str, list[dict]]) -> dict:
    dates = sorted({
        _stamp(trade["exit_time"]).date().isoformat()
        for trades in trade_sets.values() for trade in trades
    })
    daily, drawdowns = {}, {}
    for key, trades in trade_sets.items():
        values = defaultdict(float)
        for trade in trades:
            values[_stamp(trade["exit_time"]).date().isoformat()] += float(trade["r"])
        series = [values[day] for day in dates]
        daily[key] = series
        equity = peak = 0.0
        drawdowns[key] = []
        for value in series:
            equity += value
            peak = max(peak, equity)
            drawdowns[key].append(peak - equity)
    output = {}
    keys = list(STRATEGIES)
    for i, left in enumerate(keys):
        for right in keys[i + 1:]:
            possible = len(trade_sets[left]) * len(trade_sets[right])
            overlaps = sum(
                max(_stamp(a["entry_time"]), _stamp(b["entry_time"]))
                <= min(_stamp(a["exit_time"]), _stamp(b["exit_time"]))
                for a in trade_sets[left] for b in trade_sets[right]
            )
            output[f"{left}__{right}"] = {
                "daily_pnl_correlation": _pearson(daily[left], daily[right]),
                "drawdown_correlation": _pearson(drawdowns[left], drawdowns[right]),
                "overlapping_position_pairs": overlaps,
                "possible_position_pairs": possible,
                "position_pair_overlap_pct": round(100 * overlaps / possible, 3) if possible else 0.0,
                "calendar_days": len(dates),
            }
    return output


def signal_times(key: str, symbol: str, bars: list[Bar]) -> set[str]:
    strategy = cached_strategy(key, symbol, "5m", None)
    return {bar.timestamp.isoformat() for bar in bars if strategy.on_bar(bar) is not None}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    raw, integrity = {}, {}
    for symbol in SYMBOLS:
        raw[symbol], integrity[symbol] = load_symbol(args.data_dir, symbol)
    framed = {(symbol, "5m"): bars for symbol, bars in raw.items()}
    for symbol, bars in raw.items():
        framed[(symbol, "15m")] = resample(bars, "15m", "5m")
    DATASETS.update(framed)

    evidence = {
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment": {
            "data_source": "Binance Vision official public spot monthly klines",
            "source_template": "https://data.binance.vision/data/spot/monthly/klines/{symbol}/5m/{symbol}-5m-2025-{month}.zip",
            "symbols": list(SYMBOLS), "timeframes": list(TIMEFRAMES),
            "train": [TRAIN_START.isoformat(), VALIDATION_START.isoformat()],
            "validation": [VALIDATION_START.isoformat(), TEST_START.isoformat()],
            "untouched_test": [TEST_START.isoformat(), END.isoformat()],
            "production": PRODUCTION,
        },
        "data_integrity": integrity,
        "resampled_data_integrity": {
            symbol: resampled_integrity(symbol, framed[(symbol, "15m")], "15m", 900)
            for symbol in SYMBOLS
        },
        "strategy_fingerprints": {key: strategy_fingerprint(key) for key in STRATEGIES},
        "phases": {}, "baseline": {}, "component_ab": {}, "walk_forward": {},
        "sensitivity": {}, "stress": {}, "conditional": {}, "monte_carlo": {},
        "correlation": {}, "return_correlation": {}, "learning_ab": {},
        "risk_ab": {}, "quality_scores": {}, "decision_brain_ab": {},
    }

    # Train/validation/OOS are evaluated independently with causal warm-up.
    phase_bounds = {
        "train": (TRAIN_START, VALIDATION_START),
        "validation": (VALIDATION_START, TEST_START),
        "test": (TEST_START, END),
    }
    for key in STRATEGIES:
        evidence["phases"][key] = {}
        for symbol in SYMBOLS:
            evidence["phases"][key][symbol] = {}
            for phase, (start, end) in phase_bounds.items():
                bars, trade_start = slice_with_warmup(raw[symbol], start, end)
                result = simulate(key, symbol, bars, trade_start_at=trade_start)
                evidence["phases"][key][symbol][phase] = _stats(result["trades"])

    # Full fixed-production baseline and component A/B/C/D.
    for key in STRATEGIES:
        evidence["baseline"][key] = {}
        evidence["component_ab"][key] = {}
        for symbol in SYMBOLS:
            bars = raw[symbol]
            base = simulate(key, symbol, bars)
            evidence["baseline"][key][symbol] = _stats(base["trades"])
            evidence["component_ab"][key][symbol] = {}
            variants = {
                "A_strategy_only": dict(gate=False, risk=False, costs=False),
                "B_plus_decision_gate": dict(gate=True, risk=False, costs=False),
                "C_plus_risk_engine": dict(gate=True, risk=True, costs=False),
                "D_current_execution": dict(gate=True, risk=True, costs=True),
            }
            for label, options in variants.items():
                result = simulate(key, symbol, bars, **options)
                evidence["component_ab"][key][symbol][label] = {
                    **_stats(result["trades"]), "blocked": result["blocked_count"],
                    "missed_entries": result.get("missed_entries", 0),
                }

    # Walk-forward: fixed production parameters, rolling 3-month test windows.
    folds = []
    for month in range(4, 13):
        test_start = datetime(2025, month, 1, tzinfo=UTC)
        test_end = (datetime(2025, month + 1, 1, tzinfo=UTC)
                    if month < 12 else END)
        train_start = datetime(2025, month - 3, 1, tzinfo=UTC)
        folds.append((train_start, test_start, test_end))
    for key in STRATEGIES:
        evidence["walk_forward"][key] = {}
        for symbol in SYMBOLS:
            rows = []
            for train_start, test_start, test_end in folds:
                bars, trade_start = slice_with_warmup(raw[symbol], test_start, test_end)
                result = simulate(key, symbol, bars, trade_start_at=trade_start)
                rows.append({"train_start": train_start.isoformat(),
                             "train_end": test_start.isoformat(),
                             "test_start": test_start.isoformat(),
                             "test_end": test_end.isoformat(),
                             **_stats(result["trades"])})
            evidence["walk_forward"][key][symbol] = rows

    # Controlled nearby parameter and RR checks use validation only; test stays untouched.
    parameter_grid = {
        "supertrend": [{"period": p, "mult": m} for p, m in ((8, 3.0), (10, 2.5), (10, 3.0), (10, 3.5), (12, 3.0))],
        "donchian": [{"channel": n} for n in (20, 25, 30, 35, 40)],
        "brain": [{"conviction_threshold": n} for n in (.50, .53, .56, .59, .62)],
    }
    for key in STRATEGIES:
        evidence["sensitivity"][key] = {}
        for symbol in SYMBOLS:
            bars, trade_start = slice_with_warmup(raw[symbol], VALIDATION_START, TEST_START)
            rows = []
            for params in parameter_grid[key]:
                result = simulate(key, symbol, bars, trade_start_at=trade_start, params=params)
                rows.append({"parameters": params, **_stats(result["trades"])})
            rr_rows = []
            for rr in (1.5, 2.0, 2.5, 3.0):
                result = simulate(key, symbol, bars, trade_start_at=trade_start,
                                  params={"rr_target": rr})
                rr_rows.append({"rr_target": rr, **_stats(result["trades"])})
            evidence["sensitivity"][key][symbol] = {"parameters": rows, "rr": rr_rows}

    # Cost, latency, adjacent timeframe, conditional performance, learning/risk A/B.
    for key in STRATEGIES:
        evidence["stress"][key] = {}
        evidence["conditional"][key] = {}
        evidence["learning_ab"][key] = {}
        evidence["risk_ab"][key] = {}
        evidence["quality_scores"][key] = {}
        for symbol in SYMBOLS:
            bars, trade_start = slice_with_warmup(raw[symbol], TEST_START, END)
            production = simulate(key, symbol, bars, trade_start_at=trade_start)
            stress = {}
            for name, fee, friction, delay in (
                ("zero_cost", 0, 0, 0), ("production", .0004, .0006, 0),
                ("cost_1_5x", .0006, .0009, 0), ("cost_2x", .0008, .0012, 0),
                ("latency_1_bar", .0004, .0006, 1), ("latency_2_bars", .0004, .0006, 2),
            ):
                result = simulate(key, symbol, bars, trade_start_at=trade_start,
                                  costs=False, delay=delay)
                if fee or friction:
                    # Run with exact requested stress costs using direct simulator call.
                    result = simulate_strategy(
                        cached_strategy(key, symbol, "5m", None), bars,
                        fee=fee, slippage=friction,
                        starting_balance=1_000, risk_pct=.005, brain=TradeBrain(), min_score=60,
                        entry_mode="limit", limit_ttl_bars=3, entry_delay_bars=delay,
                        max_daily_loss_pct=.01, max_drawdown_pct=.10,
                        max_consecutive_losses=3, cooldown_after_loss=60,
                        trade_start_at=trade_start, retain_all=True)
                stress[name] = _stats(result["trades"])
            adjacent = framed[(symbol, "15m")]
            adj_bars, adj_start = slice_with_warmup(adjacent, TEST_START, END)
            stress["adjacent_15m"] = _stats(
                simulate(key, symbol, adj_bars, trade_start_at=adj_start, timeframe="15m")["trades"])
            evidence["stress"][key][symbol] = stress
            evidence["conditional"][key][symbol] = conditional(production["trades"], bars)
            evidence["learning_ab"][key][symbol] = learning_ab(
                production["trades"], symbol)
            no_risk = simulate(key, symbol, bars, trade_start_at=trade_start, risk=False)
            risk_variants = {
                "disabled": no_risk,
                "daily_loss_only": simulate(
                    key, symbol, bars, trade_start_at=trade_start, risk=False,
                    risk_overrides={"max_daily_loss_pct": .01}),
                "drawdown_only": simulate(
                    key, symbol, bars, trade_start_at=trade_start, risk=False,
                    risk_overrides={"max_drawdown_pct": .10}),
                "loss_streak_only": simulate(
                    key, symbol, bars, trade_start_at=trade_start, risk=False,
                    risk_overrides={"max_consecutive_losses": 3}),
                "cooldown_only": simulate(
                    key, symbol, bars, trade_start_at=trade_start, risk=False,
                    risk_overrides={"cooldown_after_loss": 60}),
                "all_production_guards": production,
            }
            evidence["risk_ab"][key][symbol] = {
                label: _stats(result["trades"])
                for label, result in risk_variants.items()
            }
            scores = [float(t["quality_score"]) for t in production["trades"]
                      if t.get("quality_score") is not None]
            evidence["quality_scores"][key][symbol] = {
                "accepted_count": len(scores), "mean": round(statistics.fmean(scores), 2) if scores else None,
                "min": min(scores) if scores else None, "max": max(scores) if scores else None,
                "blocked_count": production["blocked_count"],
            }

    # Decision Brain quality-gate attribution on the two independent base
    # strategies. This compares the same OOS window and execution costs with
    # safety halts disabled so one early streak cannot censor the audit.
    for key in ("supertrend", "donchian"):
        evidence["decision_brain_ab"][key] = {}
        for symbol in SYMBOLS:
            bars, trade_start = slice_with_warmup(raw[symbol], TEST_START, END)
            base = simulate(key, symbol, bars, trade_start_at=trade_start,
                            gate=False, risk=False, costs=True)
            gated = simulate(key, symbol, bars, trade_start_at=trade_start,
                             gate=True, risk=False, costs=True)
            accepted_times = {trade["entry_time"] for trade in gated["trades"]}
            rejected = [trade for trade in base["trades"]
                        if trade["entry_time"] not in accepted_times]
            rejected_regimes = defaultdict(list)
            for trade in rejected:
                rejected_regimes[str(trade.get("signal_regime") or "not captured")].append(trade)
            evidence["decision_brain_ab"][key][symbol] = {
                "base": _stats(base["trades"]),
                "with_gate": _stats(gated["trades"]),
                "signals_rejected": len(rejected),
                "rejected_winners": sum(float(t["r"]) > 0 for t in rejected),
                "rejected_losers": sum(float(t["r"]) < 0 for t in rejected),
                "rejected_net_r": round(sum(float(t["r"]) for t in rejected), 3),
                "rejected_by_strategy_regime": {
                    regime: _stats(trades)
                    for regime, trades in sorted(rejected_regimes.items())
                },
                "additional_trades": 0,
                "attribution_note": (
                    "Rejected outcomes are matched by causal entry timestamp from the ungated path; "
                    "path dependence can change later position availability."),
            }

    # OOS Monte Carlo plus signal, position, return and drawdown correlation.
    oos_trade_sets = {symbol: {} for symbol in SYMBOLS}
    for key in STRATEGIES:
        pooled = []
        for symbol in SYMBOLS:
            bars, trade_start = slice_with_warmup(raw[symbol], TEST_START, END)
            trades = simulate(key, symbol, bars, trade_start_at=trade_start)["trades"]
            oos_trade_sets[symbol][key] = trades
            pooled.extend(trades)
        evidence["monte_carlo"][key] = monte_carlo(pooled)

    for symbol in SYMBOLS:
        bars, _ = slice_with_warmup(raw[symbol], TEST_START, END)
        streams = {key: signal_times(key, symbol, bars) for key in STRATEGIES}
        evidence["correlation"][symbol] = {}
        keys = list(STRATEGIES)
        for i, left in enumerate(keys):
            for right in keys[i + 1:]:
                union = streams[left] | streams[right]
                overlap = streams[left] & streams[right]
                evidence["correlation"][symbol][f"{left}__{right}"] = {
                    "left_signals": len(streams[left]), "right_signals": len(streams[right]),
                    "same_candle": len(overlap),
                    "jaccard_pct": round(100 * len(overlap) / len(union), 3) if union else 0.0,
                }
        evidence["return_correlation"][symbol] = strategy_correlation(
            oos_trade_sets[symbol])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "symbols": list(SYMBOLS),
                      "bars": {symbol: len(raw[symbol]) for symbol in SYMBOLS}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
