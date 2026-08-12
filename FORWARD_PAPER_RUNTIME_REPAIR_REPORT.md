# Forward Paper Runtime Repair Report

Date: 2026-08-12

Scope: TradeLogX Nexus Trading Instances and forward paper execution

Safety: paper execution only; no broker order was sent

## Final Status

**PARTIAL - Code fixed but runtime verification incomplete**

The repaired worker was verified against genuine public Kraken candles through
bootstrap, warm-up, WebSocket/forward ingestion, exactly-once processing, cursor
advance, and dashboard/API state. The naturally closed verification candle did
not create a strategy signal. No signal, order, position, or exit was fabricated,
so this report does not claim the full signal-to-closed-trade path has occurred on
the repaired production runtime yet.

## Root Causes

1. Closed-candle detection used the provider payload's final list position as a
   proxy for whether the candle was forming. Some venues omit a forming candle,
   so the newest genuinely closed 5-minute candle was discarded and a healthy
   feed appeared five minutes older than reality.
2. Freshness used a candle's open timestamp as though it were the close
   timestamp. This added one full timeframe to the reported age and caused the
   observed `age > 450s` failures on otherwise current 5-minute data.
3. Restart bootstrap requested a latest fixed-size window. When the persisted
   cursor was older than that window, the strategy could not reconstruct 150
   bars before the cursor and failed permanently.
4. Bootstrap requested too little depth and accepted shallow WebSocket cache
   data in place of REST history. A stream cache could therefore masquerade as
   the historical warm-up required for a safe restart.
5. Gap detection counted missing candles but did not halt decisions and run a
   cursor-relative REST repair first.
6. Instances were persisted as `running` before REST bootstrap, warm-up, and
   synchronization had succeeded. Browser refreshes could therefore show a
   state that the worker had never actually reached.
7. After bounded reconnect attempts, a terminal error discarded desired-run
   intent. A later process restart had no authoritative instruction to restore
   that instance.
8. Readiness mixed historical cache coverage with active feed health and used
   the legacy autonomous engine as the primary execution signal. Cached data
   could be labelled healthy while all Trading Instance workers were stopped.
9. Platform status excluded desired workers in bootstrap/recovery states, which
   turned execution into `not_available` instead of a truthful warming,
   recovering, waiting-for-signal, or failed state.
10. Learning regime findings were hard rejection gates. Rejected regimes could
    no longer collect the fresh trade evidence needed to recover, producing a
    self-sealing stream of repeated learning rejections.
11. Learning state was not consistently isolated and durable for every local
    SQLite instance; a local fallback could restart with memory-only lessons.
12. The dashboard combined historical profitability with current runtime health,
    so a historically profitable instance could simultaneously appear healthy
    even while its current worker was in `ERROR`.

## Architecture Before

```text
latest REST/cache window
  -> positional drop of final candle
  -> fixed 150-bar warm-up
  -> instance marked RUNNING early
  -> poll latest cache window
  -> count gaps but continue/terminate
  -> hard learning veto
  -> paper execution

Historical cache coverage + legacy engine state
  -> generic readiness/dashboard health
```

Failure recovery restarted the same shallow bootstrap. It did not guarantee a
cursor-relative history window, continuity, or durable desired-run intent.

## Architecture After

```text
Exchange REST (cursor-relative, paginated, warm-up + safety buffer)
  -> normalize timestamps
  -> validate OHLCV structure
  -> sort + deduplicate
  -> retain only timestamp-proven closed candles
  -> verify warm-up depth and continuity
  -> warm strategy only through durable cursor
  -> process unseen closed candles chronologically once
  -> READY

Exchange WebSocket (REST fallback when unavailable/stale)
  -> validate closed candles
  -> timeframe-aware freshness
  -> detect first missing candle before any decision
  -> DATA_STALE / RECOVERING
  -> cursor-relative REST repair
  -> SYNCING
  -> RUNNING
  -> strategy
  -> soft learning adjustment + hard safety/risk gates
  -> paper execution
  -> instance-scoped ledger/journal/learning
  -> durable candle checkpoint
  -> runtime API/dashboard
```

The legacy replay engine remains available for research compatibility, but a
Trading Instance in `paper_forward` mode accepts only a source labelled live.
There is no CSV, synthetic, local historical replay, or stale-cache fallback in
that path.

## Bugs Fixed

- Replaced positional forming-candle removal with timestamp-plus-timeframe
  closure validation.
- Normalized timestamps to UTC and rejected malformed/non-finite/invalid OHLCV.
- Corrected feed age to start at candle close.
- Added dynamic strategy warm-up discovery and a safety buffer (normally 2x;
  150 required requests approximately 300).
- Added cursor-relative paginated REST bootstrap across long downtime and venue
  page-size caps.
- Forced cursor recovery requests to bypass the rolling WebSocket cache.
- Prevented a shallow WebSocket cache from satisfying a deep warm-up request.
- Required the persisted candle to exist in repaired history before recovery.
- Added strict candle continuity verification before indicator initialization
  and before any unseen decision.
