# Paper Trading Runtime Verification

Date: 2026-08-12
Scope: Trading Instance forward-paper runtime
Safety: public market data and simulated paper execution only; no exchange credentials and no broker order

## Environment

| Item | Value |
| --- | --- |
| Host | `SANAs-MacBook-Air.local` (Darwin arm64) |
| Branch | `main` |
| Base commit tested | `6d2826d` plus the runtime fixes listed below |
| Python | `3.9.6` |
| Market-data runtime | `ccxt 4.5.64`, Kraken public REST and WebSocket |
| Startup path | `TradingInstanceManager` → `AutoStrategyEngine` → `SignalPipeline` → `PaperExecutionEngine` |
| Production startup command | `docker compose up -d` |

Docker is not installed on this Mac. Container health must therefore be
rechecked on the VPS after this commit is deployed; the paper engine itself was
run locally against real public Kraken data through the production classes.

## Instance Tested

| Item | Value |
| --- | --- |
| ID | `2384adad524b4c4899b95cf992653408` |
| Symbol | `BTCUSDT` |
| Strategy | Supertrend |
| Version | `builtin-1` |
| Timeframe | `5m` |
| Venue | Kraken spot |
| Mode | `paper_forward` / paper execution |
| Workers | One for the controlled test |

The durable object was created in `CREATED` state. Its persisted lifecycle log
then recorded:

```text
CREATED
→ STARTING       2026-08-12T20:25:59.140118Z
→ BOOTSTRAPPING  2026-08-12T20:25:59.140992Z
→ WARMING        2026-08-12T20:26:01.392573Z
→ SYNCING        2026-08-12T20:26:01.395854Z
→ READY          2026-08-12T20:26:01.398564Z
→ RUNNING        2026-08-12T20:26:01.399652Z
```

## Market Feed Evidence

Initial running snapshot at `2026-08-12T20:26:02.153866Z`:

```text
provider=kraken
source=live (ccxt:kraken)
websocket.running=true
websocket.available=true
symbol=BTCUSDT
timeframe=5m
latest_received=2026-08-12T20:25:00Z  (forming candle)
latest_closed=2026-08-12T20:20:00Z
feed_age=62s
next_expected_close=2026-08-12T20:30:00Z
```

At `20:30:00Z`, the genuine Kraken WebSocket window advanced:

```text
latest_closed=2026-08-12T20:25:00Z
last_processed=2026-08-12T20:25:00Z
feed_age=0s
source=live (websocket)
processed_candles=1
missing=0
out_of_order=0
```

No local cache, CSV, synthetic candle, historical replay, or forced signal was
used as forward evidence.

## Warm-up Evidence

```text
Required:               150
Requested:              300
Fetched:                300
Valid closed candles:   299
Oldest:                 2026-08-11T19:30:00Z
Newest:                 2026-08-12T20:20:00Z
Duplicates removed:     0
Malformed rejected:     0
Incomplete excluded:    1
Strategy bars loaded:   150
Warm-up:                PASS
```

The strategy warmed only on timestamp-proven closed provider bars. The forming
`20:25` bar was observed separately and excluded from warm-up.

## Exactly-Once Evidence

The new candle created exactly one cycle report:

```text
identity=2384adad524b4c4899b95cf992653408:builtin-1:BTCUSDT:5m:2026-08-12T20:25:00+00:00
decision=WAIT
score=54
price=63502.7
```

Before deliberate worker restart:

```text
cursor=2026-08-12T20:25:00Z
cycles=1 decisions=0 skips=0 trades=0
duplicate_candles=0
```

A fresh manager and fresh database connections restored the desired worker by
ID. After bootstrap against the repeated provider window:

```text
restored_ids=[2384adad524b4c4899b95cf992653408]
cursor=2026-08-12T20:25:00Z
cycles=1 decisions=0 skips=0 trades=0
duplicate evidence rows created=false
duplicate_candles=0
```

Decisions, cycle reports, and skipped decisions now share the stable identity
`instance + strategy version + symbol + timeframe + candle timestamp` and use
database uniqueness for restart idempotency. Repeated rolling feed windows are
filtered before `_ingest`, so overlap is no longer misreported as thousands of
duplicate decisions.

## Strategy Decision Evidence

The real `20:25` candle followed:

```text
closed candle received
→ Supertrend evaluated
→ no trend flip
→ WAIT cycle persisted (score 54)
→ no learning/risk/correlation/execution gate invoked
→ durable cursor committed
```

This is `READY / WAITING`, not broken execution. No threshold was changed and no
signal was fabricated to make this run trade.

## Risk and Learning Evidence

No natural signal occurred during the controlled five-minute observation, so
risk and correlation correctly had nothing to approve or reject. Learning was
loaded for the isolated instance and exposed as:

```json
{"active_adjustments": {}, "evolution": [], "lessons": [], "updated_at": null}
```

This means no generic historical regime label hard-blocked the candle. Store
tests also prove the same candle cannot increment brain, learning, risk, or
correlation skip evidence more than once. The production learning book remains
soft and re-evaluated as new closed-trade evidence arrives.

## Execution and Trade Lifecycle Evidence

This controlled candle generated no natural Supertrend signal; therefore no
order, reservation, position, or trade was expected. The execution engine was
idle/ready rather than broken.

