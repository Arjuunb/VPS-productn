# Isolated Forward-Paper Operations

Nexus has three independent forward-paper execution scopes:

| Surface | Mode | Account scope |
| --- | --- | --- |
| Trading Instance | `FORWARD_PAPER` | One instance and simulation session |
| Price Action Lab | `SIGNALS_ONLY` or `ISOLATED_FORWARD_PAPER` | Price Action only |
| SMC Strategy Lab | `SIGNALS_ONLY` or `ISOLATED_FORWARD_PAPER` | SMC only |

All three consume Binance USD-M public market data from the same in-process
market-data hub. A completed kline is fanned out with one candle identity and
the strategies evaluate only that closed candle. Balances, positions, orders,
fills, P&L, pause state, risk limits, capacity and journals are not shared.

## Fill timing

An accepted entry creates an intent at the signal candle close. It cannot fill
at that close. A market intent fills from the first complete public bid/ask/mark
snapshot whose receipt timestamp is strictly later than the decision timestamp.
A limit intent additionally waits for that later quote to cross its price. Paper
spread, slippage and commission are then applied. Stops and targets are
exposure-reducing and cannot reverse a position.

Incomplete, stale, disconnected or unreconciled market data blocks new entries.
It does not authorize a fallback to synthetic candles or private exchange APIs.

## Arming a lab

Both labs start in `SIGNALS_ONLY`. On the Price Action or SMC Strategy Lab page:

1. Confirm the banner names Binance USD-M and shows synchronized closed-candle,
   bid/ask and mark evidence.
2. Select **Automatic paper**.
3. Set the isolated risk percentage.
4. Select **Apply paper configuration**.

The saved mode changes to `ISOLATED_FORWARD_PAPER`. Existing intents retain
their immutable configuration. The default lab balance is 10,000 USDT unless
the server configuration explicitly changes it.

Trading Instances use `FORWARD_PAPER` and their own allocated balance. An
instance at its capacity limit does not block either lab.

## Live-routing boundary

Live exchange submission remains disabled. These paths use Binance public
market data only and contain no `ccxt.create_order` call. Do not describe this
as live-venue execution certification: every order and fill is simulated and no
exchange API credential is required.
