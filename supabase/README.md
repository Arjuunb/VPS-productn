# TradeLogX Supabase migrations

Apply `0001_saas_auth.sql` before turning on `HUB_AUTH_MODE=supabase`. It makes
Supabase Auth the identity authority, creates a profile automatically for every
new user, enforces per-user RLS, and retains an administrator-only audit log.

Then create and verify the first admin through the normal email flow. In the
Supabase SQL editor, run:

```sql
update public.tradexa_profiles set role = 'admin' where id = '<AUTH_USER_UUID>'::uuid;
```

For a deployment containing legacy rows, copy `0002_legacy_owner_backfill.sql`
to a temporary local file, set `first_admin`, review it, and execute it in one
transaction. Never commit the real UUID or any credential.

`SUPABASE_SERVICE_ROLE_KEY` is server-only and is used solely for account
deletion, audit writes, and bounded administrator user metadata. The public
browser uses only `SUPABASE_ANON_KEY`; RLS protects every table it can access.
