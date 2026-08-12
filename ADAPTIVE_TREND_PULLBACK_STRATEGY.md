# Adaptive Multi-Timeframe Trend Pullback — v1.0.0

Production candidate for deterministic backtesting, walk-forward research and
forward-paper validation. It is not presented as profitable and it is not
enabled for live brokerage.

## Causal decision architecture

| Timeframe | Single responsibility |
| --- | --- |
| 4H | Directional market regime and volatility safety |
| 1H | Main trend and meaningful swing invalidation |
| 15M | Corrective pullback and trade location |
| 5M | Closed-candle entry confirmation and local structure break |

Trading Instances must use a **5m decision timeframe**. Forward workers fetch
each timeframe independently through the strict live-provider adapter. The
forming candle is removed on every timeframe, and context is filtered by candle
close time at each recovered 5m decision. Historical, CSV, cache and synthetic
fallbacks cannot feed a forward-paper worker.

## Entry contract

A signal requires all of these hard stages:

1. 4H `BULL_TREND` or `BEAR_TREND`, confidence at least 70.
2. Matching 1H EMA, structure, invalidation and ADX evidence.
3. A corrective 15M pullback at EMA or prior breakout structure, without an
   invalidating swing break or abnormal opposing volume.
4. A completed 5M rejection/engulfing/dominance candle **and** local structure
   break. A zone touch cannot trigger an order.
5. Quality score at least 75/100 and planned reward/risk at least 2.0R.

LONG and SHORT are structural mirrors. The stop is beyond the confirmed 15M
pullback swing plus a configurable 5M ATR buffer. The default target is 2.5R;
an optional next-1H-structure target is available but fails closed below 2R.

## Safety and execution

- `RANGE`, `UNCERTAIN` and `HIGH_VOLATILITY` regimes block new entries.
- Missing or stale data on any required timeframe blocks the worker.
- Position sizing, account drawdown, daily loss, consecutive-loss cooldown,
  correlation, venue filters and kill switch remain independent pipeline gates.
- Trading Instances now receive the server's `HUB_MAX_DRAWDOWN`,
  `HUB_MAX_DAILY_LOSS`, `HUB_MAX_CONSEC_LOSSES`, session/day and cooldown
  policy; these controls are no longer limited to the legacy engine.
- Trading Instances default to `RealisticFill`, with spread, slippage, fees,
  latency and venue precision handled by the existing execution engine.
- Every accepted signal records timeframe closes, regime confidence, pullback
  location, confirmation, score, stop, target and R:R in the decision journal.
- Every WAIT/REJECT scan is written as a structured strategy decision log.

## Initial defaults

```text
EMA: 20 / 50
ADX period/minimum: 14 / 20
Regime/trend confidence minimum: 70
Entry quality minimum: 75
Risk recommendation: 0.25%–0.50% per trade
Minimum/target R:R: 2.0 / 2.5
Stop ATR buffer: 0.25
High-volatility handling: block
```

These are conservative starting parameters, not optimized profitability claims.
Before any live-broker use: out-of-sample and walk-forward tests, fee/spread/
slippage stress tests, BTC/ETH/SOL regime coverage and at least 100–300 genuine
forward-paper trades must demonstrate acceptable profit factor, expectancy,
drawdown, losing streak and fees as a percentage of gross profit.
