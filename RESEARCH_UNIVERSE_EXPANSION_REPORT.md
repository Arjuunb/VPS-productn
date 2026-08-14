# Research Universe Expansion & Alternative Edge Discovery

## Previous Research Stop Decision

`RESEARCH_UNIVERSE_SPOT_OHLCV_V1` remains **EXHAUSTED / NOT CURRENTLY VIABLE**. This study neither changes nor reopens V1/V2/V3, Jul–Sep 2025 validation, or Oct–Dec 2025 untouched test data.

## New Research Universes

- **Universe A — Binance USDⓈ-M perpetual futures:** 15m price, mark/index, funding, observed open interest, and observed taker long/short-volume ratio.
- **Universe B — Binance Spot higher timeframe:** 4h and 1D OHLCV resamples, reported separately from futures microstructure.
- **Universe C — flow information:** observed futures taker ratio plus genuine price-volume measures; no signed-volume reconstruction.

## Data Sources and Provenance

All evidence is Jan–Jun 2025 TRAIN only. Source manifests, every archive SHA-256, observation count, start/end, and gaps are in `automation-hub/data/research_universe_expansion.json`.

### BTCUSDT — Binance USDⓈ-M Futures

- 15m futures price: 17376 observations; gaps: 0.
- 5m observed OI/taker-ratio: 52128 observations; gaps: 0.
- Funding events: 543; no interpolation was applied.

### ETHUSDT — Binance USDⓈ-M Futures

- 15m futures price: 17376 observations; gaps: 0.
- 5m observed OI/taker-ratio: 52128 observations; gaps: 0.
- Funding events: 543; no interpolation was applied.

### SOLUSDT — Binance USDⓈ-M Futures

- 15m futures price: 17376 observations; gaps: 0.
- 5m observed OI/taker-ratio: 52128 observations; gaps: 0.
- Funding events: 543; no interpolation was applied.

## Futures Execution Model

The conservative futures round-trip research hurdle is **16 bp** (2 bp maker fee each side, 2 bp spread/slippage each side, and 2 bp latency/missed-fill allowance). It is an account-tier-agnostic research assumption, not the user’s actual commission schedule. Funding is not netted as a benefit; an adverse funding transfer is additional cost. Leverage is explicitly not treated as edge.

## Unconditional Baselines

Every futures condition is compared with the unconditional long distribution and a deterministic, matched-frequency random timestamp/direction control at 15m horizons 1, 4, 12, and 24 bars. Every higher-timeframe result contains 4h horizons 1/3/6/12 and 1D horizons 1/3/5/10.

## Funding Analysis

- **ETHUSDT funding_low**: RESEARCHABLE premise only; n=1758, 24-bar gross mean 24.09 bp, net of 16 bp 8.09 bp, positive months 4/6, random-control difference 24.19 bp.
- **SOLUSDT funding_low**: RESEARCHABLE premise only; n=1759, 24-bar gross mean 27.38 bp, net of 16 bp 11.38 bp, positive months 5/6, random-control difference 29.20 bp.

## Open Interest Analysis

OI and price/OI state results are retained in the JSON evidence. No OI state independently met the full predeclared researchable screen in this first six-month sample.

## Basis Analysis

Mark/index basis states are retained in the JSON evidence. No basis state independently met the full predeclared researchable screen.

## Flow Analysis

- **ETHUSDT relative_volume_high**: RESEARCHABLE premise only; n=3072, 24-bar gross mean 18.06 bp, net of 16 bp 2.06 bp, positive months 5/6.

## Higher-Timeframe Analysis

- **BTCUSDT 4h**: WEAK; longest-horizon directional persistence mean 2.43 bp, positive months 3/6, cost dominance 5.7665.
- **BTCUSDT 1d**: WEAK; longest-horizon directional persistence mean 39.59 bp, positive months 3/6, cost dominance 0.3536.
- **ETHUSDT 4h**: NOT VIABLE; longest-horizon directional persistence mean -36.30 bp, positive months 1/6, cost dominance None.
- **ETHUSDT 1d**: RESEARCHABLE; longest-horizon directional persistence mean 101.87 bp, positive months 4/6, cost dominance 0.1374.
- **SOLUSDT 4h**: WEAK; longest-horizon directional persistence mean 37.03 bp, positive months 3/6, cost dominance 0.3781.
- **SOLUSDT 1d**: WEAK; longest-horizon directional persistence mean 8.93 bp, positive months 2/6, cost dominance 1.5679.

