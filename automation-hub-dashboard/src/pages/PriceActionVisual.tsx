import { useCallback, useEffect, useMemo, useState } from "react";
import NativeSMCChartOverlay, {
  type NativeCandle, type NativeSMCChartState, type NativeSMCOverlayFilters,
} from "../components/chart/NativeSMCChartOverlay";
import { apiDownload, apiGet, apiPostJson } from "../lib/api";
import { useApp } from "../app-context";

type Mode = "live" | "replay";
type BottomTab = "positions" | "orders" | "trades" | "setups" | "rejected" | "session" | "connection";
type Direction = "bullish" | "bearish";
interface Swing { id: string; kind: "high" | "low"; price: number; occurred_at: string; confirmed_at: string; label: string }
interface Zone { id: string; role: "support" | "resistance"; low: number; high: number; created_at: string; confirmed_at: string; active: boolean; flipped: boolean; invalidated_at?: string | null }
interface PAEvent { id: string; event_type: string; direction: Direction | "neutral"; level: number; occurred_at: string; confirmed_at: string; zone_id?: string | null; pattern?: string | null; reasons: string[] }
interface Condition { key: string; status: "PASS" | "MISSING"; detail: string; object_id?: string | null }
interface Trace { strategy_id: string; direction: Direction; state: string; conditions: Condition[]; missing_conditions: string[]; setup_id?: string | null; next_required_event: string }
interface Setup { id: string; strategy_id: string; direction: Direction; phase: string; created_at: string; reasons: string[]; missing_conditions: string[] }
interface Proposal { id: string; setup_id: string; strategy_id: string; direction: Direction; entry: number; stop: number; target: number; rr_ratio: number; signal_at: string; execution_allowed: false; paper_execution_allowed: true }
interface PASnapshot { candle_open: string; candle_close: string; structure_bias: Direction | "neutral"; pattern?: string | null; patterns?: { name: string; direction: Direction | "neutral" }[]; proposal_ids: string[]; strategy_traces: Trace[] }
interface PAState {
  research_id: string; research_only: true; execution_allowed: false; paper_execution_allowed: true;
  symbol: string; timeframe: string; candles: NativeCandle[]; swings: Swing[]; zones: Zone[]; events: PAEvent[];
  setups: Setup[]; proposals: Proposal[]; snapshot: PASnapshot | null; selected_snapshot: PASnapshot | null;
  snapshot_ledger?: PASnapshot[];
  orders: Record<string, any>[]; trades: Record<string, any>[];
  metrics: { closed: number; wins: number; losses: number; unfilled: number; net_r: number; costs_r: number };
  forming_candle?: NativeCandle | null; live_display?: NativeSMCChartState["live_display"] & { last_update?: string | null };
  replay?: { cursor: number; total: number; future_candles_visible: false; has_next: boolean };
  data_provenance?: Record<string, string | number | boolean>;
}
interface PaperState {
  account_scope: string; currency: "USDT"; execution_mode: "PAPER"; real_funds: false; live_execution_allowed: false;
  session: { id: string; started_at: string; status: string; starting_balance: number; symbol?: string; timeframe?: string; operating_mode?: OperatingMode; execution_config?: { strategy_id?: string; risk_pct?: number } };
  account: { starting_balance: number; balance: number; equity: number; unrealized_pnl: number; fees_paid: number; free_margin: number; leverage: number };
  positions: Record<string, any>[]; orders: Record<string, any>[]; trades: Record<string, any>[];
  candidates: { proposal_id: string; source_proposal_id: string; status: string; payload: Record<string, any> }[];
  order_metadata: { order_id: string; status: string; config: { proposal: Proposal; entry: number; stop: number; target: number } }[];
  activity: Record<string, any>[];
}
type OperatingMode = "signals_only" | "manual_approval" | "automatic";

