"""Immutable eligibility gate for forward-paper strategy validation.

The historical validation completed on 2026-08-13 did not produce a single
candidate that may legitimately enter a forward-validation experiment.  This
module makes that conclusion machine-readable and prevents a dashboard or API
client from quietly lowering the evidence standard.

It intentionally does not create experiment tables or attach observers to the
paper engine while the eligible set is empty.  Ordinary paper trading remains
separate from validation evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final


REJECTED: Final = "REJECTED"
RESEARCH_ONLY: Final = "RESEARCH ONLY"
FORWARD_PAPER_ELIGIBLE: Final = "FORWARD PAPER ELIGIBLE"


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    strategy: str
    version: str
    symbol: str
    timeframe: str
    code_hash: str
    configuration_hash: str
    combined_hash: str
    evidence_status: str
    reason: str
    test_trades: int
    test_win_rate_pct: float
    test_profit_factor: float
    test_expectancy_r: float
    test_net_r: float
    walk_forward_positive_folds: int
    walk_forward_folds: int

    def public(self) -> dict:
        return asdict(self)


FROZEN_EXECUTION_CONFIG: Final[dict] = {
    "entry_type": "resting_limit_at_signal_price",
    "limit_lifetime_candles": 3,
    "starting_equity_usd": 1000.0,
    "risk_per_trade_pct": 0.5,
    "quality_gate_minimum": 60,
    "daily_loss_limit_pct": 1.0,
    "maximum_drawdown_halt_pct": 10.0,
    "maximum_consecutive_losses": 3,
    "loss_cooldown_minutes": 60,
    "commission_pct_per_side": 0.04,
    "exit_adverse_price_impact_pct": 0.06,
    "partial_fills": False,
    "order_rejections": False,
    "position_management": "strategy_atr_stop_and_target",
    "learning_state": "disabled_for_frozen_baseline",
}


_STRATEGIES: Final[dict] = {
    "supertrend": {
        "label": "Supertrend",
        "code_hash": "0f396e48fd14c2c5d9756f80e5310e54bda2c81da80b5d11a0639f026b5a836b",
        "configuration_hash": "5e1a47e1d864f550cc69f0640a3898ca6366ca9442e623a040e9e1cd6b2240e2",
        "combined_hash": "8b86b9ea17812eb1139a43a97ab3a027176f773b93cc5cd6e7fae623b0af2234",
        "status": REJECTED,
        "reason": "Negative pooled untouched test and failed walk-forward stability.",
        "symbols": {
            "BTCUSDT": (3, 0.00, 0.000, -1.7617, -5.285, 0),
            "ETHUSDT": (3, 0.00, 0.000, -1.1280, -3.384, 1),
            "SOLUSDT": (11, 45.45, 1.316, 0.2319, 2.551, 2),
        },
    },
    "donchian": {
        "label": "Donchian Breakout",
        "code_hash": "7a1e9d9205a1ec296f9819fd65e7230d7c68f366c4d82563d808f3261ffeeb63",
        "configuration_hash": "6641421014ecf685133faa18b209ae38560290e29c31d826a80b28fbc978aa26",
        "combined_hash": "2249acdf1577e4a365f2981c5e22af5fed65d5fce13c312ce462584b9018760b",
        "status": REJECTED,
        "reason": "Negative pooled untouched test and failed walk-forward stability.",
        "symbols": {
            "BTCUSDT": (5, 20.00, 0.177, -1.2068, -6.034, 0),
            "ETHUSDT": (7, 28.57, 0.500, -0.5367, -3.757, 2),
            "SOLUSDT": (7, 28.57, 0.587, -0.4161, -2.913, 2),
        },
    },
    "decision-brain": {
        "label": "Decision Brain",
        "code_hash": "9af1463744f4e1133ff858129f052dd2a3dbe59acd854ccd9ed7e4b51e3e8bd8",
        "configuration_hash": "9986dc1e0fab72962a122dc78c6509fd4dd38bcfef0f4fc4338596b6831d19ca",
        "combined_hash": "5d4fec252eed595b6cc637f6650cc7a33bc69b4386c02bc6e07fd4736104fd6c",
        "status": RESEARCH_ONLY,
        "reason": "Pooled untouched test was negative with only 15 trades; venue parity and robustness remain unproved.",
        "symbols": {
            "BTCUSDT": (6, 50.00, 1.302, 0.2763, 1.658, 2),
            "ETHUSDT": (6, 33.33, 0.901, -0.0963, -0.578, 3),
            "SOLUSDT": (3, 0.00, 0.000, -1.3520, -4.056, 3),
        },
    },
}


def _build_candidates() -> tuple[Candidate, ...]:
    rows: list[Candidate] = []
    for key, spec in _STRATEGIES.items():
        for symbol, metrics in spec["symbols"].items():
            trades, win_rate, profit_factor, expectancy, net_r, positive_folds = metrics
            rows.append(Candidate(
                candidate_id=f"{key}-1.0.0-{symbol.lower()}-5m",
                strategy=spec["label"],
                version="1.0.0",
                symbol=symbol,
                timeframe="5m",
                code_hash=spec["code_hash"],
                configuration_hash=spec["configuration_hash"],
                combined_hash=spec["combined_hash"],
                evidence_status=spec["status"],
                reason=spec["reason"],
                test_trades=trades,
                test_win_rate_pct=win_rate,
                test_profit_factor=profit_factor,
                test_expectancy_r=expectancy,
                test_net_r=net_r,
                walk_forward_positive_folds=positive_folds,
                walk_forward_folds=9,
            ))
    return tuple(rows)


CANDIDATES: Final[tuple[Candidate, ...]] = _build_candidates()


class ForwardValidationEligibilityError(RuntimeError):
    pass


def candidate(candidate_id: str) -> Candidate | None:
    return next((row for row in CANDIDATES if row.candidate_id == candidate_id), None)


def require_eligible(candidate_id: str) -> Candidate:
    row = candidate(candidate_id)
    if row is None:
        raise KeyError(candidate_id)
    if row.evidence_status != FORWARD_PAPER_ELIGIBLE:
        raise ForwardValidationEligibilityError(
            f"{row.strategy} {row.version} {row.symbol} {row.timeframe} is "
            f"{row.evidence_status}: {row.reason}"
        )
    return row


def summary() -> dict:
    counts = {status: 0 for status in (REJECTED, RESEARCH_ONLY, FORWARD_PAPER_ELIGIBLE)}
    for row in CANDIDATES:
        counts[row.evidence_status] += 1
    return {
        "stage_status": "BLOCKED_NO_ELIGIBLE_CANDIDATES",
        "verdict": "INSUFFICIENT FORWARD DATA",
        "validation_started_at": None,
        "active_experiments": [],
        "candidate_counts": counts,
        "candidates": [row.public() for row in CANDIDATES],
        "frozen_execution_config": dict(FROZEN_EXECUTION_CONFIG),
        "historical_evidence": {
            "exchange": "Binance Spot",
            "instrument": "USDT spot",
            "timeframe": "5m",
            "start_utc": "2025-01-01T00:00:00Z",
            "end_utc": "2025-12-31T23:55:00Z",
            "candles_per_symbol": 105120,
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            "bundle_sha256": "5254d64de2170b64f4f101e010265c9d2659830a704b54e6ee2f87c4e4ca36ba",
            "forward_venue": "Kraken Spot",
            "exact_venue_parity": "FAILED",
        },
        "forward_evidence": {
            "experiments": 0,
            "counted_candles": 0,
            "decisions": 0,
            "trades": 0,
            "note": "Ordinary paper trades are excluded from forward-validation evidence.",
        },
        "next_action": (
            "Create a new immutable research version and require it to pass the "
            "untouched real-data and robustness gates before starting one isolated "
            "forward-paper experiment."
        ),
    }
