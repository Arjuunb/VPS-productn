# TradeLogX Strategy Validation Report

Generated: 2026-08-13  
Code baseline: `c3e6fa1` plus the local validation-harness parity corrections listed below  
Scope: Supertrend 1.0.0, Donchian Breakout 1.0.0, Decision Brain 1.0.0  
Primary timeframe: 5 minutes  
Primary conclusion: **No strategy has a verified positive edge on the untouched, pooled, real-data test set.**

## Executive decision

| Strategy | Untouched-test result | Decision |
| --- | --- | --- |
| Supertrend | -6.118R, PF 0.635, 29.41% wins over 17 trades | **FAILED** |
| Donchian Breakout | -12.704R, PF 0.420, 26.32% wins over 19 trades | **FAILED** |
| Decision Brain | -2.976R, PF 0.806, 33.33% wins over 15 trades | **INSUFFICIENT EVIDENCE** — experimental only |

`R` is the initial risk unit. At the frozen 0.5% risk assumption, 1R equals 0.5% of the starting equity before compounding and venue-size rounding. None of these results authorizes live trading or supports a profitability claim.

## Evidence status definitions

- **VERIFIED**: directly established by reproducible code inspection, tests, or the recorded real-data run.
- **FAILED**: the tested requirement or edge did not hold.
- **INSUFFICIENT EVIDENCE**: the available sample cannot support the claim.
- **BLOCKED**: the necessary source or environment was unavailable.

## Market-data source audit

| Source/path | What it provides | Evidence decision |
| --- | --- | --- |
| Official Binance Vision monthly spot klines | Immutable monthly ZIP archives with exchange OHLCV | **VERIFIED and used** |
| 15m causal aggregation of the verified Binance 5m series | Adjacent-timeframe stress data | **VERIFIED secondary evidence**; not an independently downloaded archive |
| `data/forward_market_data.py` via CCXT | Current live Kraken/Binance/etc. forward candles | **EXCLUDED** from historical claims: live snapshots are not a frozen full-year archive |
| `data/historical.py` / Binance REST | Real Binance downloads and SQLite cache | Network source is genuine, but the local cache was **EXCLUDED** because its complete provenance and freshness were not established for this run |
| `data/market_data_v2.py` | Binance USDT perpetual history/cache | **EXCLUDED**: different instrument and no frozen audited archive was supplied |
| Official Kraken downloadable OHLCVT archive | Venue-identical candidate for the VPS Kraken feed | **BLOCKED** for this run: a complete frozen archive was not acquired; no Kraken historical profitability claim is made |
| Yahoo adapter | Non-crypto and alternate historical series | **EXCLUDED**: not the production crypto venue/instrument |
| Bundled sample CSVs | Development/demo history | **EXCLUDED** |
| Synthetic generators | Deterministic development/test candles | **EXCLUDED** |
| Test fixtures and regression snapshots | Unit/characterization inputs | **EXCLUDED** |
| `market_data.db`, replay caches and stale local SQLite history | Previously fetched/replayed bars | **EXCLUDED** |
| Paper-trade ledger and Memory page records | Forward/simulated trade outcomes, including legacy/unattributed runs | **EXCLUDED** from this historical strategy validation |

No excluded source contributes a candle, signal, trade, or metric in this report.

## Real-data inventory

Primary data source template:

`https://data.binance.vision/data/spot/monthly/klines/{symbol}/5m/{symbol}-5m-2025-{month}.zip`

| Exchange | Instrument | Symbol | TF | First candle UTC | Last candle UTC | Candles | Duplicates | Missing intervals | Invalid OHLC |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| Binance Spot | USDT spot | BTCUSDT | 5m | 2025-01-01 00:00 | 2025-12-31 23:55 | 105,120 | 0 | 0 | 0 |
| Binance Spot | USDT spot | ETHUSDT | 5m | 2025-01-01 00:00 | 2025-12-31 23:55 | 105,120 | 0 | 0 | 0 |
| Binance Spot | USDT spot | SOLUSDT | 5m | 2025-01-01 00:00 | 2025-12-31 23:55 | 105,120 | 0 | 0 | 0 |
| Binance Spot | USDT spot | BTCUSDT | 15m | 2025-01-01 00:00 | 2025-12-31 23:45 | 35,040 | 0 | 0 | 0 |
| Binance Spot | USDT spot | ETHUSDT | 15m | 2025-01-01 00:00 | 2025-12-31 23:45 | 35,040 | 0 | 0 | 0 |
| Binance Spot | USDT spot | SOLUSDT | 15m | 2025-01-01 00:00 | 2025-12-31 23:45 | 35,040 | 0 | 0 | 0 |

