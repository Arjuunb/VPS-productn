# Trading Performance Regression Investigation

Audit date: 2026-08-08
Historical baseline inspected: `v1.0.0-production` (`5d0782c`, 2026-07-31)
Pre-audit current revision inspected: `aafd224` (2026-08-08)

## Executive conclusion

There is **no preserved Git commit, tag, database row, paper-trade log, test
report, or runtime configuration in this repository that records the reported
paper-account growth from 500 to approximately 2,500**. No honest audit can
therefore identify a “high-performance version” or claim to restore that result.

The earliest preserved production baseline is `5d0782c`. It is the closest
available comparison point, not proof that it was the strong-performing run.

The two named strategy implementations are not the regression:

- `DecisionBrain` is byte-identical between `5d0782c` and `aafd224`.
- `SupertrendStrategy` is byte-identical between `5d0782c` and `aafd224`.
- On the same fixed 2,000-bar BTC 1h fixture, historical and current code
  emitted the same 78 signals: 56 Decision Brain and 22 Supertrend. Timestamp,
  side, confidence, entry, stop, and target all matched.

Credible behavioural differences are in **market-data mode, execution
configuration, and defensive gates/sizing**. Whether they explain the
production account change requires the VPS/Supabase history and the runtime
configuration active during the claimed run.

## Historical high-performance version

| Item | Evidence-based result |
| --- | --- |
| Likely commit | **Not identifiable.** Earliest preserved baseline: `5d0782c`. |
| Date | 2026-07-31. |
| Reported 500 → 2,500 run | No supporting record in Git or the local ignored ledger. |
| Local ledger evidence | Zero paper trades; zero cached candles; zero V2 orders/fills/positions. |
| Historical strategy/timeframe/symbols/risk | **Unknown.** Old `.env.example` did not set them. |
| Historical TP/SL | Brain: ATR(14) × 1.5, 3.0R. Supertrend: ATR(14) × 1.5, 2.5R. Unchanged. |

The old configuration example had `HUB_USE_LIVE_DATA=0`, daily-loss limit 3%,
and drawdown limit 20%. It does not prove those values were used on the VPS.

## Findings

### ROOT CAUSE — historical evidence is missing

The claimed high-performance run cannot be reproduced or attributed from this
repository. This is an audit-data issue, not a claim that a strategy is broken.

### HIGH IMPACT — deployment mode and data source changed

| Setting / behaviour | `5d0782c` example | Current example | Effect |
| --- | --- | --- | --- |
| `HUB_USE_LIVE_DATA` | `0` | `1` | Replay consumes historical bars; forward mode warms up and trades only new **closed** candles. Frequency and market period differ. |
| `HUB_MARKET_DATA_V2` | absent | `1` | `get_bars()` uses only the V2 real cache; no cache means no bars. |
| Fill configuration | perfect by default | realistic spread, slippage, and fees requested | Lowers paper P&L relative to ideal fills when active. |
| Daily loss / drawdown examples | 3% / 20% | 1% / 10% | More conservative new-entry protection. |

V2 data mode is fail-closed. If `HUB_MARKET_DATA_V2=1` is set on the VPS
without a populated V2 cache, it returns
`unavailable (Market Data V2 cache required)` and cannot form valid trades.
This can explain a stopped/no-trade engine, but is **not confirmed** for the
VPS by a repository-only audit.

### HIGH IMPACT — Supertrend quality-gate interaction on the fixture

The separate `TradeBrain` quality gate can reject Supertrend signals. It is not
the `DecisionBrain` strategy; they are distinct components. The live engine
applies the quality gate after a built-in strategy signal when enough history
exists.

| Test | Controlled layers | Trades | Wins / losses | Win rate | PF | Net R | Max DD | Blocked |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | Supertrend only | 20 | 6 / 14 | 30.0% | 1.01 | +0.22 | 6.1% | 0 |
| B | A + score ≥ 60 quality gate | 17 | 5 / 12 | 29.4% | 0.99 | −0.15 | 6.4% | 4 |
| C | B + representative 5-loss risk control | 17 | 5 / 12 | 29.4% | 0.99 | −0.15 | 6.4% | 4 |
| D | C + maker-limit execution proxy | 17 | 5 / 12 | 29.4% | 1.00 | −0.01 | 6.3% | 4 |

Test D had zero expired limit orders. It cannot recreate unknown VPS balances,
learning state, global instance limits, or missing live candles. It is fixture
evidence, not a live-profitability claim.

### MEDIUM IMPACT — defensive state added after baseline

Current `AutoStrategyEngine` gives the quality gate the per-symbol closed-loss
streak. Three losses lower quality; five losses hard-block a setup. The new
`StrategyHealthMonitor` lowers only new-entry risk to 75% for a degrading
record and 50% for an unhealthy record after a sufficient sample. These do not
change signals, stops, or targets, but can change accepted count and growth.

