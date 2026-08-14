# Research Program Closure — 2026-08-15

## Decision

The current strategy-generation branch is closed. No production strategy,
forward-paper candidate, or live candidate is authorized by this evidence.

## Closed universes

| Research universe | Status | Evidence |
| --- | --- | --- |
| Binance Spot OHLCV, BTC/ETH/SOL, 5m/15m/1h | Exhausted / not currently viable | `EDGE_FEASIBILITY_AUDIT.md` |
| V1/V2/V3 strategy families | Exhausted / research only or rejected | V2/V3 reports and ledgers |
| Basic funding/OI/futures-flow discovery | Insufficient for strategy construction | `RESEARCH_UNIVERSE_EXPANSION_REPORT.md` |
| Independent replication, 2024 H1 | Closed: zero premises passed | `INDEPENDENT_EDGE_REPLICATION_REPORT.md` |

## Preserved boundaries

- Jan–Jun 2025 remains discovery evidence only.
- Jul–Sep 2025 validation remains sealed.
- Oct–Dec 2025 untouched test remains sealed.
- The 2024 H1 replication archives are independent evidence and are not pooled
  into discovery metrics.

## Execution economics

`ACCOUNT_EXECUTION_COST_AUDIT` is **UNVERIFIED**. Future research must obtain
the actual account's maker/taker commissions, order-type behaviour, observed
spread/slippage, and funding treatment before using account-specific cost
assumptions. This cannot retroactively change the replication verdicts.

## Authorized next decision

Pause strategy generation. Before beginning any new strategy research, decide
whether a materially different primary-source information set is available—for
example liquidation imbalance, order-book depth, cross-venue dislocations,
options volatility, term structure, cross-asset lead/lag, or event data. A new
universe requires a fresh source-quality gate and independent discovery plan.
