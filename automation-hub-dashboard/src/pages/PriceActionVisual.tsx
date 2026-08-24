import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import NativeSMCChartOverlay, {
  type NativeCandle, type NativeSMCChartState, type NativeSMCOverlayFilters,
} from "../components/chart/NativeSMCChartOverlay";
import { apiDownload, apiGet, apiPostJson } from "../lib/api";
import { useApp } from "../app-context";

type Mode = "live" | "replay";
type BottomTab = "positions" | "orders" | "trades" | "setups" | "rejected" | "session" | "connection";
type ChartPreset = "clean" | "structure" | "zones" | "strategy" | "trades" | "debug";
type Direction = "bullish" | "bearish";
interface Swing { id: string; kind: "high" | "low"; price: number; occurred_at: string; confirmed_at: string; label: string }
interface Zone { id: string; role: "support" | "resistance"; low: number; high: number; created_at: string; confirmed_at: string; active: boolean; flipped: boolean; invalidated_at?: string | null; expiration_reason?: string | null; last_touch_at?: string | null; touch_count?: number; source_swing_ids?: string[] }
interface PAEvent { id: string; event_type: string; direction: Direction | "neutral"; level: number; occurred_at: string; confirmed_at: string; zone_id?: string | null; pattern?: string | null; reasons: string[] }
interface Condition { key: string; status: "PASS" | "MISSING"; detail: string; object_id?: string | null }
interface Trace { strategy_id: string; direction: Direction; state: string; conditions: Condition[]; missing_conditions: string[]; setup_id?: string | null; next_required_event: string }
interface Setup { id: string; strategy_id: string; direction: Direction; phase: string; created_at: string; zone_id?: string | null; trigger_event_id?: string | null; invalidation_reason?: string | null; reasons: string[]; missing_conditions: string[] }
interface Proposal { id: string; setup_id: string; strategy_id: string; direction: Direction; entry: number; stop: number; target: number; rr_ratio: number; signal_at: string; execution_allowed: false; paper_execution_allowed: true }
interface PASnapshot { candle_open: string; candle_close: string; structure_bias: Direction | "neutral"; pattern?: string | null; patterns?: { name: string; direction: Direction | "neutral" }[]; proposal_ids: string[]; strategy_traces: Trace[] }
interface PAMetrics { closed: number; wins: number; losses: number; unfilled: number; cancelled?: number; rejected?: number; gross_r?: number; net_r: number; costs_r: number; by_strategy?: Record<string, Omit<PAMetrics, "by_strategy">> }
interface DataIdentity { request_id?: string | null; session_id: string; mode: string; symbol: string; timeframe: string }
interface PAState {
  research_id: string; research_only: true; execution_allowed: false; paper_execution_allowed: true;
  symbol: string; timeframe: string; candles: NativeCandle[]; swings: Swing[]; zones: Zone[]; events: PAEvent[];
  setups: Setup[]; proposals: Proposal[]; snapshot: PASnapshot | null; selected_snapshot: PASnapshot | null;
  snapshot_ledger?: PASnapshot[];
  orders: Record<string, any>[]; trades: Record<string, any>[];
  metrics: PAMetrics; metrics_scope?: Record<string, any>; data_identity?: DataIdentity;
  forming_candle?: NativeCandle | null; live_display?: NativeSMCChartState["live_display"] & { last_update?: string | null; last_candle_update?: string | null; last_quote_update?: string | null; last_mark_update?: string | null; last_closed_update?: string | null; transport_state?: string; health_reason?: string; reliable?: boolean; quote_source?: string; candle_age_seconds?: number | null; quote_age_seconds?: number | null; mark_age_seconds?: number | null; closed_candle_age_seconds?: number | null; candle_quote_deviation_bps?: number | null };
  replay?: { cursor: number; total: number; future_candles_visible: false; has_next: boolean };
  data_provenance?: Record<string, string | number | boolean>;
}
interface PaperState {
  account_scope: string; currency: "USDT"; execution_mode: "PAPER"; real_funds: false; live_execution_allowed: false;
  session: { id: string; started_at: string; status: string; mode?: "LIVE_PAPER" | "HISTORICAL"; starting_balance: number; symbol?: string; timeframe?: string; operating_mode?: OperatingMode; execution_config?: { strategy_id?: string; risk_pct?: number } };
  account: { starting_balance: number; balance: number; equity: number; unrealized_pnl: number; fees_paid: number; free_margin: number; leverage: number };
  positions: Record<string, any>[]; orders: Record<string, any>[]; trades: Record<string, any>[];
  candidates: { proposal_id: string; source_proposal_id: string; status: string; payload: Record<string, any> }[];
  order_metadata: { order_id: string; status: string; config: { proposal: Proposal; entry: number; stop: number; target: number } }[];
  activity: Record<string, any>[];
  order_audit?: { session_id: string; pending_paper_orders: number; pending_strategy_orders: number; pending_manual_orders: number; duplicate_strategy_orders: Record<string, any>[]; discrepancies: Record<string, any>[]; manual_orders_are_never_auto_cancelled: boolean };
}
type OperatingMode = "signals_only" | "manual_approval" | "automatic";