Trading Instances add deliberately global position-risk and daily-loss guards.
That sharing is account protection, not cross-symbol indicator leakage.

### MEDIUM IMPACT — paper-fill realism

`PaperExecutionEngine` source itself is unchanged. The current example now
requests `RealisticFill`: 0.04% spread, 0.03% slippage, and 0.04% taker fee per
side. A prior `PerfectFill` run looks better with identical signals. This is a
measurement change, not proof of a strategy regression.

### LOW IMPACT — lifecycle, UI, and isolated instances

Lifecycle recovery, single-pair selection, fixed sizing, and isolated Trading
Instances were added after the baseline. They do not alter the Decision Brain or
Supertrend formulas. Instances isolate their own ledger and strategy state.

### UNRELATED — formula changes, look-ahead, and accidental state sharing

- No committed change was found to Decision Brain EMA/RSI/regime/HTF logic,
  Supertrend ATR period/multiplier/direction logic, or ATR brackets.
- The live engine feeds only `bars[:-1]`; in-progress candles are excluded.
  Both strategies act at candle close, and the fixed stream is causal.
- The backtest exits stop-first when a candle touches stop and target, a
  conservative ambiguity policy.
- Engine strategy objects, pending orders, targets, managed trades, and health
  records are keyed per symbol/instance. Portfolio risk is intentionally global.

## Identical market replay

Fixture: `data/samples/BTC-USD.csv`, mapped to BTCUSDT, 2,000 hourly candles,
2020-01-01T00:00:00Z through 2020-03-24T07:00:00Z. Costs: 0.04% fee plus
0.03% slippage per side. This is bundled development data, not the claimed run.

| Strategy | Agreement | Trades | Win rate | PF | Net R | Max DD R |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Decision Brain | Historical/current identical | 8 | 37.5% | 1.704992 | +3.671934 | 2.077856 |
| Supertrend | Historical/current identical | 20 | 30.0% | 1.014947 | +0.217468 | 6.123313 |

Signal comparison: 78 matches; 0 missing historical signals; 0 new current
signals; 0 direction conflicts; 0 entry/stop/target changes.

Decision Brain beat Supertrend on this one old fixture. That does **not** prove
which strategy produced the claimed account growth and must not be used as an
optimisation result.

## Default configuration comparison

| Setting | Baseline code/default | Current code/default | Result |
| --- | --- | --- | --- |
| Default auto strategy | `brain` | `brain` | Unchanged. |
| Default timeframe | `4h` | `4h` | Unchanged in code. |
| Default symbols | BTCUSDT, ETHUSDT, SOLUSDT | same | Unchanged in code. |
| Default risk per trade | 1% | 1% | Unchanged. |
| Max open positions | 3 | 3 | Unchanged. |
| Brain RR | 3.0 | 3.0 | Unchanged. |
| Supertrend ATR / multiplier / RR | 10 / 3.0 / 2.5 | same | Unchanged. |
| Entry-mode code default | `limit` | `limit` | Unchanged; VPS persisted setting is still required. |

The local runtime settings are a development artifact, not VPS history. They
currently specify `brain`, 1h, auto sizing, and limit entries; they have no
completed trades.

## Strategy version integrity

Before this audit, custom strategies had stored snapshots but built-in Decision
Brain and Supertrend had only display names. Added:

- `Decision Brain v1.0.0` and `Supertrend v1.0.0` provenance tied to the
  preserved production tag and source blobs.
- Deterministic causal signal fingerprints over the fixed BTC fixture.
- A/B/C/D execution regression coverage for Supertrend.
- Catalogue metadata so newly created Trading Instances default to the
  immutable version while explicitly supplied legacy labels still work.

Future observable behaviour must use a new version and fixture baseline; it
must not silently overwrite v1.0.0.

## Phase 2 investigation status

### Actual VPS configuration, persisted overrides, and V2 cache health

This repository cannot read the running Hostinger VPS or the private Supabase
project. A repeatable, **read-only** collector is now included at
`automation-hub/scripts/trading_performance_forensics.py`. It reports only the
allow-listed trading settings, effective override precedence, V2 cache health,
and aggregate ledger evidence. It never prints credentials, prices, trade IDs,
payloads, or log content. Its SQLite cache inspection uses `mode=ro` and does
not call the V2 service's cache-quarantine path.

Run this exact command on the VPS after pulling the audit changes:

```bash
cd /opt/VPS-productn
docker compose exec -T app python scripts/trading_performance_forensics.py \
  --format markdown > CURRENT_VPS_TRADING_CONFIG.md
sed -n '1,260p' CURRENT_VPS_TRADING_CONFIG.md
```

The file gives the actual answer to the unresolved configuration questions:

- `.env` value versus durable `runtime_settings.json` value versus effective
  engine value;
