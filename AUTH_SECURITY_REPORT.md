# Authentication security report

## Implemented controls

| Control | Status | Implementation |
|---|---:|---|
| Managed password hashing and reset tokens | PASS | Supabase Auth only; FastAPI no longer accepts customer passwords in Supabase mode. |
| Email verification before dashboard | PASS | Server session bridge rejects an unconfirmed Supabase account. |
| Token verification | PASS | Backend validates the presented access token with Supabase `/auth/v1/user`; successful checks are cached for at most 30 seconds. |
| Session transport | PASS | Same-origin, HttpOnly, Secure-in-production cookie; Supabase client refresh events renew it. |
| CSRF | PASS | SameSite=Lax plus origin validation for cookie-authenticated state changes. |
| OAuth exposure | PASS | Google/Apple buttons only render when the corresponding server flag is enabled. |
| Privilege escalation | PASS | Role lives in `tradexa_profiles`; a database trigger rejects a self-service role change. |
| Server secrets | PASS | Service-role key exists only in backend environment and is never injected into browser config. |
| RLS / foreign keys / cascade delete | PASS | Supabase migration adds `user_id`, `auth.users` FKs, indexes, forced RLS and owner/admin policies for known data tables. |
| Existing shared engine data | GUARDED | Regular users are denied access to the retained deployment-wide legacy engine until its stores are migrated; this prevents cross-user disclosure. |

## Operational requirements

- Enable email confirmation and configure SMTP in Supabase.
- Apply `supabase/migrations/0001_saas_auth.sql` before enabling Supabase mode.
- Promote the first verified account to `admin` with the documented SQL.
- Keep `HUB_EMERGENCY_ADMIN_ENABLED=0` during normal operation.
- Use HTTPS; the Compose Nginx configuration is already prepared for it.

## Residual risk

Full customer paper/live engine tenancy requires a subsequent per-user engine
runtime migration. It is intentionally not faked or allowed against the
current shared SQLite state. The current access guard fails closed for normal
users while preserving administrator operation of the existing engine.
