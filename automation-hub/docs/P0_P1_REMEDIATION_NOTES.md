# P0/P1 Paper-Execution Remediation Notes

## Scope and safety boundary

This change makes forward paper execution fail closed and operator-visible. It
does not certify a live venue. External CCXT submission remains disabled unless
`HUB_ENABLE_EXTERNAL_LIVE=1` is deliberately set after a separate venue review.
The Price Action and SMC labs retain isolated books and are not merged into a
Trading Instance account.

## Decision and blocker contract

`decision` records the strategy's quality verdict. `final_state` records what
the worker actually did after every downstream gate. A qualified signal that is
not filled must therefore end in one of `PENDING_INTENT`, `SIGNALS_ONLY`,
`APPROVAL_REQUIRED`, or `GATE_REJECTED`; it must never remain merely
`Accepted`.

Stable blocker values begin with `GATE_REJECTED:`. The current operator-facing
codes include:

- `NO_SETUP`, `WARMUP`, `STALE_CANDLE`, `INSUFFICIENT_RR`
- `PAUSED`, `OUTSIDE_SESSION`, `TRADING_DAY_DISABLED`
- `DUPLICATE_SIGNAL`, `CORRELATED_EXPOSURE`, `PORTFOLIO_EXPOSURE`
- `INVALID_RISK`, `RISK_LIMIT`, `DAILY_LOSS_LIMIT`, `WEEKLY_LOSS_LIMIT`
- `LOSS_COOLDOWN`, `TRADE_LIMIT`, `MARKET_QUALITY`, `EVENT_BLACKOUT`
- `ORDER_PENDING`, `LIMIT_EXPIRED`, `POSITION_ALREADY_ALIGNED`
- `SIGNALS_ONLY`, `APPROVAL_REQUIRED`, `PIPELINE_ERROR`, `EXECUTION`

Dashboard signal and rejection totals come from the instance worker counters,
not a second UI calculation. The only runtime labels are
`RUNNING_UNARMED`, `RUNNING_ARMED`, `BLOCKED`, and `ERROR`.

## Arming one paper instance on one venue

1. Create one Trading Instance with the exact strategy id, symbol, timeframe,
   and paper-data venue required by the operator.
2. Start that instance and verify the configured and worker strategy ids match.
3. Wait for live closed-candle warm-up and confirm the status is
   `RUNNING_ARMED`. `BLOCKED` or `ERROR` must be resolved, not overridden.
4. Inspect `last_closed_candle`, `last_evaluation`, `blocker`, and
   `pending_intent` before expecting a fill.
5. Keep the Price Action lab in `signals_only` unless Automatic paper is
   explicitly applied inside that lab. Lab balances never represent instance
   equity, even when symbols happen to match.

Pausing an instance closes its entry gate, waits for worker acknowledgement,
cancels pending entries, and checkpoints state. Protective reduce/close actions
remain eligible. A missing acknowledgement is an API failure, not a successful
pause.

## Persistence deployment requirement

Apply the updated Supabase ledger schema before rollout, including the
`paper_executions` table and atomic OPEN/REDUCE/CLOSE RPCs. If Supabase is
configured but unavailable, the application enters read-only degraded mode and
will not open a writable SQLite fallback. Any break-glass recovery must be an
explicit operator action outside the normal startup path.

