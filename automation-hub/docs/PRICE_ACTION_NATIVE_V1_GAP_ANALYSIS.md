# PRICE_ACTION_NATIVE_V1_RESEARCH gap analysis

Audit date: 2026-08-23
Basis: master PRD, Visual Lab addendum, continuation requirements, current source, and current tests.
Status describes the code before the continuation implementation began.

## Functional requirements

| Requirement | Status | Evidence / gap |
|---|---|---|
| FR-1 completed OHLC ingestion and validation | Partial | `NativePriceActionEngine.process_closed_bar` validates prices/order and rejects duplicates/out-of-order input. It does not identify missing periods or carry complete/source/data-quality fields. |
| FR-2 confirmed swings without look-ahead | Complete | Physical and confirmation timestamps are separate and right-side candles are required. Look-ahead tests exist. Equal-high clustering/tie policy is not complete. |
| FR-3 bullish, bearish and unclear structure | Partial | HH/HL and LH/LL produce a deterministic bias, but the richer emerging/pullback/invalidated transition ledger is absent. |
| FR-4 support/resistance zones | Partial | Swing zones, clustering, touches and age expiry exist. Body interactions, closing violations, reactions, expiration reasons, stronger-zone replacement and full state history are incomplete. |
| FR-5 flipped zones | Complete | Closed violation, role flip and later retest are deterministic and timestamped. |
| FR-6 generic rejection | Partial | Rejection events exist, but PA1 also requires a named-pattern-or-dominance condition; named patterns are therefore not metadata-only. |
| FR-7 pin, inside, combinations and fakeys | Partial | Single pin, inside, outside and engulfing exist. Mother references, double pin, pin+inside, inside pin and fakey variants are missing. |
| FR-8 general false breakout independent of fakey | Complete | `false_break_reclaim` is based on a structural boundary and does not require inside-bar structure. |
| FR-9 independent PA1-PA4 | Partial | Four independent traces/proposals/results exist. Some required strategy metadata and premise invalidation rules are incomplete. |
| FR-10 multiple entry and stop models | Missing | Only stop beyond dominance/rejection extreme plus rejection/zone stop is implemented. |
| FR-11 fixed 2.5R baseline | Complete | Default is 2.5R and recorded on every proposal. |
| FR-12 realistic costs/conservative execution | Partial | Commission, adverse slippage, gap-aware stop entry and stop-first ambiguity exist. Spread, funding and contract filters exist only in the separate manual paper ledger, not one normalized research execution. |
| FR-13 reproducible configuration | Missing | Engine config is deterministic but not snapshotted on every order/run; dataset/code IDs and immutable experiments are absent. |
| FR-14 rejected and unfilled setups | Complete | Strategy traces include missing conditions; normalized orders expire and remain reported. |
| FR-15 controlled PA versus SMC | Missing | No assumption-locking comparison report exists. |

## Non-functional requirements

| Requirement | Status | Evidence / gap |
|---|---|---|
| Reproducibility | Partial | Deterministic in-memory engine, but no persisted dataset/config/code manifest or rerun workflow. |
| Determinism | Complete | Closed-bar rules and deterministic IDs; no stochastic path. |
| Performance | Partial | Linear single-engine processing is suitable for chart windows; no multi-asset experiment orchestration or benchmark. |
| Modularity | Complete | Native engine, market data, paper broker and router are separated and unit-testable. |
| Explainability | Partial | Conditions and some plain reasons exist; broker actions lack setup/config reason provenance. |
| Safety | Complete | Engine rejects execution flags; Visual Lab has no private-key or real-order adapter. |
| Integrity | Partial | Paper state persists, but resets erase broker history and research runs are not immutable. |

## Master PRD acceptance criteria

