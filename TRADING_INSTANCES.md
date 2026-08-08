# Trading Instances

A Trading Instance is the isolated paper-trading unit in TradeLogX. It has one
pair, strategy key, strategy version, timeframe, risk allocation and lifecycle.
Records created through an instance carry its immutable `instance_id`; historic
legacy paper rows remain unassigned and are never blended into instance metrics.

## Modes

- **Trading** runs an isolated paper execution worker. It consumes one active
  trading slot and is protected by the global risk limit.
- **Research** runs the same analysis, data and risk gates but cannot create a
  position or paper order. It is intended to collect decision evidence without
  execution.

The default is one active trading slot. An administrator can select one, two or
three slots on the Trading Instances page. The selection and global risk limit
are persisted with the instance platform configuration.

## Lifecycle

Start, pause, resume, restart and stop are server-owned actions. A running
instance is restored after an app/container restart from `desired_running`; a
browser refresh, sidebar navigation or websocket state does not change it. A
terminal worker error is stored as an error state rather than shown as running.

When one or more Trading Instances are deliberately active, the older shared
multi-pair autonomous worker is not started on application boot. This avoids
mixing newly attributed trades with legacy account-wide records. The legacy
engine stays available for backwards compatibility whenever no instance has
been marked to resume.

## Auto selection

`Auto-select measured winner` does not scan-and-trade every pair. It ranks only
existing Trading Mode instances with at least five of *their own* closed paper
trades. The score considers profit factor, expectancy, Sharpe, drawdown and
sample size; win rate alone is never used. It starts exactly one winning
instance, subject to active-slot and global-risk rules.

## Required Supabase migration

Before using Trading Instances with Supabase, open **Supabase Dashboard → SQL
Editor**, paste the complete contents of
`automation-hub/data/trading_instances_schema.sql`, and run it once. It is
additive and safe to run again. It creates instance tables and adds nullable
attribution columns (defaulting existing rows to an empty legacy value) to
existing paper tables without deleting or changing old trades.

Then on the VPS:

```bash
cd /opt/VPS-productn
git pull --ff-only origin main
docker compose build app
docker compose up -d --force-recreate --wait --wait-timeout 180 app
curl -fsS https://trade-logx.com/health
```

The health response should report both Supabase settings and ledger as
`connected: true`. Sign in as an administrator and open **Trading Instances**.
No credential, service-role key, certificate, or database URL belongs in this
repository or this document.
