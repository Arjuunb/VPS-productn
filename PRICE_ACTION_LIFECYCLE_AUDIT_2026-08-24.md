# Price Action lifecycle and result audit — 2026-08-24

## Safety boundary

This audit and the implementation it accompanies are restricted to native Price Action research and the isolated paper account. The native engine still rejects `execution_allowed=True`; no private Binance API, credential, real-order, withdrawal, leverage-changing, or live-account path was introduced.

## Reproduced rolling-window result

The current public Binance USDⓈ-M `BTCUSDT` 5-minute window was rerun before the lifecycle changes with the saved baseline configuration:

- candles: 1,500 completed bars
- range: `2026-08-19T08:35:00Z` through `2026-08-24T13:30:00Z`
- closed normalized outcomes: 391 (99 wins / 292 losses)
- unfilled: 82
- cancelled setups: 11
- gross result: `-101.1617R`
- modeled execution costs: `111.9999R`
- net result: `-213.1616R`
- PA1: `-120.787R`
- PA2: `-25.889R`
- PA3: `-50.373R`
- PA4: `-16.113R`
- duplicate trade/proposal/setup identifiers: 0
- pending outcomes beyond their saved expiry: 0
- dataset SHA-256: `407d307a...`
- configuration SHA-256: `8d896d21...`

This is a rolling observation window, not a frozen walk-forward experiment. It is close to the previously displayed approximately `-221.98R`, but it is not expected to be byte-identical because the live rolling window advanced. Funding is not included in the visual-engine metrics. Commission, spread, slippage, and adverse same-candle ambiguity are included. The result is negative evidence and must not be presented as profitability or economic validation.

## Post-repair bounded public-data verification

The repaired `1.1.0` engine was rerun against a fresh bounded public Binance window after implementation. This is a new rolling observation—not a replacement for the pre-repair evidence above:

- completed candles: 1,499
- range: `2026-08-19T10:00:00Z` through `2026-08-24T14:50:00Z`
- completed normalized outcomes: 555 (173 wins / 382 losses)
- expired unfilled orders: 52
- structurally invalidated unfilled orders: 272
- currently valid normalized orders: 1 pending and 2 open
- pending orders beyond saved expiry: 0
- duplicate trade, proposal, or setup identifiers: 0
- gross result: `-29.1391R`
- modeled commission result: `-159.2256R`
- net result: `-188.3647R`
- PA1: `-108.4088R`
- PA2: `-6.3048R`
- PA3: `-61.1236R`
- PA4: `-12.5275R`
- dataset SHA-256: `3dab5379...`
- engine SHA-256: `216b960b...`
- configuration SHA-256: `8d896d21...`

This remains materially negative. No parameter was tuned and no loss was suppressed or relabelled to improve it. The changed count and result reflect the repaired later-candle lifecycle, deterministic invalidation policy, and a later rolling data window; economic claims still require a frozen walk-forward study.

## Frozen multi-market walk-forward result

The predeclared PA1–PA4 reference study was then run once against newly downloaded public Binance USDⓈ-M data. The run used the frozen five-symbol universe (`BTCUSDT`, `ETHUSDT`, `BNBUSDT`, `SOLUSDT`, `XRPUSDT`), `15m`, `1h`, and `4h` timeframes, 3,000 completed candles per market, chronological development/validation/untouched-OOS partitions, the declared per-symbol commission/spread/slippage assumptions, complete public funding histories, and the baseline plus seven predeclared one-change-at-a-time variants. No result was used to tune or select a parameter.

- artifact: `pa-reference-f28419179a0f970bb717`
- artifact JSON SHA-256: `417c521efd542094e7a831503fab9becccd9e0ac5f1146bfdd3b0cfe96477fb1`
- dataset version: `dataset-28460b0692493205fe2b`
- code version: `code-17dc5ec8e70e35a6e854`
- study definition SHA-256: `7b1dfc6ba1bebccfe8583268f2b1d40121b0e03be213d701d6d16205761bcd86`
- all-period completed trades: 18,179 (5,388 wins / 12,791 losses)
- all-period gross / total costs / funding component / net: `1861.5904R / 4919.4925R / -25.6850R / -3057.9021R`
- all-period expectancy / profit factor / maximum drawdown: `-0.1682R / 0.8022 / 3668.9838R`
- untouched-OOS completed trades: 3,450 (1,048 wins / 2,402 losses)
- untouched-OOS gross / total costs / funding component / net: `420.1250R / 845.0035R / -2.5968R / -424.8785R`
- untouched-OOS expectancy / profit factor / maximum drawdown: `-0.1232R / 0.8507 / 720.2053R`
- complete historical funding coverage: 5 / 5 symbols
- variants with positive untouched-OOS expectancy: 0 / 7
- quality classification: `EVIDENCE_INSUFFICIENT_OR_FAILS_FILTERS`
- live execution allowed: `false`

