# Paper Trading Engine hardening report

## Outcome

The Trading Instance paper engine is now a live-market-data simulator with
instance-owned execution assumptions. It still uses simulated funds and never
submits exchange orders. These changes improve realism and correctness; they do
not guarantee profitability and do not authorize live-money trading.

## Improvements completed

1. **Per-instance fill model** — new instances default to `RealisticFill`.
   Spread, slippage, latency, fees, optional rejects and partial fills are
   configurable. Probabilistic outcomes are restart-deterministic per order.
   `UnifiedFees` and research-only `PerfectFill` remain available.
2. **Restart-exact positions** — target, stop, original risk, break-even,
   scale-out, best price, age, MFE and MAE persist with each open position.
3. **Conservative OHLC ordering** — stop wins an ambiguous stop/target candle;
   gap-through stops use the adverse open; a new intrabar limit fill cannot
   claim an unprovable same-candle winner.
4. **Venue parity** — each instance owns an exchange and spot instrument type.
   Order quantities are floored to venue steps and fail closed below the current
   minimum quantity or notional.
5. **Exactly-once execution** — live-forward candle actions use permanent,
   instance-scoped deterministic identifiers. Research replay remains repeatable.
6. **WebSocket-first data** — the worker uses a fresh CCXT Pro stream when
   available, falls back to strict live REST, and reconnects exponentially.
7. **Truthful UI language** — the dashboard consistently says that funds and
   fills are simulated while market data is live.

## Supabase migration required before deployment

Run the complete, additive file `automation-hub/data/trading_instances_schema.sql`
in the Supabase SQL Editor. The newly required fields are:

- `positions.target`
- `positions.management_json`
- `paper_trades.target`
- `trading_instances.exchange`
- `trading_instances.instrument_type`

The migration uses `IF NOT EXISTS`, preserves existing rows, and reloads the
PostgREST schema cache. Do not deploy the application before this succeeds.

## Deployment order after an approved commit and push

On the VPS, preserve its existing local Compose capability override, pull the
production branch, validate the resolved Compose model, rebuild the application
image, and recreate the application and proxy. The exact commands are included
in the final handoff after the change is committed.

## Operational acceptance criteria

- `/health` reports HTTP 200 and both Supabase persistence probes connected.
- All desired Trading Instance workers restore after container recreation.
- Each running instance reports `paper_forward`, a healthy/recovering live data
  state, its effective venue, a durable processed-candle cursor, and a selected
  fill model.
- Recreating the app does not alter an open position's stop, target, size or
  management checkpoint and does not duplicate the last processed candle.
- `docker compose ps` reports app, Nginx and Certbot running/healthy as designed,
  and startup logs contain no exceptions.

## Local validation

- Core repository regression suite: **PASS — 505 passed, 1 skipped**.
- Focused execution/instance/forward-data suite: **PASS — 79 passed, 6
  dependency-gated skips**.
- WebSocket/operations suite: **PASS — 10 passed, 1 dependency-gated skip**.
- Dashboard production build (TypeScript + Vite): **PASS**. Vite reports the
  pre-existing large `ui` chunk advisory; this is not a build error.
- Landing production build (TypeScript + Vite): **PASS**.
- Changed Python modules compile: **PASS**.
- `git diff --check`: **PASS**.

Docker is not installed in the local macOS workspace, and its system Python is
3.9 while this project requires Python 3.10 or newer. Container build, Supabase
migration verification, external live-provider behaviour and restart recovery
must therefore receive their final runtime acceptance on the Ubuntu VPS after
the migration and approved deployment. This limitation is not reported as a
runtime pass.
