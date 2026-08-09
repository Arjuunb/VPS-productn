-- Trading Instance migration for Supabase/Postgres (additive; safe to rerun).
ALTER TABLE positions ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';
ALTER TABLE positions ADD COLUMN IF NOT EXISTS target DOUBLE PRECISION;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS management_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS target DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS strategy_id TEXT NOT NULL DEFAULT '';
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sizing_mode TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sizing_engine_version TEXT;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS risk_basis_at_entry DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS risk_pct_at_entry DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS risk_amount_at_entry DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS equity_before_trade DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS equity_after_close DOUBLE PRECISION;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS fees DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS realized_pnl DOUBLE PRECISION;
ALTER TABLE webhook_events ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';
ALTER TABLE bot_logs ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS trading_instances (
 id TEXT PRIMARY KEY, symbol TEXT NOT NULL, strategy_key TEXT NOT NULL,
 strategy_label TEXT NOT NULL, strategy_version TEXT NOT NULL, timeframe TEXT NOT NULL,
 risk_per_trade_pct DOUBLE PRECISION NOT NULL, capital_allocation DOUBLE PRECISION NOT NULL,
 mode TEXT NOT NULL, state TEXT NOT NULL, desired_running BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, last_error TEXT NOT NULL DEFAULT ''
);
-- CREATE TABLE IF NOT EXISTS does not repair a table from a partially applied
-- migration.  Keep every runtime column additive as well, so this file is the
-- one canonical recovery migration for existing production databases.
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS symbol TEXT NOT NULL DEFAULT '';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS strategy_key TEXT NOT NULL DEFAULT '';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS strategy_label TEXT NOT NULL DEFAULT '';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS strategy_version TEXT NOT NULL DEFAULT 'builtin-1';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS timeframe TEXT NOT NULL DEFAULT '5m';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS risk_per_trade_pct DOUBLE PRECISION NOT NULL DEFAULT 0.005;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS capital_allocation DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS exchange TEXT NOT NULL DEFAULT 'inherit';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS instrument_type TEXT NOT NULL DEFAULT 'spot';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'trading';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'stopped';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS desired_running BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS last_error TEXT NOT NULL DEFAULT '';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS market_data_mode TEXT NOT NULL DEFAULT 'paper_forward';
-- Instance-owned execution configuration.  Existing rows retain the prior
-- paper defaults; no legacy global setting is copied or guessed.
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS sizing_mode TEXT NOT NULL DEFAULT 'fixed_starting_equity_percent';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS fixed_position_size DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS fixed_quantity DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS profit_reinvestment BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS maximum_risk_amount DOUBLE PRECISION;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS minimum_equity DOUBLE PRECISION;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS starting_equity DOUBLE PRECISION;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS current_realized_equity DOUBLE PRECISION;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS risk_basis DOUBLE PRECISION;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS sizing_engine_version TEXT NOT NULL DEFAULT 'v2';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS entry_mode TEXT NOT NULL DEFAULT 'limit';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS fill_model TEXT NOT NULL DEFAULT 'RealisticFill';
-- Existing rows deliberately retain their recorded model. Only future inserts
-- that omit the explicit application value inherit realistic paper execution.
ALTER TABLE trading_instances ALTER COLUMN fill_model SET DEFAULT 'RealisticFill';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS execution_mode TEXT NOT NULL DEFAULT 'paper';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS stopped_at TIMESTAMPTZ;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS max_open_positions INTEGER NOT NULL DEFAULT 3;
-- Legacy "auto" used a fixed constructor-time equity snapshot. Preserve that
-- behaviour explicitly; operators must opt into dynamic compounding.
UPDATE trading_instances
SET sizing_mode = CASE
  WHEN sizing_mode IN ('auto', 'fixed_starting_equity_pct') THEN 'fixed_starting_equity_percent'
  WHEN sizing_mode IN ('fixed', 'fixed_position') THEN 'fixed_quantity'
  ELSE sizing_mode
END;
UPDATE trading_instances SET fixed_quantity = fixed_position_size
 WHERE fixed_quantity = 0 AND fixed_position_size > 0;
UPDATE trading_instances SET starting_equity = capital_allocation
 WHERE starting_equity IS NULL OR starting_equity <= 0;
UPDATE trading_instances SET current_realized_equity = capital_allocation
 WHERE current_realized_equity IS NULL;
UPDATE trading_instances SET risk_basis = capital_allocation
 WHERE risk_basis IS NULL;