The unchanged production execution path already has genuine natural runtime
evidence in `FORWARD_PAPER_RUNTIME_REPAIR_REPORT.md`: an isolated ETHUSDT
Supertrend forward worker produced 23 natural signals, 2 accepted decisions, 2
instance-scoped paper positions, 2 natural closes, 2 closed journal entries,
and 2 permanent memories. Its restart retained the two original trade IDs
exactly once. That evidence covers entry, simulated fill, position persistence,
natural exit, realized P&L, equity update, journal, memory, analytics, and trade
identity without injecting a signal.

## Recovery Test Evidence

A controlled one-shot provider timeout was triggered while the worker was
running. No candle or signal was altered. The persisted transitions were:

```text
RUNNING
→ DATA_STALE   2026-08-12T20:30:13.399876Z
→ RECOVERING  2026-08-12T20:30:13.402491Z (attempt 1/5)
→ BOOTSTRAPPING
→ WARMING
→ SYNCING
→ READY
→ RUNNING     2026-08-12T20:30:17.677817Z
```

The recovery used real Kraken REST history, preserved the `20:25` cursor, found
no gap, created no duplicate evidence, and resumed forward polling. A separate
real Kraken WebSocket test proved start → stop → restart → stop leaves zero
`ws-feed` threads; this fixed the resource leak found during the first run.

## Dashboard Evidence

The dashboard production build passed. Its instance page consumes the same
authoritative status payload used above and renders runtime state separately
from historical metrics. `ERROR` cannot be presented as runtime `Healthy` merely
because historical strategy statistics are profitable. New instances now show
`CREATED`, and Start/Restart/Delete controls remain visible where valid.

The bottom status bar consumes `/instances` and renders real values for mode,
running workers, market-data status, positions, global risk, selected worker
uptime, and active symbol/strategy/timeframe. A healthy running instance does
not depend on the legacy engine or historical cache to show live market data.

A browser check of `https://www.trade-logx.com/app/` returned the production
authentication flow at `https://www.trade-logx.com/auth/login`, rendered the
sign-in form successfully, and emitted no browser console errors. Protected
instance controls were not mutated during this read-only check; their current
code was verified by the successful production build and backend tests.

## Multi-Instance Evidence

Scaling happened only after the controlled fresh-candle, restart, and recovery
checks passed.

```text
BTCUSDT · Supertrend       · 5m · RUNNING · cursor 20:30Z
ETHUSDT · Donchian         · 5m · RUNNING · cursor 20:30Z
SOLUSDT · Supertrend       · 5m · RUNNING · cursor 20:30Z
worker threads=3
WebSocket threads=3
```

Each worker had a unique instance ID, strategy object, learning file, scoped
ledger, cursor row, paper account, and feed. After stopping all three:

```text
worker threads=0
WebSocket threads=0
```

Automated integration tests cover independent symbols/timeframes/cursors,
simultaneous start, restore, no duplicate candle processing, and deleting one
instance without changing another.

## Performance Sanity

The controlled single worker made one strategy evaluation for one newly closed
five-minute candle. It did not write on every WebSocket update or two-second
status poll. The initial resource sample was later superseded by the post-fix
three-worker cleanup proof above; no orphan engine or WebSocket task remained.

Automated validation:

- focused runtime/lifecycle/store suite: **92 passed**;
- full Automation Hub suite: **1647 passed, 15 skipped**;
- root and trading-bot suites: **527 passed, 1 skipped**;
- Python compile/import check: PASS;
- dashboard TypeScript/Vite production build: PASS;
- landing TypeScript/Vite production build: PASS;
- `git diff --check`: PASS.

The dashboard still emits its existing large-chunk performance warning. It does
not fail the build or affect runtime correctness.

## Files Changed During Runtime Verification

- `automation-hub/services/auto_engine.py` — warm-up evidence, received-vs-closed
  timestamps, exact decision identity, lifecycle recovery visibility, learning
  telemetry, and overlap filtering.
- `automation-hub/services/trading_instances.py` — attaches per-instance
  cycle/skip evidence, strategy version, and truthful `CREATED` state.
- `automation-hub/services/signal_pipeline.py` — propagates stable identity to
  rejected-decision evidence.
- `automation-hub/data/decision_store.py`
- `automation-hub/data/cycle_store.py`
- `automation-hub/data/skipped_store.py` — additive identity columns and unique
  indexes for restart idempotency.
- `automation-hub/data/ws_feed.py` — cancellation-safe WebSocket stop/restart.
- `automation-hub/webhook_api.py` — injects cycle and skip stores into instance
  workers.
- `automation-hub-dashboard/src/pages/TradingInstances.tsx` — created-state UI.
- focused test files for the contracts above.

No Supabase SQL migration is required for these telemetry stores; they are
additive durable SQLite stores under the existing application data volume. No
secret, credential, certificate, or private key was added.

## Remaining Problems

1. Docker/Compose and deployed Supabase restoration were not runnable on this
   Mac because Docker is unavailable and no VPS shell session was available.
   Deploy and run the commands below before treating the current VPS image as
   updated.
2. The real controlled five-minute candle was a natural `WAIT`; the earlier
   genuine forward runtime report remains the natural signal-to-closed-trade
   proof for the same production execution path.
3. The dashboard's existing `ui` bundle exceeds Vite's 500 kB warning threshold.

These are deployment confirmation and frontend optimization items. The local
forward-paper engine path, exact-once processing, recovery, and three-worker
isolation are runtime-verified.

## Final Verdict

**PASS — forward paper trading runtime verified**
