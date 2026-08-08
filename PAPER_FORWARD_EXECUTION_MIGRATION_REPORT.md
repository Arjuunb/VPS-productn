# Paper Forward Execution Migration

## Previous behaviour

Trading Instances inherited the ambiguous `HUB_USE_LIVE_DATA` boolean. When it
was false, a worker consumed local historical data in replay mode. With a cache
present, the replay worker could reload the same newest window after reaching
its end and create additional paper decisions from already-seen history.

## Root architecture issue

Paper execution, research replay, and forward-market execution were selected by
one global boolean. The active instance worker was therefore able to use the
same cache/replay path intended for research. The production evidence recorded
BTC 5m cache data ending on 2026-08-01, while a Supertrend instance created 371
closed trades on 2026-08-08. That was replay evidence, not forward paper
trading.

## New data-mode boundary

| Instance mode | Market data mode | Permitted source |
| --- | --- | --- |
| `trading` | `paper_forward` | Provider live OHLCV only (`ccxt`) |
| `research` | `replay` | Existing historical/research path |
| Backtest / simulation | separate callers | Historical datasets only |

Paper Trading no longer depends on `HUB_USE_LIVE_DATA`: a trading instance
always constructs a forward engine with the strict `data.forward_market_data`
adapter. Provider absence produces a market-data error; it never falls back to
SQLite cache, CSV fixtures, synthetic candles, or replay.

## Warm-up and exactly-once design

1. The provider fetch supplies historical context solely to warm indicators.
2. The final provider candle is conservatively excluded because REST OHLCV
   normally represents the forming candle by position rather than a closed flag.
3. On first forward start the newest closed warm-up candle becomes the durable
   cursor; no warm-up candle is traded.
4. Later candles must be strictly newer than the persisted cursor.
5. After each processed candle, the cursor is persisted in
   `instance_market_state`.
6. After restart, candles newer than that cursor are processed in chronological
   order. Duplicate and older events are ignored.

## Data health and stale protection

The engine exposes data source, latest closed candle, last processed candle,
warm-up count, duplicate/missing/out-of-order counts, reconnect state, and a
market-data status. A source older than 1.5 timeframe intervals is stale and
causes the forward worker to enter the existing bounded reconnect path. New
entries never switch to old history. If valid data is unavailable after the
bounded retries, the worker becomes an explicit error rather than a replay.

Existing positions are retained in the scoped ledger. When valid forward data
returns, the PaperExecutionEngine reads the existing position and the engine
adopts its stop for safe continued management. This migration never deletes or
recreates positions.

## Persistence migration

Before deploying, run the current
`automation-hub/data/trading_instances_schema.sql` in the Supabase SQL editor.
It adds `trading_instances.market_data_mode` and the durable
`instance_market_state` table. Existing historical paper trades are untouched.

No historical row is relabelled as forward paper, because its execution source
cannot be proven. The cutover is the deployment timestamp of this migration;
all future Trading Instance entries are governed by `paper_forward` mode.

## Dashboard

The Trading Instance detail card now presents live-market status, source, last
closed/processed candles, warm-up progress, and integrity counters. A non-
healthy data status explicitly says that new entries are paused.

## Validation

Focused deterministic tests cover:

- exact-once cursor processing and duplicate rejection;
- stale data fail-closed behaviour;
- provider failure without fallback;
- market-cursor persistence across manager reconstruction;
- existing instance isolation and platform-risk tests.

## Remaining operational risks

- A VPS IP may be blocked by the configured exchange. This correctly produces
  an explicit data error. Choose a reachable supported exchange before treating
  paper results as forward evidence.
- A very long outage beyond the provider's returned history window fails closed
  rather than silently skipping candles. Operator review is required.
- Entry/fill attribution is not retroactively available on legacy rows. This
  migration preserves them as legacy evidence instead of guessing metadata.