- manual-symbol precedence over automatic symbols;
- one row per effective V2 symbol with newest candle age, bar count, missing,
  duplicate, and ordering counts, and whether the 150-bar indicator warm-up is
  met;
- whether `Market Data V2 cache required` is the active failure;
- aggregate Supabase paper-trade/Trading Instance evidence, or a clearly marked
  local SQLite fallback if Supabase is unavailable;
- a candidate near 500 to at least 2,250 only when opening capital can actually
  be reconstructed.

`paper_trades` does not persist entry mode or fill model, and legacy rows can
lack a strategy id. The collector deliberately reports those as **unknown** and
labels missing strategy attribution **UNATTRIBUTED LEGACY RUN**. It must not
guess a strategy based on a profitable result.

### Actual VPS snapshot — 2026-08-08

The collector was run in the production app container on 2026-08-08. This is
real runtime evidence, not an `.env.example` inference:

| Area | Observed value | Consequence |
| --- | --- | --- |
| Data mode | `HUB_USE_LIVE_DATA=0`; V2 disabled | Workers are in historical replay mode, not forward paper-trading mode. |
| Legacy cache | BTCUSDT 5m: 6,000 real cached candles, 2026-07-11T05:40Z to 2026-08-01T01:35Z | The data exists but is stale relative to the audit date. No current market candle can arrive in this mode. |
| Legacy runtime selection | Supertrend / BTCUSDT / 5m / limit / manual symbol / fixed `0.3` | This is the legacy autonomous-engine configuration. It is not proof that an active Trading Instance uses those settings. |
| Legacy engine state | Startup logged: `restored 2 trading instance worker(s); legacy autonomous engine remains stopped` | A stopped legacy engine is expected in this instance-first deployment; it is not a worker crash. |
| Current settings mirror | Supabase connected | Settings and ledger data survived restart and the collector had access to the production source of truth. |

The replay loop loads 150 warm-up bars plus 250 tradeable bars. When a local
cache is present, it then reloads the same newest cache window after exhausting
the 250 bars. With live data disabled, a static cache can therefore be replayed
again rather than being replaced with a newly closed market candle. The
attributed BTC Supertrend sequence (371 closed trades in about 22 hours) is
consistent with repeated fast replay of stale history. It is **not evidence of
371 independent live 5-minute opportunities** and must not be used to estimate
live profitability.

The real cached BTC 5m data rules out the synthetic-fallback concern for the
observed BTC replay. It does not make the result forward-valid: its final
candle is a week old and the same replay window can recur.

The 469 closed Supabase paper trades show no reconstructable 500 → 2,500 run.
The largest correctly attributed group is BTCUSDT Supertrend, 5m, 371 closed
trades, 50.9% win rate, PF 2.66, average R +0.859, and 1,000 → 1,022.69. Its
ledger schema does not store entry-mode or fill-model attribution. Earlier
multi-pair rows are correctly retained as **UNATTRIBUTED LEGACY RUN** rather
than being credited to Supertrend.

#### Instance-versus-legacy control boundary

This VPS has two restored Trading Instance workers. An instance worker is
constructed from its own persisted `symbol`, `strategy_key`, `timeframe`,
`risk_per_trade_pct`, and `capital_allocation`; it uses the global
`HUB_USE_LIVE_DATA` mode but does not consume the legacy runtime settings for
manual symbol selection, fixed position sizing, daily-loss limit, drawdown
limit, or quality threshold. In particular, the instance constructor currently
uses automatic sizing by default even though the legacy runtime mirror reports
`position_sizing_mode=fixed` and `fixed_position_size=0.3`.

Therefore the visible global settings panel can describe the stopped legacy
engine while the active instance workers continue under their own persisted
configuration. This is a confirmed explanation for confusing dashboard state
and for historical BTC and ETH trades appearing despite a legacy manual BTC
selection. It is an architecture/control-scope finding, not a change to either
strategy formula.

### Full production-pipeline fixture: execution realism and entry mode

The following is a second controlled test on the same bundled 2,000-bar BTC
fixture. Unlike the A--D strategy simulator above, it drives the in-memory
`AutoStrategyEngine → SignalPipeline → PaperExecutionEngine` path with current
representative settings: 500 starting capital, 1% risk, three-position cap,
score threshold 60, 20% drawdown limit, and no daily-loss cap. It remains a
fixture proxy, not a VPS reconstruction.

