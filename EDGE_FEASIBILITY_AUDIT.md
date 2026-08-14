# Research Environment & Edge Feasibility Audit

## Decision gate

**Decision: STOP CURRENT MARKET/TIMEFRAME RESEARCH.**

This is not a claim that crypto markets contain no edge. It is an evidence-based
conclusion that the current environment—BTC/ETH/SOL spot data, 5m/15m/1h,
the examined causal features, and the current realistic execution model—does
not show enough robust, cost-surviving structure to justify another strategy
cycle. No new strategy was created, no V3.2 was created, and no validation or
untouched-test data was opened.

The best description of the result is: **apparent opportunity is generally too
small for execution at 5m/15m and too sparse/unstable at 1h.**

## Preserved V1/V2/V3 conclusions

| Family / premise | Status preserved by this audit |
| --- | --- |
| Supertrend V1/V2 | Rejected benchmark. |
| Donchian V1/V2 | Retired. |
| Decision Brain V1/V2 | Research-only evidence; not a demonstrated general edge. |
| V3 naked breakout | Permanently rejected. |
| V3 global mean reversion | Permanently rejected. |
| TrendPullbackV3Research | Rejected at the TRAIN gate. |
| VolatilityExpansionV3Research | Rejected at the TRAIN gate. |

No prior report, ledger, fingerprint, strategy classification, or sealed-data
boundary was altered.

## Dataset boundary and provenance

Only official **Binance Spot / Binance Vision** monthly OHLCV archives for
January--June 2025 were used. The source is 5m data, causally aggregated only
into complete 15m and 1h bars.

| Asset | 5m candles | 15m candles | 1h candles | Period | Gaps |
| --- | ---: | ---: | ---: | --- | ---: |
| BTCUSDT | 52,128 | 17,376 | 4,344 | 2025-01-01 through 2025-06-30 UTC | 0 |
| ETHUSDT | 52,128 | 17,376 | 4,344 | 2025-01-01 through 2025-06-30 UTC | 0 |
| SOLUSDT | 52,128 | 17,376 | 4,344 | 2025-01-01 through 2025-06-30 UTC | 0 |

Validation (July--September) and the October--December untouched test were
never opened. The evidence JSON records both flags as `false`.

## Execution hurdle

The existing realistic limit-entry model carries a **minimum direct round-trip
hurdle of 14 bps**:

| Component | Cost |
| --- | ---: |
| Maker entry fee | 4 bps |
| Exit fee | 4 bps |
| Exit spread, slippage, and latency allowance | 6 bps |
| Direct round-trip lower bound | **14 bps** |

This is deliberately a lower bound: missed limit fills and adverse gap-through
behaviour create additional execution risk rather than an assumed advantage.
The same 14-bp hurdle applies across the three assets/timeframes as a notional
cost; its economic severity differs because gross observed movement differs.
Sensitivity is 10 bps (improved but not assumed), 14 bps (current), and 20
bps (worse but plausible).

## Market opportunity, feature information value, and random baseline

Each causal event was measured at 1, 3, 6, 12, and 24 bars after it occurred:
mean/median return, standard deviation, hit rate, MFE, MAE, 5th/95th tails,
month, regime/volatility state, and a matched random-entry baseline. The JSON
contains every asset × timeframe × feature result.

Features were evaluated individually: recent direction, high/low efficiency,
ATR expansion/contraction, pullback depth, candle body strength, structural
distance, plus only three predeclared interactions: trend + pullback,
compression + expansion, and direction/body alignment.

### Best observed six-bar movements, before cost

| Asset / TF | Best observed feature | Events | Gross mean | Matched random mean | Net after 14 bp | Classification |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| BTC 5m | Compression + expansion | 573 | +1.78 bp | +0.11 bp | -12.22 bp | NOT VIABLE |
| BTC 15m | ATR expansion | 3,815 | +1.83 bp | +0.46 bp | -12.17 bp | NOT VIABLE |
| BTC 1h | Trend + pullback | 37 | +12.91 bp | -2.71 bp | -1.09 bp | NOT VIABLE |
| ETH 5m | High efficiency | 4,407 | +2.06 bp | -0.85 bp | -11.94 bp | NOT VIABLE |
| ETH 15m | High efficiency | 1,812 | +8.49 bp | +4.54 bp | -5.51 bp | NOT VIABLE |
| ETH 1h | Compression + expansion | 91 | +30.92 bp | -22.59 bp | +16.92 bp | WEAK |
| SOL 5m | Compression + expansion | 266 | +7.60 bp | +5.89 bp | -6.40 bp | NOT VIABLE |
| SOL 15m | ATR expansion | 3,038 | +4.99 bp | -0.89 bp | -9.01 bp | NOT VIABLE |
| SOL 1h | Compression + expansion | 49 | +27.07 bp | -1.83 bp | +13.07 bp | WEAK |

No listed condition was statistically distinguishable from its direction- and
frequency-matched random baseline at the audit's simple 95% threshold. The
two positive-net 1h observations therefore do not establish a researchable
edge: their sample sizes are 91 and 49, their standard deviations are 253 and
364 bps, and both have only four positive months.

## Timeframe feasibility and cost dominance