const STRATEGIES = ["PA1_SR_REJECTION", "PA2_TREND_PULLBACK", "PA3_FLIP_RETEST", "PA4_FALSE_BREAK_REVERSAL"];
const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d"];
const TABS: BottomTab[] = ["positions", "orders", "trades", "setups", "rejected", "session", "connection"];
const money = (value?: number) => Number(value ?? 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const stamp = (value?: string | null) => value ? value.replace("T", " ").replace("+00:00", " UTC").slice(0, 22) : "—";
const pretty = (value: string) => value.replace(/^PA\d_/, "").replace(/_/g, " ");

function chartState(state: PAState, paper?: PaperState | null): NativeSMCChartState {
  const last = state.candles[state.candles.length - 1];
  const high = state.candles.length ? Math.max(...state.candles.map((row) => row.high)) : 0;
  const low = state.candles.length ? Math.min(...state.candles.map((row) => row.low)) : 0;
  const snap = state.snapshot ? {
    id: `pa-chart-${state.snapshot.candle_open}`, candle_open: state.snapshot.candle_open, candle_close: state.snapshot.candle_close,
    htf_bias: 0, htf_ema: null, swing_bias: state.snapshot.structure_bias === "bullish" ? 1 : state.snapshot.structure_bias === "bearish" ? -1 : 0,
    internal_bias: 0, session: "24/7", dealing_range: { high, low, equilibrium: (high + low) / 2, area: "native zones" },
    price_action: { bullish_rejection: false, bearish_rejection: false, body: last ? Math.abs(last.close - last.open) : 0, upper_wick: 0, lower_wick: 0 },
    active_setup_id: null, setup_phase: null, next_required_event: "See strategy trace", latest_sweep_id: null,
    event_ids: state.events.map((row) => row.id), active_fvg_ids: [], active_ob_ids: state.zones.filter((row) => row.active).map((row) => row.id), proposal_ids: state.snapshot.proposal_ids,
  } : null;
  const patternEvents = (state.snapshot_ledger ?? []).flatMap((snapshot) => (snapshot.patterns ?? []).map((pattern, index) => {
    const candle = state.candles.find((row) => row.timestamp === snapshot.candle_open);
    return { id: `pattern-${snapshot.candle_open}-${index}`, event_type: pattern.name,
      direction: pattern.direction, level: candle?.close ?? 0, occurred_at: snapshot.candle_open,
      confirmed_at: snapshot.candle_close, zone_id: null, pattern: pattern.name, reasons: ["completed-candle pattern metadata"] };
  }));
  const paperPlans = (paper?.order_metadata ?? []).map((row) => ({
    ...row.config.proposal, id: `paper-${row.order_id}`, snapshot_id: `paper-${row.order_id}`,
    entry: row.config.entry, stop: row.config.stop, target: row.config.target,
    risk_status: `PAPER_${row.status}`,
  }));
  return {
    research_id: state.research_id, execution_allowed: false, candles: state.candles,
    pivots: state.swings.map((row) => ({ ...row, scope: "swing", strength: row.label === "HH" || row.label === "LL" ? "strong" : "weak" })),
    events: [...state.events, ...patternEvents].map((row) => ({ ...row, scope: "swing", label: pretty(row.event_type) })),
    fair_value_gaps: [],
    order_blocks: state.zones.map((row) => ({ id: row.id, direction: row.role === "support" ? "bullish" : "bearish", label: `${row.flipped ? "FLIPPED " : ""}${row.role.toUpperCase()}`, low: row.low, high: row.high, created_at: row.confirmed_at, active: row.active, mitigated: !row.active, mitigation_at: row.invalidated_at })),
    proposals: [...state.proposals.map((row) => ({ ...row, snapshot_id: `pa-chart-${row.signal_at}`, risk_status: "SIGNAL_ONLY" })), ...paperPlans],
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
  const [activeStrategy, setActiveStrategy] = useState(STRATEGIES[0]);
  const [operatingMode, setOperatingMode] = useState<OperatingMode>("signals_only");
  const [riskPct, setRiskPct] = useState("0.5");
  const [filters, setFilters] = useState<NativeSMCOverlayFilters>({ pivots: true, internal: true, swing: true, structure: true, liquidity: true, fvg: false, orderBlocks: true, mitigated: true, labels: true });
  const [order, setOrder] = useState({ side: "buy", type: "market", quantity: "0.001", limit_price: "" });
  const [controlsOpen, setControlsOpen] = useState(() => typeof window === "undefined" || window.innerWidth > 820);

  const loadPaper = useCallback(async () => {
    try {
      const next = await apiGet<PaperState>("/research/price-action/paper"); setPaper(next);
      const history = await apiGet<{ sessions: typeof sessions }>("/research/price-action/sessions");
      setSessions(history.sessions); setSelectedSession((current) => current || next.session.id);
    } catch { /* chart remains usable */ }
  }, []);
  const loadChart = useCallback(async () => {
    setLoading(true);
    const path = mode === "live"
      ? `/research/price-action/live-chart?symbol=${symbol}&timeframe=${timeframe}&window=800&visible=500`
      : `/research/price-action/sessions/current/replay/step?symbol=${symbol}&timeframe=${timeframe}&cursor=${cursor}&limit=3000`;
    try { setState(mode === "live" ? await apiGet<PAState>(path) : await apiPostJson<PAState>(path, {})); setError(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Price Action data unavailable"); }
    finally { setLoading(false); }
  }, [mode, symbol, timeframe, cursor]);

  useEffect(() => { void apiGet<{ contracts: string[] }>("/research/price-action/contracts?limit=500").then((row) => setContracts(row.contracts)).catch(() => undefined); }, []);
  useEffect(() => { void loadChart(); if (mode !== "live") return; const timer = window.setInterval(() => void loadChart(), 3_000); return () => window.clearInterval(timer); }, [loadChart, mode]);
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
  useEffect(() => {
    if (!paper?.session) return;
    setOperatingMode(paper.session.operating_mode ?? "signals_only");
    setActiveStrategy(paper.session.execution_config?.strategy_id ?? STRATEGIES[0]);
    setRiskPct(String(paper.session.execution_config?.risk_pct ?? .5));
  }, [paper?.session.id]);

  const displayed = useMemo(() => state ? {
    ...state,
    setups: state.setups.filter((row) => row.strategy_id === activeStrategy),
    proposals: state.proposals.filter((row) => row.strategy_id === activeStrategy),
  } : null, [state, activeStrategy]);
  const chart = useMemo(() => displayed ? chartState(displayed, paper) : null, [displayed, paper]);
  const traces = state?.snapshot?.strategy_traces.filter((row) => row.strategy_id === activeStrategy) ?? [];
  const ready = traces.filter((row) => row.state === "ENTRY_READY");
  const rejected = traces.filter((row) => row.state !== "ENTRY_READY").map((row) => ({ strategy: row.strategy_id, direction: row.direction, missing: row.missing_conditions.join(", "), next: row.next_required_event }));
  const orderRows = [
    ...(paper?.orders ?? []).map((row) => ({ scope: "PAPER", ...row })),
    ...(state?.orders ?? []).map((row) => ({ scope: "RESEARCH", ...row })),
  ];
  const tradeRows = [
    ...(paper?.trades ?? []).map((row) => ({ scope: "PAPER", ...row })),
    ...(state?.trades ?? []).map((row) => ({ scope: "RESEARCH", ...row })),
  ];

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
    try { setPaper(await apiPostJson<PaperState>("/research/price-action/paper/reset", { confirmation: phrase })); toast("Fresh Price Action paper session created", "success"); }
    catch (reason) { toast(reason instanceof Error ? reason.message : "Reset failed", "error"); }
  };
  const sessionAction = async (action: "start" | "resume" | "duplicate" | "end") => {
    try {
      let updated: PaperState;
      if (action === "start") updated = await apiPostJson<PaperState>("/research/price-action/sessions", { mode: mode === "live" ? "LIVE_PAPER" : "HISTORICAL", symbol, timeframe, starting_balance: paper?.account.starting_balance ?? 10_000, operating_mode: operatingMode, strategy_id: activeStrategy, risk_pct: Number(riskPct) });
      else if (action === "end") { await apiPostJson("/research/price-action/sessions/current/end", {}); await loadPaper(); toast("Session ended and snapshotted", "success"); return; }
      else updated = await apiPostJson<PaperState>(`/research/price-action/sessions/${selectedSession}/${action}`, {});
      setPaper(updated); setSelectedSession(updated.session.id); await loadPaper(); toast(`Session ${action} complete`, "success");
    } catch (reason) { toast(reason instanceof Error ? reason.message : `Session ${action} failed`, "error"); }
  };
  const confirmMarketChange = (nextSymbol: string, nextTimeframe: string, nextMode: Mode = mode) => {
    const exposed = Boolean((paper?.positions.length ?? 0) + (paper?.orders.filter((row) => ["open", "partially_filled", "triggered"].includes(String(row.status))).length ?? 0));
    if (exposed && !window.confirm("This paper session has an open position or pending order. Change the viewed market without changing ownership of those paper records?")) return;
    setSymbol(nextSymbol); setTimeframe(nextTimeframe); setMode(nextMode);
  };

  return <div className="pa-lab">
    <header className="pa-titlebar">
      <div><span className="pa-kicker">NATIVE RESEARCH TERMINAL</span><h1>Price Action Visual Lab</h1><p>One closed-candle engine · historical replay + live paper observation</p></div>
      <button type="button" className="pa-controls-toggle" onClick={() => setControlsOpen((open) => !open)} aria-expanded={controlsOpen}>Controls</button>
      <div className="pa-safety"><b>PAPER ONLY</b><span>REAL ORDERS DISABLED</span></div>
    </header>

    <div className="pa-workspace">
      <aside className={`pa-sidebar ${controlsOpen ? "is-open" : ""}`} aria-label="Price Action controls">
        <section><h2>Market</h2><label>Binance USDⓈ-M contract<select value={symbol} onChange={(event) => confirmMarketChange(event.target.value, timeframe)}>{contracts.map((row) => <option key={row}>{row}</option>)}</select></label><div className="pa-segment"><button className={mode === "live" ? "active" : ""} onClick={() => confirmMarketChange(symbol, timeframe, "live")}>Live paper</button><button className={mode === "replay" ? "active" : ""} onClick={() => confirmMarketChange(symbol, timeframe, "replay")}>Replay</button></div></section>
        <section><h2>Strategy &amp; execution</h2><label>Visible automated strategy<select value={activeStrategy} onChange={(event) => setActiveStrategy(event.target.value)}>{STRATEGIES.map((id) => <option key={id} value={id}>{pretty(id)}</option>)}</select></label><label>Paper operating mode<select value={operatingMode} onChange={(event) => setOperatingMode(event.target.value as OperatingMode)}><option value="signals_only">Signals only</option><option value="manual_approval">Manual approval</option><option value="automatic">Automatic paper</option></select></label><label>Risk per trade (%)<input value={riskPct} onChange={(event) => setRiskPct(event.target.value)} inputMode="decimal" /></label><button type="button" className="pa-export" onClick={() => void applyAutomation()}>Apply paper configuration</button><small>One visible strategy at a time. Existing orders retain their immutable configuration snapshot.</small></section>
        <section><h2>Layers</h2>{([['pivots','Swings'], ['structure','Events'], ['orderBlocks','S/R zones'], ['labels','Labels']] as [keyof NativeSMCOverlayFilters, string][]).map(([key, label]) => <label className="pa-check" key={key}><input type="checkbox" checked={filters[key]} onChange={() => toggleLayer(key)} /><span>{label}</span></label>)}</section>
        <section><h2>Virtual account</h2><div className="pa-account"><span>Balance<b>{money(paper?.account.balance)} USDT</b></span><span>Equity<b>{money(paper?.account.equity)} USDT</b></span><span>Open P&amp;L<b className={(paper?.account.unrealized_pnl ?? 0) >= 0 ? "positive" : "negative"}>{money(paper?.account.unrealized_pnl)}</b></span><span>Free margin<b>{money(paper?.account.free_margin)}</b></span></div><label>Isolated leverage<select value={paper?.account.leverage ?? 1} onChange={(event) => void setLeverage(Number(event.target.value))}>{Array.from({ length: 20 }, (_, index) => index + 1).map((value) => <option key={value} value={value}>{value}×</option>)}</select></label><small>Persistent and isolated from every other paper account.</small></section>
        <section className="pa-legend"><h2>Chart truth</h2><span><i className="confirmed" />Confirmed</span><span><i className="provisional" />Forming · display only</span><span><i className="invalid" />Invalidated</span></section>
      </aside>

      <main className="pa-main">
        <div className="pa-toolbar"><div className="pa-symbol"><i className={error ? "stale" : "live"} />{symbol}<span>PERPETUAL</span></div><div className="pa-timeframes">{TIMEFRAMES.map((row) => <button key={row} className={row === timeframe ? "active" : ""} onClick={() => confirmMarketChange(symbol, row)}>{row}</button>)}</div><button onClick={() => setFitSignal((n) => n + 1)}>Fit</button><button onClick={() => setLatestSignal((n) => n + 1)}>Latest</button><span className="pa-mode-chip">{mode === "live" ? "LIVE DATA" : "REPLAY"}</span></div>
        {mode === "replay" ? <div className="pa-replay"><button onClick={() => setCursor(1)}>Restart</button><button onClick={() => setReplayPlaying((value) => !value)}>{replayPlaying ? "Pause" : "Play"}</button><button onClick={() => setCursor((n) => Math.max(1, n - 1))}>◀</button><input aria-label="Replay candle cursor" type="range" min="1" max={state?.replay?.total ?? 1000} value={Math.min(cursor, state?.replay?.total ?? cursor)} onChange={(event) => setCursor(Number(event.target.value))} /><button disabled={!state?.replay?.has_next} onClick={() => setCursor((n) => n + 1)}>▶</button><select aria-label="Replay speed" value={replaySpeed} onChange={(event) => setReplaySpeed(Number(event.target.value))}>{[1, 2, 5, 10, 25, 100].map((value) => <option key={value} value={value}>{value === 100 ? "Maximum" : `${value}×`}</option>)}</select><span>Candle {state?.replay?.cursor ?? cursor} / {state?.replay?.total ?? "—"} · future bars hidden</span></div> : null}
        <div className="pa-chart-shell">
          <div className="pa-chart-head"><div><b>{symbol} · {timeframe}</b><span>{state?.data_provenance?.exchange ?? "Binance USDⓈ-M Futures"} · OHLC only</span></div><div><span>{state?.metrics ? `${state.metrics.net_r.toFixed(2)}R · ${state.metrics.wins}W/${state.metrics.losses}L · ${state.metrics.unfilled} unfilled` : "NO CLOSED TRADES"}</span><span>{state?.snapshot?.structure_bias?.toUpperCase() ?? "NEUTRAL"}</span><b>{ready.length ? `${ready.length} READY` : "WAIT"}</b></div></div>
          {error ? <div className="pa-error"><b>Market data unavailable</b><span>{error}</span><button onClick={() => void loadChart()}>Retry</button></div> : null}
          {!chart ? <div className="pa-loading">{loading ? "Loading verified Binance candles…" : "No verified candle state"}</div> : <NativeSMCChartOverlay state={chart} timeframe={timeframe} filters={filters} onCandleSelect={() => undefined} fitContentSignal={fitSignal} latestSignal={latestSignal} modelLabel="native price action" height={570} liveDataStale={Boolean(error)} />}
          <div className="pa-chart-foot"><span><i className="live" />{mode === "live" ? `Binance · ${state?.live_display?.connection_state ?? "CONNECTING"}` : "Verified historical cache"}</span><span>Updated {stamp(state?.live_display?.last_update)}</span><span>Bid {state?.live_display?.bid?.toLocaleString() ?? "—"} · Ask {state?.live_display?.ask?.toLocaleString() ?? "—"} · Mark {state?.live_display?.mark?.toLocaleString() ?? "—"}</span><span>Closed candles used: {String(state?.data_provenance?.closed_candles_used ?? state?.candles.length ?? 0)}</span><span>Forming candle excluded: {state?.forming_candle ? "YES" : "N/A"}</span><b>PAPER · NO LIVE EXECUTION PATH</b></div>
        </div>

        <div className="pa-bottom">
          <nav>{TABS.map((row) => <button key={row} className={tab === row ? "active" : ""} onClick={() => setTab(row)}>{row}<em>{row === "positions" ? paper?.positions.length ?? 0 : row === "orders" ? orderRows.length : row === "trades" ? tradeRows.length : row === "setups" ? state?.setups.length ?? 0 : row === "rejected" ? rejected.length : ""}</em></button>)}</nav>
          <div className="pa-bottom-body">
            {tab === "positions" ? <DataTable rows={paper?.positions ?? []} empty="No open Price Action paper positions." /> : null}
            {tab === "orders" ? <><div className="pa-order-ticket"><select value={order.side} onChange={(e) => setOrder({ ...order, side: e.target.value })}><option value="buy">Buy / Long</option><option value="sell">Sell / Short</option></select><select value={order.type} onChange={(e) => setOrder({ ...order, type: e.target.value })}><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option></select><input value={order.quantity} onChange={(e) => setOrder({ ...order, quantity: e.target.value })} inputMode="decimal" placeholder="Quantity" />{order.type !== "market" ? <input value={order.limit_price} onChange={(e) => setOrder({ ...order, limit_price: e.target.value })} inputMode="decimal" placeholder="Trigger / limit price" /> : null}<button onClick={() => void submitOrder()}>Place paper order</button><span>PAPER · no real funds</span></div>{(paper?.candidates ?? []).filter((row) => row.status === "PENDING_APPROVAL").map((row) => <div className="pa-order-ticket" key={row.proposal_id}><b>{row.payload.proposal?.strategy_id} · {row.payload.proposal?.direction}</b><span>{row.payload.reason}</span><button onClick={() => void approve(row.source_proposal_id)}>Approve paper order</button></div>)}<DataTable rows={orderRows} empty="No pending paper or normalized research orders." /></> : null}
            {tab === "trades" ? <DataTable rows={tradeRows} empty="No completed paper fills or normalized research outcomes." /> : null}
            {tab === "setups" ? <DataTable rows={(state?.setups ?? []) as unknown as Record<string, any>[]} empty="No confirmed setups in the visible engine state." /> : null}
            {tab === "rejected" ? <DataTable rows={rejected} empty="No rejected or waiting strategy traces." /> : null}
            {tab === "session" ? <><div className="pa-session"><span>Session ID<b>{paper?.session.id ?? "—"}</b></span><span>Started<b>{stamp(paper?.session.started_at)}</b></span><span>Starting balance<b>{money(paper?.session.starting_balance)} USDT</b></span><span>Status<b>{paper?.session.status?.toUpperCase() ?? "—"}</b></span><span>Operating mode<b>{pretty(paper?.session.operating_mode ?? "signals_only")}</b></span></div><div className="pa-order-ticket"><select aria-label="Saved Price Action session" value={selectedSession} onChange={(event) => setSelectedSession(event.target.value)}>{sessions.map((row) => <option key={row.id} value={row.id}>{row.symbol} · {row.timeframe} · {row.status} · {stamp(row.started_at)}</option>)}</select><button onClick={() => void sessionAction("start")}>Start new</button><button disabled={!selectedSession} onClick={() => void sessionAction("resume")}>Resume</button><button disabled={!selectedSession} onClick={() => void sessionAction("duplicate")}>Duplicate</button><button disabled={!paper?.session.id} onClick={() => void sessionAction("end")}>End</button><button className="pa-export" onClick={() => void apiDownload("/research/price-action/paper/export", `price-action-session-${paper?.session.id ?? "current"}.json`)}>Export</button><button className="btn-danger" onClick={() => void resetSession()}>Reset</button></div><DataTable rows={paper?.activity ?? []} empty="No session audit events yet." /></> : null}
            {tab === "connection" ? <div className="pa-session"><span>Exchange<b>Binance USDⓈ-M Futures</b></span><span>Connection<b>{state?.live_display?.connection_state ?? (mode === "replay" ? "REPLAY" : "CONNECTING")}</b></span><span>Last update<b>{stamp(state?.live_display?.last_update)}</b></span><span>New entries<b>{state?.live_display?.new_entries_paused ? "PAUSED · feed unreliable" : "CLOSED BARS ONLY"}</b></span><span>Real execution<b>DISABLED</b></span></div> : null}
          </div>
        </div>
      </main>
    </div>
  </div>;
}