| Strategy | Fill / entry | Signal state changes | Trades opened | Entry rejections | Closed trades | PF | Realized P&L | Ending balance | Fees |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Supertrend | Perfect / market | 22 | 18 | 12 | 9 | 0.71 | -1.83 | 498.17 | 0.00 |
| Supertrend | Realistic / market | 22 | 18 | 12 | 9 | 0.65 | -2.28 | 497.72 | 0.18 |
| Supertrend | Realistic / limit | 22 | 18 | 12 | 9 | 0.67 | -2.15 | 497.85 | 0.18 |
| Decision Brain | Perfect / market | 8 | 16 | 0 | 8 | 1.71 | +2.99 | 502.99 | 0.00 |
| Decision Brain | Realistic / market | 8 | 16 | 0 | 8 | 1.58 | +2.60 | 502.60 | 0.16 |
| Decision Brain | Realistic / limit | 8 | 16 | 0 | 8 | 1.62 | +2.72 | 502.72 | 0.16 |

For the current `RealisticFill` defaults, a taker-side price impact is 0.06%
(0.02% half-spread + 0.03% slippage + 0.01% latency) before a 0.04% taker fee
per side. Limit entries avoid price impact at entry but the current paper
engine deliberately books taker commission both sides on close. The strategy
simulator's simplified maker-cost proxy therefore must **not** be compared as
an exact account P&L with the full pipeline.

On this fixture, neither market nor limit mode caused expiry-driven trade loss;
the small differences are fill and fee mechanics. Actual VPS order/fill records
are still required before drawing a conclusion about live limit participation.

### Compounding and risk-limit audit

The current `SignalPipeline` stores `self.equity` when it is constructed and
uses that same value for automatic size and exposure calculations. It does not
replace it with `paper.balance()` after a realized close. Trading Instances
construct their pipeline with `capital_allocation`, which has the same effect.
Therefore current automatic fixed-risk sizing is **fixed-base risk**, not
dynamic-equity compounding. Account balance itself changes correctly, but a 1%
new-trade risk amount does not grow as an account grows unless a pipeline is
rebuilt with a new allocation. This is an observed behavioural difference, not
a strategy-formula change and has not been changed in this audit.

The 3% daily / 20% drawdown historical examples versus the 1% / 10% current
examples cannot be quantified fairly without a dated, ordered historical trade
stream. The collector records the effective live limits; a deterministic
counterfactual should be run only after the relevant attributed trades are
available. The safety limits have not been weakened.

### Multi-regime and historical attribution status

The local CSV fixture covers only BTC and one 1h historical period. It cannot
honestly stand in for BTC, ETH, and SOL over 5m, 15m, 1h, and 4h bull, bear,
choppy, high-volatility, and low-volatility conditions. The V2 cache evidence
above is the prerequisite for an equivalent deterministic replay dataset. Once
a candidate historical run and matching cached candles exist, replay only
Decision Brain v1.0.0 and Supertrend v1.0.0 against those timestamps, report
each match rate, and mark any result **inferred attribution**, never certain
attribution.

### Ranked root-cause conclusion (current evidence)

| Rank | Cause | Evidence |
| --- | --- | --- |
| Ruled out | Decision Brain / Supertrend formula regression | Source blobs and all 78 deterministic causal signals are identical. |
| Confirmed behavioural difference | Forward live versus historical replay semantics | Forward mode warms up then acts only on newly closed candles; replay consumes existing history. Growth speed is not comparable. |
| Confirmed behavioural difference | Non-compounding fixed-base sizing | Pipeline uses construction-time equity, not current realized balance, for future auto-size. |
| Strong but VPS-unconfirmed | V2 cache availability / freshness | V2 fails closed with no verified cache and can produce no valid bars. |
| Strong fixture evidence | Supertrend quality-gate interaction | The gate slightly reduced Supertrend fixture expectancy; whether it did so on the VPS needs actual rejected-signal history. |
| Strong fixture evidence | Realistic execution costs | Identical decisions lose P&L to price impact and fees; not enough to prove the claimed 500→2,500 difference alone. |
| Moderate | Entry mode | Fixture differences were small and no limit expiry occurred; production order records are required. |
| Unknown | Historical strategy/timeframe/market regime | No preserved timestamped attributed trade stream exists locally. |

## Recommended next action

Generate and retain `CURRENT_VPS_TRADING_CONFIG.md` using the command above,
then provide its non-secret contents for review. Do not change strategy
parameters, risk limits, or fill assumptions before that evidence identifies a
specific cause.

## Files changed

- `automation-hub/strategies/builtin_versions.py`
- `automation-hub/tests/test_builtin_strategy_versions.py`
- `automation-hub/webhook_api.py`
- `automation-hub/services/strategy_presets.py`
- `automation-hub/routers/instances.py`
- `TRADING_PERFORMANCE_REGRESSION_REPORT.md`
- `automation-hub/scripts/trading_performance_forensics.py`

## Validation

Focused validation passed locally:

```text
36 passed, 1 skipped
```

The suite includes version/fingerprint and A/B/C/D regression coverage, plus
backtest, Decision Brain, quality-gate, execution, health, and Trading Instance
tests. The skip is FastAPI-dependent in local macOS; Docker installs that
dependency for production.
