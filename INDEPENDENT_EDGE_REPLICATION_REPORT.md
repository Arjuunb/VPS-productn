# Independent Replication of Derivatives Edge Premises

## Purpose

Falsify four frozen discovery premises on declared, independent 2024 H1 official exchange archives. No strategy was created.

## Preserved Research Conclusions

2025 discovery remains exploratory only; Jul–Sep 2025 validation and Oct–Dec 2025 untouched test were not opened.

## Frozen Hypotheses

Definitions and fingerprints are in `automation-hub/data/independent_edge_replication_hypotheses.json`.

## Replication Dataset

2024-01-01 through 2024-06-30, declared before archive access. Binance USDⓈ-M Futures is separate from Binance Spot ETH 1D.

## Data Provenance

Archive hashes, observations, and gaps are in the machine-readable evidence.

## Execution Hurdle

Futures uses the frozen conservative 16 bp round-trip hurdle. ETH 1D Spot uses its frozen 14 bp comparison hurdle. Account-specific fees/fills were not verified.

## ETH Funding-Low Replication

**REPLICATION WEAK** — events 1708, independent 85, gross 40.85 bp, net 24.85 bp, positive months 3/6, random difference 34.50 bp.

## SOL Funding-Low Replication

**REPLICATION WEAK** — events 1683, independent 91, gross 16.87 bp, net 0.87 bp, positive months 4/6, random difference 14.01 bp.

## ETH Relative-Volume Replication

**REPLICATION FAILED** — events 3092, independent 534, gross -1.14 bp, net -17.14 bp, positive months 2/6, random difference 0.23 bp.

## ETH 1D Persistence Replication

**REPLICATION WEAK** — events 152, independent 14, gross 194.18 bp, net 180.18 bp, positive months 3/6, random difference 184.56 bp.

## Discovery vs Replication

| Hypothesis | Discovery gross bp | Replication gross bp | Replication ratio | Verdict |
| --- | ---: | ---: | ---: | --- |
| REP-H1-ETH-FUNDING-LOW | 24.09 | 40.85 | 1.696 | REPLICATION WEAK |
| REP-H2-SOL-FUNDING-LOW | 27.38 | 16.87 | 0.616 | REPLICATION WEAK |
| REP-H3-ETH-REL-VOLUME-HIGH | 18.06 | -1.14 | -0.063 | REPLICATION FAILED |
| REP-H4-ETH-1D-PERSISTENCE | 101.87 | 194.18 | 1.906 | REPLICATION WEAK |

## Effect Shrinkage

Replication ratios above are descriptive; a sign reversal or cost failure outweighs any raw magnitude.

## Monthly Stability

Per-month event metrics are retained in JSON. The gate requires at least four positive months and no single month contributing over 50% of absolute event contribution.

## Regime Dependence

Causal trend/range and volatility labels are diagnostic only; no regime filter was added.

## Random / Placebo Controls

Matched-frequency deterministic timestamp controls with shuffled directions were used, identical in form to discovery.

## Execution-Cost Sensitivity

All conclusions use frozen hurdles; no post-result fee reduction was allowed.

## Account Execution-Cost Audit

**UNVERIFIED** — No verified account-specific fee/fill schedule is embedded in this research harness; conservative frozen hurdles remain in force.

## Replication Verdicts

- **REP-H1-ETH-FUNDING-LOW**: REPLICATION WEAK
- **REP-H2-SOL-FUNDING-LOW**: REPLICATION WEAK
- **REP-H3-ETH-REL-VOLUME-HIGH**: REPLICATION FAILED
- **REP-H4-ETH-1D-PERSISTENCE**: REPLICATION WEAK

## Remaining Risks

Six months is a limited independent sample. Association is not causality; queue position, fills, funding payments, and actual account fees remain unverified.

## Next Authorized Stage

**PASSED REPLICATION PREMISES: 0**
- None. Stop; do not create a strategy.

Only a passed premise would authorize minimal strategy architecture design, never validation, paper trading, or live trading.
