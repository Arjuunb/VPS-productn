# Trading Simulation Engine — architecture & the fees upgrade

This documents the *existing* paper-trading simulator (which is already a
candle-driven exchange simulation, not an instant-close system), its
per-Trading-Instance execution model, and the trading-fee behaviour.

## Trading Instance fill models

Every Trading Instance persists its own fill model and restores the same model
with its worker:

- **RealisticFill (default for new instances)** — configurable spread,
  slippage, latency, commission, optional partial fills, and optional rejects.
- **UnifiedFees** — uses the shared backtest fee/slippage assumptions so a
  forward-paper result can be compared directly with research output.
- **PerfectFill** — exact full fills with no costs. It remains available only
  for controlled ideal-fill comparisons and backward compatibility; existing
  rows are not silently converted.

Changing a running instance's model safely rebuilds its worker before its first
trade. After the first trade, the model is immutable and a new instance is
required; this prevents one performance record from mixing incompatible cost
assumptions. Changes are also rejected while the instance owns an open
position. All models remain paper-only; none can submit an exchange order.
Autonomous reject/partial outcomes are derived from the instance-scoped order
identifier and configured seed, so a worker restart cannot rewrite whether the
same order filled.

## Restart-safe trade management

Each open instance position durably stores its exact strategy target and its
complete mutable management checkpoint: original risk, break-even and
scale-out flags, best price, candle age, MFE and MAE. The checkpoint is updated
after every processed candle and after manual level changes. A restored worker
therefore resumes the same stop, target and lifecycle rather than inventing a
new 3R target or resetting its age. Legacy positions created before these
columns existed retain a compatibility-only 3R fallback; all newly opened
positions use exact persisted values.

Production Supabase deployments must rerun the additive
`data/trading_instances_schema.sql` migration before deploying this version.

## Deterministic candle execution

Forward-paper execution consumes closed OHLC candles, which reveal price range
but not the intrabar path. The engine therefore uses explicit conservative
rules:

- an existing position whose stop and target are both touched exits at the
  stop;
- a stop gapped through exits at the adverse candle open, then the configured
  fill costs apply;
- a limit order filled during a candle may be stopped on that candle, because
  price necessarily crossed entry before reaching the adverse stop;
- that newly filled order cannot claim a target, scale-out, break-even move or
  trailing move until the next candle, because the favorable extreme might
  have occurred before entry;
- market signals are evaluated at the closed candle and become eligible for
  exits from the next candle.

These rules prevent optimistic wins caused solely by unknowable OHLC ordering
and are pinned by regression tests.

## Venue and instrument parity

New Trading Instances persist their selected CCXT venue and `spot` instrument
type. Live-forward candles are fetched from that venue, and every candidate
quantity is floored to its current amount step then checked against minimum lot
and minimum notional filters before the paper fill is allowed. If venue
metadata cannot be loaded, the entry fails closed under `venue_rules`; it does
not assume unconstrained execution. Venue and instrument type become immutable
after the first trade so one performance record cannot mix market conventions.

## Exactly-once forward execution

Every autonomous forward order receives a deterministic identifier derived
from its Trading Instance, pair, timeframe, closed-candle timestamp and action.
Those identifiers are checked permanently and within the instance's ledger
scope. A failed cursor checkpoint, container restart or delayed retry therefore
cannot execute the same candle action twice. Research replay keeps run-scoped
identifiers so an intentional repeated experiment remains possible.

## WebSocket-first data with REST recovery

A trading worker starts a venue-specific CCXT Pro OHLCV stream when that
capability is installed. It reads the stream cache only while the connection is
available and the newest candle is fresh; otherwise it immediately uses the
same venue's strict live REST source. Disconnects retry with bounded exponential
backoff and status reports the reconnect count plus WebSocket/REST read counts.
A REST warm-up does not falsely label itself as WebSocket data.

## What already exists (reused, not rebuilt)

The paper system is a candle-by-candle simulator built from these parts:

- **`services/auto_engine.py`** — the per-bar loop. On every candle it
  (`_process_bar`): fills resting **limit** orders when price trades through
  them (`_check_pending`, with gap-fills and TTL expiry), then checks the bar's
  **high/low range** to trigger **stop-loss / take-profit** (`_check_exit`) —
  it does *not* close on a signal instantly. Runs in replay or live-forward mode
  from strict live-provider candles in a Trading Instance. Historical sources
  are reserved for research/replay; provider failure fails closed.
- **`execution/paper_engine.py`** — the fill / P&L / ledger layer
  (`open` / `reduce` / `close`), driven by a pluggable **fill model**.
- **`services/fill_model.py`** — `RealisticFill` applies **spread + slippage +
  latency** (fill moves against you), **partial fills**, **rejection**, and now
  **commission**; maker (resting-limit) fills skip the spread.
- **`services/trade_manager.py`** — **trailing stop**, **break-even**, and
  **scale-out (partial take-profit)**, evaluated bar-by-bar.
- **`data/account_store.py`** — persists initial capital + equity snapshot so
  the account survives logout / restart; the ledger is the source of truth.
- Analytics (`/paper/equity-curve`, Strategy Proof) already cover equity curve,
  drawdown, win rate, profit factor, avg R, Sharpe/Sortino, per-symbol/session.

Because all of the above already works (and is covered by the backend suite),
the upgrade did **not** re-implement any of it.

## The gap that was closed: trading fees

Previously the engine booked spread/slippage as price impact but **no explicit
commission** (`trade_memory.py` literally read *"fees: 0.00 — fees not
modeled"*). Now:

- The fill model exposes `fee_pct(maker=…)` — a commission as a fraction of
  notional. `PerfectFill` returns **0** (so all existing behaviour and tests are
  unchanged); `RealisticFill` charges Binance-like defaults (taker `0.04%`,
  maker `0.02%`), configurable via `HUB_FILL_TAKER_FEE_PCT` /
  `HUB_FILL_MAKER_FEE_PCT`.
- `PaperExecutionEngine` deducts a **round-trip commission** (entry + exit
  notional, taker rate — a conservative paper assumption) from realized P&L on
  every `close` and proportionally on every partial `reduce`. Realized P&L —
  and therefore account equity — is now **net of fees**.
- `FillResult.fee` carries the commission for logging; `paper.fees_paid()` sums
  total commission across closed trades and is surfaced on `/paper/account`
  (`fees_paid`) and the Paper Trading account card.

Round-trip fee for a trade = `taker_rate × size × (|entry| + |exit|)`.

### Deliberately deferred: leverage / margin / liquidation

The engine is **spot / risk-based** (unleveraged — position size comes from
risk-per-trade, not margin). Adding leverage, margin and liquidation prices
would be a genuinely *new* synthetic capability, not a fix, so it was left out
pending a decision that the product actually simulates leveraged futures.

## Tests

`tests/test_paper_fees.py` (6): maker/taker fee rates, default engine charges
nothing (regression guard), round-trip fee deducted on long and short closes,
proportional fee on a partial close, and `fees_paid()` accounting.
