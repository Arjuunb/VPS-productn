# TradeLogX Research Strategy V2 Report

Generated: 2026-08-14
Development data: official Binance Vision Spot 5-minute archives, January–September 2025
Untouched test: October–December 2025 — **NOT OPENED**
Final result: **0 candidates frozen; 0 forward-paper eligible**

## Previous Gate Result

| Frozen V1 | Code SHA-256 | Configuration SHA-256 | Previous verdict | Evidence |
| --- | --- | --- | --- | --- |
| Supertrend 1.0.0 | `0f396e48fd14c2c5d9756f80e5310e54bda2c81da80b5d11a0639f026b5a836b` | `5e1a47e1d864f550cc69f0640a3898ca6366ca9442e623a040e9e1cd6b2240e2` | **REJECTED** | `STRATEGY_VALIDATION_REPORT.md` |
| Donchian 1.0.0 | `7a1e9d9205a1ec296f9819fd65e7230d7c68f366c4d82563d808f3261ffeeb63` | `6641421014ecf685133faa18b209ae38560290e29c31d826a80b28fbc978aa26` | **REJECTED** | `STRATEGY_VALIDATION_REPORT.md` |
| Decision Brain 1.0.0 | `9af1463744f4e1133ff858129f052dd2a3dbe59acd854ccd9ed7e4b51e3e8bd8` | `9986dc1e0fab72962a122dc78c6509fd4dd38bcfef0f4fc4338596b6831d19ca` | **RESEARCH ONLY** | `STRATEGY_VALIDATION_REPORT.md` |

The V1 source files were not modified. V2 classes live only in `strategies/research_v2.py`, are absent from the production registry, and cannot be selected by a Trading Instance.

## Data Boundary and Integrity

Development opened only the nine monthly archives from January through September for BTCUSDT, ETHUSDT and SOLUSDT. It did not open the October, November or December ZIP files.

| Source | Symbols | Native TF | Development start | Development end | Native candles per symbol | Duplicates | Gaps | Invalid OHLC |
| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Binance Vision Spot | BTCUSDT, ETHUSDT, SOLUSDT | 5m | 2025-01-01 00:00 UTC | 2025-09-30 23:55 UTC | 78,624 | 0 | 0 | 0 |

The 15m and 1h series are causal aggregations of the same verified observations. They are separate research configurations, not independently downloaded datasets. Generated fixtures, synthetic candles, replay caches, paper ledgers and stale local market caches were excluded.

## Failure Diagnosis

### Supertrend V1

- Raw January–September signals were marginally positive: +137.808R over 6,073 trades, expectancy +0.0227R, PF 1.034.
- Realistic costs changed that to -3,717.256R, expectancy -0.6121R, PF 0.456.
- Low-volatility transitions, false flips and turnover made the tiny raw margin non-tradeable.
- Risk and Decision gates reduced participation but did not recover positive expectancy.
- V1 parameter neighbourhood and untouched cost stress had already failed.

### Donchian V1

- Raw signals were already negative: -75.184R over 3,875 trades, expectancy -0.0194R, PF 0.973.
- Realistic execution widened the loss to -2,407.118R, expectancy -0.6212R, PF 0.455.
- The problem therefore was not only fees. Marginal 5-minute channel breaches lacked underlying follow-through.
- Risk, learning and the Decision gate reduced the number of losses but did not create edge.

### Decision Brain V1

- Raw signals were marginally positive: +225.011R over 3,807 trades, expectancy +0.0591R, PF 1.081.
- Costs changed the result to -1,315.674R, expectancy -0.3456R, PF 0.665.
- Risk containment reduced the development sample to 32 trades and +0.642R, but the causal LearningBook changed the weighted result to -1.964R.
- Prior evidence showed asset and directional instability; complexity did not produce a cost-robust universal edge.

## Performance Attribution

January–September only. Drawdown is the worst individual-symbol drawdown. Because different gates alter later position availability, each stage is a causal replay of the same strategy signal stream, not an arithmetic adjustment.