- Added lifecycle states `starting`, `bootstrapping`, `warming`, `syncing`,
  `ready`, `running`, `data_stale`, `recovering`, `error`, and `stopped`.
- Persisted lifecycle transitions with time, reason, instance, symbol, and
  timeframe in the instance engine log.
- Added bounded exponential recovery and retained desired-run intent after an
  error so process restart can retry it.
- Replaced stale runtime/feed objects safely on manual recovery without tearing
  down an already-running idempotent Start request.
- Preserved and checkpointed pending paper orders and per-instance candle cursors.
- Exposed real warm-up, feed, cursor, recovery, heartbeat, worker, signal, and
  rejection diagnostics.
- Made learning a bounded observable risk multiplier instead of a hard veto;
  kill switches, stale/corrupt data, invalid orders, loss limits, and exposure
  remain hard gates.
- Isolated and persisted each instance's learning book for Supabase and durable
  SQLite modes.
- Split readiness into historical coverage, active feed freshness, WebSocket or
  explicit REST fallback, worker heartbeat, and execution readiness.
- Made readiness require every desired WebSocket feed to be connected, unless
  every unavailable stream has demonstrated an active REST forward fallback.
- Separated dashboard runtime diagnostics from explicitly historical metrics and
  corrected controls for bootstrap/recovery states.
- Corrected the legacy engine control payload so it no longer overwrites precise
  bootstrap/recovery states or claims REST-only transport when WebSocket exists.

## Files Changed

### Forward runtime and persistence

- `.env.example` — documents the adaptive strategy values and durable
  per-instance learning directory.
- `automation-hub/data/forward_market_data.py` — strict closed-candle validation,
  normalization, and provider-only forward adapter.
- `automation-hub/data/ws_feed.py` — deep-cache requirement and REST-only cursor
  backfill behavior.
- `automation-hub/services/auto_engine.py` — lifecycle state machine, dynamic
  bootstrap, cursor recovery, continuity, stale recovery, checkpointing, and
  runtime diagnostics.
- `automation-hub/services/trading_instances.py` — lifecycle persistence,
  instance supervision, worker cleanup, platform semantics, and durable isolated
  learning.
- `automation-hub/services/learning.py` — bounded soft learning influence and
  updated evidence semantics.
- `automation-hub/services/signal_pipeline.py` — observable soft-learning sizing
  input while preserving hard risk gates.
- `automation-hub/services/production.py` — independent active-feed, transport,
  heartbeat, cache, and execution readiness checks.
- `automation-hub/routers/health.py` — passes authoritative Trading Instance
  runtime state to readiness.
- `automation-hub/routers/engine.py` — precise lifecycle and WebSocket/REST
  transport semantics.
- `automation-hub/routers/instances.py` — expanded instance configuration and
  runtime API shape.
- `automation-hub/webhook_api.py` — instance-aware Bot OS execution state.

### Dashboard

- `automation-hub-dashboard/src/lib/api.ts` — lifecycle and diagnostics types.
- `automation-hub-dashboard/src/pages/TradingInstances.tsx` — runtime/history
  separation, detailed diagnostics, and state-correct controls.
- `automation-hub-dashboard/src/components/instances/ActiveTradingInstances.tsx`
  — lifecycle-aware active instance presentation.
- `automation-hub-dashboard/src/components/layout/HeaderControls.tsx` — precise
  active/recovery state support.
- `automation-hub-dashboard/src/components/trading/EngineControlCard.tsx` — new
  lifecycle state handling and recovery semantics.

### Adaptive strategy work retained in this working tree

- `ADAPTIVE_TREND_PULLBACK_STRATEGY.md` — strategy design and operating contract.
- `automation-hub/strategies/adaptive_trend_pullback/` — multi-timeframe strategy
  implementation.
- `automation-hub/strategies/builtin_versions.py` — built-in strategy version.
- `automation-hub/services/strategy_presets.py` — preset registration.
- `automation-hub/bots/registry.py` — strategy registry exposure.

### Tests

- `automation-hub/tests/test_forward_paper_execution.py`
- `automation-hub/tests/test_live_engine.py`
- `automation-hub/tests/test_trading_instances.py`
- `automation-hub/tests/test_learning.py`
- `automation-hub/tests/test_production.py`
- `automation-hub/tests/test_counterfactual.py`
- `automation-hub/tests/test_monitor_runner.py`
- `automation-hub/tests/test_risk_gate_order.py`
- `automation-hub/tests/test_adaptive_trend_pullback.py`

These tests cover the new runtime contracts and intentionally update the former
hard-learning-gate characterization.

## Tests Added and Evidence Map

The automated suite covers the required scenarios as follows:

1. Fresh startup — real two-worker forward test and live worker recovery test.
2. 150-bar warm-up — live worker and bootstrap tests.
3. Insufficient local cache — cursor bootstrap ignores local cache dependency.
4. REST backfill — paginated cursor-relative bootstrap test.
5. WebSocket disconnect — stale/unavailable WebSocket fallback tests.
6. WebSocket reconnect — bounded reconnect lifecycle test.
7. Missing-candle backfill — continuity failure prevents decisions until repair.
8. Stale-feed detection — strict closed-candle freshness tests.
9. Automatic recovery — transient warm-up failure recovers to running.
10. Process restart — desired instances restore independently.
11. Checkpoint recovery — each instance restores its own cursor and orders.
12. Duplicate candle rejection — repeated windows do not process again.
13. Duplicate signal prevention — durable autonomous execution IDs and dedup tests.
14. Exactly-once closed-candle processing — cursor/checkpoint regression test.
15. Multiple independent instances — two actual worker threads and three isolated
    runtime objects.
16. Different symbols — BTC, ETH, and SOL instance isolation tests.
17. Different timeframes — simultaneous 5m, 15m, and 30m workers.
18. Learning soft veto — learned regime and confidence reduce risk without block.
19. Hard risk veto — unchanged gate-order and risk safety suites.
20. Closed paper trade persistence — account, ledger, and decision journal tests.
21. Dashboard runtime correctness — instance status/readiness API and TypeScript
    production build.

Regression coverage also proves a repeated provider window processes no candle
or signal twice and that forward provider failure cannot fall back to replay.

## Validation Results

- `git diff --check`: PASS.
- Python syntax/import compilation: PASS.
- Automation Hub: **1623 passed, 15 skipped**.
- Root and trading bot suites: **527 passed, 1 skipped**.
- Focused forward/lifecycle/learning/readiness suite: **72 passed**.
- Dashboard TypeScript and Vite production build: PASS.
- Landing TypeScript and Vite production build: PASS.
- Secret/certificate path and added-line scan: no credential or private-key
  material found.
- Docker build/Compose: NOT RUN locally because Docker is not installed on this
  Mac; production container validation remains a VPS step.

The skipped tests are existing optional/integration skips. Warnings were limited
to FastAPI `on_event` deprecation, the Mac system Python LibreSSL warning, and a
large dashboard bundle warning; none failed this change.

## Runtime Verification

### Genuine provider bootstrap

Read-only Kraken request on 2026-08-12:

```text
source=live (ccxt:kraken)
raw_bars=300
valid_closed_bars=299
latest_closed_open_timestamp=2026-08-12T12:40:00+00:00
latest_closed_age_seconds=156.3
continuous_tail_150=True
```

### Genuine forward worker

A temporary in-memory paper instance used `BTCUSDT`, Decision Brain, `1m`, one
worker, and public Kraken data. It never had broker credentials and could not
place a real order.

```text
runtime=('bootstrapping', 0, 0, 0, 0, None)
runtime=('running', 150, 0, 0, 0, '2026-08-12T12:47:00+00:00')
runtime=('running', 150, 1, 0, 0, '2026-08-12T12:48:00+00:00')
VERIFY_STATE=running
VERIFY_SOURCE=live (websocket)
VERIFY_WARMUP=150/150
VERIFY_LAST_CLOSED=2026-08-12T12:48:00+00:00
VERIFY_LAST_PROCESSED=2026-08-12T12:48:00+00:00
VERIFY_BARS_PROCESSED=1
VERIFY_SIGNALS=0
VERIFY_REJECTIONS=0
VERIFY_MARKET_STATUS=healthy
```

This proves real REST bootstrap, dynamic warm-up, a running worker, WebSocket
forward delivery, one new naturally closed candle, exactly-once cursor advance,
strategy evaluation (no signal), and healthy runtime state. Because no natural
signal occurred, it does not prove a repaired-runtime open and close.

## Remaining Risks and Required Production Proof

1. Deploy the changes on the VPS and rebuild the application image.
2. Confirm all desired instances reach `running` with fresh provider timestamps,
   a live WebSocket or demonstrated REST forward fallback, and advancing cursors.
3. Allow a strategy to generate a natural signal. Do not weaken thresholds or
   inject an artificial signal for validation.
4. Capture the accepted/rejected gate result. If accepted, confirm a paper order,
   instance-scoped position, natural exit, closed `paper_trades` row, journal
   review, and dashboard metric update.
5. Restart the application and prove the cursor, pending order/position,
   learning book, and desired workers restore without reprocessing a candle.
6. Repeat with two, then three simultaneous instances.
7. Monitor provider-specific REST pagination and WebSocket behavior on the VPS;
   this Mac verification used Kraken.
8. The dashboard main UI bundle remains over Vite's 500 kB warning threshold.
   It builds successfully but is a future performance/code-splitting task, not a
   forward-execution correctness blocker.

No database schema migration is introduced by this runtime repair. Existing
`trading_instances_schema.sql` and its `instance_market_state` table remain
required on Supabase.