The 15m rows are causal OHLCV aggregations of the verified 5m exchange observations. They are used only as adjacent-timeframe stress evidence. The primary profitability decision uses the native downloaded 5m bars.

All 36 source ZIP files are SHA-256 fingerprinted in the machine-readable run output. The final evidence bundle SHA-256 is `5254d64de2170b64f4f101e010265c9d2659830a704b54e6ee2f87c4e4ca36ba`.

## Chronological partition

| Partition | Inclusive start | Exclusive end | Purpose |
| --- | --- | --- | --- |
| Train | 2025-01-01 | 2025-07-01 | Initial observation only |
| Validation | 2025-07-01 | 2025-10-01 | Controlled sensitivity analysis |
| Untouched test | 2025-10-01 | 2026-01-01 | Final edge decision |

Each partition receives up to 600 prior candles as indicator warm-up, but trades are forbidden before the partition start. Test data was not used to select parameters.

Walk-forward validation uses nine fixed-parameter folds: the preceding three calendar months are labelled as the training window and the next month is the test window, from April through December 2025. There is no per-fold optimisation.

## Frozen production configuration

| Setting | Frozen value |
| --- | --- |
| Entry | Resting limit at strategy signal price |
| Limit lifetime | 3 subsequent candles |
| Starting equity | US$1,000 per isolated run |
| Risk | 0.5% per trade |
| Quality gate | TradeBrain score at least 60 |
| Daily loss limit | 1% |
| Maximum drawdown halt | 10% |
| Consecutive-loss halt | 3 losses |
| Loss cooldown | 60 minutes |
| Commission accounting | 0.04% of notional per side; conservative taker rate on both legs |
| Exit adverse price impact | 0.06% per exit: half-spread 0.02% + slippage 0.03% + latency 0.01% |
| Partial fills/rejections | Disabled, matching the current documented defaults |
| Position management | Strategy ATR stop and strategy target; optional manager features remain disabled |

No parameter search altered the frozen production result. Nearby parameters and risk/reward values were evaluated only on the validation partition and reported as sensitivity evidence.

## Production-strategy parity audit

| Component | Status | Evidence |
| --- | --- | --- |
| Strategy class and indicators | **VERIFIED** | Production and harness instantiate the same `SupertrendStrategy`, `DonchianStrategy`, and `DecisionBrain` classes. |
| Signal generation | **VERIFIED** | Same `on_bar` methods, same parameters and immutable fingerprints; strategies receive closed candles in timestamp order. |
| Long/short conditions | **VERIFIED** | Same `SignalType.LONG`/`SHORT` outputs and side mapping. |
| Look-ahead safety | **VERIFIED** | Future-mutation tests preserve every earlier signal for all three strategies. Signal-time regime classification uses only prior/current bars and fixed thresholds. |
| Entry type | **VERIFIED** | Limit at the signal price, active only on following candles, expires after three bars. Gap-through limit entries fill at the better open. |
| Same-candle ordering | **VERIFIED** | A just-filled limit may be stopped on the fill candle but cannot receive a target win because OHLC path ordering is unknown. |
| Stop loss | **VERIFIED** | Strategy-provided ATR stop; a gap through the stop exits at the worse open. Stop is evaluated before target on ambiguous OHLC bars. |
| Take profit | **VERIFIED** | Strategy-provided target and risk/reward value; no manufactured same-candle target after a pending fill. |
| Opposite signal | **VERIFIED** | Closes the current position; it does not close and reopen on the same candle. |
| Fees | **VERIFIED** | Harness matches production's deliberately conservative 0.04% commission on entry and exit, including maker-style entries. |
| Spread/slippage/latency | **VERIFIED** | Entry maker fill has no adverse price movement; exit applies 0.06% adverse price movement. |
| Realistic fills | **VERIFIED for configured defaults** | Deterministic full fills, no rejects or partials; limit TTL, gap handling and ambiguous-bar ordering match production. |
| Per-trade risk basis | **VERIFIED in R-space** | Results use the frozen 0.5% risk basis and US$1,000 start. |
| Exact venue quantity and notional | **INSUFFICIENT EVIDENCE** | The harness does not apply Kraken step size, minimum notional, fixed-quantity mode, account allocation caps, or symbol-specific rounding. |
| Compounding/profit reinvestment | **INSUFFICIENT EVIDENCE** | The decision is made in R-space. It must not be read as proof of exact account-dollar growth under every production sizing mode. |
| Instance daily loss/drawdown/streak/cooldown | **VERIFIED** | Causal guards now match production persistence: daily P&L resets at UTC midnight; loss streak and drawdown halt do not silently reset. |
| Global cross-instance risk | **INSUFFICIENT EVIDENCE** | Strategies are validated independently; global slot, capital and correlated-position limits are not portfolio-simulated here. |
| Learning Engine | **INSUFFICIENT EVIDENCE** | Baseline is frozen without mutable learned state. A separate causal A/B was run; it did not prove an edge. |
| Production venue identity | **FAILED for exact parity** | Historical evidence is Binance Spot while the deployed forward instances currently use Kraken Spot. Cross-venue conclusions are allowed only as research evidence. |

