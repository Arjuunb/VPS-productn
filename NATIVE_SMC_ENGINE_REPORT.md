# Native SMC Market Structure & Execution Engine

Status: **SMC_NATIVE_V1_VISUALLY_VERIFIED_FROZEN**
Research ID: `SMC_NATIVE_V1_RESEARCH`
Execution authority: **false**

## Architecture

`closed exchange candles → SMCMarketStructureEngine → SMCMarketSnapshot / setup
state → proposed trade → existing risk and execution boundaries`

The engine has no TradingView, Pine, webhook, order, paper-execution, or live
execution dependency. `ingest_authoritative_closed_bars()` accepts only
provider-validated closed candles through the existing forward-data adapter.

## Native domain objects

- `PivotPoint`: occurrence timestamp and later confirmation timestamp.
- `StructureEvent`: internal/swing BOS or CHoCH, direction, source pivot and
  break price.
- `LiquiditySweep`, `FairValueGap`, `OrderBlock`, `DealingRange`, and
  `PriceAction`.
- `SMCSetup`: deterministic chronological state plus transition log.
- `ProposedTrade`: entry, ATR stop, 2.5R target, setup and snapshot IDs.
- `ChartObject`: references the exact FVG/OB ID used in state and reasoning.

## Pine mapping and deliberate differences

The Pine source remains a behavioural reference only. Its right-confirmed
pivot timing, closed-bar HTF context, ATR-14 / 1.5 stop and 2.5R target are
mapped into native defaults.

The native model deliberately corrects documented reference architecture
defects: rendering never changes FVG/OB state; the native FVG baseline does
not depend on arbitrary loaded-chart history; and chronological setup order is
required rather than combining unrelated “recent” events.

Consequently `PINE_REFERENCE` and `NATIVE_CORRECTED` must be compared as
separate versions. This build does not claim Pine signal parity.

## Setup state machine

Setups move only in order:

`LIQUIDITY_SWEPT → STRUCTURE_SHIFT_CONFIRMED → POI_CREATED → WAITING_RETEST
→ REJECTION_CONFIRMED → ENTRY_READY`

The engine rejects out-of-order FVGs, requires a retest after POI creation,
expires stale setups, invalidates on HTF/opposite structure changes, and gives
every setup, event, zone, transition and proposal a stable ID.

## Risk and execution isolation

`ENTRY_READY` creates a `ProposedTrade` only. Its `risk_status` is
`PENDING_RISK_ENGINE`; it cannot call an execution engine. `execution_allowed`
is hard-coded false in the service, configuration, endpoints and manifest.

## Chart, observability and API

The read-only endpoints under `/research/smc/*` expose snapshots, the same
event objects, setups, proposals and chart objects. A future chart consumes
these object IDs rather than reconstructing its own SMC zones.

Checkpoint/restore deterministically rebuilds state from retained closed bars
and preserves stable IDs, so replaying a persisted candle cannot duplicate a
structure event or proposal.

## Tests

The focused suite verifies closed-bar idempotency, completed HTF use, pivot
timing, BOS/CHoCH exact-once creation, sweeps, FVG/OB lifecycles,
premium/discount, rejection candles, ordered setup rules, expiry, restart
recovery, UI/state separation, and execution isolation.

## Remaining risks and next stage

No forward worker has been attached and no performance data has been opened.
The frozen 82-item visual sample is verified through a retrospective human bulk
attestation, with all non-performance technical gates passing. The only
authorized next stage is `TRAIN_UNIVERSE_APPROVAL`; execution remains disabled.
