export type NexusPetState =
  | "running"
  | "analysing"
  | "signal-found"
  | "trade-open"
  | "trade-win"
  | "trade-loss"
  | "paused"
  | "warning"
  | "error"
  | "offline";

export type NexusPetId =
  | "sprig"
  | "pulse"
  | "orbit"
  | "glint"
  | "echo"
  | "nova"
  | "volt"
  | "kiro";

export type NexusPetSize = "small" | "medium" | "large";

export type NexusPetAppearance = {
  pet: NexusPetId;
  size: NexusPetSize;
};

export type NexusMarketData = {
  market_data_status?: string | null;
  last_market_data_timestamp?: string | null;
};

export type NexusDecision = {
  id?: string | null;
  decision?: string | null;
  verdict?: string | null;
  signal?: string | null;
  side?: string | null;
  created_at?: string | null;
  time?: string | null;
  ts?: string | null;
};

export type NexusInstance = {
  id: string;
  symbol: string;
  strategy_label: string;
  strategy_version?: string | null;
  timeframe: string;
  state: string;
  started_at?: string | null;
  last_error?: string | null;
  current_position?: { id?: string | null; opened_at?: string | null } | null;
  market_data?: NexusMarketData | null;
  engine?: {
    lifecycle_state?: string | null;
    last_error?: string | null;
    stop_reason?: string | null;
    started_at?: string | null;
    uptime_s?: number | null;
    last_heartbeat?: string | null;
  } | null;
  metrics?: { trades?: number | null; realized_pnl?: number | null } | null;
  last_decision?: NexusDecision | null;
};

export type NexusInstancesSnapshot = {
  instances: NexusInstance[];
  active_slots: number;
  max_active_slots: number;
  total_open_positions?: number | null;
  current_global_risk_amount?: number | null;
  max_global_risk_amount?: number | null;
  market_data_status?: string | null;
  global_risk_status?: string | null;
  global_status?: string | null;
};

export type NexusPetViewModel = {
  state: NexusPetState;
  statusLabel: string;
  statusDetail: string | null;
  instance: NexusInstance | null;
  runningInstances: number;
  maxActiveSlots: number | null;
  marketDataLabel: string;
  openPositions: number | null;
  currentRiskAmount: number | null;
  maxRiskAmount: number | null;
  uptimeSeconds: number | null;
  lastHeartbeat: string | null;
};
