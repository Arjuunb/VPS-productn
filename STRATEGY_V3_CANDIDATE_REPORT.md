# Strategy V3 Candidate Construction and Validation Gate

## Final classification

| Strategy version | Classification | Reason |
| --- | --- | --- |
| TrendPullbackV3Research 3.0.0-research.1 | **REJECTED** | All five pre-registered TRAIN variants had negative raw expectancy before costs and failed the TRAIN gate. |
| VolatilityExpansionV3Research 3.0.0-research.1 | **REJECTED** | The closest raw baseline was only +0.0172R/trade, became negative after realistic costs, and failed all required robustness gates. |

There are **zero** `UNTOUCHED_TEST_ELIGIBLE` candidates. No strategy is
forward-paper eligible or live ready. This is the valid zero-candidate result;
no additional V3 variants were generated after the fixed budget was exhausted.

## Discovery premises

The V3 discovery study remains unchanged. It retained only two causal research
directions: a 15m pullback within a 1h trend context and a volatility
compression-to-expansion event with independent directional confirmation.
Donchian remains retired, Supertrend rejected, Decision Brain research-only,
and naked breakout/global mean reversion remain permanently rejected.

## Candidate definitions

### TrendPullbackV3Research

- **Timeframe/context:** closed 15m execution bars; completed 1h candles only.
- **Trend:** 1h directional efficiency plus a 15m directional return/efficiency
  condition. No current or future incomplete 1h candle is used.
- **Pullback:** a three-bar countertrend retracement of at least 0.50 ATR.
- **Confirmation/entry:** current closed 15m candle must have a same-direction
  body of at least 0.15 ATR. Entry uses the existing realistic limit model.
- **Invalidation/stop:** prior local structure plus a 0.10-ATR buffer.
- **Exit:** fixed 2R structural-risk target (one planned 1.5R neighbour).
- **Directions:** long and short were evaluated independently; one
  long-only neighbour was pre-registered rather than assuming symmetry.

### VolatilityExpansionV3Research

- **Timeframe/context:** closed 15m execution bars; completed 1h direction
  from an efficiency-qualified directional return.
- **Compression:** at least one of the preceding eight ATR(10)/ATR(50) ratios
  is <= 0.80.
- **Expansion:** current ATR(10)/ATR(50) is >= 1.25.
- **Direction/entry:** a 1h direction and a same-direction 15m body >= 0.35
  ATR are both required. It does **not** use a price-channel break as direction.
- **False-expansion rejection:** no trade without a prior compression state,
  closed-bar body confirmation, and independent 1h direction.
- **Invalidation/exit:** same structural stop and fixed 2R target framework.

Both classes are isolated in `automation-hub/strategies/research_v3.py`; they
are absent from the production strategy registry and do not call the Learning
Engine or Decision Brain.

## Experiment budget and data protocol

The budget was fixed before results: five configurations per family, ten total.
No grid search, genetic optimisation, AI/ML model, or post-result parameter
expansion was used.

All TRAIN runs opened only official Binance Spot Binance Vision 5m archives for
BTCUSDT, ETHUSDT, and SOLUSDT, January--June 2025. Each 52,128-candle source
was causally aggregated into 17,376 15m closed bars; reported gaps were zero.
Fixtures, synthetic data, regression data and replay caches were excluded.

The harness rejects any non-declared archive window. It permits only:

- TRAIN: months 1--6;
- VALIDATION, only after freeze: months 6--9, with June used solely as warm-up
  and July--September as the trade window; and
- no Oct--Dec code path at all.

The final manifest has `selected: []` and `test_data_opened: false`.

## TRAIN experiments and signal-to-execution attribution

`Raw` means strategy signals with zero fees/slippage and no risk-engine
blocking. `Realistic` adds the deployed fee/slippage/limit assumptions.
`Risk` additionally applies normal production daily-loss, max-drawdown,
loss-streak and cooldown controls. Learning and Decision Brain are excluded in
every stage, so risk engineering cannot manufacture a base-signal claim.

| Family / pre-registered neighbour | Raw (trades / expR / PF) | Realistic (trades / expR / PF) | Risk (trades / expR / PF / max DD R) | Result |
| --- | --- | --- | --- | --- |
| Trend baseline | 92 / -0.1689 / 0.765 | 92 / -0.5530 / 0.442 | 16 / -0.6516 / 0.405 / 10.426 | REJECTED |
| Trend long-only | 42 / -0.2831 / 0.628 | 42 / -0.7073 / 0.333 | 19 / -0.8813 / 0.259 / 16.745 | REJECTED |
| Trend lower ER | 205 / -0.0853 / 0.876 | 205 / -0.4984 / 0.488 | 19 / -0.6857 / 0.385 / 13.472 | REJECTED |
| Trend deeper pullback | 43 / -0.0208 / 0.969 | 43 / -0.4957 / 0.496 | 17 / -0.5218 / 0.498 / 12.381 | REJECTED |
| Trend 1.5R exit | 93 / -0.0702 / 0.888 | 93 / -0.4532 / 0.481 | 19 / -0.5698 / 0.418 / 10.873 | REJECTED |
| Expansion baseline | 146 / -0.0071 / 0.988 | 146 / -0.1514 / 0.777 | 18 / -0.1201 / 0.827 / 5.292 | REJECTED |
| Expansion long-only | 80 / -0.1240 / 0.810 | 80 / -0.2588 / 0.652 | 14 / -0.2204 / 0.681 / 6.461 | REJECTED |
| Expansion tighter compression | 96 / -0.1733 / 0.738 | 96 / -0.3133 / 0.586 | 16 / -0.4181 / 0.496 / 6.689 | REJECTED |
| Expansion stronger ratio | 90 / -0.2835 / 0.585 | 90 / -0.4084 / 0.466 | 19 / -0.3899 / 0.499 / 8.203 | REJECTED |
| Expansion larger body | 121 / +0.0172 / 1.030 | 121 / -0.1103 / 0.832 | 16 / -0.1994 / 0.728 / 5.614 | REJECTED |

