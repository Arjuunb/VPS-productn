# Core Trading Engine V2 — Shadow Foundation

This package is the first, non-executing phase of Core Trading Engine V2.
It wraps existing deterministic market, multi-timeframe, economic-event and
session analysis in immutable evidence contracts. It does **not** call the
legacy `AutoStrategyEngine`, `SignalPipeline`, approval system, paper engine or
live bridge; it cannot open, close, size or route a trade.

## Current boundary

`MarketSnapshot` is an immutable input containing only data known at its
timezone-aware `as_of` time. `ShadowEvidenceRunner.evaluate(snapshot)` returns
four evidence records:

- `market_context` — existing `services.market_analysis` result;
- `trend` — existing causal `services.mtf_engine` result;
- `news_event` — existing `services.econ_guard` policy, with calendar
  connectedness and freshness enforced honestly;
- `session` — existing standard UTC session convention, explicitly labelled as
  a Phase-1 approximation pending a DST-aware calendar engine.

Results are always `WAIT` and `execution_eligible=False`, even if an evidence
record is `PASS`. A high-impact event blackout is represented as a `VETO`; it
still has no routing effect in this phase.

## Phase 2: proposal and confidence contracts

`proposal_from_signal()` converts an existing strategy signal into a validated
`StrategyProposal`. The existing entry, stop and target are retained exactly;
the stop is documented as the proposal's invalidation level. Invalid direction,
stop, target or RR values fail before a later risk phase can see them.

`ConfidenceComposer` uses versioned fixed weights: strategy conviction 45%,
TradeBrain quality 35%, and MTF trend 20%. Missing values contribute zero and
are never re-normalised, so unavailable data cannot inflate confidence. The
output is a 0–100 score with `low`, `medium` or `high` category and a complete
weight breakdown. It remains non-executing.

## Phase 3: mandatory risk assessment in shadow mode

`RiskBridge` adapts the existing standalone `tradexa.risk.RiskEngine`, which
already owns policy evaluation and sizing. It replaces the context's proposal
with the exact validated V2 proposal before asking the risk engine to assess
it, then records every rule result in a typed `RiskAssessment`.

The bridge fails closed: an unavailable policy or policy error returns `VETO`
with no size. An `ALLOW` is still only evidence in this phase—it cannot route a
paper or live order. Existing `SignalPipeline` risk wiring remains unchanged.

## Phase 4: complete market-context evidence

The shadow runner now emits additional 1H trend, liquidity, volume and
volatility evidence alongside its original MTF/context/event/session evidence.
It executes the existing market analysis once per snapshot and shares that
result with dependent evidence engines. Liquidity output uses declared
equal-level and wick-through/close-back criteria; no institutional-trap label
is guessed. Volume reports candle pressure as a proxy and reports order-flow
delta as unavailable unless an actual feed is supplied. Volatility reports ATR
and only reports spread/slippage when snapshot metadata provides them.

Named Sydney, Tokyo, London and New York sessions use IANA timezone data and
08:00–17:00 local weekday windows, so DST is represented. This is transparent
session context rather than a claim about a particular exchange's calendar.

## Phase 5: V2 read and diagnostics API

`/api/v2` is an additive, session-authenticated surface. `POST
/api/v2/decisions/evaluate` accepts a bounded, timezone-aware research snapshot,
validates its OHLCV bars, evaluates V2 shadow evidence, and persists the result
to a separate `core_v2_shadow_decisions` SQLite table. It has no executor
dependency and records `execution_eligible=false` at both application and
database levels.

The dashboard-facing read models are `GET /api/v2/decisions/latest`, `GET
/api/v2/decisions/{id}`, `GET /api/v2/health/engines`, and `GET
/api/v2/metrics/decision`. Legacy `/api/v1` endpoints and legacy decision
tables are unchanged.

## Automatic paper-cycle shadow observations

Set `HUB_CORE_V2_MODE=shadow` to attach `CoreV2ShadowObserver` to the existing
autonomous engine. It records evidence from each closed paper-engine bar using
the strategy's existing bar history. `off` is the production default. No other
value is accepted, and there is no V2 execution mode.

## Test

Install development dependencies at the repository root, then run:

```bash
python -m pip install -e '.[dev]'
python -m pytest automation-hub/tests/test_core_engine_v2_shadow.py -q
```

The existing paper-trading suite remains the authority for legacy execution
behaviour. V2 will only be attached to that path after shadow comparison and a
separate approval.
