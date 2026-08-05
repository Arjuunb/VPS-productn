# Authentication and data migration report

## What changes

1. Customers authenticate through Supabase Auth, not `HUB_USERNAME` /
   `HUB_PASSWORD`.
2. Each Supabase user receives a `tradexa_profiles` row through an
   `auth.users` trigger.
3. Known trading and workspace tables receive `user_id`, an `auth.users`
   foreign key with `ON DELETE CASCADE`, a user index and forced RLS.
4. Existing rows are not overwritten. A one-time explicit transaction assigns
   null owners to the first administrator.

## Safe execution order

1. Back up the `tradexa-data` Docker volume and export the existing Supabase
   database if present.
2. Apply `supabase/migrations/0001_saas_auth.sql`.
3. Register and verify the first administrator using `/auth/register`.
4. Promote that user with the SQL in `supabase/README.md`.
5. If legacy Supabase rows exist, copy `0002_legacy_owner_backfill.sql` to a
   temporary untracked file, set `first_admin`, review the transaction and run
   it once.
6. Put the Supabase variables in `.env`, run `docker compose up -d --build`,
   then execute the checks in `DEPLOYMENT_VPS.md`.

## Rollback

Do not drop tables or reverse the migration in place. If a deployment fails,
set `HUB_AUTH_MODE=legacy` only together with
`HUB_EMERGENCY_ADMIN_ENABLED=1`, restore the volume/database backup, and fix
the Supabase configuration before retrying. This emergency mode is not a
customer authentication path.