| Timeframe | Raw opportunity / SNR | Cost dominance | Event frequency | Feasibility |
| --- | --- | --- | --- | --- |
| 5m | Best gross outcomes 1.8--7.6 bp at six bars; feature distributions close to random. | Cost is 1.8x to 27x gross expected move. | High, but clustered. | **NOT VIABLE**: noise/cost dominated. |
| 15m | Best gross outcomes 1.8--8.5 bp. | Cost is 1.6x to 11.5x gross expected move. | High enough, but not economic. | **NOT VIABLE**: high frequency does not overcome cost. |
| 1h | Some expansion states reach 27--31 bp gross. | ETH/SOL compression-expansion ratios are 0.45/0.52; other states are near or above 1.0. | Only 49--91 events in six months for the promising state. | **WEAK**: sample/noise/monthly instability blocks research. |

The cost dominance ratio is direct execution cost divided by gross expected
move. Ratios >=1 mean the observed average movement does not cover the lower
bound execution hurdle. No 5m or 15m condition passed this economic screen.

## Holding-horizon analysis

The few 1h expansion observations do not agree on a durable holding profile:

- ETH compression + expansion: gross movement is +30.92 bp at six bars and
  +33.87 bp at twelve bars, but turns sharply negative by 24 bars.
- SOL compression + expansion: +28.99 bp at one bar and +27.07 bp at six,
  then weakens below the 14-bp hurdle by twelve bars.
- ETH/SOL ATR expansion is essentially at the current 14-bp hurdle at six
  bars, while median returns are negative.

This is observational evidence of transient, highly variable movement—not a
reason to select a fixed exit horizon or optimise an exit rule.

## Monthly, regime, and asset stability

The 1h candidates derive much of their apparent average from unstable months.
For ETH compression + expansion, six-bar monthly means were +55.1, +58.6,
-55.8, +209.2, -16.7 and +2.4 bp. For SOL the same values were -3.6, +25.2,
+22.0, +82.9, -180.7 and +115.7 bp. Four positive months out of six is not
enough when the negative months are material and standard deviations are high.

Normal/range regimes were more persistent than trend labels in the prior V3
study; this audit finds no feature whose conditional movement is robustly
positive across both regime and volatility states. The apparent expansion
effect is **asset-specific and unstable**, not cross-asset. BTC does not share
the ETH/SOL 1h result.

## Execution-model sensitivity

At the current 14-bp cost, 5m/15m results are below cost. A hypothetical
10-bp execution model makes some 15m observations less negative but does not
make them statistically distinct from random. A 20-bp model leaves only the
two sparse 1h compression-expansion averages above zero, without curing their
noise or monthly instability. Therefore no conclusion depends on optimistic
fills.

## Methodology audit and limitations

- This is an event-distribution study, not a backtest. It does not claim
  tradeability from raw forward movement.
- Fixed-R exits do not enter this audit; prior candidate tests showed the raw
  premise failed before an exit could plausibly rescue it.
- The 14-bp hurdle mirrors the current conservative limit-entry model. Missed
  entries and gaps remain unpriced additional risks in this feasibility view.
- Conservative stop ordering and risk-stage suppression were retained in the
  candidate work; this audit keeps them out so they cannot mask signal quality.
- A warm-up of at least 60 bars is excluded before every causal observation;
  15m/1h aggregation uses complete bars only.
- “Independent event” counts use a 24-bar spacing heuristic. Market returns
  may still be serially correlated, which makes the reported weak evidence
  less—not more—persuasive.
- The historical source is Binance Spot. Production may use another venue or
  execution path. Broad movement distributions are market-structure evidence;
  exact cost/fill conclusions are venue-sensitive and should not be treated as
  venue parity.

## Final feasibility matrix

| Asset × timeframe | Raw information value | Execution hurdle outcome | Monthly/regime stability | Classification |
| --- | --- | --- | --- | --- |
| BTC 5m / 15m / 1h | Weak, no robust conditional distribution shift | Below cost | Insufficient / inconsistent | NOT VIABLE |
| ETH 5m | Weak | Below cost | No usable conditional edge | NOT VIABLE |
| ETH 15m | Some high-efficiency movement, but < 14 bp | Below cost | Not distinct from random | NOT VIABLE |
| ETH 1h | Compression-expansion is the strongest observation | Above direct cost at 6/12 bars | Low sample, unstable, not random-distinguishable | WEAK |
| SOL 5m / 15m | Weak | Below cost | Inconsistent | NOT VIABLE |
| SOL 1h | Compression-expansion observation | Above direct cost at 1/6 bars | 49 events, unstable, not random-distinguishable | WEAK |

## Recommended research direction

Stop further strategy construction for this market/timeframe/execution set.
Do not promote a weak 1h observation to a V3.2 candidate. A future research
cycle needs genuinely new evidence—such as a different market microstructure,
another independently sourced venue/data type, or a materially different and
verified execution capability—not another filter stack on the same features.

Machine-readable evidence: `automation-hub/data/edge_feasibility_audit.json`
(SHA-256 `3253b69dd2813c88f755a6d560a57384466f65ec6da1bb4b9cc48f2eaa82e54c`).