| # | Status | Evidence / gap |
|---|---|---|
| 1. PA1-PA4 historical end-to-end | Complete | All strategies generate independent proposals and normalized outcomes. |
| 2. Completed information only | Complete | Engine accepts closed bars; forming live candle is excluded. |
| 3. Future candles cannot affect earlier decisions | Complete | Timestamped snapshots and no-look-ahead tests cover swings/zones/replay. |
| 4. Deterministic zone create/update/expire | Partial | Deterministic create/cluster/age expiry exists; full expiration and interaction policies do not. |
| 5. Every trade has entry/stop/target/cost/outcome | Complete | Normalized research trades include these fields. |
| 6. Pending/unfilled represented | Complete | PENDING/OPEN/EXPIRED are retained. |
| 7. Conservative ambiguous outcomes | Complete | Stop-first is explicit and tested. |
| 8. Reproduce saved configuration | Missing | No immutable run store/rerun API. |
| 9. Chart displays full representative reason | Partial | Structure/zones/events/proposals display; pattern relationships, lifecycle and execution reasons are incomplete. |
| 10. Controlled PA/SMC comparison | Missing | No comparison framework. |
| 11. Favourable and unfavourable results | Complete | Wins/losses/unfilled are all retained without filtering. |
| 12. No live trading | Complete | Hard-coded paper/research boundary and no order client. |

## Visual Lab acceptance criteria

| # | Status | Evidence / gap |
|---|---|---|
| 1. One sidebar and main chart | Complete | Responsive single-sidebar layout. |
| 2. Active Binance USDT perpetuals | Complete | Public `exchangeInfo` metadata feeds the selector. |
| 3. Historical candles | Complete | REST/cache history is supported. |
| 4. Live public updates | Partial | Three-second REST polling, not WebSocket. |
| 5. Current versus completed candles | Complete | Forming candle is display-only and labelled. |
| 6. No-look-ahead replay | Complete | Cursor prefix is the entire engine input. |
| 7. Same PA1-PA4 engine historical/live | Complete | Both paths instantiate `NativePriceActionEngine`. |
| 8. Chart structure/zones/triggers/trades | Partial | Research proposal lines exist; persistent paper order/position/result overlays are incomplete. |
| 9. Persistent virtual account | Complete | Isolated SQLite-backed 10,000 USDT account. |
| 10. Long/short paper positions | Complete | One-way long/short broker. |
| 11. Spread/slippage/commission/funding | Partial | Broker models costs and observed funding, but live bid/ask is not the direct market-fill anchor and research reporting does not unify all costs. |
| 12. Contract filters | Complete | Tick, step, quantity and notional checks are applied to manual orders. |
| 13. Never real order | Complete | No private Binance credential or real-order call. |
| 14. Pause entries on unreliable feed | Partial | REST response exposes a pause flag; no automatic entry engine exists to enforce it. |
| 15. Reconcile after reconnect | Missing | No Visual Lab WebSocket/reconciliation owner. |
| 16. Explain accepted/rejected | Partial | Strategy traces explain eligibility; broker lifecycle decisions do not. |
| 17. Replay/live parity | Partial | Shared engine exists; no explicit identical-stream parity test. |
| 18. Paper environment labelled | Complete | PAPER ONLY / NO REAL ORDERS labels are persistent. |

## Strategy, execution, paper and research continuation requirements