The result fails positive net OOS expectancy, drawdown, multi-asset support, increased-cost resistance, and parameter-stability gates. It is evidence against promotion, not a profitability claim. The PA-versus-SMC comparison was not run because no normalized SMC dataset with identical datasets, partitions, costs, funding, risk, and fill/ambiguity rules was supplied.

## Lifecycle defects found

1. A rejection/reaction candle could satisfy the former dominance condition and create an entry-ready result on that same candle. The engine now records the reaction first, stages a confirmation stop, and only a later completed candle may activate it.
2. Strategy evaluation selected the oldest qualifying recent event. It now selects the latest qualifying trigger and resolves the zone through that trigger's `zone_id`.
3. If a pending order's entry and invalidation were both inside one OHLC candle, the old model could invent an entry followed by a loss. It now applies conservative adverse-first uncertainty: the unfilled order is invalidated without fabricating a fill.
4. A structurally invalidated setup could leave a normalized pending research order behind. Terminal setup state now propagates to the normalized order.
5. Paper broker candles and mark-based liquidation could advance while market-data channels were unreconciled. Pending strategy orders are now cancelled as `DATA_PAUSED`, and no fill, protective exit, funding debit, or liquidation is inferred until candles, bid/ask, and mark price are synchronized and fresh.
6. Setup evidence was spread across transient engine state, broker metadata, and UI tables. A dedicated append-only Price Action journal now preserves setup identity, state transitions, data health, configuration/dataset/engine fingerprints, order/risk evidence, outcomes, costs, classification, and revisions.
7. Excursion and timing fields were previously always null. The normalized research lifecycle now records conservative completed-OHLC MFE/MAE in R, bars-to-entry, and bars-in-trade. It excludes activation-candle extremes and uses only the executed exit on a terminal candle because intrabar sequence is not known.
8. Fill-time bid/ask/mark evidence was previously discarded. PAPER fills now retain the quote only when candles, bid/ask, and mark are reconciled and fresh; replay or unreconciled paths leave the field explicitly unavailable.
9. Funding debits were session/symbol scoped but not attributable to an order. Applied funding events now preserve the owning PAPER order identifier and the journal reports both USDT and risk-normalized R when an order-scoped risk amount exists.

## Canonical sequence after repair

`WATCHING_LOCATION → LOCATION_REACHED → REJECTION_DETECTED → WAITING_FOR_CONFIRMATION → ORDER_PENDING → ENTERED → STOPPED | TARGET_HIT | LIQUIDATED_PAPER | CLOSED_OTHER`

The valid pre-entry terminal outcomes are `CANCELLED`, `EXPIRED`, `INVALIDATED`, and `DATA_PAUSED`. A reaction candle and a confirmation/activation candle are never the same candle under the baseline confirmation model.

## Learning governance

- Every setup, including open, rejected/unfilled, invalidated, expired, data-paused, winning, and losing evidence, can remain visible in the journal.
- Outcome classification is deterministic and does not modify strategy configuration.
- Pattern analysis reports sample size and uncertainty and labels hypotheses; it does not promote changes.
- Candidate creation is development-only, must contain exactly one allow-listed rule difference, and requires explicit state transitions before shadow observation.
- Shadow engines consume the same later completed bars while remaining isolated from the official paper account.
- Validation, untouched out-of-sample, and paper-forward evidence cannot be used to tune candidates.
- No automatic optimization or live promotion exists.

## Remaining research limitations

- The rolling 5-minute visual result is not the walk-forward artifact and funding remains excluded from that UI aggregate. The frozen reference artifact above is the cost- and funding-aware research result.
- MFE, MAE, bars-to-entry, bars-in-trade, fill-time quote, and normalized funding are populated only when their source evidence proves them. Legacy/replay records without that evidence remain explicitly unavailable rather than fabricated.
- Normalized paper-dollar outcome remains unavailable when the broker evidence cannot safely associate every entry, exit, fee, and funding event with the same order.
- The full reference artifact and provider caches were generated outside source control; production verification should retain them in the documented durable VPS validation directory.
- A fair PA-versus-SMC comparison remains blocked until a compatible normalized SMC dataset exists.
- A candidate must still complete explicit development, validation, untouched out-of-sample, and paper-forward gates before it can be considered for a separately governed baseline version.
