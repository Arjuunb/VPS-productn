# TradeLogX Forward Paper Validation Report

Generated: 2026-08-14
Historical evidence baseline: `STRATEGY_VALIDATION_REPORT.md` at commit `48cb0e4`
Forward-validation implementation baseline: local changes based on commit `af70461`
Scope: Supertrend 1.0.0, Donchian Breakout 1.0.0, Decision Brain 1.0.0
Final status: **BLOCKED — NO FORWARD PAPER ELIGIBLE CANDIDATE**

## Executive result

Forward paper validation did **not** start. This is the required safety outcome, not an incomplete deployment.

The preceding real-data validation found:

| Strategy | Frozen historical decision | Forward eligibility |
| --- | --- | --- |
| Supertrend 1.0.0 | Pooled untouched test -6.118R, PF 0.635, 17 trades; walk-forward failed | **REJECTED** |
| Donchian Breakout 1.0.0 | Pooled untouched test -12.704R, PF 0.420, 19 trades; walk-forward failed | **REJECTED** |
| Decision Brain 1.0.0 | Pooled untouched test -2.976R, PF 0.806, 15 trades; exact venue parity failed | **RESEARCH ONLY** |

There are zero **FORWARD PAPER ELIGIBLE** candidates. The protocol explicitly forbids lowering the standard merely to populate a paper terminal. Therefore:

- no experiment ID was issued;
- no forward boundary timestamp was declared;
- no warm-up, recovery, replay or ordinary paper records were counted;
- no forward performance or profitability claim is made;
- no candidate is described as live-ready.

## Evidence status definitions

- **VERIFIED** — established by the immutable historical evidence, code inspection, or a passing validation check.
- **FAILED** — the tested requirement did not hold.
- **INSUFFICIENT EVIDENCE** — the sample does not support the claim.
- **BLOCKED** — a preceding mandatory gate was not satisfied, so the later stage was not run.

## Stage 1 — frozen candidate versions

**VERIFIED.** Nine candidate/market combinations are frozen by the server registry in `automation-hub/services/forward_validation.py`. Every entry contains the strategy version, symbol, timeframe, code hash, configuration hash, combined hash, historical result and evidence classification.

| Strategy | Version | Code SHA-256 | Configuration SHA-256 | Combined SHA-256 |
| --- | --- | --- | --- | --- |
| Supertrend | 1.0.0 | `0f396e48fd14c2c5d9756f80e5310e54bda2c81da80b5d11a0639f026b5a836b` | `5e1a47e1d864f550cc69f0640a3898ca6366ca9442e623a040e9e1cd6b2240e2` | `8b86b9ea17812eb1139a43a97ab3a027176f773b93cc5cd6e7fae623b0af2234` |
| Donchian Breakout | 1.0.0 | `7a1e9d9205a1ec296f9819fd65e7230d7c68f366c4d82563d808f3261ffeeb63` | `6641421014ecf685133faa18b209ae38560290e29c31d826a80b28fbc978aa26` | `2249acdf1577e4a365f2981c5e22af5fed65d5fce13c312ce462584b9018760b` |
| Decision Brain | 1.0.0 | `9af1463744f4e1133ff858129f052dd2a3dbe59acd854ccd9ed7e4b51e3e8bd8` | `9986dc1e0fab72962a122dc78c6509fd4dd38bcfef0f4fc4338596b6831d19ca` | `5d4fec252eed595b6cc637f6650cc7a33bc69b4386c02bc6e07fd4736104fd6c` |

The frozen execution contract is: resting limit at signal price; three-candle lifetime; US$1,000 isolated start; 0.5% risk; quality score at least 60; 1% daily loss limit; 10% drawdown halt; three-loss halt; 60-minute cooldown; 0.04% commission per side; 0.06% adverse exit impact; strategy ATR stop and target; mutable learning disabled for the baseline.

No strategy or execution parameter was changed during this stage.

## Stage 2 — eligibility gate

**VERIFIED and closed.** Candidate classification is enforced server-side:

- six strategy/market candidates are **REJECTED**;
- three Decision Brain market candidates are **RESEARCH ONLY**;
- zero candidates are **FORWARD PAPER ELIGIBLE**.

`POST /forward-validation/experiments` returns HTTP 409 for every frozen current candidate. An unknown candidate returns HTTP 404. This prevents a direct API client from bypassing the dashboard decision.

## Stage 3 — minimum viable forward experiment

**BLOCKED.** No qualifying strategy/asset/timeframe combination exists. No worker was repurposed and no ordinary Trading Instance was relabelled as validation evidence.

## Stage 4 — forward boundary

**BLOCKED.** `validation_started_at` remains `null`. A future candidate must receive an explicit UTC boundary after warm-up and cursor recovery are complete. Data at or before that boundary must be excluded.

## Stage 5 — experiment identity and record association

**BLOCKED.** No experiment ID exists, so no candle, decision, rejection, order, position, trade or P&L record is associated with an experiment. Persistence tables were intentionally not created before an eligible candidate exists and receives a reviewed immutable schema.

## Stage 6 — historical out-of-sample baseline

**VERIFIED.** The immutable baseline uses official Binance Vision spot klines:

| Exchange | Instrument | Symbols | Timeframe | Start UTC | End UTC | Candles per symbol | Gaps |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| Binance Spot | USDT spot | BTCUSDT, ETHUSDT, SOLUSDT | 5m | 2025-01-01 00:00 | 2025-12-31 23:55 | 105,120 | 0 |

Evidence bundle SHA-256: `5254d64de2170b64f4f101e010265c9d2659830a704b54e6ee2f87c4e4ca36ba`.

The production forward venue is Kraken Spot. Exact venue parity **FAILED**, so Binance historical results are not represented as Kraken profitability evidence.

## Stages 7–22 — decision, execution, drift and integrity evidence

All are **BLOCKED** by Stage 2:

| Stage | Required evidence | Status |
| ---: | --- | --- |
| 7 | Every closed-candle decision and rejection | **BLOCKED** |
| 8 | Exactly-once experiment/version/symbol/timeframe/candle key | **BLOCKED** |
| 9 | Predicted versus realized entry/exit and execution attribution | **BLOCKED** |
| 10 | Trades, WR, PF, expectancy, net return, DD, costs, signal/rejection metrics | **BLOCKED** |
| 11 | Forward versus historical baseline comparison | **BLOCKED** |
| 12 | Rolling drift detector | **BLOCKED** |
| 13 | Confidence intervals | **BLOCKED** |
| 14 | 10/20/50/100/200-trade checkpoints | **BLOCKED** |
| 15 | Regime analysis | **BLOCKED** |
| 16 | Long/short decomposition | **BLOCKED** |
| 17 | Learning Engine versus frozen baseline comparison | **BLOCKED** |
| 18 | Risk-system attribution | **BLOCKED** |
| 19 | Execution attribution | **BLOCKED** |
| 20 | Reconnect/restart/cursor/duplication integrity | **BLOCKED** |
| 21 | Infrastructure incidents separated from strategy losses | **BLOCKED** |
| 22 | No-optimisation experiment lock | **BLOCKED** |

Generated fixtures, synthetic candles, regression datasets, replay caches, stale local market caches, warm-up history, cursor recovery bars, and legacy paper trades remain excluded from all forward claims.

## Stage 23 — dashboard evidence panel

**VERIFIED for the current blocked state.** The dashboard includes a **Forward Validation** page backed by `GET /forward-validation`. It displays:

- zero eligible candidates;
- rejected/research-only counts;
- zero active experiments and zero counted forward records;
- frozen candidate hashes and untouched-test metrics;
- historical data provenance and the failed venue-parity result;
- the required next action.

It does not show an equity curve, drift chart or checkpoint success state because no experiment exists. Missing evidence is displayed as missing, not replaced with demo data.

## Stages 24–26 — equity curves, snapshots and monitoring cadence

**BLOCKED.** There is no experiment from which to calculate a baseline curve, actual forward curve, daily immutable snapshot, milestone report, drawdown alert or regime alert. Ordinary paper-account curves are not substituted.

## Stages 27–28 — decision rules and promotion policy

**VERIFIED as policy; not evaluated against forward results.** A future candidate may be labelled only:

- **INSUFFICIENT FORWARD DATA** before its minimum sample;
- **FORWARD EDGE CONSISTENT** only if confidence, drift, risk, execution and integrity criteria all hold;
- **FORWARD EDGE DEGRADED** if the forward distribution materially underperforms the frozen baseline;
- **FORWARD EDGE FAILED** if the failure rules are met.

No result from this report authorizes live trading. Any future promotion requires a separate human-approved decision after sufficient genuine forward evidence.

## Stage 29 — current eligibility table

| Candidate group | Eligibility | Reason |
| --- | --- | --- |
| Supertrend 1.0.0 · BTC/ETH/SOL · 5m | **REJECTED** | Negative pooled untouched test; walk-forward instability; venue parity failed |
| Donchian Breakout 1.0.0 · BTC/ETH/SOL · 5m | **REJECTED** | Negative pooled untouched test; walk-forward instability; venue parity failed |
| Decision Brain 1.0.0 · BTC/ETH/SOL · 5m | **RESEARCH ONLY** | Negative pooled test, 15-test-trade sample, robustness and venue parity unproved |

## Stage 30 — final evidence summary

| Required final field | Result |
| --- | --- |
| Candidate versions | Frozen and exposed by API/dashboard |
| Historical baseline | Verified official Binance Spot 2025 dataset; exact Kraken parity failed |
| Forward sample size | 0 experiments, 0 counted candles, 0 decisions, 0 trades |
| Forward metrics | **INSUFFICIENT EVIDENCE** — not calculated |
| Regime/side analysis | **BLOCKED** |
| Historical-versus-forward comparison | **BLOCKED** |
| Drift verdict | **BLOCKED** |
| Execution/risk attribution | **BLOCKED** |
| Infrastructure integrity | **BLOCKED** |
| Readiness classification | **BLOCKED — NO ELIGIBLE CANDIDATE** |

## Required next step

Research must produce a new immutable strategy version. That version must pass a chronological untouched test on genuine exchange data, robustness and cost checks, walk-forward stability, and production-venue parity review. Only then may a separately reviewed migration create append-only experiment records and start one isolated paper-forward worker after an explicit boundary.

Until that happens, continuing normal paper trading is allowed for product operation, but its records must not be used to claim that a strategy passed this forward-validation protocol.