The local parity work changes only the research simulator's execution semantics and audit output; it does not change indicator parameters or strategy entry logic. Specifically, it corrected earlier harness divergence in same-candle pending fills, gap stops, opposite-signal exits, bounded quality-gate history, and risk-halt persistence. Results produced by the older divergent simulator must not be compared as if they used this execution contract.

### Immutable fingerprints

| Strategy | Version | Code hash | Configuration hash | Combined hash |
| --- | --- | --- | --- | --- |
| Supertrend | 1.0.0 | `0f396e48fd14c2c5d9756f80e5310e54bda2c81da80b5d11a0639f026b5a836b` | `5e1a47e1d864f550cc69f0640a3898ca6366ca9442e623a040e9e1cd6b2240e2` | `8b86b9ea17812eb1139a43a97ab3a027176f773b93cc5cd6e7fae623b0af2234` |
| Donchian | 1.0.0 | `7a1e9d9205a1ec296f9819fd65e7230d7c68f366c4d82563d808f3261ffeeb63` | `6641421014ecf685133faa18b209ae38560290e29c31d826a80b28fbc978aa26` | `2249acdf1577e4a365f2981c5e22af5fed65d5fce13c312ce462584b9018760b` |
| Decision Brain | 1.0.0 | `9af1463744f4e1133ff858129f052dd2a3dbe59acd854ccd9ed7e4b51e3e8bd8` | `9986dc1e0fab72962a122dc78c6509fd4dd38bcfef0f4fc4338596b6831d19ca` | `5d4fec252eed595b6cc637f6650cc7a33bc69b4386c02bc6e07fd4736104fd6c` |

## Chronological results

Pooled rows combine BTC, ETH and SOL trade outcomes. `Worst symbol DD` is the worst individual-symbol drawdown, not a fabricated multi-asset portfolio drawdown.

| Strategy | Partition | Trades | W/L | Win rate | PF | Expectancy | Net R | Return at 0.5% risk | Worst symbol DD |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Supertrend | Train | 18 | 3/15 | 16.67% | 0.257 | -0.9357R | -16.842R | -8.421% | 7.267R |
| Supertrend | Validation | 20 | 6/14 | 30.00% | 0.495 | -0.5786R | -11.572R | -5.786% | 5.590R |
| Supertrend | Untouched test | 17 | 5/12 | 29.41% | 0.635 | -0.3599R | -6.118R | -3.059% | 5.285R |
| Donchian | Train | 10 | 1/9 | 10.00% | 0.126 | -1.1901R | -11.901R | -5.950% | 4.955R |
| Donchian | Validation | 16 | 3/13 | 18.75% | 0.306 | -0.8449R | -13.518R | -6.759% | 5.743R |
| Donchian | Untouched test | 19 | 5/14 | 26.32% | 0.420 | -0.6686R | -12.704R | -6.352% | 6.034R |
| Decision Brain | Train | 32 | 12/20 | 37.50% | 1.021 | +0.0201R | +0.642R | +0.321% | 4.729R |
| Decision Brain | Validation | 33 | 13/20 | 39.39% | 1.074 | +0.0692R | +2.282R | +1.141% | 6.080R |
| Decision Brain | Untouched test | 15 | 5/10 | 33.33% | 0.806 | -0.1984R | -2.976R | -1.488% | 5.486R |

