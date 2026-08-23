# Native SMC Visual State Verification

Visual Lab status: **SMC_VISUAL_LAB_READY**  
Human-verification status: **VISUAL_STATE_VERIFICATION_PASSED**
Research ID: `SMC_NATIVE_V1_RESEARCH`  
Execution authority: **false**

This is a structure-visualisation audit only. It contains no profitability
claim, no parameter selection, no paper execution, and no live execution.

## Provenance resolution

The original v1 manifest remains retained at
`automation-hub/data/native_smc_visual_verification.json`. Its original value
`a1f4381240c422cda18464a054b34f75bb32bc5bb9137306e6c9d412e1b160aa`
was not a March BTCUSDT artifact: the frozen research inventory at commit
`26460803317302fa9bc68738405a9d59b52e60ac` identifies it as the ZIP hash for
`BTCUSDT-5m-2025-05.zip`. It was copied into the March v1 record, making this a
**transcription/configuration mistake**, not a changed Binance artifact or a
CSV-versus-ZIP ambiguity.

The versioned successor,
`automation-hub/data/native_smc_visual_verification_manifest_v2.json`, records
the independently verified March ZIP hash:

`1eb1fb0c76a0e8302abdec42d54b844bd3c3d6d7e6e69ae434aaa1dea445d54d`

Both the downloaded official archive and Binance Vision's `.CHECKSUM` sidecar
matched it. The full supersession decision, preserved old value, source links,
and canonical evidence hash are in
`automation-hub/data/native_smc_dataset_provenance_resolution.json`.

## Verified dataset integrity

| Field | Verified value |
| --- | --- |
| Exchange / source | Binance Spot / official Binance Vision monthly kline archive |
| Symbol / timeframe | BTCUSDT / 5m |
| Partition | TRAIN only |
| Archive / payload | `BTCUSDT-5m-2025-03.zip` / `BTCUSDT-5m-2025-03.csv` |
| ZIP SHA-256 | `1eb1fb0c76a0e8302abdec42d54b844bd3c3d6d7e6e69ae434aaa1dea445d54d` |
| CSV SHA-256 | `095ffd662b5ae8b0d2780b86c1145791930c9a6da0403b7e658462831aee9db1` |
| Size | 475,553 bytes |
| Period | 2025-03-01 00:00 through 2025-03-31 23:55 UTC |
| Closed rows | 8,928 |
| Duplicates / gaps / malformed / incomplete | 0 / 0 / 0 / 0 |

Binance's current export carries microsecond epoch timestamps. The verified
loader explicitly handles documented millisecond and microsecond epochs and
rejects all other malformed timestamp/value layouts. It also validates filename,
ZIP hash, official checksum identity, payload filename/hash, continuity,
positive finite OHLC, OHLC ordering, and non-negative volume before calling
`ingest_authoritative_closed_bars()`.

## Native visual ingest

The verified archive was passed through the authoritative closed-bar adapter,
not injected into the engine. It produced 8,928 native snapshots and the
following inspectable native objects: 6,640 pivots, 1,987 structure/sweep
events, 908 FVGs, 673 order blocks, 507 setups, and 1 research proposal. These
counts are observability inventory only—not strategy performance statistics.

The original frozen ingest evidence hash is
`c8641eda709e673718b6902a6eb84595280f24fe62802049f32281c0e4bf5ceb`.
Raw archives, checkpoints, review samples, and evidence outputs stay outside
the repository.

## Visual Lab and independent review

The Visual Lab reads only `GET /research/smc/chart`; the browser does not
calculate pivots, structure, zones, setup state, entries, stops, or targets.
It renders genuine closed OHLCV candles with linked volume, crosshair,
mouse-wheel zoom, drag pan, scroll zoom slider, fit-content control, current
price line, a selectable candle OHLCV strip, a light/dark chart canvas, and
native-only FVG/OB/pivot/structure/liquidity/proposal overlays. Rendering
filters change only browser presentation.

Every processed closed candle now has an immutable `SMCMarketSnapshot` in the
engine's historical snapshot ledger. The selected-candle inspector reads that
record directly and shows the exact closed-candle HTF/swing/internal bias,
dealing range and location, liquidity/structure references, active FVG/OB IDs,
rejection state, setup/next state, and proposal references. This ledger is
observability evidence only and is never read by strategy or execution code.

An 82-item stratified deterministic review sample was frozen from the
verified input (seed `SMC_NATIVE_V1_RESEARCH:BTCUSDT:2025-03`). It covers
swing/internal pivots, BOS, CHoCH, liquidity sweeps, active/mitigated FVGs,
active/mitigated order blocks, setup phases, and the recorded research proposal.
Review entries are append-only `CORRECT`, `INCORRECT`, or `AMBIGUOUS` evidence
and cannot mutate engine rules. They are persisted outside source control at
`/var/lib/tradexa/smc_visual_reviews.json` by default.

The user subsequently attested that all 82 frozen items had already been
manually inspected and matched the intended SMC interpretation. This was
recorded truthfully as one `HUMAN_RETROSPECTIVE_BULK_ATTESTATION` parent plus
82 append-only `CORRECT` classification records. It is not represented as 82
contemporaneous UI submissions. No individual notes, reasoning, timestamps, or
screenshots were fabricated. The final append-only visual evidence hash is
`c60d72de508181a80b9d9d8531c339ed5460b8f9f91fe3e6bf94b3789d200220`.

Premium/discount and rejection-candle states are now exposed from the
historical native snapshot ledger, not reconstructed in the browser. Equal
high/equal-low labels are not drawn because the native V1 engine does not emit
an EQH/EQL domain object; the lab deliberately does not invent one.

## Current component status

| Component | Status |
| --- | --- |
| Pivot, BOS, CHoCH, sweep, FVG, OB, setup sequence, invalidation | VERIFIED — sampled items classified CORRECT |
| ENTRY_READY, proposed entry, SL, TP | VERIFIED — sampled items classified CORRECT |
| Premium/discount historical path, rejection-candle historical path | VERIFIED — sampled items classified CORRECT |
| Equal-high / equal-low labels | NOT AVAILABLE — no native V1 domain object |
| Human agreement / critical-mismatch rate | 82 / 82 CORRECT; 0 INCORRECT; 0 AMBIGUOUS |

No critical mismatch was recorded. The sample, native object IDs, engine source,
configuration, and original ingest evidence were revalidated before the bulk
attestation was bound to this run.

## Verification performed

The focused non-performance suite passes **56 tests**, including full-month archive
integrity, payload-hash rejection, deterministic sampling, no engine mutation
from review operations, checkpoint recovery, per-candle snapshot ledger,
chart/domain-object identity, confirmed-pivot timing, attestation conflict
protection, frozen fingerprints, entry/SL/TP integrity, and execution isolation.

## Next required action

The exact native engine is frozen as
`SMC_NATIVE_V1_VISUALLY_VERIFIED_FROZEN`, and the unchanged S1-S6 definitions
are promoted to `SMC_STRATEGY_LADDER_V1.0.0-research`. Execution authority
remains **false**. The next authorized stage is `TRAIN_UNIVERSE_APPROVAL`;
TRAIN, validation, untouched test, forward paper, and live trading remain closed.
