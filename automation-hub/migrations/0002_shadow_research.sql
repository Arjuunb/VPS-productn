-- Additive PA/SMC observational research schema.
-- Contains no paper account, real position, margin, or capacity table.
CREATE TABLE IF NOT EXISTS shadow_decisions(
  decision_id TEXT PRIMARY KEY, decision_key TEXT NOT NULL UNIQUE,
  engine TEXT NOT NULL, account_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
  strategy_version TEXT NOT NULL, config_hash TEXT NOT NULL,
  candle_id TEXT NOT NULL, action_class TEXT NOT NULL, direction TEXT,
  execution_class TEXT NOT NULL CHECK(execution_class='SHADOW'),
  blocker TEXT NOT NULL, decision_timestamp TEXT NOT NULL,
  snapshot_lineage TEXT NOT NULL, context_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_orders(
  order_id TEXT PRIMARY KEY, order_key TEXT NOT NULL UNIQUE,
  decision_id TEXT NOT NULL, symbol TEXT NOT NULL, order_type TEXT NOT NULL,
  side TEXT NOT NULL, requested_price REAL, stop_loss REAL,
  take_profit REAL, quantity REAL NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_fills(
  fill_id TEXT PRIMARY KEY, fill_key TEXT NOT NULL UNIQUE,
  order_id TEXT NOT NULL, quote_event_id TEXT NOT NULL,
  event_timestamp TEXT NOT NULL, sequence INTEGER NOT NULL,
  executable_side_price REAL NOT NULL, fill_price REAL NOT NULL,
  quantity REAL NOT NULL, spread_attribution REAL NOT NULL,
  spread_charged_again REAL NOT NULL DEFAULT 0, slippage REAL NOT NULL,
  commission REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_outcomes(
  outcome_id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE,
  exit_reason TEXT NOT NULL, exit_price REAL NOT NULL,
  gross_pnl REAL NOT NULL, commission REAL NOT NULL,
  slippage REAL NOT NULL, funding REAL NOT NULL, net_pnl REAL NOT NULL,
  gross_r REAL NOT NULL, net_r REAL NOT NULL, closed_at TEXT NOT NULL,
  validation_state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_mae_mfe(
  order_id TEXT PRIMARY KEY, mae_price REAL NOT NULL DEFAULT 0,
  mfe_price REAL NOT NULL DEFAULT 0, mae_pct REAL NOT NULL DEFAULT 0,
  mfe_pct REAL NOT NULL DEFAULT 0, mae_r REAL NOT NULL DEFAULT 0,
  mfe_r REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_funding(
  funding_key TEXT PRIMARY KEY, account_id TEXT NOT NULL,
  position_id TEXT NOT NULL, funding_timestamp TEXT NOT NULL,
  amount REAL NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_quote_cursors(
  symbol TEXT PRIMARY KEY, event_timestamp TEXT NOT NULL,
  sequence INTEGER NOT NULL, quote_event_id TEXT NOT NULL
);
