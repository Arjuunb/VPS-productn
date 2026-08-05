-- One-time migration. Replace the UUID below with the verified Supabase user
-- ID chosen as the first TradeLogX administrator, review the transaction, and
-- run it once after 0001_saas_auth.sql. Do not commit a real UUID to Git.
begin;

-- update public.tradexa_profiles set role = 'admin' where id = '<FIRST_ADMIN_UUID>'::uuid;

do $$
declare tbl text;
declare first_admin uuid := null; -- replace NULL with '<FIRST_ADMIN_UUID>'::uuid
begin
  if first_admin is null then
    raise exception 'Set first_admin before running the legacy ownership migration';
  end if;
  foreach tbl in array array[
    'webhook_events', 'positions', 'paper_trades', 'bot_logs', 'alerts',
    'trade_memories', 'memory_reviews', 'user_settings', 'paper_accounts',
    'paper_orders', 'paper_positions', 'journal', 'analytics', 'notifications',
    'strategies', 'backtests', 'watchlists', 'settings'
  ] loop
    if to_regclass('public.' || tbl) is not null then
      execute format('update public.%I set user_id = $1 where user_id is null', tbl) using first_admin;
    end if;
  end loop;
end;
$$;

commit;
