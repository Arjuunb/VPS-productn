# SMC PRO Research Integration & Pine-to-Python Parity Audit

Status: **PARITY_AUDIT**  
Family: `SMC_PRO_V1_RESEARCH`  
Execution authority: **false**

## Research purpose

This is a new, isolated hypothesis. It does not reopen the V1/V2/V3, spot
OHLCV, or derivatives-replication conclusions. No result in this document is a
profitability claim, and no SMC variant is registered for production, forward
paper, or live trading.

## Immutable Pine reference

| Field | Value |
| --- | --- |
| File | `automation-hub/research_references/smc_pro_v2_reference.pine` |
| Pine version | v6 |
| Strategy | Smart Money Concepts Master [PRO STRATEGY v2] |
| SHA-256 | `95ec2874dd52abba0d26088d1fbce6208f73ed747a885b0dc0ca89fc0fb33e8c` |
| Imported | 2026-08-15 |

The placeholder `CHANGE_ME_SECRET` is preserved as part of the supplied
reference; it is not a usable credential.

## Actual execution versus dashboard

The reference submits `strategy.entry` using `longCondition` or
`shortCondition` only. Its dashboard computes `tradeReadyCondition` using the
extra `contextScore >= 70`, `executionScore >= 75`, and `rrOK` requirements,
but those requirements do **not** gate `strategy.entry`.

Therefore two deliberately separate research variants exist:

| Variant | Entry semantics |
| --- | --- |
| `SMC_PRO_CORE_V1` | Exact supplied TradingView order condition |
| `SMC_PRO_GATED_V1` | Core condition plus the dashboard score gate |

Scores are classified as `HEURISTIC_SCORE`; no score is treated as a
probability or calibrated confidence.

## Causality audit

| Component | Finding | Status |
| --- | --- | --- |
| Orders | `calc_on_every_tick=false`, `process_orders_on_close=true`; entries are close-bar orders in the Pine broker emulator | Causal signal timing; fill semantics need separate comparison |
| Confirmed bar gate | `barstate.isconfirmed` is used when `confirmBarsOnly=true` | Causal |
| HTF bias | `request.security(... close[1])`, EMA `[1]`, `lookahead_off` uses the prior completed 4h candle | Causal if the Python port uses completed 4h bars only |
| Pivots | A pivot occurs at T but the custom `leg(size)` logic makes it knowable at T + `size` closed bars | Causal only from confirmation timestamp |
| Structure / OB | CHoCH/BOS and the resulting OB are created on the confirmed break bar | Causal when the port delays use until that bar |
| FVG | Default timeframe detection becomes available only after the detection bar closes | Causal at default timeframe; higher-timeframe export is still required for parity |
| Event windows | `barssince` deliberately combines events from separate bars | Causal but must record ages at every signal |

### Reference defects documented, not silently corrected

1. **Dashboard/order mismatch:** the dashboard can say anything other than
   “Trade Allowed” while core orders are still submitted. This is the reason for
   separate Core and Gated variants.
2. **Display settings alter strategy state:** disabling Fair Value Gap drawing
   prevents the function that sets the required FVG alert state from running.
   Order-block mitigation is also conditional on display toggles. This makes
   visual settings execution-relevant. The parity port preserves default
   behaviour; a corrected, separately versioned variant may only be considered
   after this audit is closed.
3. **Auto FVG threshold is chart-history dependent:** it divides cumulative
   historical movement by `bar_index`. Different loaded chart histories can
   alter signals without any future-data access. This is a reproducibility
   defect, not a proven lookahead defect.

## Python port

`automation-hub/strategies/research_smc_pro.py` contains a stateful,
research-only closed-bar port. It exposes HTF bias, structure state, pivot
occurrence and confirmation timing, FVG/sweep/CHoCH ages, order-block
proximity, heuristic scores, core conditions, gated conditions, and the exact
ATR signal-candle SL / 2.5R TP formula.

`execution_allowed` is hard-coded false and the identifiers are absent from the
server strategy catalogue. This is server-side isolation, not UI hiding.

## Pine-to-Python event parity

**BLOCKED — no trustworthy TradingView closed-bar event export has been
provided.** A synthetic test fixture cannot prove parity. Required export:
timestamp, HTF/swing/internal bias, sweep flags, BOS/CHoCH, FVG flags, OB
proximity, rejection flags, `longCondition`, `shortCondition`, and
`tradeReadyCondition`, for a fixed symbol, timeframe, parameter set, and
genuine exchange-candle period.

The required instrumentation-only exporter is now available at
`automation-hub/research_references/instrumentation/smc_pro_v2_parity_export_only.pine`.
Its operating procedure and fixed collection window are in
`SMC_PRO_PARITY_EXPORT_GUIDE.md`. It cannot emit the original Tradexa trade
webhooks; it emits confirmed-bar parity records only.

Until this exists, the only completed tests are source-integrity, causal-pivot,
variant-semantics, registry-isolation, and SL/TP formula invariants. No
performance study, component attribution, score calibration, validation, or
untouched-test access has occurred.

## Final classification

**PARITY FAILED** is not asserted; signal comparison has not yet occurred.

**Current required status: `PARITY_AUDIT` (BLOCKED on an authoritative Pine
event series).**