| Strategy | Stage | Trades | Net R | Exp R | PF | Worst symbol DD R |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Supertrend V1 | Raw signal | 6,073 | +137.808 | +0.0227 | 1.034 | 62.552 |
|  | After execution costs | 6,073 | -3,717.256 | -0.6121 | 0.456 | 2,406.243 |
|  | After Risk Engine | 15 | -15.978 | -1.0652 | 0.195 | 6.108 |
|  | After Learning | 15 | -14.631 | -0.9754 | 0.209 | 5.446 |
|  | After Decision Brain | 18 | -16.842 | -0.9357 | 0.257 | 7.267 |
|  | Final realised weighting | 18 | -14.781 | -0.8212 | 0.247 | 5.747 |
| Donchian V1 | Raw signal | 3,875 | -75.184 | -0.0194 | 0.973 | 60.391 |
|  | After execution costs | 3,875 | -2,407.118 | -0.6212 | 0.455 | 1,347.322 |
|  | After Risk Engine | 9 | -14.318 | -1.5909 | 0.000 | 5.523 |
|  | After Learning | 9 | -14.318 | -1.5909 | 0.000 | 5.523 |
|  | After Decision Brain | 10 | -11.901 | -1.1901 | 0.126 | 4.955 |
|  | Final realised weighting | 10 | -11.901 | -1.1901 | 0.126 | 4.955 |
| Decision Brain V1 | Raw signal | 3,807 | +225.011 | +0.0591 | 1.081 | 67.420 |
|  | After execution costs | 3,807 | -1,315.674 | -0.3456 | 0.665 | 603.666 |
|  | After Risk Engine | 32 | +0.642 | +0.0201 | 1.021 | 4.729 |
|  | After Learning | 32 | -1.964 | -0.0614 | 0.919 | 4.729 |
|  | After Decision Brain | 32 | +0.642 | +0.0201 | 1.021 | 4.729 |
|  | Final realised weighting | 32 | -1.964 | -0.0614 | 0.919 | 4.729 |

## Research Hypotheses

Only three bounded structural hypotheses were implemented.

| Family | Problem and evidence | Exact change | Expected mechanism | Failure condition |
| --- | --- | --- | --- | --- |
| Supertrend V2 | Tiny raw edge destroyed by cost and low-efficiency turnover | Require causal 30-bar efficiency ratio and minimum ATR percentage at a flip | Exclude weak transitions unlikely to cover friction | Reject on negative/fragile validation, insufficient sample, unstable folds or cost failure |
| Donchian V2 | Raw V1 signal itself negative; false breakout/follow-through problem | Require close penetration in ATR units, minimum channel width and volume ratio | Admit only observable expansion breakouts | Retire if confirmed breakouts remain negative or sample-poor |
| Decision Brain V2 | Asset/directional instability and cost sensitivity | Require causal efficiency; compare bidirectional with an explainable long-only policy | Remove low-persistence votes and measure side asymmetry | Reject on train/validation conflict, asset fragility, unstable folds or tail risk |

No new black-box feature, optimiser, test-selected filter or production strategy registration was added.

## V2 Architecture

Research source hash: `447aea596e654f6ac7dad9b8c13e0d76651738872b230af99d2a767ce240b240`.

- Version: `2.0.0-research.1` for all three isolated classes.
- Execution: three-candle resting limit; realistic spread, slippage, latency and 0.04% commission per side; conservative stop/gap handling.
- Risk: 0.5% per trade, 1% daily loss, 10% maximum drawdown, three-loss halt and 60-minute cooldown.
- Decision gate: production TradeBrain score at least 60.
- Starting equity: US$1,000 per isolated run.
- Search size: three neighbouring structural values for Supertrend, three for Donchian, three Brain efficiency values plus one predeclared long-only comparison.
- Timeframes: 5m, 15m and 1h only.
- Assets: BTC, ETH and SOL.

Total immutable experiments: **30**.

## Training Results

Best later validation row per strategy family is shown; a positive cell is not a pass unless all gates hold.

