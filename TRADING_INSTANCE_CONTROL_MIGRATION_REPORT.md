# Trading Instance Control Migration

## Previous control conflict

Paper Trading had two competing displays: the legacy autonomous engine exposed
global symbols, strategy, timeframe, sizing, and entry settings while active
paper workers already used isolated Trading Instances. This could make a stopped
legacy engine appear to control a running instance.

## Authoritative control model

Active paper behaviour now comes exclusively from a `TradingInstance`. An
instance persists its pair, strategy key and version, timeframe, capital,
per-trade risk, sizing mode, fixed quantity, entry mode, fill model, execution
mode, forward-market mode, lifecycle state, and start/stop timestamps.

The legacy autonomous engine is retained only for backward-compatible
diagnostics. `HUB_AUTO_ENGINE` now defaults to `0`; it is never used to rebuild
or override an instance worker.

## Database migration

Run `automation-hub/data/trading_instances_schema.sql` in the Supabase SQL
editor before deploying this release. It is additive and safe to re-run. It adds
instance execution columns and `paper_account_capital` to the platform settings.
Existing records retain the old paper defaults; no legacy settings are guessed
or copied into them.

## API changes

- `POST /instances` accepts instance-owned `sizing_mode`, `fixed_position_size`,
  `entry_mode`, and `fill_model`.
- `PATCH /instances/{id}` explicitly edits inactive-instance capital, risk,
  sizing, fixed quantity, or entry mode. Pair, strategy/version, and timeframe
  are immutable, preserving historical attribution.
- `POST /instances/platform` accepts paper-account capacity in addition to
  active-slot and global-risk settings.
- Exact active duplicates (pair + strategy + version + timeframe) are rejected.
- New allocations cannot exceed remaining paper-account capacity.

## Dashboard and settings

The header, dashboard hero, metric cards, footer, recovery panel, and active
instance cards all derive state from `GET /instances`. The global Settings page
now provides account-level capacity, active slots, global risk, and daily-loss
limits. Retained legacy controls are collapsed and labelled **Legacy Autonomous
Engine — not used by Trading Instances**.

## Restart behaviour and risk boundary

Each desired-running worker is restored from its persisted configuration after a
backend restart. The global risk manager remains above all instance-scoped
position and trade ledgers. Instance A can be stopped without stopping Instance
B; duplicate strategies on the same pair remain isolated when their strategy or
version differs.

## Validation

Focused instance and forward-paper tests cover persistence, isolation, global
risk, duplicate prevention, account-capacity rejection, forward cursor
persistence, and stale-data fail-closed behaviour. The dashboard production
build is run before release.

## Production verification

1. Run the Supabase migration file in the SQL Editor.
2. Deploy with `docker compose up -d --build --wait app`.
3. Open **Trading Instances** and confirm every running card’s pair, strategy,
   version, timeframe, capital, risk, sizing, entry mode, and market-data health.
4. Open **Global Account Settings** and set account capacity and the recommended
   maximum of two active instances.
5. Confirm `/instances` reports the same active count, allocation, and worker
   statuses as the dashboard.
