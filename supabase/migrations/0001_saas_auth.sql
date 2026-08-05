-- TradeLogX SaaS identity, roles, audit trail and user-data isolation.
-- Apply in the Supabase SQL editor or with `supabase db push` BEFORE enabling
-- HUB_AUTH_MODE=supabase. This migration is idempotent where PostgreSQL permits.

create table if not exists public.tradexa_profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  full_name text not null default '',
  avatar_url text,
  timezone text not null default 'UTC',
  preferences jsonb not null default '{}'::jsonb,
  role text not null default 'user' check (role in ('user', 'admin')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  last_login timestamptz
);

create or replace function public.tradexa_is_admin()
returns boolean language sql stable security definer set search_path = public
as $$ select exists (select 1 from public.tradexa_profiles where id = auth.uid() and role = 'admin') $$;

create or replace function public.tradexa_new_user()
returns trigger language plpgsql security definer set search_path = public
as $$
begin
  insert into public.tradexa_profiles (id, full_name, avatar_url)
  values (new.id, coalesce(new.raw_user_meta_data->>'full_name', ''), new.raw_user_meta_data->>'avatar_url')
  on conflict (id) do nothing;
  return new;
end;
$$;

drop trigger if exists tradexa_on_auth_user_created on auth.users;
create trigger tradexa_on_auth_user_created
  after insert on auth.users for each row execute procedure public.tradexa_new_user();

-- No customer can turn themselves into an administrator through an otherwise
-- valid profile UPDATE policy. Server-side admin provisioning is documented in
-- DEPLOYMENT_VPS.md and is auditable.
create or replace function public.tradexa_guard_profile_role()
returns trigger language plpgsql security definer set search_path = public
as $$
begin
  if new.role is distinct from old.role and not public.tradexa_is_admin() then
    raise exception 'Only an administrator may change a TradeLogX role';
  end if;
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists tradexa_guard_profile_role on public.tradexa_profiles;
create trigger tradexa_guard_profile_role before update on public.tradexa_profiles
  for each row execute procedure public.tradexa_guard_profile_role();

alter table public.tradexa_profiles enable row level security;
alter table public.tradexa_profiles force row level security;
drop policy if exists tradexa_profiles_select on public.tradexa_profiles;
create policy tradexa_profiles_select on public.tradexa_profiles for select
  using (id = auth.uid() or public.tradexa_is_admin());
drop policy if exists tradexa_profiles_update on public.tradexa_profiles;
create policy tradexa_profiles_update on public.tradexa_profiles for update
  using (id = auth.uid() or public.tradexa_is_admin())
  with check (id = auth.uid() or public.tradexa_is_admin());

create table if not exists public.tradexa_audit_log (
  id bigint generated always as identity primary key,
  actor_id uuid references auth.users(id) on delete set null,
  target_id uuid references auth.users(id) on delete set null,
  event text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists tradexa_audit_actor_created_idx on public.tradexa_audit_log(actor_id, created_at desc);
alter table public.tradexa_audit_log enable row level security;
alter table public.tradexa_audit_log force row level security;
drop policy if exists tradexa_audit_admin_read on public.tradexa_audit_log;
create policy tradexa_audit_admin_read on public.tradexa_audit_log for select using (public.tradexa_is_admin());

-- Add an immutable Supabase Auth owner to existing production tables if they
-- already exist. This preserves old rows (user_id remains NULL until the one-
-- time legacy migration assigns them to the first admin), while all new client
-- access is deny-by-default under RLS.
do $$
declare tbl text;
begin
  foreach tbl in array array[
    'webhook_events', 'positions', 'paper_trades', 'bot_logs', 'alerts',
    'trade_memories', 'memory_reviews', 'user_settings', 'paper_accounts',
    'paper_orders', 'paper_positions', 'journal', 'analytics', 'notifications',
    'strategies', 'backtests', 'watchlists', 'settings'
  ] loop
    if to_regclass('public.' || tbl) is not null then
      execute format('alter table public.%I add column if not exists user_id uuid references auth.users(id) on delete cascade', tbl);
      execute format('create index if not exists %I on public.%I(user_id)', 'tradexa_' || tbl || '_user_idx', tbl);
      execute format('alter table public.%I enable row level security', tbl);
      execute format('alter table public.%I force row level security', tbl);
      if not exists (select 1 from pg_policies where schemaname = 'public' and tablename = tbl and policyname = 'tradexa_owner_or_admin') then
        execute format('create policy tradexa_owner_or_admin on public.%I for all using (user_id = auth.uid() or public.tradexa_is_admin()) with check (user_id = auth.uid() or public.tradexa_is_admin())', tbl);
      end if;
    end if;
  end loop;
end;
$$;

-- The profile trigger applies only to accounts created after it was installed.
-- Backfill existing Supabase Auth accounts safely and idempotently.
insert into public.tradexa_profiles (id, full_name, avatar_url)
select id, coalesce(raw_user_meta_data->>'full_name', ''), raw_user_meta_data->>'avatar_url'
from auth.users
on conflict (id) do nothing;
