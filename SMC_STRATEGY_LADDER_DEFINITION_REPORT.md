# SMC Strategy Ladder Definition Report — Frozen Research Definition

## Scope and status

- Research family: `SMC_NATIVE_V1_RESEARCH`
- Ladder: `SMC_STRATEGY_LADDER_V1`
- Version: `SMC_STRATEGY_LADDER_V1.0.0-research`
- Promoted from: `SMC_STRATEGY_LADDER_DRAFT_PRE_VERIFICATION`
- Definition status: **PASSED**
- Candidate status: **PASSED**
- Visual prerequisite: **VISUAL_STATE_VERIFICATION_PASSED**
- `execution_allowed`: **false** for every candidate, trace, and proposal
- Performance research: **NOT RUN**

The frozen 82-item visual sample, human retrospective bulk attestation, and
non-performance technical gates passed. The six existing draft fingerprints
matched exactly and were promoted without changing a strategy rule. This is a
frozen research definition, not a backtest, strategy-selection report,
forward-paper recommendation, or execution release. No TRAIN, validation,
walk-forward, or untouched-test dataset was opened during promotion.

## Preserved boundaries

The existing V1/V2/V3 conclusions remain unchanged. The future TRAIN manifest is deliberately `PENDING_APPROVAL`:

- Proposed TRAIN: BTCUSDT, ETHUSDT, SOLUSDT; 5m, 15m, 1h; 2025-01-01 through 2025-06-30.
- Validation from 2025-07-01: **SEALED_NOT_OPENED**.
- Untouched test from 2025-10-01: **SEALED_NOT_OPENED**.

No source data, candle count, gap count, result, ranking, or candidate score exists yet. An authoritative exchange dataset must be separately approved and registered before the TRAIN harness may read it.

## Native-object authority

`services/native_smc.py` remains the single source of market-structure facts. Frozen source SHA-256:

`741d8018cf53de53ed84a4cedd5513748e16a1b33e70e2058ccc28ebdbe3a389`

The ladder only consumes native `PivotPoint`, `StructureEvent`, `LiquiditySweep`, `FairValueGap`, `OrderBlock`, `DealingRange`, `PriceAction`, and `SMCMarketSnapshot` objects. It does not calculate pivots, BOS, CHoCH, sweeps, FVGs, order blocks, sessions, or premium/discount itself.

The existing Native SMC Visual Lab displays read-only traces. Selecting a candidate merely highlights the native object IDs supplied by the backend; the browser never calculates SMC, changes snapshots, creates a signal, or changes a trading engine.

## Draft shared mechanics

| Mechanic | Frozen definition |
| --- | --- |
| Candle eligibility | confirmed closed bars only |
| Entry semantics | qualifying closed-bar close; fill simulation is deferred |
| ATR | 14 |
| Stop | 1.5 ATR beyond signal-bar low/high |
| Target | 2.5R |
| Risk, sizing, fills, costs | pending one shared future research model |
| Direction conflict | reject both when long and short are simultaneously ready |
| Position overlap | one future research position per strategy/symbol; suppress further entries until that future simulator closes it |

Proposal IDs are deterministic from the candidate/setup identity, allowing a later simulator to de-duplicate repeated observations. They are not order identifiers and cannot reach paper or live execution.

## Freshness and invalidation

| Component | Maximum age |
| --- | ---: |
| Pivot | 10 bars |
| Sweep | 10 bars |
| Structure shift | 8 bars |
| FVG / order-block POI | 5 bars |
| Retest | current confirmed bar only |

Expired components cannot be recombined with newer components. A native opposite structure invalidates a qualifying sequence. For POI candidates, native mitigation before the current-bar retest also invalidates it.

## Candidate ladder

| ID | Strict ordered sequence | POI rule | Status |
| --- | --- | --- | --- |
| `SMC_S1_PIVOT_REVERSAL` | confirmed pivot low/high, then same-side native rejection | none | PASSED |
| `SMC_S2_STRUCTURE` | pivot, same-side native BOS/CHoCH after pivot, rejection | none | PASSED |
| `SMC_S3_LIQUIDITY_STRUCTURE` | native liquidity reference, sweep, same-side native BOS/CHoCH after sweep, rejection | none | PASSED |
| `SMC_S4_FVG_RETEST` | sweep, structure, same-direction FVG after structure, exact FVG retest, rejection | exact FVG only | PASSED |
| `SMC_S5_ORDER_BLOCK_RETEST` | sweep, structure, same-shift order block, exact OB retest, rejection | exact same-shift OB only | PASSED |
| `SMC_S6_FULL_SMC` | completed HTF, location, sweep, structure, POI, retest, rejection, native session | **FVG_OR_OB** | PASSED |

Long and short definitions are mirrored. The S6 `FVG_OR_OB` interpretation is
frozen in `SMC_STRATEGY_LADDER_V1.0.0-research`.

## Traceability and visual review

Each direction trace records the candidate/setup ID, native supporting object IDs, per-object age in bars, pass/missing/expired/invalidated condition state, causal ordering, next required event, and (only when ready) entry, stop, target, and 2.5R. Every trace and proposal carries `execution_allowed=false`.

## Validation performed

Definition-level tests passed without reading a market dataset:

- registry contains S1 through S6 and all candidates are non-executable;
- S1/S2 use native pivot and structure facts;
- S3 rejects structure that predates its sweep;
- S4 links the exact native FVG retest;
- S5 links the same-shift native order-block retest;
- S6 enforces native HTF/location/session and frozen `FVG_OR_OB`;
- expired pieces cannot be recombined;
- evaluation is read-only and proposal IDs are deterministic.

No profitability claim, performance metric, ranking, optimization, candidate selection, or forward-paper authorization exists.

## Completed freeze gate

1. Frozen sample and engine fingerprints matched the reviewed run.
2. The retrospective bulk attestation recorded 82 CORRECT, 0 INCORRECT, and 0 AMBIGUOUS.
3. All non-performance technical gates passed.
4. S1-S6 fingerprints remained unchanged during promotion.

## Later research requires separate authorization

1. Complete `TRAIN_UNIVERSE_APPROVAL` and register authoritative historical exchange-data manifest(s).
2. Run only the pre-registered TRAIN harness after separate authorization.
3. Define one shared realistic fill, cost, risk, and sizing model.
4. Report candidates independently without tuning or selection.

Validation, walk-forward, and the untouched test remain sealed. No candidate is eligible for forward paper or live execution.
