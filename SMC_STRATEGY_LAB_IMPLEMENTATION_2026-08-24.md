# SMC Strategy Lab implementation status — 24 August 2026

## Completed

- Renamed the product surface to **SMC Strategy Lab** and preserved `#/smc-visual-lab` as a legacy alias.
- Added `SMC_SOURCE_V1.0.0-paper-draft`, the model registry, deterministic M1 evaluation, native-object condition evidence, native HTF/location gates, OB preference, FVG fallback, split targets and parked M2/M3 contracts.
- Kept `services/native_smc.py` and `services/smc_strategy_ladder.py` unchanged.
- Made Binance USDⓈ-M Futures the default live SMC venue; MEXC and Kraken remain explicit alternate displays.
- Added a separate `HUB_SMC_PAPER_DB` database and SMC-only sessions, balance, positions, orders, fills, candidates, ownership metadata, activity, funding namespace and processed-candle ledger.
- Added signals-only, manual-approval and automatic-paper boundaries. All real-execution flags are hard false.
- Added server-side quantity/risk sizing, Binance tick/step/notional checks, idempotency, market/limit/stop validation, protection geometry, account leverage and exact reset confirmation.
- Added 50% T1 reduce-only scale-out plus remaining-position T2/stop protection and conservative stop-first handling when T1 and stop are both touched in one OHLC candle.
- Added a daemon PAPER-only runtime that advances only unique closed candles and fails closed on provider errors.
- Added session create/configure/end/resume/duplicate/export, paper reset/export, order create/cancel/reconcile, candidate approval, protection, journal and metrics APIs.
- Added session-owned historical replay stepping over verified cached candles. The cursor is durable, future bars remain hidden, and a LIVE session cannot use the replay endpoint.
- Added an account/control sidebar, manual paper ticket, model selector, Clean-by-default chart layers, decision conditions and the required bottom terminal tabs.
- Replaced raw JSON terminal panes with responsive positions, orders, fills, setups, rejected, session and journal tables. The journal includes filters, CSV/JSON export, an evidence drawer, chart focus and append-only notes.
- Preserved completed-session journal evidence through immutable session snapshots after resets or new sessions.
- Added source-strategy entry, stop, 50% T1 and T2 projections plus factual paper fill/exit markers derived from candle-time audit evidence.
- Connected Binance public funding events to the isolated paper ledger with session-scoped idempotency and explicit funding reporting.
- Added completed-trade net P&L, expectancy, profit factor, target-hit rate, fees, funding and sample-size reporting.
- Reused the Binance public websocket + REST-recovery freshness state machine. PAPER entries now fail closed unless candles, bid/ask and mark are fresh, gap-reconciled and identity-matched.
- Added desktop, tablet and mobile Playwright interaction/overflow verification for the SMC terminal.
- Added global Factory Reset integration for SMC operational state.
- Made the frozen state-machine attestation hash portable across Python 3.11/3.12 by removing only Python 3.12's empty AST `type_params` metadata. The engine and stored fingerprint were not changed.

## Deliberate evidence boundaries

- M2 and M3 remain parked exactly as required; they have not been activated.
- MFE and MAE remain `null`: the broker stores candle-level fill evidence but not a trustworthy intratrade tick path. These values are explicitly disclosed as unavailable and are never fabricated.
- M2/M3 activation and research remain separate future phases; this implementation does not tune or promote them.
- This implementation remains PAPER-only. No endpoint, worker, setting or UI control can enable real exchange execution.