The final expansion neighbour is especially important: its small raw positive
mean disappears under ordinary costs. That is execution-cost failure, not an
edge, so it was not rescued with filters or a larger search.

## Candidate fingerprints and ledger integrity

Every row has an immutable experiment ID, semantic version, hypothesis ID,
source hash, configuration hash, candidate fingerprint and creation timestamp.
Full fingerprints are in:

- `automation-hub/data/strategy_v3_train_development.json`
- `automation-hub/data/strategy_v3_freeze_manifest.json`
- `automation-hub/data/strategy_v3_research_ledger.jsonl`

The current artefact SHA-256 values are:

| Artefact | SHA-256 |
| --- | --- |
| Train development | `4d4f68854b882b56c752ee9668731d738c421e3263354a469eaca0c91221a833` |
| Freeze manifest | `bbc02e08d4680d4ed071df597653aec539d7b30f0adc368c39a3224d1af7d979` |
| Final results | `fc0b31162e43cfcb20b825431a599833b409c0f497254917cbcef4bdbac84810` |
| Append-only ledger | `9958de0b57b89eeaf24429eac3997dc37645424169e569d496bf571316eedf64` |

The ledger refuses an experiment-ID collision with changed content. The test
suite also verifies fingerprint configuration sensitivity and frozen-evidence
integrity checks.

## Selected TRAIN candidates, validation, and degradation

No candidate passed the pre-registered TRAIN gate. In particular, every family
failed raw-signal positivity or cost survival; none had positive risk-stage
expectancy, PF >= 1.10, 30 risk-stage trades, two positive asset results,
four positive TRAIN months, stable neighbours, and non-concentrated winners.

Therefore:

| Stage | Status |
| --- | --- |
| Selected TRAIN candidates | **0** |
| Candidate freeze for validation | **Not created** (empty selected list) |
| July--September VALIDATION data opened | **No** |
| Train-vs-validation degradation | **Not applicable** |
| Walk-forward across TRAIN + VALIDATION | **Blocked** |
| Parameter neighbourhood after validation | **Blocked**; train neighbourhood already failed |
| Execution stress | **Blocked**; candidates failed before stress eligibility |
| Monte Carlo | **Blocked**; candidates failed before tail-risk eligibility |

This is intentional fail-closed behaviour, not missing work. Opening validation
after a failed TRAIN stage would turn it into an optimisation data source.

## Directional, asset, and complexity analysis

The attempted long-only forms were worse than their bidirectional baselines.
Risk-stage asset expectancy was not robust: Trend baseline BTC -0.9772,
ETH -0.8142, SOL -0.2448; Expansion baseline BTC -0.3153, ETH -0.2433,
SOL +0.2868. One isolated SOL value cannot establish an asset-specific
candidate, particularly with 18 risk-stage pooled trades.

| Family | Indicators / state measures | Optional filters | Tuned values | TF dependencies | Branch complexity | Assessment |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Trend Pullback | 2 (efficiency, ATR) | 3 (HTF alignment, pullback, body) | 3 neighbour changes | 2 (15m, 1h) | low | Simple enough; premise failed signal quality. |
| Volatility Expansion | 2 (ATR ratio, efficiency) | 3 (compression, HTF direction, body) | 3 neighbour changes | 2 (15m, 1h) | low | Simple enough; cost survival failed. |

## Rejected variants and remaining risks

All ten variants are permanently recorded as rejected in the research ledger.
No new V3 variant should be added merely because the first budget failed. A
future research cycle would need genuinely new market-behaviour evidence,
pre-register a new hypothesis and budget, and start from TRAIN only.

Remaining risk is not that a promising candidate was missed by an insufficient
search. The larger risk would be overfitting a weak, near-zero raw observation
by adding filters until it appears profitable. This stage intentionally stops
before that happens.

## Untouched-test gate

**BLOCKED.** The required TRAIN, VALIDATION, walk-forward, neighbourhood,
execution-stress, Monte Carlo, sample-adequacy and causality gates did not all
pass for any candidate. Oct--Dec 2025 remains sealed and was not accessed.
