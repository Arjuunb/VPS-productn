# Price Action operational validation and reference study

This procedure validates only public Binance USDⓈ-M market data and the local
paper executor. It does not read private exchange credentials, submit exchange
orders, enable live trading, or make a profitability claim.

## Safety boundary

- Run the commands from `/opt/VPS-productn` on the target VPS.
- Keep the application in PAPER mode.
- The connectivity script uses a dedicated SQLite paper account under
  `/var/lib/tradexa/price_action_validation`; it does not use the production
  Trading Instance ledger.
- Stop if any report says `real_execution_allowed: true`,
  `private_credentials_used: true`, or an execution mode other than `PAPER`.
- Historical results are research evidence only. They do not guarantee future
  performance and do not authorize live trading.

## 1. Deploy and preflight

After the implementation commit is present on the server, rebuild the app and
check its normal health endpoint:

```bash
cd /opt/VPS-productn
git status --short --branch
docker compose build app
docker compose up -d --force-recreate app nginx
docker compose ps
docker compose exec app python -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=10).read().decode())'
docker compose exec app sh -c 'mkdir -p /var/lib/tradexa/price_action_validation/artifacts'
```

Do not continue if the app is unhealthy or the checkout does not contain
`automation-hub/scripts/validate_price_action_public_feed.py`.

## 2. Public-feed and PAPER-only soak

Run a bounded five-minute validation. This checks public exchange metadata,
REST candles, public bid/ask/mark/funding, routed market and public WebSocket
updates, a controlled market-channel disconnect/reconnect, stale-feed entry
blocking, candle/event deduplication, funding deduplication, and persistence of
a deliberately far-away PAPER limit order across reopening the validation account.

```bash
cd /opt/VPS-productn
docker compose exec app python scripts/validate_price_action_public_feed.py \
  --symbol BTCUSDT \
  --timeframe 1m \
  --seconds 300 \
  --cache-dir /var/lib/tradexa/price_action_validation/market \
  --state-db /var/lib/tradexa/price_action_validation/paper.sqlite3 \
  --output /var/lib/tradexa/price_action_validation/artifacts/public-feed.json
```

The command must exit zero and the report must contain:

- `outcome: VALIDATED`
- `execution_mode: PAPER`
- `private_credentials_used: false`
- `real_execution_allowed: false`
- final market-data state `SYNCHRONIZED`, transport state `CONNECTED`, and
  `reliable: true`
- routed transport channels `market: CONNECTED` and `public: CONNECTED`; the
  market channel carries kline/mark-price updates and the public channel carries
  bid/ask updates
- non-null independent candle, bid/ask and mark update timestamps after the
  controlled reconnect
- the last completed candle advances during the soak
- zero duplicate completed candles
- a completed controlled WebSocket reconnect
- one deliberately removed closed candle restored by public REST reconciliation
- zero duplicate strategy events
- zero duplicate zone/direction orders
- stale feed with `new_entries_paused: true`
- controlled reconnect performed
- same session, pending PAPER order and PAPER position preserved after reopening
- the same funding event remains deduplicated after reopening

Inspect the durable report without exposing environment values:

```bash
docker compose exec app python -c 'import json; p="/var/lib/tradexa/price_action_validation/artifacts/public-feed.json"; d=json.load(open(p)); print(json.dumps({"outcome":d.get("outcome"),"execution_mode":d.get("execution_mode"),"private_credentials_used":d.get("private_credentials_used"),"real_execution_allowed":d.get("real_execution_allowed"),"checks":d.get("checks")},indent=2))'
```

Verify that the dedicated validation session survives an actual application
container restart:

```bash
cd /opt/VPS-productn
docker compose restart app
docker compose ps
docker compose exec app python -c 'import json; from services.price_action_lab import PriceActionPaperAccount; report=json.load(open("/var/lib/tradexa/price_action_validation/artifacts/public-feed.json")); expected=report["checks"]["restart_persistence"]["session_id"]; account=PriceActionPaperAccount("/var/lib/tradexa/price_action_validation/paper.sqlite3"); state=account.state(); print(json.dumps({"same_session_after_container_restart":account.session()["id"]==expected,"orders":len(state["orders"]),"positions":len(state["positions"])},indent=2))'
```

Require `same_session_after_container_restart: true`, at least one order and at
least one position. This restart affects the app container only; Nginx remains
available subject to its normal upstream health handling.

If it fails, retain the JSON report and capture only application logs (never
print `.env`):

```bash
docker compose logs --since=15m --tail=500 app
```

## 3. Frozen PA1–PA4 walk-forward reference study

The study uses the predeclared BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT and XRPUSDT
universe on 15m, 1h and 4h. It downloads 3,000 closed candles per market,
public historical funding, applies the declared per-symbol commission/spread/
slippage assumptions, and runs the frozen baseline plus seven isolated
one-change-at-a-time variants. It does not optimize per asset or select on the
untouched OOS partition.

```bash
cd /opt/VPS-productn
docker compose exec app python scripts/run_price_action_reference_study.py \
  --cache-dir /var/lib/tradexa/price_action_validation/market \
  --research-db /var/lib/tradexa/price_action_validation/research.sqlite3 \
  --output /var/lib/tradexa/price_action_validation/artifacts \
  --bars 3000
```

The command must exit zero. Copy the reported artifact ID, then list the
durable machine-readable and Markdown files:

```bash
docker compose exec app sh -c 'ls -lh /var/lib/tradexa/price_action_validation/artifacts'
```

The JSON artifact is the evidence source of truth. Review at minimum:

- dataset and source-code fingerprints;
- UTC date ranges and partitions;
- exact PA1–PA4 counts, status and rejection reasons;
- gross and net normalized-R metrics;
- commission, spread, slippage and funding decomposition;
- funding coverage for every asset;
- untouched-OOS metrics per strategy, symbol, timeframe and regime;
- cost sensitivity, parameter sensitivity and OOS degradation;
- all fail-closed quality gates;
- data-quality and conversion warnings.

Any `HISTORICAL_FUNDING_UNAVAILABLE` or
`HISTORICAL_FUNDING_PARTIALLY_AVAILABLE` state means total-cost results are
provisional. Missing funding must never be interpreted as zero.

## 4. Optional frozen SMC comparison

First create a read-only normalized SMC JSON through the adapter using SMC
records from the identical dataset and partitions. Then rerun with:

```bash
cd /opt/VPS-productn
docker compose exec app python scripts/run_price_action_reference_study.py \
  --cache-dir /var/lib/tradexa/price_action_validation/market \
  --research-db /var/lib/tradexa/price_action_validation/research.sqlite3 \
  --output /var/lib/tradexa/price_action_validation/artifacts \
  --bars 3000 \
  --skip-download \
  --normalized-smc /var/lib/tradexa/price_action_validation/normalized-smc.json
```

The comparison refuses fair labeling unless source data, symbols, timeframes,
partitions, costs, fill/ambiguity rules, risk, exits, metrics version and
funding completeness are identical. Proposal-only or incomplete SMC records
remain explicitly unavailable; the adapter never invents fills or outcomes.

## 5. Acceptance decision

Operational readiness requires the public-feed report to be `VALIDATED`, the
artifact to be reproducible from its frozen dataset/code identifiers, and no
funding/data completeness gate to be hidden. Research quality is reported by
the configured gates; failing a gate is evidence against promotion, while
passing every gate is still not a future-performance guarantee and never
enables live trading.