## Feature Information Value

The complete per-feature output includes event count, conditional mean/median/volatility/skew/tails/hit-rate/MFE/MAE, monthly rows, and matched random controls. It is machine-readable to avoid selective presentation.

## Feature Interactions

Exactly 6 pre-registered interactions were tested: price_up_oi_up_taker_buy, price_down_oi_up_taker_sell, funding_high_oi_rising, funding_low_oi_rising, basis_widening_taker_buy, basis_widening_taker_sell. The interaction budget was not expanded after results were observed.

## Random/Placebo Controls

Each condition uses deterministic matched-frequency random timestamps with its condition directions shuffled. This is a placebo association control, not proof of causality.

## Economic Significance

A positive gross result is never called profitable. The screen requires at least 100 events, 30 independent events, positive net of the conservative cost hurdle, four positive months, and simple 95% separation from the matched random control before the label `RESEARCHABLE` is possible.

## Cost Dominance

The futures model is separate from the earlier Spot 14 bp model. The report does not use fee discounts, leverage, or perfect fills to create an apparent advantage. Actual account commissions, spreads, fills, and funding payment direction remain required before any execution validation.

## Monthly Stability

Monthly event count, conditional mean, median, and hit rate are retained for every feature in the JSON. A result driven by fewer than four positive months cannot pass the researchable screen.

## Asset Stability

There is no cross-asset universality claim. The initial signals are asset-specific: low funding qualified only for ETH and SOL; a relative-volume feature qualified only for ETH; 1D directional persistence qualified only for ETH.

## Timeframe Stability

The first screen gives no blanket 4h/1D conclusion. ETH 1D warrants separate follow-up discovery; BTC and SOL are weak, and 4h results are mixed or non-viable.

## Multiple-Testing Audit

20 single-feature hypotheses and 6 interactions were tested per futures asset. Nominal 95% matched-control separation is descriptive only; no multiple-comparison-corrected profitability claim is made.

## Data Limitations

- The study has six TRAIN months only; no validation/test data were read.
- Funding is an event series and was aligned only from information known at or before T; it was not forward-filled from the future.
- Archive data do not provide the actual account fee tier, order-book queue position, or realized fills; these prevent an execution claim.
- No synthetic candles, generated fixtures, replay caches, reconstructed OI, or mixed-venue microstructure were used.

## Research Universe Classifications

- **RESEARCH_UNIVERSE_SPOT_OHLCV_V1**: EXHAUSTED / NOT CURRENTLY VIABLE
- **UNIVERSE_A_CRYPTO_DERIVATIVES_STRUCTURE**: CONTINUE DISCOVERY
- **UNIVERSE_B_HIGHER_TIMEFRAME_STRUCTURE**: CONTINUE DISCOVERY
- **UNIVERSE_C_VOLUME_FLOW**: CONTINUE DISCOVERY

## Decision Gate

**DERIVATIVES-STRUCTURE RESEARCH JUSTIFIED (limited discovery only)**; **FLOW-DATA RESEARCH JUSTIFIED (limited discovery only)**; **HIGHER-TIMEFRAME RESEARCH JUSTIFIED (ETH 1D follow-up only)**. This does **not** authorize a strategy, a forward-paper candidate, or a live candidate.

## Recommended Next Research Stage

Freeze this result. Before any strategy construction, pre-register a small independent TRAIN extension or a completely new, separately sealed futures universe; verify actual account-specific futures fees and fills; then repeat only the named asset-specific premises. Do not open the existing Jul–Sep validation or Oct–Dec untouched-test data for this discovery study.

## Status

**VERIFIED:** provenance-gated Jan–Jun inputs, causal timestamp alignment, archive hashes, venue separation, deterministic matched controls, and sealed-boundary enforcement.

**INSUFFICIENT EVIDENCE:** any profitable strategy, edge persistence beyond train, realistic fill survival, cross-asset generality, or forward-paper readiness.