ALTER TABLE trading_instances ALTER COLUMN sizing_mode SET DEFAULT 'fixed_starting_equity_percent';
CREATE TABLE IF NOT EXISTS instance_metrics (
 instance_id TEXT PRIMARY KEY REFERENCES trading_instances(id) ON DELETE CASCADE,
 data_json JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE instance_metrics ADD COLUMN IF NOT EXISTS data_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE instance_metrics ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE TABLE IF NOT EXISTS instance_engine_logs (
 id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES trading_instances(id) ON DELETE CASCADE,
 ts TIMESTAMPTZ NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
ALTER TABLE instance_engine_logs ADD COLUMN IF NOT EXISTS instance_id TEXT;
ALTER TABLE instance_engine_logs ADD COLUMN IF NOT EXISTS ts TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE instance_engine_logs ADD COLUMN IF NOT EXISTS level TEXT NOT NULL DEFAULT 'info';
ALTER TABLE instance_engine_logs ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT '';
-- Durable forward-paper cursor. Each active Paper Trading instance stores the
-- last closed candle it actually processed, so a restart can request only
-- newer candles and cannot replay historical opportunities as new trades.
CREATE TABLE IF NOT EXISTS instance_market_state (
 instance_id TEXT PRIMARY KEY REFERENCES trading_instances(id) ON DELETE CASCADE,
 last_processed_candle_timestamp TIMESTAMPTZ,
 market_data_mode TEXT NOT NULL DEFAULT 'paper_forward',
 market_data_status TEXT NOT NULL DEFAULT 'stopped',
 last_market_data_timestamp TIMESTAMPTZ,
 data_source TEXT,
 warmup_bars INTEGER NOT NULL DEFAULT 0,
 duplicate_candles INTEGER NOT NULL DEFAULT 0,
 missing_candles INTEGER NOT NULL DEFAULT 0,
 out_of_order_candles INTEGER NOT NULL DEFAULT 0,
 pending_orders_json JSONB NOT NULL DEFAULT '{}'::jsonb,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS last_processed_candle_timestamp TIMESTAMPTZ;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS market_data_mode TEXT NOT NULL DEFAULT 'paper_forward';
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS market_data_status TEXT NOT NULL DEFAULT 'stopped';
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS last_market_data_timestamp TIMESTAMPTZ;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS data_source TEXT;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS warmup_bars INTEGER NOT NULL DEFAULT 0;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS duplicate_candles INTEGER NOT NULL DEFAULT 0;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS missing_candles INTEGER NOT NULL DEFAULT 0;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS out_of_order_candles INTEGER NOT NULL DEFAULT 0;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS pending_orders_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE instance_market_state ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.instance_market_state'::regclass AND contype = 'p'
  ) THEN
    ALTER TABLE public.instance_market_state
      ADD CONSTRAINT instance_market_state_pkey PRIMARY KEY (instance_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'public.instance_market_state'::regclass
      AND contype = 'f' AND confrelid = 'public.trading_instances'::regclass
  ) THEN
    ALTER TABLE public.instance_market_state
      ADD CONSTRAINT instance_market_state_instance_id_fkey
      FOREIGN KEY (instance_id) REFERENCES public.trading_instances(id) ON DELETE CASCADE;
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS trading_instance_platform_settings (
 id TEXT PRIMARY KEY, max_active_slots INTEGER NOT NULL DEFAULT 1,
 max_global_risk_pct DOUBLE PRECISION NOT NULL DEFAULT 0.02,
 max_global_daily_loss_pct DOUBLE PRECISION NOT NULL DEFAULT 0.05,
 updated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE trading_instance_platform_settings
 ADD COLUMN IF NOT EXISTS max_global_daily_loss_pct DOUBLE PRECISION NOT NULL DEFAULT 0.05;
ALTER TABLE trading_instance_platform_settings
 ADD COLUMN IF NOT EXISTS paper_account_capital DOUBLE PRECISION NOT NULL DEFAULT 10000;
ALTER TABLE trading_instance_platform_settings
 ADD COLUMN IF NOT EXISTS max_active_slots INTEGER NOT NULL DEFAULT 1;
ALTER TABLE trading_instance_platform_settings
 ADD COLUMN IF NOT EXISTS max_global_risk_pct DOUBLE PRECISION NOT NULL DEFAULT 0.02;
ALTER TABLE trading_instance_platform_settings
 ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_instance_pair_strategy ON trading_instances(symbol, strategy_key, strategy_version);
CREATE UNIQUE INDEX IF NOT EXISTS idx_instance_market_state_owner ON instance_market_state(instance_id);
CREATE INDEX IF NOT EXISTS idx_instance_trade ON paper_trades(instance_id, closed_at);
CREATE INDEX IF NOT EXISTS idx_instance_position ON positions(instance_id, status);
CREATE INDEX IF NOT EXISTS idx_instance_logs ON bot_logs(instance_id, ts DESC);

-- Make newly created/altered relations visible to the REST API immediately.
NOTIFY pgrst, 'reload schema';