All three pooled untouched tests are negative. Therefore the required edge criterion **FAILED** for Supertrend and Donchian. Decision Brain's positive train/validation result did not persist into test and has only 15 test trades, so its status is **INSUFFICIENT EVIDENCE**, not profitable.

## Untouched test by symbol

| Strategy | Symbol | Trades | W/L | WR | PF | Exp R | Net R | Max DD R | Sharpe | Sortino | Avg win/loss R | Avg MFE/MAE R | Avg cost R |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: |
| Supertrend | BTCUSDT | 3 | 0/3 | 0.00% | 0.000 | -1.7617 | -5.285 | 5.285 | -11.314 | -1.719 | 0 / -1.7617 | 1.052 / 1.046 | 0.9704 |
| Supertrend | ETHUSDT | 3 | 0/3 | 0.00% | 0.000 | -1.1280 | -3.384 | 3.384 | -2.619 | -1.524 | 0 / -1.1280 | 1.197 / 0.830 | 0.5272 |
| Supertrend | SOLUSDT | 11 | 5/6 | 45.45% | 1.316 | +0.2319 | +2.551 | 4.254 | +0.423 | +0.770 | +2.1272 / -1.3475 | 1.917 / 0.917 | 0.3849 |
| Donchian | BTCUSDT | 5 | 1/4 | 20.00% | 0.177 | -1.2068 | -6.034 | 6.034 | -1.903 | -1.633 | +1.2950 / -1.8323 | 1.055 / 0.988 | 0.9068 |
| Donchian | ETHUSDT | 7 | 2/5 | 28.57% | 0.500 | -0.5367 | -3.757 | 4.444 | -0.857 | -1.112 | +1.8785 / -1.5028 | 2.136 / 0.921 | 0.5374 |
| Donchian | SOLUSDT | 7 | 2/5 | 28.57% | 0.587 | -0.4161 | -2.913 | 4.207 | -0.646 | -0.919 | +2.0725 / -1.4116 | 1.533 / 0.932 | 0.4254 |
| Decision Brain | BTCUSDT | 6 | 3/3 | 50.00% | 1.302 | +0.2763 | +1.658 | 5.486 | +0.293 | +0.523 | +2.3813 / -1.8287 | 1.990 / 0.778 | 0.7236 |
| Decision Brain | ETHUSDT | 6 | 2/4 | 33.33% | 0.901 | -0.0963 | -0.578 | 4.232 | -0.112 | -0.198 | +2.6170 / -1.4530 | 2.379 / 0.725 | 0.4299 |
| Decision Brain | SOLUSDT | 3 | 0/3 | 0.00% | 0.000 | -1.3520 | -4.056 | 4.056 | -40.870 | -1.731 | 0 / -1.3520 | 0.436 / 1.152 | 0.3523 |

Sharpe and Sortino are not treated as reliable annualized portfolio statistics: each row contains only 3–11 trades and observations are trade outcomes, not a stable daily return series. The evidence bundle records per-trade Sharpe and Sortino for completeness; the decision relies on expectancy, PF, drawdown, costs and stability instead.

## Walk-forward validation

| Strategy | Symbol | Monthly test folds | Positive folds | Trades across folds | Net R across folds |
| --- | --- | ---: | ---: | ---: | ---: |
| Supertrend | BTCUSDT | 9 | 0 | 40 | -39.207 |
| Supertrend | ETHUSDT | 9 | 1 | 74 | -21.075 |
| Supertrend | SOLUSDT | 9 | 2 | 53 | -17.028 |
| Donchian | BTCUSDT | 9 | 0 | 67 | -33.285 |
| Donchian | ETHUSDT | 9 | 2 | 60 | -21.556 |
| Donchian | SOLUSDT | 9 | 2 | 64 | -21.091 |
| Decision Brain | BTCUSDT | 9 | 2 | 56 | -17.191 |
| Decision Brain | ETHUSDT | 9 | 3 | 66 | -2.667 |
| Decision Brain | SOLUSDT | 9 | 3 | 37 | -18.044 |

