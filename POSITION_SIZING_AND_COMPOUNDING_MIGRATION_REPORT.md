# Position Sizing and Compounding Migration Report

## Scope

Stage 3 changes only position sizing, instance capital accounting, risk
consistency, persistence, and their dashboard presentation. Strategy signal
logic, strategy parameters, entry/exit conditions, and market-data rules were
not changed.

## Position sizing policy

All new entries use `tradexa.risk.position_sizing.PositionSizingService`.

| Persisted mode | Risk basis | Quantity |
| --- | --- | --- |
| `fixed_quantity` | Current realized equity is recorded for audit only | Configured base-asset quantity |
| `fixed_starting_equity_percent` | Original instance starting equity | `(basis × effective risk %) / stop distance` |
| `dynamic_current_equity_percent` | Current realized equity | `(basis × effective risk %) / stop distance` |

Dynamic sizing excludes unrealized P&L. With profit reinvestment enabled,
closed net profit increases the next risk budget. With it disabled, profitable
equity is capped at starting equity while realized losses still reduce risk.
Fees are included because the realized-equity basis is starting capital plus
net closed-trade P&L.

The optional maximum-risk amount caps percentage-mode risk and rejects a fixed
quantity that exceeds the cap. The optional minimum-equity floor rejects new
entries and reports a risk halt. Invalid or too-small stops, invalid instrument
metadata, quantity boundaries, notional limits, and available-margin limits
fail closed.

## Backward compatibility

Legacy Trading Instance rows are migrated deliberately:

- `auto` → `fixed_starting_equity_percent`
- `fixed` → `fixed_quantity`
- `fixed_position_size` is copied to `fixed_quantity`
- starting/current realized equity are initialized from capital allocation

No existing instance is silently opted into dynamic compounding. Historical
trade rows remain unchanged and may have null Stage 3 attribution fields. New
trades are tagged with sizing engine `v2`.

## Database migration

Run `automation-hub/data/trading_instances_schema.sql` in the Supabase SQL
Editor before deploying the new application image. It is additive and safe to
rerun. It adds the instance sizing policy/equity fields and immutable entry and
close attribution fields to `paper_trades`.

After SQL execution, reload the PostgREST schema cache if Supabase has not done
so automatically:

```sql
NOTIFY pgrst, 'reload schema';
```

## Accounting definitions

- Starting equity: immutable allocation basis established at instance creation.
- Current realized equity: starting equity plus net closed-trade P&L.
- Gross realized P&L: net realized P&L plus recorded fees.
- Unrealized P&L: current open-position mark-to-market result.
- Mark-to-market equity: current realized equity plus unrealized P&L.
- Available capital: current realized equity less open notional under the
  current unlevered paper model.

The legacy `paper_trades.pnl` field remains net of fees. New `fees` and
`realized_pnl` columns make this policy explicit without rewriting history.

## Verification

Automated coverage includes all three modes, legacy mapping, deterministic
`+2R, +2R, -1R` compounding, profit-reinvestment policy, post-loss risk
reduction, fees, exclusion of unrealized profit, stop validation, risk caps,
equity floor, instrument quantity rules, paper-trade attribution, and shared
backtest sizing.

Production runtime verification still requires applying the Supabase migration,
rebuilding the app image, and creating test instances on the VPS. Exact commands
are included in the deployment handoff after validation.

Local validation completed on 2026-08-09:

- Automation Hub suite: 1,578 passed, 15 skipped (before the final additional
  dynamic-pipeline regression case); the final Stage 3 focused file then passed
  8/8.
- Core engine suite: 502 passed, 1 skipped.
- Dashboard loopback tests: 3 passed.
- Dashboard TypeScript typecheck: passed.
- Dashboard production build: passed.
- Python compile check and `git diff --check`: passed.
- Docker/Supabase runtime: pending VPS deployment; Docker is unavailable on the
  local macOS test host.
