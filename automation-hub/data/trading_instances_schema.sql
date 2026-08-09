-- Trading Instance migration for Supabase/Postgres (additive; safe to rerun).
ALTER TABLE positions ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS instance_id TEXT NOT NULL DEFAULT '';
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS strategy_id TEXT NOT NULL DEFAULT '';
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
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'trading';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS state TEXT NOT NULL DEFAULT 'stopped';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS desired_running BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS last_error TEXT NOT NULL DEFAULT '';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS market_data_mode TEXT NOT NULL DEFAULT 'paper_forward';
-- Instance-owned execution configuration.  Existing rows retain the prior
-- paper defaults; no legacy global setting is copied or guessed.
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS sizing_mode TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS fixed_position_size DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS entry_mode TEXT NOT NULL DEFAULT 'limit';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS fill_model TEXT NOT NULL DEFAULT 'PerfectFill';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS execution_mode TEXT NOT NULL DEFAULT 'paper';
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS stopped_at TIMESTAMPTZ;
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS max_open_positions INTEGER NOT NULL DEFAULT 3;
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