Walk-forward stability **FAILED** for all strategy/symbol combinations.

## Long versus short, regime and session evidence

Untouched-test pooled side results:

| Strategy | Long | Short | Status |
| --- | --- | --- | --- |
| Supertrend | 13 trades, -3.887R | 4 trades, -2.231R | Both negative |
| Donchian | 16 trades, -8.194R | 3 trades, -4.510R | Both negative; no short winners |
| Decision Brain | 13 trades, +0.005R | 2 trades, -2.981R | Long effectively flat; short sample too small |

Regime observations are extremely small and cannot justify filters:

- Supertrend: low volatility -3.892R (12 trades); weak uptrend -1.938R (2); ranging -0.288R (3).
- Donchian: low volatility -10.970R (13); weak downtrend -2.020R (1); ranging -3.780R (3); weak uptrend +4.066R (2).
- Decision Brain: low volatility -0.865R (3); weak uptrend -3.396R (7); ranging -2.548R (2); strong uptrend +5.234R (2); weak downtrend -1.401R (1).

Session observations are also **INSUFFICIENT EVIDENCE**. Apparent London gains in tiny Donchian/Decision Brain subsets must not be converted into production filters.

## Fees, slippage, latency and timeframe stress

| Strategy/symbol | Production net R | Zero-cost net R | 1.5x costs | 2x costs | +1 bar latency | +2 bars latency | 15m stress |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Supertrend BTC | -5.285 | -2.373 | -7.240 | -8.862 | -5.285 | -5.944 | +7.380 |
| Supertrend ETH | -3.384 | -3.302 | -4.174 | -4.939 | -4.822 | -5.267 | -3.926 |
| Supertrend SOL | +2.551 | +6.788 | +0.437 | -2.721 | +4.587 | +3.592 | -3.793 |
| Donchian BTC | -6.034 | -1.000 | -6.464 | -7.617 | -6.831 | -6.826 | -2.995 |
| Donchian ETH | -3.757 | +0.004 | -2.279 | -7.207 | -5.347 | -5.595 | -1.782 |
| Donchian SOL | -2.913 | +0.065 | -4.401 | -9.157 | -7.547 | -4.225 | -1.547 |
| Decision Brain BTC | +1.658 | +6.000 | -0.512 | -2.682 | -4.687 | -5.896 | +6.406 |
| Decision Brain ETH | -0.578 | +2.000 | -1.869 | -5.884 | -5.477 | +2.789 | -0.882 |
| Decision Brain SOL | -4.056 | -3.000 | -4.586 | -5.113 | -4.429 | -7.183 | -0.713 |

The isolated positive cells are not robust. Supertrend SOL and Decision Brain BTC lose their edge under higher costs; Decision Brain BTC also fails with one-bar latency. The positive 15m BTC cells were found only in a stress check and are not untouched confirmatory tests after being observed. Cost robustness **FAILED**.

## Parameter sensitivity

Nearby validation-only parameter checks showed:

- Supertrend positive configurations: BTC 0/5, ETH 0/5, SOL 1/5. Nearby RR values: BTC 0/4, ETH 1/4, SOL 0/4.
- Donchian positive configurations: BTC 0/5, ETH 1/5, SOL 0/5. Nearby RR values: 0/4 for every symbol.
- Decision Brain conviction thresholds: BTC 0/5, ETH 5/5, SOL 5/5. Nearby RR values: BTC 0/4, ETH 2/4, SOL 4/4. The ETH/SOL validation strength did not persist on untouched test.

Parameter robustness **FAILED** for Supertrend and Donchian and is **INSUFFICIENT EVIDENCE** for Decision Brain.

## Risk and learning A/B

The production safety gates reduced losses but did not manufacture a positive pooled untouched result:

| Strategy | Risk disabled | All production guards | Interpretation |
| --- | ---: | ---: | --- |
| Supertrend | 30 trades, -14.014R | 17 trades, -6.118R | Reduced participation/loss; no edge |
| Donchian | 60 trades, -23.726R | 19 trades, -12.704R | Reduced participation/loss; no edge |
| Decision Brain | 70 trades, -17.532R | 15 trades, -2.976R | Strong loss containment; no positive OOS edge |