const STRATEGIES = ["PA1_SR_REJECTION", "PA2_TREND_PULLBACK", "PA3_FLIP_RETEST", "PA4_FALSE_BREAK_REVERSAL"];
const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"];
const TABS: BottomTab[] = ["positions", "orders", "trades", "setups", "rejected", "session", "connection"];
const money = (value?: number) => Number(value ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const stamp = (value?: string | null) => value ? value.replace("T", " ").replace("+00:00", " UTC").slice(0, 22) : "—";
const pretty = (value: string) => value.replace(/^PA\d_/, "").replace(/_/g, " ");
const age = (value?: number | null) => value == null ? "—" : value < 1 ? "<1s" : `${value.toFixed(value < 10 ? 1 : 0)}s`;
const CLEAN_VISIBLE_BARS = 72;
const ACTIVE_PAPER_PLAN_STATUSES = new Set(["ORDER_PENDING", "PARTIALLY_FILLED", "ENTERED"]);
const OPEN_BROKER_ORDER_STATUSES = new Set(["open", "partially_filled", "triggered"]);
const TERMINAL_SETUP_PHASES = new Set(["STOPPED", "TARGET_HIT", "CANCELLED", "INVALIDATED", "EXPIRED"]);
const PRESET_FILTERS: Record<ChartPreset, NativeSMCOverlayFilters> = {
  clean: { pivots: false, internal: true, swing: true, structure: false, liquidity: false, fvg: false, orderBlocks: true, mitigated: false, labels: false },
  structure: { pivots: true, internal: true, swing: true, structure: true, liquidity: false, fvg: false, orderBlocks: false, mitigated: false, labels: false },
  zones: { pivots: false, internal: true, swing: true, structure: false, liquidity: false, fvg: false, orderBlocks: true, mitigated: false, labels: false },
  strategy: { pivots: false, internal: true, swing: true, structure: true, liquidity: false, fvg: false, orderBlocks: true, mitigated: false, labels: true },
  trades: { pivots: false, internal: true, swing: true, structure: false, liquidity: false, fvg: false, orderBlocks: true, mitigated: false, labels: true },
  debug: { pivots: true, internal: true, swing: true, structure: true, liquidity: true, fvg: false, orderBlocks: true, mitigated: true, labels: true },
};

function clusteredEvents(rows: PAEvent[], limit: number): PAEvent[] {
  const seen = new Set<string>();
  const result: PAEvent[] = [];
  for (const row of [...rows].reverse()) {
    const key = `${row.confirmed_at}|${row.event_type}|${row.zone_id ?? "market"}`;
    if (seen.has(key)) continue;
    seen.add(key); result.push(row);
    if (result.length >= limit) break;
  }
  return result.reverse();
}

function chartState(state: PAState, paper: PaperState | null, preset: ChartPreset,
                    focusedSetup?: Setup | null): NativeSMCChartState {
  const last = state.candles[state.candles.length - 1];
  const high = state.candles.length ? Math.max(...state.candles.map((row) => row.high)) : 0;
  const low = state.candles.length ? Math.min(...state.candles.map((row) => row.low)) : 0;
  const snap = state.snapshot ? {
    id: `pa-chart-${state.snapshot.candle_open}`, candle_open: state.snapshot.candle_open, candle_close: state.snapshot.candle_close,
    htf_bias: 0, htf_ema: null, swing_bias: state.snapshot.structure_bias === "bullish" ? 1 : state.snapshot.structure_bias === "bearish" ? -1 : 0,
    internal_bias: 0, session: "24/7", dealing_range: { high, low, equilibrium: (high + low) / 2, area: "native zones" },
    price_action: { bullish_rejection: false, bearish_rejection: false, body: last ? Math.abs(last.close - last.open) : 0, upper_wick: 0, lower_wick: 0 },
    active_setup_id: focusedSetup?.id ?? null, setup_phase: focusedSetup?.phase ?? null, next_required_event: "See strategy trace", latest_sweep_id: null,
    event_ids: state.events.map((row) => row.id), active_fvg_ids: [], active_ob_ids: state.zones.filter((row) => row.active).map((row) => row.id), proposal_ids: state.snapshot.proposal_ids,
  } : null;
  const candleByTimestamp = new Map(state.candles.map((row) => [row.timestamp, row]));
  const patternEvents = preset === "debug" ? (state.snapshot_ledger ?? []).flatMap((snapshot) => (snapshot.patterns ?? []).map((pattern, index) => {
    const candle = candleByTimestamp.get(snapshot.candle_open);
    return { id: `pattern-${snapshot.candle_open}-${index}`, event_type: pattern.name,
      direction: pattern.direction, level: candle?.close ?? 0, occurred_at: snapshot.candle_open,
      confirmed_at: snapshot.candle_close, zone_id: null, pattern: pattern.name, reasons: ["completed-candle pattern metadata"] };
  })) : [];
  const cleanCandles = state.candles.slice(-120);
  const cleanHigh = cleanCandles.length ? Math.max(...cleanCandles.map((row) => row.high)) : high;
  const cleanLow = cleanCandles.length ? Math.min(...cleanCandles.map((row) => row.low)) : low;
  const cleanSpan = Math.max(cleanHigh - cleanLow, Math.abs(last?.close ?? 0) * .002, 1);
  const latestPrice = state.forming_candle?.close ?? last?.close ?? 0;
  const nearbyActiveZones = state.zones
    .filter((row) => row.active && row.high >= cleanLow - cleanSpan * .25 && row.low <= cleanHigh + cleanSpan * .25)
    .sort((left, right) => {
      const leftDistance = Math.abs((left.high + left.low) / 2 - latestPrice);
      const rightDistance = Math.abs((right.high + right.low) / 2 - latestPrice);
      return leftDistance - rightDistance || right.confirmed_at.localeCompare(left.confirmed_at);
    });
  let cleanZones = [
    ...nearbyActiveZones.filter((row) => row.role === "support").slice(0, 4),
    ...nearbyActiveZones.filter((row) => row.role === "resistance").slice(0, 4),
  ].sort((left, right) => left.confirmed_at.localeCompare(right.confirmed_at));
  const focusedZone = focusedSetup?.zone_id ? state.zones.find((row) => row.id === focusedSetup.zone_id) : null;
  if (focusedZone && !cleanZones.some((row) => row.id === focusedZone.id)) cleanZones = [...cleanZones, focusedZone];
  const recentStart = state.candles[Math.max(0, state.candles.length - 120)]?.timestamp;
  const researchPlans = focusedSetup
    ? state.proposals.filter((row) => row.setup_id === focusedSetup.id)
    : preset === "debug" ? state.proposals
    : preset === "trades" ? state.proposals.filter((row) => !recentStart || row.signal_at >= recentStart).slice(-8)
    : state.proposals.filter((row) => !recentStart || row.signal_at >= recentStart).slice(-1);
  const paperPlans = (paper?.order_metadata ?? [])
    .filter((row) => (preset === "debug" || ACTIVE_PAPER_PLAN_STATUSES.has(row.status)) &&
      (!focusedSetup || row.config.proposal.setup_id === focusedSetup.id))
    .map((row) => ({
    ...row.config.proposal, id: `paper-${row.order_id}`, snapshot_id: `paper-${row.order_id}`,
    entry: row.config.entry, stop: row.config.stop, target: row.config.target,
    risk_status: `PAPER_${row.status}`,
  }));
  const focusObjectIds = new Set([
    focusedSetup?.zone_id, focusedSetup?.trigger_event_id,
    ...(focusedZone?.source_swing_ids ?? []),
  ].filter(Boolean));
  const visibleSwings = focusedSetup
    ? state.swings.filter((row) => focusObjectIds.has(row.id))
    : preset === "debug" ? state.swings : state.swings.slice(-24);
  const visibleEvents = focusedSetup
    ? state.events.filter((row) => focusObjectIds.has(row.id) || row.zone_id === focusedSetup.zone_id)
    : clusteredEvents(state.events, preset === "debug" ? 240 : preset === "structure" ? 64 : 20);
  const chartZones = focusedZone ? [focusedZone] : preset === "debug" ? state.zones : cleanZones;
  return {
    research_id: state.research_id, execution_allowed: false, candles: state.candles,
    pivots: visibleSwings.map((row) => ({ ...row, scope: "swing", strength: row.label === "HH" || row.label === "LL" ? "strong" : "weak" })),
    events: [...visibleEvents, ...patternEvents].map((row) => ({ ...row, scope: "swing", label: pretty(row.event_type) })),
    fair_value_gaps: [],
    order_blocks: chartZones.map((row) => ({ id: row.id, direction: row.role === "support" ? "bullish" : "bearish", label: `${row.active ? "ACTIVE" : row.expiration_reason ? "EXPIRED" : "INVALIDATED"} · ${row.flipped ? "FLIPPED " : ""}${row.role.toUpperCase()}`, low: row.low, high: row.high, created_at: row.confirmed_at, active: row.active, mitigated: !row.active, mitigation_at: row.invalidated_at, lifecycle: row.active ? "active" : row.expiration_reason ? "expired" : "invalidated" })),
    proposals: [...researchPlans.map((row) => ({ ...row, snapshot_id: `pa-chart-${row.signal_at}`, risk_status: "SIGNAL_ONLY" })), ...paperPlans],
    snapshot: snap, selected_snapshot: snap, snapshot_ledger: snap ? [snap] : [],
    forming_candle: state.forming_candle, live_display: state.live_display,
  };
}

function DataTable({ rows, empty }: { rows: Record<string, any>[]; empty: string }) {
  if (!rows.length) return <div className="pa-empty">{empty}</div>;
  const columns = Object.keys(rows[0]).slice(0, 8);
  return <div className="pa-table-wrap"><table className="pa-table"><thead><tr>{columns.map((key) => <th key={key}>{pretty(key)}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={String(row.id ?? i)}>{columns.map((key) => <td key={key}>{typeof row[key] === "number" ? Number(row[key]).toLocaleString(undefined, { maximumFractionDigits: 6 }) : String(row[key] ?? "—")}</td>)}</tr>)}</tbody></table></div>;
}

export default function PriceActionVisual() {
  const { toast } = useApp();
  const [mode, setMode] = useState<Mode>("live");
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("5m");
  const [contracts, setContracts] = useState(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  const [state, setState] = useState<PAState | null>(null);
  const [paper, setPaper] = useState<PaperState | null>(null);
  const [sessions, setSessions] = useState<{ id: string; status: string; symbol: string; timeframe: string; started_at: string }[]>([]);
  const [selectedSession, setSelectedSession] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState(200);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const [tab, setTab] = useState<BottomTab>("positions");
  const [fitSignal, setFitSignal] = useState(0);
  const [latestSignal, setLatestSignal] = useState(0);
  const [visibleBars, setVisibleBars] = useState(CLEAN_VISIBLE_BARS);
  const [chartPreset, setChartPreset] = useState<ChartPreset>("clean");
  const [selectedSetupId, setSelectedSetupId] = useState("");
  const [focusedSetupId, setFocusedSetupId] = useState("");
  const [marketBusy, setMarketBusy] = useState(false);
  const [pendingMarket, setPendingMarket] = useState<{ symbol: string; timeframe: string; mode: Mode } | null>(null);
  const [activeStrategy, setActiveStrategy] = useState(STRATEGIES[0]);
  const [operatingMode, setOperatingMode] = useState<OperatingMode>("signals_only");
  const [riskPct, setRiskPct] = useState("0.5");
  const [filters, setFilters] = useState<NativeSMCOverlayFilters>(PRESET_FILTERS.clean);
  const [order, setOrder] = useState({ side: "buy", type: "market", quantity: "0.001", limit_price: "" });
  const [controlsOpen, setControlsOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 820);
  const identityInitialized = useRef(false);
  const chartRequestSequence = useRef(0);
  const marketSwitchSequence = useRef(0);
  const marketSwitchQueue = useRef<Promise<void>>(Promise.resolve());

  const applyPaperIdentity = useCallback((next: PaperState) => {
    const nextMode: Mode = next.session.mode === "HISTORICAL" ? "replay" : "live";
    setSymbol(next.session.symbol ?? "BTCUSDT");
    setTimeframe(next.session.timeframe ?? "5m");
    setMode(nextMode);
    setOperatingMode(next.session.operating_mode ?? "signals_only");
    setActiveStrategy(next.session.execution_config?.strategy_id ?? STRATEGIES[0]);
    setRiskPct(String(next.session.execution_config?.risk_pct ?? .5));
  }, []);

  const loadPaper = useCallback(async () => {
    try {
      const [next, history] = await Promise.all([
        apiGet<PaperState>("/research/price-action/paper"),
        apiGet<{ sessions: typeof sessions }>("/research/price-action/sessions"),
      ]);
      setPaper(next);
      setSessions(history.sessions); setSelectedSession((current) => current || next.session.id);
      if (!identityInitialized.current) {
        identityInitialized.current = true;
        applyPaperIdentity(next);
      }
    } catch { /* chart remains usable */ }
  }, [applyPaperIdentity]);
  const loadChart = useCallback(async () => {
    if (!paper?.session.id) return;
    const sequence = ++chartRequestSequence.current;
    const requestId = `${paper.session.id}:${mode}:${symbol}:${timeframe}:${sequence}`;
    setLoading(true);
    const path = mode === "live"
      ? `/research/price-action/live-chart?symbol=${symbol}&timeframe=${timeframe}&window=800&visible=500&request_id=${encodeURIComponent(requestId)}`
      : `/research/price-action/sessions/current/replay/step?symbol=${symbol}&timeframe=${timeframe}&cursor=${cursor}&limit=3000`;
    try {
      const next = mode === "live" ? await apiGet<PAState>(path) : await apiPostJson<PAState>(path, {});
      if (sequence !== chartRequestSequence.current) return;
      const identity = next.data_identity;
      const expectedSessionMode = mode === "live" ? "LIVE_PAPER" : "HISTORICAL";
      const matches = next.symbol === symbol && next.timeframe === timeframe &&
        identity?.session_id === paper.session.id && identity.symbol === symbol &&
        identity.timeframe === timeframe && identity.mode === expectedSessionMode &&
        (mode !== "live" || identity.request_id === requestId);
      if (!matches) throw new Error("Ignored stale market-data response with a different session, symbol or timeframe identity");
      setState(next); setError(null);
    } catch (reason) {
      if (sequence === chartRequestSequence.current) {
        setError(reason instanceof Error ? reason.message : "Price Action data unavailable");
      }
    } finally {
      if (sequence === chartRequestSequence.current) setLoading(false);
    }
  }, [mode, symbol, timeframe, cursor, paper?.session.id]);

  useEffect(() => { void apiGet<{ contracts: string[] }>("/research/price-action/contracts?limit=500").then((row) => setContracts(row.contracts)).catch(() => undefined); }, []);
  useEffect(() => { if (!identityInitialized.current || !paper?.session.id) return; void loadChart(); if (mode !== "live") return; const timer = window.setInterval(() => void loadChart(), 3_000); return () => window.clearInterval(timer); }, [loadChart, mode, paper?.session.id]);
  useEffect(() => { void loadPaper(); const timer = window.setInterval(() => void loadPaper(), 5_000); return () => window.clearInterval(timer); }, [loadPaper]);
  useEffect(() => {
    if (mode !== "replay" || !replayPlaying) return;
    const timer = window.setInterval(() => setCursor((value) => {
      const total = state?.replay?.total ?? value;
      if (value >= total) { setReplayPlaying(false); return value; }
      return Math.min(total, value + 1);
    }), Math.max(40, 1000 / replaySpeed));
    return () => window.clearInterval(timer);
  }, [mode, replayPlaying, replaySpeed, state?.replay?.total]);
  const displayed = useMemo(() => state ? {
    ...state,
    setups: state.setups.filter((row) => row.strategy_id === activeStrategy),
    proposals: state.proposals.filter((row) => row.strategy_id === activeStrategy),
  } : null, [state, activeStrategy]);
  const setupChoices = useMemo(() => (displayed?.setups ?? []).slice(-100).reverse(), [displayed?.setups]);
  const selectedSetup = useMemo(() => setupChoices.find((row) => row.id === selectedSetupId) ??
    setupChoices.find((row) => !TERMINAL_SETUP_PHASES.has(row.phase)) ?? setupChoices[0] ?? null,
  [setupChoices, selectedSetupId]);
  const focusedSetup = useMemo(() => setupChoices.find((row) => row.id === focusedSetupId) ?? null,
    [setupChoices, focusedSetupId]);
  const chart = useMemo(() => displayed ? chartState(displayed, paper, chartPreset, focusedSetup) : null,
    [displayed, paper, chartPreset, focusedSetup]);
  const traces = state?.snapshot?.strategy_traces.filter((row) => row.strategy_id === activeStrategy) ?? [];
  const ready = traces.filter((row) => row.state === "ENTRY_READY");
  const rejected = traces.filter((row) => row.state !== "ENTRY_READY").map((row) => ({ strategy: row.strategy_id, direction: row.direction, missing: row.missing_conditions.join(", "), next: row.next_required_event }));
  const pendingPaperOrders = (paper?.orders ?? []).filter((row) => OPEN_BROKER_ORDER_STATUSES.has(String(row.status)));
  const researchOrderRows = (state?.orders ?? []).map((row) => ({ scope: "RESEARCH ENGINE · NOT PAPER BROKER", ...row }));
  const tradeRows = [
    ...(paper?.trades ?? []).map((row) => ({ scope: "PAPER", ...row })),
    ...(state?.trades ?? []).map((row) => ({ scope: "RESEARCH", ...row })),
  ];
  const selectedMetrics = state?.metrics.by_strategy?.[activeStrategy];
  const aggregateMetrics = state?.metrics;
  const healthState = mode === "replay" ? "REPLAY" : state?.live_display?.connection_state ?? "CONNECTING";
  const feedReliable = mode === "replay" || state?.live_display?.reliable === true;
  const lastClosed = state?.candles[state.candles.length - 1];
  const marketSelection = pendingMarket ?? { symbol, timeframe, mode };
  const focusedObjectIds = focusedSetup ? [focusedSetup.id, focusedSetup.zone_id, focusedSetup.trigger_event_id].filter((row): row is string => Boolean(row)) : [];

  const submitOrder = async () => {
    try {
      await apiPostJson("/research/price-action/paper/orders", {
        symbol, side: order.side, type: order.type, quantity: Number(order.quantity),
        limit_price: order.type === "limit" && order.limit_price ? Number(order.limit_price) : null,
        stop_price: order.type === "stop" && order.limit_price ? Number(order.limit_price) : null,
      });
      toast("Price Action paper order accepted", "success"); setTab("orders"); await loadPaper();
    } catch (reason) { toast(reason instanceof Error ? reason.message : "Order failed", "error"); }
  };
  const setLeverage = async (leverage: number) => {
    try { setPaper(await apiPostJson<PaperState>("/research/price-action/paper/leverage", { leverage })); toast(`Price Action leverage set to ${leverage}x`, "success"); }
    catch (reason) { toast(reason instanceof Error ? reason.message : "Leverage update failed", "error"); }
  };
  const toggleLayer = (key: keyof NativeSMCOverlayFilters) => setFilters((row) => ({ ...row, [key]: !row[key] }));
  const changeVisibleBars = (value: number) => {
    setVisibleBars(value);
    window.requestAnimationFrame(() => setFitSignal((signal) => signal + 1));
  };
  const applyPreset = (preset: ChartPreset) => {
    setChartPreset(preset);
    setFilters(PRESET_FILTERS[preset]);
    if (preset !== "strategy") setFocusedSetupId("");
    if (preset === "clean") setVisibleBars(CLEAN_VISIBLE_BARS);
    window.requestAnimationFrame(() => {
      setFitSignal((signal) => signal + 1);
      setLatestSignal((signal) => signal + 1);
    });
  };
  const focusSelectedSetup = () => {
    if (!selectedSetup) return;
    setFocusedSetupId(selectedSetup.id);
    applyPreset("strategy");
  };
  const applyAutomation = async () => {
    try {
      const updated = await apiPostJson<PaperState>("/research/price-action/sessions/current/configuration", {
        mode: mode === "live" ? "LIVE_PAPER" : "HISTORICAL", symbol, timeframe,
        operating_mode: operatingMode, strategy_id: activeStrategy, risk_pct: Number(riskPct),
      });
      setPaper(updated); toast(`Paper mode set to ${operatingMode.replace(/_/g, " ")}`, "success");
    } catch (reason) { toast(reason instanceof Error ? reason.message : "Configuration failed", "error"); }
  };
  const approve = async (proposalId: string) => {
    try { await apiPostJson(`/research/price-action/paper/candidates/${proposalId}/approve`, {}); toast("Paper candidate approved", "success"); await loadPaper(); }
    catch (reason) { toast(reason instanceof Error ? reason.message : "Approval failed", "error"); }
  };
  const resetSession = async () => {
    const phrase = window.prompt("Reset this Price Action paper session? Type RESET PRICE ACTION PAPER exactly. Prior session snapshots and audit records are preserved.");
    if (phrase !== "RESET PRICE ACTION PAPER") return;
    try {
      const updated = await apiPostJson<PaperState>("/research/price-action/paper/reset", { confirmation: phrase });
      identityInitialized.current = true; chartRequestSequence.current += 1;
      setPaper(updated); applyPaperIdentity(updated); setState(null);
      toast("Fresh Price Action paper session created", "success");
    }
    catch (reason) { toast(reason instanceof Error ? reason.message : "Reset failed", "error"); }
  };
  const sessionAction = async (action: "start" | "resume" | "duplicate" | "end") => {
    try {
      let updated: PaperState;
      if (action === "start") updated = await apiPostJson<PaperState>("/research/price-action/sessions", { mode: mode === "live" ? "LIVE_PAPER" : "HISTORICAL", symbol, timeframe, starting_balance: paper?.account.starting_balance ?? 10_000, operating_mode: operatingMode, strategy_id: activeStrategy, risk_pct: Number(riskPct) });
      else if (action === "end") { await apiPostJson("/research/price-action/sessions/current/end", {}); await loadPaper(); toast("Session ended and snapshotted", "success"); return; }
      else updated = await apiPostJson<PaperState>(`/research/price-action/sessions/${selectedSession}/${action}`, {});
      identityInitialized.current = true; chartRequestSequence.current += 1;
      setPaper(updated); applyPaperIdentity(updated); setState(null);
      setSelectedSession(updated.session.id); await loadPaper(); toast(`Session ${action} complete`, "success");
    } catch (reason) { toast(reason instanceof Error ? reason.message : `Session ${action} failed`, "error"); }
  };
  const confirmMarketChange = (nextSymbol: string, nextTimeframe: string, nextMode: Mode = mode) => {
    if (nextSymbol === symbol && nextTimeframe === timeframe && nextMode === mode) return;
    const exposed = Boolean((paper?.positions.length ?? 0) + (paper?.orders.filter((row) => ["open", "partially_filled", "triggered"].includes(String(row.status))).length ?? 0));
    if (exposed) {
      toast("Close or cancel this Price Action session's paper exposure before changing its market, timeframe or mode", "error");
      return;
    }
    const sequence = ++marketSwitchSequence.current;
    setPendingMarket({ symbol: nextSymbol, timeframe: nextTimeframe, mode: nextMode });
    setMarketBusy(true);
    marketSwitchQueue.current = marketSwitchQueue.current.catch(() => undefined).then(async () => {
      if (sequence !== marketSwitchSequence.current) return;
      try {
        const updated = await apiPostJson<PaperState>("/research/price-action/sessions/current/configuration", {
          mode: nextMode === "live" ? "LIVE_PAPER" : "HISTORICAL", symbol: nextSymbol,
          timeframe: nextTimeframe, operating_mode: operatingMode,
          strategy_id: activeStrategy, risk_pct: Number(riskPct),
        });
        if (sequence !== marketSwitchSequence.current) return;
        chartRequestSequence.current += 1;
        setPaper(updated); setSymbol(nextSymbol); setTimeframe(nextTimeframe); setMode(nextMode);
        setState(null); setError(null); setCursor(200); setFocusedSetupId("");
        toast(`Price Action session synchronized to ${nextSymbol} ${nextTimeframe}`, "success");
      } catch (reason) {
        if (sequence === marketSwitchSequence.current) toast(reason instanceof Error ? reason.message : "Market synchronization failed", "error");
      } finally {
        if (sequence === marketSwitchSequence.current) { setPendingMarket(null); setMarketBusy(false); }
      }
    });
  };

  const reconcileOrders = async () => {
    try {
      const result = await apiPostJson<{ actions: Record<string, any>[]; after: PaperState["order_audit"] }>("/research/price-action/paper/orders/reconcile", {});
      toast(result.actions.length ? `${result.actions.length} stale strategy order record(s) reconciled` : "Pending paper orders are already reconciled", "success");
      await loadPaper();
    } catch (reason) { toast(reason instanceof Error ? reason.message : "Order reconciliation failed", "error"); }
  };

  return <div className="pa-lab">
    <header className="pa-titlebar">
      <div><span className="pa-kicker">NATIVE RESEARCH TERMINAL</span><h1>Price Action Visual Lab</h1><p>One closed-candle engine · historical replay + live paper observation</p></div>
      <button type="button" className="pa-controls-toggle" onClick={() => setControlsOpen((open) => !open)} aria-expanded={controlsOpen}>Controls</button>
      <div className="pa-safety"><b>PAPER ONLY</b><span>REAL ORDERS DISABLED</span></div>
    </header>

    <div className="pa-workspace">
      <aside className={`pa-sidebar ${controlsOpen ? "is-open" : ""}`} aria-label="Price Action controls">
        <section><h2>PA session market</h2><label>Binance USDⓈ-M contract<select aria-label="Price Action session symbol" disabled={marketBusy} value={marketSelection.symbol} onChange={(event) => confirmMarketChange(event.target.value, timeframe)}>{contracts.map((row) => <option key={row}>{row}</option>)}</select></label><div className="pa-segment"><button disabled={marketBusy} className={marketSelection.mode === "live" ? "active" : ""} onClick={() => confirmMarketChange(symbol, timeframe, "live")}>Live paper</button><button disabled={marketBusy} className={marketSelection.mode === "replay" ? "active" : ""} onClick={() => confirmMarketChange(symbol, timeframe, "replay")}>Replay</button></div><small className="pa-context-note">Independent Price Action research session. The global header remains the selected Trading Instance context.</small>{marketBusy ? <small className="pa-syncing">Synchronizing session, feed and chart…</small> : null}</section>
        <section><h2>Strategy &amp; execution</h2><label>Visible automated strategy<select value={activeStrategy} onChange={(event) => { setActiveStrategy(event.target.value); setFocusedSetupId(""); }}>{STRATEGIES.map((id) => <option key={id} value={id}>{pretty(id)}</option>)}</select></label><label>Paper operating mode<select value={operatingMode} onChange={(event) => setOperatingMode(event.target.value as OperatingMode)}><option value="signals_only">Signals only</option><option value="manual_approval">Manual approval</option><option value="automatic">Automatic paper</option></select></label><label>Risk per trade (%)<input value={riskPct} onChange={(event) => setRiskPct(event.target.value)} inputMode="decimal" /></label><button type="button" className="pa-export" onClick={() => void applyAutomation()}>Apply paper configuration</button><small>Visible metrics follow this strategy. Paper execution changes only after Apply; existing orders retain immutable snapshots.</small></section>
        <section><h2>Chart layers</h2><label className="pa-layer-mode">Preset<select aria-label="Chart layer preset" value={chartPreset} onChange={(event) => applyPreset(event.target.value as ChartPreset)}>{(["clean", "structure", "zones", "strategy", "trades", "debug"] as ChartPreset[]).map((preset) => <option key={preset} value={preset}>{pretty(preset)}</option>)}</select></label>{([['pivots','Swings'], ['structure','Events'], ['orderBlocks','S/R zones'], ['mitigated','Invalidated · lifecycle'], ['labels','Labels']] as [keyof NativeSMCOverlayFilters, string][]).map(([key, label]) => <label className="pa-check" key={key}><input type="checkbox" checked={filters[key]} onChange={() => toggleLayer(key)} /><span>{label}</span></label>)}<label className="pa-layer-mode">Selected setup<select aria-label="Selected Price Action setup" value={selectedSetup?.id ?? ""} onChange={(event) => setSelectedSetupId(event.target.value)}><option value="">Latest relevant setup</option>{setupChoices.map((row) => <option key={row.id} value={row.id}>{pretty(row.strategy_id)} · {row.direction} · {row.phase}</option>)}</select></label><button className="pa-focus-setup" disabled={!selectedSetup} onClick={focusSelectedSetup}>Focus selected setup</button><small>Presets affect rendering only. All zones, setups, orders and trades remain in the audit records below.</small></section>
        <section><h2>Virtual account</h2><div className="pa-account"><span>Balance<b>{money(paper?.account.balance)} USDT</b></span><span>Equity<b>{money(paper?.account.equity)} USDT</b></span><span>Open P&amp;L<b className={(paper?.account.unrealized_pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(paper?.account.unrealized_pnl)}</b></span><span>Free margin<b>{money(paper?.account.free_margin)}</b></span></div><label>Isolated leverage<select value={paper?.account.leverage ?? 1} onChange={(event) => void setLeverage(Number(event.target.value))}>{Array.from({ length: 20 }, (_, index) => index + 1).map((value) => <option key={value} value={value}>{value}×</option>)}</select></label><small>Persistent and isolated from every other paper account.</small></section>
        <section className="pa-legend"><h2>Chart truth</h2><span><i className="confirmed" />Confirmed</span><span><i className="provisional" />Forming · display only</span><span><i className="invalid" />Invalidated</span></section>
      </aside>

      <main className="pa-main">
        <div className="pa-toolbar"><div className="pa-symbol"><i className={feedReliable && !error ? "live" : "stale"} />{symbol}<span>PA SESSION · PERPETUAL</span></div><div className="pa-timeframes">{TIMEFRAMES.map((row) => <button key={row} disabled={marketBusy} className={row === marketSelection.timeframe ? "active" : ""} onClick={() => confirmMarketChange(symbol, row)}>{row}</button>)}</div><label className="pa-view-bars">View<select aria-label="Visible chart candles" value={visibleBars} onChange={(event) => changeVisibleBars(Number(event.target.value))}>{[48, 72, 120, 240].map((value) => <option key={value} value={value}>{value} bars</option>)}</select></label><button onClick={() => setFitSignal((n) => n + 1)}>Fit</button><button onClick={() => setLatestSignal((n) => n + 1)}>Latest</button><button type="button" className="pa-clean-view" onClick={() => applyPreset("clean")}>Clean view</button><span className={`pa-mode-chip ${feedReliable ? "" : "is-stale"}`}>{mode === "live" ? healthState : "REPLAY"}</span></div>
        {mode === "replay" ? <div className="pa-replay"><button onClick={() => setCursor(1)}>Restart</button><button onClick={() => setReplayPlaying((value) => !value)}>{replayPlaying ? "Pause" : "Play"}</button><button onClick={() => setCursor((n) => Math.max(1, n - 1))}>◀</button><input aria-label="Replay candle cursor" type="range" min="1" max={state?.replay?.total ?? 1000} value={Math.min(cursor, state?.replay?.total ?? cursor)} onChange={(event) => setCursor(Number(event.target.value))} /><button disabled={!state?.replay?.has_next} onClick={() => setCursor((n) => n + 1)}>▶</button><select aria-label="Replay speed" value={replaySpeed} onChange={(event) => setReplaySpeed(Number(event.target.value))}>{[1, 2, 5, 10, 25, 100].map((value) => <option key={value} value={value}>{value === 100 ? "Maximum" : `${value}×`}</option>)}</select><span>Candle {state?.replay?.cursor ?? cursor} / {state?.replay?.total ?? "—"} · future bars hidden</span></div> : null}
        <div className="pa-chart-shell">
          <div className="pa-chart-head"><div><b>{symbol} · {timeframe}</b><span>{state?.data_provenance?.exchange ?? "Binance USDⓈ-M Futures"} · session {paper?.session.id?.slice(0, 8) ?? "loading"}</span></div><div><span>{selectedMetrics ? `${pretty(activeStrategy)} · Net ${selectedMetrics.net_r.toFixed(2)}R · Execution R ${Number(selectedMetrics.gross_r ?? 0).toFixed(2)}R · Commission ${selectedMetrics.costs_r.toFixed(2)}R · ${selectedMetrics.wins}W/${selectedMetrics.losses}L · ${selectedMetrics.unfilled} unfilled` : `${pretty(activeStrategy)} · metric scope unavailable`}</span><span>{state?.snapshot?.structure_bias?.toUpperCase() ?? "NEUTRAL"}</span><b>{ready.length ? `${ready.length} READY` : "WAIT"}</b></div></div>
          {aggregateMetrics ? <div className="pa-metric-scope"><b>Selected strategy shown above</b><span>All PA1–PA4 aggregate remains {aggregateMetrics.net_r.toFixed(2)}R across {aggregateMetrics.closed} closed trades; it is not the selected-strategy result.</span><span>Dataset {String(state?.metrics_scope?.dataset_start ?? "—")} → {String(state?.metrics_scope?.dataset_end ?? "—")}</span><span>Config {String(state?.metrics_scope?.configuration_id ?? "—").slice(0, 12)} · funding {String(state?.metrics_scope?.cost_model?.funding_coverage ?? "—")}</span></div> : null}
          {error ? <div className="pa-error"><b>Market data unavailable</b><span>{error}</span><button onClick={() => void loadChart()}>Retry</button></div> : null}
          {!chart ? <div className="pa-loading">{loading ? "Loading and reconciling Binance market streams…" : "No identity-verified candle state"}</div> : <NativeSMCChartOverlay state={chart} timeframe={timeframe} rightOffsetBars={8} initialVisibleBars={visibleBars} filters={filters} highlightedObjectIds={focusedObjectIds} centerTimestamp={focusedSetup?.created_at} onCandleSelect={() => undefined} fitContentSignal={fitSignal} latestSignal={latestSignal} modelLabel="native price action" height="clamp(520px, 58vh, 680px)" liveDataStale={!feedReliable || Boolean(error)} />}
          <div className={`pa-stream-truth ${feedReliable ? "is-healthy" : "is-stale"}`}><b>{healthState}</b><span>{mode === "replay" ? "Historical replay is isolated from live streams" : state?.live_display?.health_reason ?? "Waiting for identity-bound feed reconciliation"}</span><span>Transport {state?.live_display?.transport_state ?? "—"} · entries {state?.live_display?.new_entries_paused ? "PAUSED" : "ELIGIBLE ON CLOSED BARS"}</span></div>
          <div className="pa-market-readout"><span>Last completed candle<b>{lastClosed ? `${stamp(lastClosed.timestamp)} · C ${lastClosed.close.toLocaleString()}` : "—"}</b><small>Age {age(state?.live_display?.closed_candle_age_seconds)}</small></span><span>Forming candle · display only<b>{state?.forming_candle ? `${stamp(state.forming_candle.timestamp)} · O ${state.forming_candle.open.toLocaleString()} H ${state.forming_candle.high.toLocaleString()} L ${state.forming_candle.low.toLocaleString()} C ${state.forming_candle.close.toLocaleString()}` : "Not available"}</b><small>Stream age {age(state?.live_display?.candle_age_seconds)}</small></span><span>Live bid / ask<b>{state?.live_display?.bid?.toLocaleString() ?? "—"} / {state?.live_display?.ask?.toLocaleString() ?? "—"}</b><small>Age {age(state?.live_display?.quote_age_seconds)}</small></span><span>Mark price<b>{state?.live_display?.mark?.toLocaleString() ?? "—"}</b><small>Age {age(state?.live_display?.mark_age_seconds)} · deviation {state?.live_display?.candle_quote_deviation_bps?.toFixed(2) ?? "—"} bps</small></span></div>
          <div className="pa-chart-foot"><span><i className={feedReliable ? "live" : "stale"} />{mode === "live" ? `Binance · ${healthState}` : "Verified historical cache"}</span><span>Updated {stamp(state?.live_display?.last_update)}</span><span>Quote source {state?.live_display?.quote_source ?? "—"}</span><span>Closed candles used: {String(state?.data_provenance?.closed_candles_used ?? state?.candles.length ?? 0)}</span><span>Forming candle excluded from strategy: {state?.forming_candle ? "YES" : "N/A"}</span><b>PAPER · NO LIVE EXECUTION PATH</b></div>
        </div>

        <div className="pa-bottom">
          <nav>{TABS.map((row) => <button key={row} className={tab === row ? "active" : ""} onClick={() => setTab(row)}>{row}<em>{row === "positions" ? paper?.positions.length ?? 0 : row === "orders" ? pendingPaperOrders.length : row === "trades" ? tradeRows.length : row === "setups" ? state?.setups.length ?? 0 : row === "rejected" ? rejected.length : ""}</em></button>)}</nav>
          <div className="pa-bottom-body">
            {tab === "positions" ? <DataTable rows={paper?.positions ?? []} empty="No open Price Action paper positions." /> : null}
            {tab === "orders" ? <><div className="pa-order-ticket"><select aria-label="Paper order side" value={order.side} onChange={(e) => setOrder({ ...order, side: e.target.value })}><option value="buy">Buy / Long</option><option value="sell">Sell / Short</option></select><select aria-label="Paper order type" value={order.type} onChange={(e) => setOrder({ ...order, type: e.target.value })}><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option></select><input aria-label="Paper order quantity" value={order.quantity} onChange={(e) => setOrder({ ...order, quantity: e.target.value })} inputMode="decimal" placeholder="Quantity" />{order.type !== "market" ? <input aria-label="Paper order trigger or limit price" value={order.limit_price} onChange={(e) => setOrder({ ...order, limit_price: e.target.value })} inputMode="decimal" placeholder="Trigger / limit price" /> : null}<button onClick={() => void submitOrder()}>Place paper order</button><button className="pa-reconcile" onClick={() => void reconcileOrders()}>Reconcile strategy orders</button><span>PAPER · {pendingPaperOrders.length} pending</span></div><div className="pa-order-audit"><b>Pending paper audit</b><span>Total {paper?.order_audit?.pending_paper_orders ?? pendingPaperOrders.length}</span><span>Strategy {paper?.order_audit?.pending_strategy_orders ?? 0}</span><span>Manual {paper?.order_audit?.pending_manual_orders ?? 0}</span><span>Duplicates {paper?.order_audit?.duplicate_strategy_orders.length ?? 0}</span><span>Discrepancies {paper?.order_audit?.discrepancies.length ?? 0}</span><small>Manual orders are never automatically cancelled by strategy reconciliation.</small></div>{(paper?.candidates ?? []).filter((row) => row.status === "PENDING_APPROVAL").map((row) => <div className="pa-order-ticket" key={row.proposal_id}><b>{String(row.payload.proposal?.strategy_id ?? "Strategy")} · {String(row.payload.proposal?.direction ?? "—")}</b><span>{String(row.payload.reason ?? "Awaiting approval")}</span><button onClick={() => void approve(row.source_proposal_id)}>Approve paper order</button></div>)}<h3 className="pa-table-heading">Pending paper broker orders</h3><DataTable rows={pendingPaperOrders} empty="No pending paper broker orders." /><h3 className="pa-table-heading">Research-engine orders · not paper broker orders</h3><DataTable rows={researchOrderRows} empty="No normalized research-engine orders in this candle window." /></> : null}
            {tab === "trades" ? <DataTable rows={tradeRows} empty="No completed paper fills or normalized research outcomes." /> : null}
            {tab === "setups" ? <DataTable rows={(state?.setups ?? []) as unknown as Record<string, any>[]} empty="No confirmed setups in the visible engine state." /> : null}
            {tab === "rejected" ? <DataTable rows={rejected} empty="No rejected or waiting strategy traces." /> : null}
            {tab === "session" ? <><div className="pa-session"><span>Session ID<b>{paper?.session.id ?? "—"}</b></span><span>Started<b>{stamp(paper?.session.started_at)}</b></span><span>Starting balance<b>{money(paper?.session.starting_balance)} USDT</b></span><span>Status<b>{paper?.session.status?.toUpperCase() ?? "—"}</b></span><span>Operating mode<b>{pretty(paper?.session.operating_mode ?? "signals_only")}</b></span></div><div className="pa-order-ticket"><select aria-label="Saved Price Action session" value={selectedSession} onChange={(event) => setSelectedSession(event.target.value)}>{sessions.map((row) => <option key={row.id} value={row.id}>{row.symbol} · {row.timeframe} · {row.status} · {stamp(row.started_at)}</option>)}</select><button onClick={() => void sessionAction("start")}>Start new</button><button disabled={!selectedSession} onClick={() => void sessionAction("resume")}>Resume</button><button disabled={!selectedSession} onClick={() => void sessionAction("duplicate")}>Duplicate</button><button disabled={!paper?.session.id} onClick={() => void sessionAction("end")}>End</button><button className="pa-export" onClick={() => void apiDownload("/research/price-action/paper/export", `price-action-session-${paper?.session.id ?? "current"}.json`)}>Export</button><button className="btn-danger" onClick={() => void resetSession()}>Reset</button></div><DataTable rows={paper?.activity ?? []} empty="No session audit events yet." /></> : null}
            {tab === "connection" ? <div className="pa-session"><span>Exchange<b>Binance USDⓈ-M Futures</b></span><span>Overall health<b>{healthState}</b></span><span>Transport<b>{state?.live_display?.transport_state ?? (mode === "replay" ? "ISOLATED" : "CONNECTING")}</b></span><span>Candle stream<b>{age(state?.live_display?.candle_age_seconds)}</b></span><span>Bid / ask stream<b>{age(state?.live_display?.quote_age_seconds)}</b></span><span>Mark stream<b>{age(state?.live_display?.mark_age_seconds)}</b></span><span>Reconciliation<b>{state?.live_display?.health_reason ?? "—"}</b></span><span>New entries<b>{state?.live_display?.new_entries_paused ? "PAUSED · FAIL CLOSED" : "CLOSED BARS ONLY"}</b></span><span>Real execution<b>DISABLED</b></span></div> : null}
          </div>
        </div>
      </main>
    </div>
  </div>;
}