| V2 experiment | TF | Parameters | Train trades | Train Net R | Train Exp R | Train PF |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| Supertrend `rv2-54488072bc89b8a74e45` | 1h | ER ≥ 0.30 | 1 | -1.030 | -1.0300 | 0.000 |
| Donchian `rv2-6c38d00a63cc2b6c86da` | 15m | volume ≥ 0.90x | 37 | -9.453 | -0.2555 | 0.722 |
| Brain `rv2-18524838c613cb99530f` | 5m | ER ≥ 0.20, long-only | 15 | -5.099 | -0.3399 | 0.683 |

All three failed the predeclared non-negative TRAIN gate.

## Validation Results

| V2 experiment | Validation trades | Net R | Exp R | PF | Positive assets | Selection result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Supertrend 1h ER 0.30 | 2 | +1.269 | +0.6345 | 2.145 | 1/3 | **REJECTED — tiny/fragile** |
| Donchian 15m volume 0.90x | 14 | -3.321 | -0.2372 | 0.741 | 1/3 | **REJECTED** |
| Brain 5m ER 0.20 long-only | 24 | +6.719 | +0.2800 | 1.333 | 2/3 | **RESEARCH ONLY — TRAIN/WF failed** |

The Brain result is not a pass: BTC +1.109R and ETH +7.119R were offset by SOL -1.509R; its preceding TRAIN result was negative on BTC and ETH, and all 24 validation trades occurred before the persistent risk halt ended participation.

## Walk-Forward Results

Fixed-parameter monthly chronological folds from April through September were recorded. No fold optimisation occurred.

| Candidate | Positive months | Negative months | Zero-trade months | Result |
| --- | ---: | ---: | ---: | --- |
| Supertrend 1h ER 0.30 | 1 | 1 | 4 | **FAILED / insufficient** |
| Donchian 15m volume 0.90x | 0 | 2 | 4 | **FAILED** |
| Brain 5m ER 0.20 long-only | 1 | 0 | 5 | **FAILED / insufficient** |

Zero-trade months are not counted as profitable folds. The concentration in a single month fails temporal stability.

## Parameter Stability

- Supertrend 1h: ER 0.20 and 0.25 each produced -0.017R in validation; ER 0.30 produced +1.269R from only two trades. This is not a stable plateau.
- Donchian 15m: volume thresholds 0.90/1.00/1.10 produced -3.321R/-7.026R/-8.186R. The entire neighbourhood was negative.
- Brain 5m bidirectional ER 0.15/0.20/0.25 produced -1.938R/+1.366R/-3.324R. Only the middle value was positive and PF was only 1.053. Long-only improved that single configuration but did not repair TRAIN or fold stability.

Parameter robustness: **FAILED for all three families**.

## Multi-Asset Results

The best development rows were not universal:

- Supertrend: one ETH validation winner and one SOL loser; BTC had no trades.
- Donchian: BTC was approximately flat (+0.120R), ETH -1.728R, SOL -1.713R.
- Brain long-only: BTC +1.109R, ETH +7.119R, SOL -1.509R; TRAIN was BTC -4.635R, ETH -4.508R, SOL +4.044R.

No universal or stable asset-specific edge was established.

## Execution Stress

All reported V2 results already include the frozen realistic execution contract. No candidate passed TRAIN, validation, sample, neighbourhood and walk-forward gates, so additional 1.5x/2x cost and latency sweeps were not used to rescue or rank a candidate. They are **BLOCKED — NO FROZEN CANDIDATE**.

The V1 attribution independently demonstrates that the marginal raw Supertrend and Brain signals did not survive production costs.

## Monte Carlo

Development-only trade resampling does not create candles or inflate sample size.