The real causal `LearningBook` is a bounded soft sizing input and rejects no trades. It left Supertrend and Decision Brain unchanged in this small sample. Donchian moved from -12.704R unweighted to -11.374 weighted R; that is loss reduction, not edge validation.

The quality gate rejected substantially more losing than winning raw entries, but the accepted samples remained tiny and pooled results remained negative. This supports the gate as a safety filter, not as proof of profitability.

## Monte Carlo resampling of untouched trades

| Strategy | Trades | Median net R | 5th–95th percentile net R | Probability of loss | Median DD R | P(DD ≥ 10R) |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Supertrend | 17 | -6.43 | -17.22 to +4.77 | 81.84% | 9.82 | 48.70% |
| Donchian | 19 | -12.89 | -23.20 to -1.57 | 96.60% | 14.96 | 81.76% |
| Decision Brain | 15 | -3.00 | -14.94 to +9.47 | 62.54% | 8.33 | 35.36% |

These simulations resample genuine untouched-test trade outcomes; they do not create synthetic candles or inflate the observed trade count. The small underlying samples make the distributions uncertain.

## Validation quality gates

| Gate | Supertrend | Donchian | Decision Brain |
| --- | --- | --- | --- |
| Genuine exchange data only | VERIFIED | VERIFIED | VERIFIED |
| No look-ahead found | VERIFIED | VERIFIED | VERIFIED |
| Execution parity for frozen isolated path | VERIFIED | VERIFIED | VERIFIED |
| Positive pooled untouched expectancy | FAILED | FAILED | FAILED |
| PF above 1 on pooled untouched test | FAILED | FAILED | FAILED |
| Walk-forward stability | FAILED | FAILED | FAILED |
| Robust to higher costs/latency | FAILED | FAILED | FAILED |
| Adequate untouched sample | INSUFFICIENT EVIDENCE | INSUFFICIENT EVIDENCE | INSUFFICIENT EVIDENCE |
| Venue-identical Kraken history | BLOCKED | BLOCKED | BLOCKED |
| Live-candidate status | FAILED | FAILED | FAILED |

## Required next evidence

1. Keep live-money execution disabled for these versions.
2. Acquire and fingerprint complete Kraken Spot OHLCVT for the same symbols/timeframes, or collect forward-only Kraken paper data without replay.
3. Require at least 100 independently closed, `paper_forward + RealisticFill` trades per strategy-version/symbol, across at least three months and multiple regimes.
4. Re-run the frozen harness without changing parameters. Require positive untouched expectancy, PF at least 1.2, acceptable drawdown, and stability under 1.5x costs and one-bar latency.
5. Validate exact venue sizing, quantity rounding, minimum notional, compounding and global cross-instance risk before any dollar-return or live-readiness claim.
6. Any later strategy modification must receive a new version and a new untouched test; this report cannot be reused for changed logic.

## Reproduction

```bash
cd Tradexa-Production

PYTHONPATH=automation-hub:. python3 \
  automation-hub/scripts/strategy_validation.py \
  --data-dir /path/to/verified-binance-vision-zips \
  --output /tmp/tradexa-strategy-validation/evidence.json

python3 -m pytest \
  automation-hub/tests/test_strategy_simulator_parity.py \
  automation-hub/tests/test_builtin_strategy_versions.py -q
```

The command refuses incomplete years, duplicates, missing 5m intervals, invalid OHLC, or an incorrect archive count. It does not download data and has no synthetic fallback.

## Verification status

| Check | Result |
| --- | --- |
| Strategy simulator parity and causality tests | **VERIFIED** — 10 passed |
| Focused execution, fees, TradeBrain, learning, risk-order and live-engine tests | **VERIFIED** — 97 passed, 3 skipped |
| FastAPI health-breakdown test in the focused group | **BLOCKED locally** — the host Python environment does not have `fastapi`; no assertion ran |
| Python syntax compilation | **VERIFIED** |
| `git diff --check` | **VERIFIED** |
| Docker/VPS runtime | Not required for the offline statistical result; the already validated production proxy incident is outside this report and was not touched |
