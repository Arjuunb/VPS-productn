# SMC PRO Pine Parity Export Guide

Status: **PARITY_AUDIT**. This procedure collects state records only. It does
not authorize an order, change Tradexa, activate paper trading, or test
profitability.

## Immutable source and temporary instrument

| Role | File | SHA-256 |
| --- | --- | --- |
| Immutable reference | `automation-hub/research_references/smc_pro_v2_reference.pine` | `95ec2874dd52abba0d26088d1fbce6208f73ed747a885b0dc0ca89fc0fb33e8c` |
| Temporary exporter | `automation-hub/research_references/instrumentation/smc_pro_v2_parity_export_only.pine` | `0390fab58121d75a0b80443243499c5723d2ef65ee05162915f37bb366dc1943` |

The exporter is not a new strategy version. It changes no condition, input,
HTF, pivot, FVG, order block, sweep, CHoCH, session, rejection, SL, TP, or
score expression. It only disables the two original Tradexa webhook calls and
emits a dedicated `SMC_PRO_V2_PARITY_EXPORT_ONLY` record through `alert()` on
each confirmed realtime close.

## Fixed collection configuration

Use exactly this first run:

| Item | Fixed value |
| --- | --- |
| Chart | `BINANCE:BTCUSDT` spot |
| Chart timeframe | `5m` |
| Script | temporary parity exporter only |
| Inputs | Pine defaults unchanged |
| Time zone | chart display UTC; script input remains default `Europe/London` |
| Collection window | 2026-08-16 00:00:00 UTC through 2026-08-16 23:55:00 UTC |
| Expected rows | 288 confirmed 5-minute bars, subject to exchange/chart availability |

This is a **parity-only** forward collection. It is not a backtest, and its
records must never be used to calculate return or select a strategy.

## TradingView steps

1. Open TradingView and load `BINANCE:BTCUSDT` on a normal 5-minute candlestick
   chart. Do not use Heikin-Ashi, range, replay-derived, or another synthetic
   chart type.
2. Open Pine Editor, paste the entire temporary exporter file, save it as
   `SMC PRO v2 — PARITY EXPORT ONLY`, and add it to the chart.
3. Open the script Inputs. Leave every value at its default; particularly keep
   the 4h HTF bias, `Europe/London` killzone, ATR 14, 1.5 stop multiplier,
   2.5 RR, and display toggles unchanged.
4. Create an alert from the chart. Select the temporary script and choose
   **alert() function calls only**. Do **not** include order-fill events.
5. Send the alert to a temporary, user-controlled collector. Do not use the
   Tradexa webhook URL, API key, or production automation endpoint. The payload
   contains no secret and is a single JSON object per confirmed live close.
6. Start it before the fixed window and let it run through the window. Do not
   alter the chart, symbol, timeframe, script, or inputs. TradingView saves a
   snapshot of a script and its inputs when the alert is created, so changes
   require deleting and recreating the alert.
7. Export/download the collector's raw request bodies in timestamp order as
   JSON Lines (`.jsonl`), retaining all rows and no hand edits. Supply that file
   and the exact raw spot candles for the same window to the parity harness.

TradingView’s `alert()` messages are dynamic strings, and strategy alerts run
at bar close by default; the official documentation also notes that alert()
events occur only on realtime bars. [TradingView alerts documentation](https://www.tradingview.com/pine-script-docs/concepts/alerts/)

## Record contract

Every record has stable lower-snake-case keys and must contain:

```text
record_type, timestamp, timestamp_ms, symbol, timeframe, close,
htf_bias, swing_trend_bias, internal_trend_bias,
sweep_high, sweep_low,
internal_bullish_bos, internal_bearish_bos,
internal_bullish_choch, internal_bearish_choch,
swing_bullish_bos, swing_bearish_bos,
swing_bullish_choch, swing_bearish_choch,
bullish_fair_value_gap, bearish_fair_value_gap,
near_bull_ob, near_bear_ob, bullish_pin_bar, bearish_pin_bar,
recent_sweep_low, recent_sweep_high, recent_bull_choch,
recent_bear_choch, recent_bull_fvg, recent_bear_fvg,
bars_since_sweep_low, bars_since_sweep_high,
bars_since_bull_choch, bars_since_bear_choch,
bars_since_bull_fvg, bars_since_bear_fvg,
long_condition, short_condition, trade_ready_condition,
context_score, execution_score, setup_quality, execution_status,
strat_atr, long_stop_loss, long_take_profit,
short_stop_loss, short_take_profit
```

`null` denotes a non-applicable prospective bracket. It is not zero. The
Python parity state must be normalised to these names before comparison; a
field mismatch, missing bar, extra bar, numeric discrepancy, direction change,
or condition mismatch is a parity failure until explained.

## What happens next

After the raw export is supplied, we will compare the exact 288 records
candle-by-candle against `research_smc_pro.py`. We will report parity first.
No train dataset, validation dataset, untouched test, component attribution,
or realistic-fill experiment is permitted before 100% event parity is proven.
