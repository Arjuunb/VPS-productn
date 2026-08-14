# Strategy V3 Architecture Discovery Report

## Status and scope

**Current result: RESEARCH ONLY.** This is a market-behaviour study, not a
strategy backtest, an optimisation exercise, or a forward-paper approval. No
V3 production strategy was added. No V3 candidate is `UNTOUCHED_TEST_ELIGIBLE`.

V3 starts from a clean premise: discover a persistent, causal market behaviour
before choosing a strategy architecture. It does not repair V1 or V2.

## Preserved V1/V2 conclusions

| Family | Preserved conclusion | V3 treatment |
| --- | --- | --- |
| Supertrend V1/V2 | Rejected after realistic execution and stability checks; V1 pooled untouched result was -6.118R / PF 0.635. | Rejected benchmark only; no further optimisation. |
| Donchian V1/V2 | Rejected; V1 pooled untouched result was -12.704R / PF 0.420. | Retired from active research. |
| Decision Brain V1/V2 | No generalisable cost-surviving edge; V2 remained research-only. | Research evidence only; no production or V3 extension. |

The V1/V2 ledgers and reports remain intact. This report neither modifies
their strategies nor uses their results to tune a V3 design.

## Dataset, provenance, and sealed boundaries

Only official **Binance Spot / Binance Vision monthly kline** archives were
opened. Generated fixtures, synthetic candles, regression-test data, local
replay caches, CSV samples, and forward-paper records were excluded.

| Asset | Series | Start (UTC) | End (UTC) | Candles | Gaps | Source |
| --- | --- | --- | --- | ---: | ---: | --- |
| BTCUSDT | 5m | 2025-01-01 00:00 | 2025-06-30 23:55 | 52,128 | 0 | Binance Spot, official Vision archive |
| BTCUSDT | 15m | 2025-01-01 00:00 | 2025-06-30 23:45 | 17,376 | 0 | Causal aggregation of the above 5m bars |
| BTCUSDT | 1h | 2025-01-01 00:00 | 2025-06-30 23:00 | 4,344 | 0 | Causal aggregation of the above 5m bars |
| ETHUSDT | 5m | 2025-01-01 00:00 | 2025-06-30 23:55 | 52,128 | 0 | Binance Spot, official Vision archive |
| ETHUSDT | 15m | 2025-01-01 00:00 | 2025-06-30 23:45 | 17,376 | 0 | Causal aggregation of the above 5m bars |
| ETHUSDT | 1h | 2025-01-01 00:00 | 2025-06-30 23:00 | 4,344 | 0 | Causal aggregation of the above 5m bars |
| SOLUSDT | 5m | 2025-01-01 00:00 | 2025-06-30 23:55 | 52,128 | 0 | Binance Spot, official Vision archive |
| SOLUSDT | 15m | 2025-01-01 00:00 | 2025-06-30 23:45 | 17,376 | 0 | Causal aggregation of the above 5m bars |
| SOLUSDT | 1h | 2025-01-01 00:00 | 2025-06-30 23:00 | 4,344 | 0 | Causal aggregation of the above 5m bars |

The exact monthly archive SHA-256 fingerprints are in
`automation-hub/data/strategy_v3_market_study.json`. Its current SHA-256 is
`5c10bb09e12a86c30e2870e3b0e89ebffa9653e84aa291827d981565e8e64031`.

The V3 loader accepts **only January--June 2025** and rejects any requested
month list or end date beyond 2025-07-01 *before it opens an archive*. It has
no validation or test command. Therefore July--September is sealed for later
candidate validation and October--December is sealed for untouched testing.
The generated manifest records `test_data_opened: false`.

## Method

This study measures outcomes that start only after the observed condition:

- trend: 20-bar efficiency ratio >= 0.45 and a 20-bar move >= one ATR;
- pullback: five-bar countertrend move >= 0.5 ATR within that trend;
- breakout: close outside the preceding 20-bar high/low, with one-hour
  re-entry used solely to count false breakouts;
- volatility: ATR(10)/ATR(50) >= 1.25 expansion or <= 0.80 contraction;
- mean reversion: a close >= 1.5 ATR from its trailing 20-bar mean;
- regimes: trend/range combined with high/normal/low volatility; and
- sessions: Asia, Europe, US-Europe overlap, US, and late UTC.

Forward measurements are one hour and six hours after the condition. Numbers
below are **descriptive price behaviour in basis points, before execution
costs**. They are not trade returns, profitability claims, or a replacement for
the production simulator.

## Market-behaviour study (TRAIN only)

### Trend, pullback, and momentum

| Behaviour | 5m evidence | 15m evidence | 1h evidence | Finding |
| --- | --- | --- | --- | --- |
| Trend persistence | ETH is positive on both directions (+20.9 to +42.3 bps / 6h); BTC and SOL are mixed. | ETH and SOL are mostly positive; BTC is near flat. | Mostly negative or weak; only ETH/SOL shorts are positive. | **INSUFFICIENT EVIDENCE** for a global trend rule. |
| Trend-aligned pullback | Longs: BTC -6.2 (n=109), ETH +9.8 (n=102), SOL +26.1 (n=96). Shorts conflict. | Longs: BTC +24.5 (n=23), ETH +36.9 (n=57), SOL +90.6 (n=44). Shorts are inconsistent. | Samples are too small (4--30) and largely negative. | A 15m continuation premise is worth a constrained research baseline, but only **RESEARCH ONLY**. |
| Momentum continuation | Directional results conflict by asset and side. | Near-flat/negative in most cells. | Near-flat/negative in most cells. | **REJECTED** as a standalone V3 direction. |