| Area | Status | Gap |
|---|---|---|
| Signals-only mode | Complete | Existing default. |
| Manual-approval mode | Missing | No candidate approval queue. |
| Automatic paper mode | Missing | No strategy-to-broker lifecycle. |
| Risk sizing / leverage / max risk | Missing | Manual quantity only. |
| Deterministic activation/cancel/invalidation | Partial | Research orders expire; broker orders are not linked to setup lifecycle. |
| Duplicate zone/direction protection | Missing | No durable setup/order idempotency key. |
| Exact order configuration snapshot | Missing | No broker metadata table. |
| Full execution explanations | Missing | Broker fills lack strategy/setup reason records. |
| Public kline/book/mark/funding WebSocket | Missing | Visual Lab uses REST polling. |
| Connection states/backoff/stale/dedupe | Missing | Only CONNECTED/DELAYED REST status. |
| Gap reconciliation and closed-only enforcement | Partial | Closed-only REST bootstrap exists; no reconnect reconciliation. |
| Resumable sessions | Missing | Session rows only contain identity/start/balance/status. |
| Start/resume/duplicate/export/end/reset | Partial | Current export/reset only; reset has confirmation but no audit row. |
| Wallet/orders/positions/trades persistence | Partial | Current broker persists, but ended-session snapshots/resume are absent. |
| Setup/connection/metrics/audit persistence | Missing | `pa_activity` exists but is unused. |
| Walk-forward partitions/windows | Missing | No Price Action experiment runner. |
| Deterministic experiment/dataset/code IDs | Missing | Manifest has only current source hash. |
| Immutable saved results/reruns/sweeps | Missing | No store or API. |
| Cost sensitivity and complete metrics/slices | Missing | Chart reports only closed/wins/losses/net/cost R. |
| Controlled PA-versus-SMC report | Missing | No assumption validation or comparison. |
| Pattern library | Partial | Missing mother/double/combinations/fakey metadata. |
| Entry/stop comparisons | Missing | One frozen model only. |
| First/repeated touch | Partial | Touch count exists but results are not sliced. |
| Same/HTF zones | Missing | Single-timeframe engine only. |
| Complete setup FSM/reason codes | Missing | Setups jump directly WATCHING→ENTRY_READY; broker lifecycle phases absent. |
| Gap-aware fills | Complete | Stop/limit/protection use first available candle open when levels are crossed. |
| Estimated paper liquidation on mark | Missing | Margin is shown but no estimated boundary/event. |
| Replay/live deterministic parity test | Missing | Design shares engine; explicit test absent. |
| FastAPI tests run locally | Missing | `fastapi` is absent and API tests call `importorskip`. |
| Browser Visual Lab E2E | Missing | No focused flow exists. |

## Explicit V1 deferrals from the PRD

These are intentionally deferred by the source PRD, not accidental omissions: real-money execution, private Binance credentials, withdrawals, hedge mode, cross margin, machine learning, news/fundamentals, SMC signal sharing, a mixed PA/SMC strategy, dynamic exits, partial exits, trailing strategy exits, break-even logic, and claims of profitability.

## Post-implementation audit

The tables above are intentionally retained as the required *pre-edit* audit. After this continuation phase:

- FR-1 through FR-15 are implemented at the V1 acceptance level: completed-bar continuity/reconciliation, deterministic structure/zones/events, PA1–PA4, multiple entry/stop experiments, fixed 2.5R, normalized conservative costs, immutable experiment provenance, rejected/unfilled reporting, and assumption-locked PA/SMC comparison.
- All 18 Visual Lab acceptance criteria are implemented, including public kline/book/mark WebSockets, REST reconciliation, feed-state entry suspension, persistent paper overlays, setup explanations and replay/live completed-candle parity.
- Signals-only, manual approval and automatic paper execution are explicit. The automatic route performs risk/contract/margin validation, durable duplicate protection, lifecycle cancellation, stop-first protection, funding deduplication, paper mark liquidation and exact order provenance. There is no private-key or real-order adapter.
- Sessions persist and restore replay cursor, configuration, broker wallet/orders/positions/fills, setup/candidate provenance, funding, metrics, connection events and audit activity. Start, resume, duplicate, export, end and confirmed reset actions are exposed.
- Research runs now use deterministic dataset/code/experiment IDs, 60/20/20 chronological partitions, expanding walk-forward windows, common multi-market parameters, cost sensitivity, immutable saved results/reruns, required aggregate metrics and required slices.
- The 12 master acceptance criteria pass in the focused Price Action verification. This is software/research-infrastructure acceptance only; it is not evidence that PA1–PA4 are profitable.

### Deliberate V1 operational limits

- Historical funding is reported as zero when the supplied OHLC dataset has no funding-rate series; live paper sessions apply and deduplicate the public provider funding event.
- The controlled PA/SMC endpoint accepts normalized SMC research output and refuses mismatched assumptions. It does not import or modify the separate frozen SMC engine.
- Fakey metadata distinguishes bullish/bearish, one-inside/multi-inside and pin/non-pin variants. A separately named two-candle failure/reclaim subtype is not promoted into a strategy gate.
- Confirmation, close and 50% retracement entries, and rejection/pattern/structural stops are implemented. Inside-bar and mother-bar breakout entries remain research metadata rather than additional execution modes.
- Optional chart conveniences (favourites, fullscreen and timestamp jump) are not acceptance blockers and remain UI backlog.