| V2 row | Observed trades | Median Net R | P05–P95 Net R | P(loss) | Median DD R | P95 DD R |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Supertrend 1h ER 0.30 | 3 | +0.24 | -3.25 to +3.72 | 29.72% | 2.14 | 3.25 |
| Donchian 15m volume 0.90x | 51 | -13.13 | -32.02 to +6.13 | 86.46% | 19.28 | 34.66 |
| Brain 5m ER 0.20 long-only | 39 | +1.57 | -18.35 to +22.24 | 44.72% | 11.87 | 24.09 |

Supertrend is statistically meaningless at three trades. Donchian tail risk is unacceptable. Brain uncertainty and tail drawdown are too large for eligibility.

## Untouched Test

**NOT OPENED.** The freeze manifest contains zero selected candidates and `test_opened: false`. The separate test command returned:

`BLOCKED_NO_FROZEN_CANDIDATE`

It returned before loading any test archive and did not create an untouched-test output file. This preserves October–December for a genuinely frozen later version.

## V1 vs V2

These comparisons use validation windows only and must not be confused with an untouched V2 comparison.

| Family | V1 validation | Best V2 validation observation | Interpretation |
| --- | --- | --- | --- |
| Supertrend | 20 trades, -11.572R, Exp -0.5786, PF 0.495 | 2 trades, +1.269R, Exp +0.6345, PF 2.145 on 1h | Trade suppression, not evidence; sample collapsed and neighbours failed |
| Donchian | 16 trades, -13.518R, Exp -0.8449, PF 0.306 | 14 trades, -3.321R, Exp -0.2372, PF 0.741 on 15m | Loss reduced, but still no edge |
| Decision Brain | 33 trades, +2.282R, Exp +0.0692, PF 1.074 | 24 trades, +6.719R, Exp +0.2800, PF 1.333 on long-only 5m | Validation improved, but TRAIN, neighbourhood and walk-forward failed |

No V2 was tested untouched, so no V2 can be called better than V1 in out-of-sample terms.

## Complexity Assessment

- Supertrend adds two measurable gates: efficiency and ATR percentage.
- Donchian adds three measurable gates: penetration, channel width and volume.
- Brain adds one numeric efficiency gate and one explicit side policy.

No rules were promoted. Since none demonstrated stable development evidence, their added complexity is not justified for production.

## Candidate Eligibility

| Research family | Final status | Reason |
| --- | --- | --- |
| Supertrend V2 Research | **REJECTED** | TRAIN negative, validation sample collapsed, parameter and temporal stability failed |
| Donchian V2 Research | **REJECTED** | TRAIN and validation negative across all tested timeframes/neighbourhoods; core breakout family should be retired at 5m/15m/1h under this contract |
| Decision Brain V2 Research | **RESEARCH ONLY** | Long-only 5m validation improved, but TRAIN was negative, neighbourhood fragile, walk-forward concentrated and Monte Carlo uncertainty unacceptable |

Forward-paper eligible: **0**. No `LIVE READY` classification is used.

## Rejected Hypotheses

- Supertrend efficiency/volatility filtering did not produce an adequate stable sample.
- Donchian penetration/volume confirmation reduced some losses but did not uncover positive edge; further parameter rescue would be overfitting.
- Brain directional efficiency alone was unstable. Long-only remains a research observation, not a candidate.

All 30 experiment records and 30 append-only selection verdict events are retained under `automation-hub/data/`.

## Remaining Risks

- Historical venue is Binance Spot while deployed forward instances use Kraken Spot.
- Exact venue quantity rounding, minimum notional and cross-instance capital constraints remain outside this isolated R-space study.
- The persistent Risk Engine halt causes long zero-trade periods; this is production parity, not missing data.
- Samples after gating remain small.
- No V2 execution stress or untouched result exists because no candidate passed the earlier gates.

## Final Recommendation

Preserve V1 and all failed V2 experiments. Retire Donchian research under this low-timeframe breakout design. Do not deploy or register any V2 class. Keep Decision Brain V2 long-only as an explainable research hypothesis only; a future version would require a new structural mechanism and must again use TRAIN then VALIDATION before the still-sealed untouched quarter.

The eligibility gate remains unchanged: **zero candidates deserve forward-paper validation**.