### Breakouts and volatility

| Behaviour | Evidence | Finding |
| --- | --- | --- |
| Naked breakout follow-through | Six-hour follow-through is mixed. Example 5m longs: BTC +0.5 bps (n=3,180), ETH +2.8 (n=2,540), SOL -5.4 (n=3,089). 15m/1h remain inconsistent by asset and side. | **REJECTED** as a standalone architecture. |
| False-breakout frequency | One-hour re-entry rates are about 43--45% at 5m, 36--41% at 15m, and 20--31% at 1h (except ETH short 27%). Lower false re-entry at 1h does not establish positive directional follow-through. | Useful filter research evidence only, not an edge. |
| Volatility expansion | Absolute six-hour moves are larger under expansion than contraction in every asset/timeframe. At 15m: BTC 104.2 vs 72.6 bps; ETH 177.7 vs 123.8; SOL 207.7 vs 156.7. | **RESEARCH ONLY** for an architecture that separately proves its direction signal. |
| Volatility contraction | Smaller absolute movements than expansion everywhere, though still material in crypto. | Does not by itself justify a breakout or mean-reversion system. |

### Mean reversion, sessions, regimes, and asymmetry

- **Mean reversion — REJECTED:** 1h reversion after a 1.5-ATR deviation is
  near zero or negative in all asset/side combinations. Lower timeframes vary
  by asset and cannot support a cross-asset baseline.
- **Long/short asymmetry — INSUFFICIENT EVIDENCE:** ETH has the clearest
  lower-timeframe trend behaviour; BTC is broadly neutral and SOL changes by
  side/timeframe. A universal long or short bias would be data-mining.
- **Session behaviour — INSUFFICIENT EVIDENCE:** sessions were recorded in the
  machine-readable study, but no stable cross-asset session edge cleared the
  standard for a proposed rule. No session filter is selected.
- **Regime persistence:** normal-volatility ranges persist more than trend
  labels over six hours at 5m/15m (roughly 43--61% for normal-vol range), while
  trend labels usually persist only 1--6%. On 1h, range-normal persistence is
  about 48--60%. This supports conditioning rather than assuming a trend is
  durable, but it is not a tradable signal by itself.

## Candidate hypotheses and permanent ledger

No executable strategy has been created. The ledger is append-only at
`automation-hub/data/strategy_v3_research_ledger.jsonl`.

| ID | Architecture | Causal premise and tentative design | Expected failure regime | Status |
| --- | --- | --- | --- | --- |
| v3-mtf-trend-pullback | 15m trend + pullback continuation, with 1h context | Require a causal 1h direction/context, a 15m pullback against it, and only a closed-bar confirmation. Invalidate on loss of the 1h condition / structural stop. A future baseline would use ATR stop and fixed R exit, then realistic limit/market simulation. | Choppy normal-volatility ranges; low 15m sample count; asymmetric shorts. | **RESEARCH ONLY** |
| v3-volatility-expansion-confirmed-breakout | Expansion + independently confirmed direction | Expansion is persistent as a larger-move state, not a direction signal. A future baseline must require an independently causal directional confirmation, define a close-only entry, invalidation, and an ATR-managed exit. | High-volatility false breaks and all direction filters that fail execution costs. | **RESEARCH ONLY** |
| v3-naked-breakout | 20-bar channel breakout | Proposed only to document the failed causal premise; close beyond a 20-bar channel, invalidation on re-entry. | Directionally inconsistent follow-through and high false re-entry. | **REJECTED** |
| v3-global-mean-reversion | 1.5-ATR deviation to SMA(20) | Proposed only to document the failed causal premise; fade a trailing-mean deviation. | Persistent momentum and inconsistent cross-asset response. | **REJECTED** |

Potential future baselines must be simple, one architecture at a time, and
must use the production execution model: fees, spread, slippage, latency,
closed-candle ordering, limit TTL, gap-through treatment, risk sizing,
drawdown/daily-loss limits, cooldown and loss-streak rules. None has reached
that stage yet.

## Results not yet allowed

| Requirement | Status | Reason |
| --- | --- | --- |
| Realistic execution baseline | **BLOCKED** | No behaviour hypothesis has earned implementation yet. |
| Validation (Jul--Sep 2025) | **BLOCKED** | Must follow a version-frozen train candidate. |
| Walk-forward | **BLOCKED** | Requires a selected validation candidate. |
| Parameter neighbourhood | **BLOCKED** | No parameters selected; no grid search was run. |
| Monte Carlo tail-risk study | **BLOCKED** | Requires an executed validation trade sequence. |
| Oct--Dec untouched test | **BLOCKED** | No candidate is cryptographically/version frozen and marked `UNTOUCHED_TEST_ELIGIBLE`. |
| Forward paper | **BLOCKED** | V3 has no untouched-test eligible candidate. |

## Complexity and risk assessment

Complexity is deliberately penalised: two narrow causal research directions
are retained, no ML/AI model is added, no genetic optimisation is permitted,
and no indicator sweep has been performed. The main risk is mistaking a
directionless volatility observation or a small, asset-specific pullback sample
for a reusable trading edge. This report makes no such claim.

## Conclusion

**No V3 candidate qualifies for validation, untouched testing, or forward
paper at this point.** The valid next action is to decide whether to implement
one minimal, frozen baseline for *one* of the two research-only directions.
If neither can satisfy a pre-registered train gate without adding complexity,
the V3 branch should record zero qualified candidates and stop.
