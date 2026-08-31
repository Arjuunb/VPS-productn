# Audit Phase 0 — trust & safety hot-fixes

Closes the audit's Critical + the highest-impact High/Medium items. All verified
in-source and covered by tests.

## CR-1 — session forgery / privilege escalation (Critical)
Sessions were HMAC-signed with `webhook_secret`, which is embedded in every
authenticated page — so any logged-in user could read it and forge an `owner`
cookie. **Fix:** sessions are now signed with the server-only `secret_key`
(`HUB_SECRET`, never embedded). A leaked webhook secret can no longer mint a
session. (`app.py` `_sign_session`/`_verify_session`.)

## M-7 — insecure defaults reach production
On a cloud host (`RENDER`/`DYNO`), the app now **refuses to boot** if
`HUB_SECRET` is the dev default (session forgery would be trivial), and prints a
loud warning if `HUB_PASSWORD` is still `admin`. Never fires on a correct deploy
(`render.yaml` generates `HUB_SECRET`) or under tests/local dev.

## H-2 — anonymous read of live config
The landing bundle exempted the `/settings` prefix so its SPA routes load, but
that also exposed the bare `/settings` **API** (live strategy/risk/symbols).
**Fix:** exempt only `/settings/` sub-routes; the bare `/settings` API is
session-gated again.

## M-3 — unlocked shared SQLite store
`SqliteStore` (users, settings, bots) is shared between request threads and the
bot lifecycle but had no lock (every other store does). **Fix:** added an
`RLock` around its writes + `busy_timeout` so concurrent access waits instead of
raising “database is locked”.

## H-3 — misleading exchange-key security messaging
The Exchange Connections page toasted “keys saved and encrypted” and showed
“Keys are encrypted before storage / we verify keys are trade-only”, but
`save()` stored nothing and nothing was encrypted or verified. **Fix:** a clear
“Preview — not wired to the engine; paper mode; keys are not stored or
transmitted” notice, honest toast, and the encryption/verification claims
removed (the good “use trade-only keys, no withdrawals” advice stays).

## H-6 — destructive controls with no confirmation
Stop Engine / Pause All / Stop All on Paper Trading now confirm before firing,
matching the Safety Center kill-switch and Settings resets. (The header
strategy/timeframe switch is intentionally left immediate — it’s a
fast-switch menu control that already announces “engine restarted”.)

## M-2 — polling intervals leaked on unmount
`LoadDataButton` and `ControlBar` tracked their status-poll id in a `useRef` and
now `clearInterval` on unmount, so navigating away mid-load no longer leaves an
interval firing `setState` on an unmounted component.

## M-1 — outages shown as a real empty account
Portfolio and Simulation now render a shared **OfflineBanner** when the backend
is unreachable, so `$0` equity reads as “connection issue,” not “wiped account.”

## M-5 — credentials separated and enforced (done)
Control, TradingView webhook, and exchange access use independent credentials:
`HUB_CONTROL_KEY`, `HUB_WEBHOOK_SECRET`, `HUB_EXCHANGE_API_KEY`, and
`HUB_EXCHANGE_API_SECRET`. Startup rejects any overlap. The webhook secret can
post alerts only; it cannot stop the engine, reset an account, or change settings.
Control mutations additionally require an operator or owner role. The dashboard
receives the control key in its runtime configuration; exchange credentials are
never exposed to the browser. Covered by `tests/test_credential_separation.py`,
`tests/test_webhook_scope.py`, and `tests/test_rbac_gating.py`.
