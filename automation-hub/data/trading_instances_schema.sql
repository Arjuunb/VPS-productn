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
ALTER TABLE trading_instances ADD COLUMN IF NOT EXISTS market_data_mode TEXT NOT NULL DEFAULT 'paper_forward';
CREATE TABLE IF NOT EXISTS instance_metrics (
 instance_id TEXT PRIMARY KEY REFERENCES trading_instances(id) ON DELETE CASCADE,
 data_json JSONB NOT NULL, updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS instance_engine_logs (
 id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES trading_instances(id) ON DELETE CASCADE,
 ts TIMESTAMPTZ NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
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
 updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS trading_instance_platform_settings (
 id TEXT PRIMARY KEY, max_active_slots INTEGER NOT NULL DEFAULT 1,
 max_global_risk_pct DOUBLE PRECISION NOT NULL DEFAULT 0.02,
 max_global_daily_loss_pct DOUBLE PRECISION NOT NULL DEFAULT 0.05,
 updated_at TIMESTAMPTZ NOT NULL
);
ALTER TABLE trading_instance_platform_settings
 ADD COLUMN IF NOT EXISTS max_global_daily_loss_pct DOUBLE PRECISION NOT NULL DEFAULT 0.05;
CREATE INDEX IF NOT EXISTS idx_instance_pair_strategy ON trading_instances(symbol, strategy_key, strategy_version);
CREATE INDEX IF NOT EXISTS idx_instance_trade ON paper_trades(instance_id, closed_at);
CREATE INDEX IF NOT EXISTS idx_instance_position ON positions(instance_id, status);
CREATE INDEX IF NOT EXISTS idx_instance_logs ON bot_logs(instance_id, ts DESC);
