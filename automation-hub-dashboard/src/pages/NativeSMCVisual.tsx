import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import Card from "../components/common/Card";
import { Badge, EmptyState, Field, PageHeader } from "../components/common/ui";
import NativeSMCChartOverlay, {
  type NativeCandle, type NativeEvent, type NativePivot, type NativeProposal, type NativeSMCChartState,
  type ChartPriceViewport, type ChartTimeViewport, type NativeSMCOverlayFilters, type NativeSnapshot, type NativeZone,
} from "../components/chart/NativeSMCChartOverlay";
import { apiDownload, apiGet, apiPostJson, useLive } from "../lib/api";

type ReviewClassification = "CORRECT" | "INCORRECT" | "AMBIGUOUS";
interface Setup { id: string; direction: "bullish" | "bearish"; phase: string; next_required_event: string; transitions: { id: string; timestamp: string; to_phase: string; reason: string; object_id?: string | null }[] }
interface LadderCondition { key: string; label: string; status: "PASS" | "MISSING" | "NOT_REQUIRED" | "INVALIDATED" | "EXPIRED"; detail: string; object_id?: string | null; bars_since?: number | null }
interface LadderTrace { direction: "bullish" | "bearish"; state: string; conditions: LadderCondition[]; missing_conditions: string[]; invalidation_reason?: string | null; next_required_event: string; supporting_object_ids: string[]; event_ages: Record<string, number | null>; setup_id?: string | null }
interface LadderCandidate { strategy_id: string; version: string; research_status: string; execution_allowed: false; selected_direction?: "bullish" | "bearish" | null; state: string; conflict: boolean; next_required_event: string; direction_traces: LadderTrace[]; selected_trace?: LadderTrace | null }
interface StrategyLadder { research_id: string; ladder_id: string; version: string; research_only: true; execution_allowed: false; definitions_frozen: true; candidates: LadderCandidate[] }
interface NativeState extends NativeSMCChartState { pivots: NativePivot[]; events: NativeEvent[]; fair_value_gaps: NativeZone[]; order_blocks: NativeZone[]; proposals: NativeProposal[]; setups: Setup[]; strategy_ladder?: StrategyLadder; source_strategy?: SMCSourceStrategyEvaluation }
interface ReviewSampleItem { object_id: string; category: string; timestamp: string; setup_id?: string | null }
interface ReviewSampleResponse { sample: ReviewSampleItem[] }
interface Review { id: string; object_id: string; component: string; classification: ReviewClassification; reason?: string | null; notes?: string | null; selected_candle_timestamp?: string | null }
interface ReviewsResponse { reviews: Review[] }
interface PineReference { reference_id: string; status: string; language: string; sha256: string; execution_allowed: false; notice: string; content: string }
interface DataProvenance { mode: string; venue: string; market: string; observed_at: string; closed_candles_loaded: number; closed_candles_visible: number; last_closed_candle: string; forming_candle_excluded: boolean; execution_allowed: false }
interface LiveHistoryPage { candles: NativeCandle[]; has_more_history: boolean; oldest: string | null; newest: string | null; execution_allowed: false }
type ChartFeed = "checkpoint" | "binance_usdm" | "mexc_perpetual" | "kraken_spot";
interface SMCSourceModel { id: string; label: string; status: "ACTIVE" | "PARKED"; narrative: string; ordered_rules: string[] }
interface SMCSourceModelsResponse { strategy_id: string; strategy_version: string; paper_only: true; real_execution_allowed: false; models: SMCSourceModel[] }
interface SMCSourceStrategyEvaluation {
  strategy_id: string; version: string; state: string; next_required_event: string;
  selected_candidate_id?: string | null; paper_only: true; execution_allowed: false;
  model: SMCSourceModel; native_object_ids: string[]; missing_conditions: string[];
  ordered_condition_results: LadderCondition[]; proposal_id?: string | null; setup_id?: string | null;
  trade_plan?: { entry: number; stop: number; target_1: number; target_1_r: number; target_2: number; target_2_r: number; risk_percent: number } | null;
}
interface SMCPaperState {
  paper_only: true; real_execution_allowed: false;
  session: { id: string; mode: string; symbol: string; timeframe: string; operating_mode: string; model_id: string; risk_pct: number };
  account: { balance: number; equity: number; available_margin: number; used_margin: number; open_risk: number; unrealized_pnl: number; leverage: number };
  positions: Record<string, unknown>[]; orders: Record<string, unknown>[]; trades: Record<string, unknown>[];
  candidates: { proposal_id: string; status: string; reason: string; created_at: string }[];
  activity: Record<string, unknown>[];
  funding_events: Record<string, unknown>[];
}
interface SMCJournalRow {
  journal_id: string; session_id: string; symbol: string; timeframe: string; model_id: string;
  direction?: string | null; status: string; signal_timestamp?: string | null; created_at: string;
  proposal_id: string; setup_id?: string | null; order_id?: string | null; net_pnl: number;
  data_quality: string; rule_compliance: string; native_object_ids: string[];
  ordered_conditions: LadderCondition[]; missing_conditions: string[];
  trade_plan?: SMCSourceStrategyEvaluation["trade_plan"]; fills: Record<string, unknown>[];
  notes: { id: string; note: string; created_at: string }[];
}
interface SMCJournalResponse { journal: SMCJournalRow[]; paper_only: true; real_execution_allowed: false }
interface SMCSessionsResponse { sessions: Record<string, unknown>[]; paper_only: true; real_execution_allowed: false }

type ReferenceInput = { label: string; value: string };
type ReferenceInputGroup = { title: string; inputs: ReferenceInput[] };
const LIVE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const CHART_TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"];
const DEFAULT_VISIBLE_BARS: Record<string, number> = {
  "1m": 180, "3m": 150, "5m": 120, "15m": 110, "30m": 96,
  "1h": 72, "4h": 60, "1d": 60, "1w": 52,
};

function defaultVisibleBars(timeframe: string) {
  const base = DEFAULT_VISIBLE_BARS[timeframe] ?? 120;
  if (typeof window === "undefined") return base;
  if (window.innerWidth >= 1_900) return Math.round(base * 1.15);
  if (window.innerWidth < 1_024) return Math.max(70, Math.round(base * 0.85));
  return base;
}

const cell = (value: unknown) => value === null || value === undefined || value === "" ? "—" :
  typeof value === "number" ? value.toLocaleString(undefined, { maximumFractionDigits: 6 }) :
  typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);

function EvidenceTable({ title, rows, columns, onSelect }: {
  title: string; rows: Record<string, unknown>[];
  columns: { key: string; label: string }[];
  onSelect?: (row: Record<string, unknown>) => void;
}) {
  return <div className="smc-evidence-table-wrap"><b>{title}</b>{rows.length ? <table className="smc-evidence-table"><thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id ?? row.order_id ?? row.proposal_id ?? index)} tabIndex={onSelect ? 0 : undefined} onClick={() => onSelect?.(row)} onKeyDown={(event) => { if (onSelect && (event.key === "Enter" || event.key === " ")) onSelect(row); }}>{columns.map((column) => <td key={column.key}>{cell(row[column.key])}</td>)}</tr>)}</tbody></table> : <p>No records in this SMC session.</p>}</div>;
}
const REFERENCE_INPUT_GROUPS: ReferenceInputGroup[] = [
  { title: "PRO Strategy & Automation", inputs: [
    { label: "ATR Length for SL", value: "14" }, { label: "Stop Loss ATR Multiplier", value: "1.5" }, { label: "Risk/Reward Ratio (TP)", value: "2.5" },
    { label: "Killzone Session", value: "0700–1100, 1300–1600" }, { label: "Killzone Timezone", value: "Europe/London" }, { label: "Liquidity Sweep Memory", value: "10 bars" },
    { label: "CHoCH Memory", value: "8 bars" }, { label: "FVG Entry Window", value: "5 bars" }, { label: "Confirmed bars only", value: "Enabled" }, { label: "Structure Break ATR Filter", value: "0.3" },
  ] },
  { title: "Price Action Filters", inputs: [{ label: "Require Rejection Candle Entry", value: "Enabled" }, { label: "Wick-to-Body Ratio", value: "2.0" }] },
  { title: "Internal & Swing Structure", inputs: [
    { label: "Show Internal Structure", value: "Enabled" }, { label: "Internal Pivot Length", value: "5" }, { label: "Internal Bull/Bear Structure", value: "All" }, { label: "Internal Label Size", value: "Tiny" }, { label: "Internal Confluence Filter", value: "Disabled" },
    { label: "Show Swing Structure", value: "Enabled" }, { label: "Swing Structure", value: "All" }, { label: "Swing Label Size", value: "Small" }, { label: "Show Swing Points", value: "Disabled" }, { label: "Swing Pivot Length", value: "50" }, { label: "Strong/Weak High/Low", value: "Enabled" },
  ] },
  { title: "Order Blocks & Fair Value Gaps", inputs: [
    { label: "Internal Order Blocks", value: "Enabled · 5" }, { label: "Swing Order Blocks", value: "Disabled · 5" }, { label: "Order Block Filter", value: "ATR" }, { label: "OB Mitigation", value: "High/Low" },
    { label: "Equal High/Low", value: "Enabled · 3 bars · threshold 0.1" }, { label: "Fair Value Gaps", value: "Enabled" }, { label: "FVG Auto Threshold", value: "Enabled" }, { label: "FVG Volume Surge Filter", value: "Enabled" }, { label: "FVG Timeframe", value: "Chart timeframe" }, { label: "Extend FVG", value: "1 bar" },
  ] },
  { title: "Premium/Discount & Dashboard", inputs: [
    { label: "Premium / Discount Zones", value: "Enabled" }, { label: "Dashboard", value: "Enabled · Top Right" }, { label: "HTF Bias Filter", value: "240 minutes (4h)" }, { label: "POI Proximity ATR Buffer", value: "0.8" },
    { label: "Zone Lookback", value: "80 bars" }, { label: "Zone / Label Right Offset", value: "2 bars" },
  ] },
  { title: "Reference Visual Colours", inputs: [
    { label: "Bullish / Bearish Structure", value: "#089981 / #F23645" }, { label: "Internal Bullish / Bearish OB", value: "#3179F5 / #F77C80 · 80% transparent" },
    { label: "Swing Bullish / Bearish OB", value: "#1848CC / #B22833 · 80% transparent" }, { label: "Bullish / Bearish FVG", value: "#00FF68 / #FF0008 · 70% transparent" },
    { label: "Premium / Equilibrium / Discount", value: "#F23645 / #878B94 / #089981" }, { label: "Colour Candles", value: "Disabled" },
  ] },
];

const defaultFilters: NativeSMCOverlayFilters = { pivots: false, internal: false, swing: false, structure: false, liquidity: false, fvg: false, orderBlocks: false, mitigated: false, labels: false };
const shortId = (id?: string | null) => id ? `${id.slice(0, 10)}…` : "—";
const at = (value?: string | null) => value ? value.replace("T", " ").replace("+00:00", " UTC").slice(0, 23) : "—";
const bias = (value?: number) => value === 1 ? "Bullish" : value === -1 ? "Bearish" : "Neutral";
const category = (value: string) => value.split("_").join(" ").toUpperCase();
const toUtc = (value: string) => value ? new Date(value).toISOString() : "";

function mergeCandles(...groups: NativeCandle[][]): NativeCandle[] {
  const byTimestamp = new Map<string, NativeCandle>();
  for (const group of groups) for (const candle of group) byTimestamp.set(candle.timestamp, candle);
  return [...byTimestamp.values()].sort((left, right) => left.timestamp.localeCompare(right.timestamp));
}

function Toggle({ label, enabled, onClick }: { label: string; enabled: boolean; onClick: () => void }) {
  return <button type="button" className={`btn ${enabled ? "btn-primary" : "btn-soft"}`} style={{ padding: "6px 9px", fontSize: 11 }} onClick={onClick} aria-pressed={enabled}>{label}</button>;
}

function VerdictPanel({ snapshot, selectedObjectId, data }: { snapshot?: NativeSnapshot | null; selectedObjectId?: string; data?: NativeState }) {
  const fvg = snapshot?.active_fvg_ids.map((id) => data?.fair_value_gaps.find((row) => row.id === id)).find(Boolean);
  const ob = snapshot?.active_ob_ids.map((id) => data?.order_blocks.find((row) => row.id === id)).find(Boolean);
  const setup = snapshot?.active_setup_id ? data?.setups.find((row) => row.id === snapshot.active_setup_id) : undefined;
  const verdict = snapshot?.setup_phase ?? "WATCHLIST";
  const reason = snapshot ? [snapshot.session === "inactive" ? "Outside active session" : null, !fvg && !ob ? "No active POI" : null, snapshot.latest_sweep_id ? "Liquidity sweep observed" : "Awaiting liquidity"].filter(Boolean).join(" · ") : "Select a closed candle";
  const rows: [string, string][] = [
    ["HTF", bias(snapshot?.htf_bias)], ["Swing", bias(snapshot?.swing_bias)], ["Internal", bias(snapshot?.internal_bias)],
    ["Location", snapshot?.dealing_range.area?.toUpperCase() ?? "—"], ["POI", fvg ? `FVG ${shortId(fvg.id)}` : ob ? `OB ${shortId(ob.id)}` : "None"],
    ["Liquidity", snapshot?.latest_sweep_id ? `Sweep ${shortId(snapshot.latest_sweep_id)}` : "None recorded"],
    ["Structure", snapshot?.event_ids.length ? `${snapshot.event_ids.length} native event(s)` : "No event"],
    ["Session", snapshot?.session ?? "—"], ["Rejection", snapshot?.price_action.bullish_rejection ? "Bullish" : snapshot?.price_action.bearish_rejection ? "Bearish" : "None"],
    ["Setup", setup?.phase ?? snapshot?.setup_phase ?? "IDLE"], ["Action", snapshot?.setup_phase === "ENTRY_READY" ? "RESEARCH ONLY" : "WAIT"],
  ];
  return <Card title="Native SMC verdict" subtitle="factual snapshot · no probabilities">
    <div className="instance-risk-notice amber"><b>FINAL VERDICT · {verdict}</b><br /><span className="dim">Reason: {reason}</span><br /><span className="dim">Next: {snapshot?.next_required_event ?? "Select a closed candle"}</span></div>
    <div className="risk-list terminal" style={{ marginTop: 10 }}>{rows.map(([label, value]) => <div className="risk-item" key={label}><span>{label}</span><b>{value}</b></div>)}</div>
    <div className="dim" style={{ marginTop: 10, fontSize: 11 }}>Selected object: <code>{shortId(selectedObjectId)}</code></div>
  </Card>;
}

function CandleInspector({ candle, snapshot, data }: { candle?: NativeCandle; snapshot?: NativeSnapshot | null; data?: NativeState }) {
  const activeFvgs = snapshot?.active_fvg_ids.map((id) => data?.fair_value_gaps.find((row) => row.id === id)).filter(Boolean) ?? [];
  const activeObs = snapshot?.active_ob_ids.map((id) => data?.order_blocks.find((row) => row.id === id)).filter(Boolean) ?? [];
  const proposal = snapshot?.proposal_ids.map((id) => data?.proposals.find((row) => row.id === id)).find(Boolean);
  if (!candle || !snapshot) return <Card title="Selected closed candle" subtitle="click a candle or choose a review item"><EmptyState text="No historical snapshot selected." /></Card>;
  return <Card title="Selected closed candle" subtitle={at(candle.timestamp)}>
    <div className="risk-list terminal">
      <div className="risk-item"><span>OHLCV</span><b>O {candle.open} · H {candle.high} · L {candle.low} · C {candle.close} · V {candle.volume}</b></div>
      <div className="risk-item"><span>Bias</span><b>{bias(snapshot.htf_bias)} HTF · {bias(snapshot.swing_bias)} swing · {bias(snapshot.internal_bias)} internal</b></div>
      <div className="risk-item"><span>Location</span><b>{snapshot.dealing_range.area.toUpperCase()} · EQ {snapshot.dealing_range.equilibrium}</b></div>
      <div className="risk-item"><span>Structure</span><b>{snapshot.event_ids.length ? snapshot.event_ids.map(shortId).join(" ") : "None"}</b></div>
      <div className="risk-item"><span>Liquidity</span><b>{shortId(snapshot.latest_sweep_id)}</b></div>
      <div className="risk-item"><span>FVG / OB</span><b>{activeFvgs.map((row: any) => shortId(row.id)).join(" ") || "—"} / {activeObs.map((row: any) => shortId(row.id)).join(" ") || "—"}</b></div>
      <div className="risk-item"><span>Rejection</span><b>{snapshot.price_action.bullish_rejection ? "Bullish" : snapshot.price_action.bearish_rejection ? "Bearish" : "None"}</b></div>
      <div className="risk-item"><span>Setup / next</span><b>{snapshot.setup_phase ?? "IDLE"} · {snapshot.next_required_event}</b></div>
      <div className="risk-item"><span>Proposal</span><b>{proposal ? `${shortId(proposal.id)} · E ${proposal.entry} · SL ${proposal.stop} · TP ${proposal.target}` : "None"}</b></div>
    </div>
  </Card>;
}

function PineReferencePanel({ reference, error }: { reference: PineReference | null; error: string | null }) {
  if (error) return <div className="instance-risk-notice red">{error}</div>;
  if (!reference) return <EmptyState text="Loading the immutable Pine reference…" />;
  return <Card title="Pine reference source" subtitle={`${reference.reference_id} · ${reference.status} · SHA-256 ${reference.sha256.slice(0, 12)}…`}>
    <div className="instance-risk-notice amber"><b>Reference only — not executed.</b> {reference.notice}</div>
    <pre aria-label="Read-only Pine reference source" style={{ margin: "12px 0 0", maxHeight: "calc(100vh - 300px)", minHeight: 560, overflow: "auto", padding: 16, borderRadius: 8, background: "#0a0c10", border: "1px solid var(--border)", color: "#d8deea", fontSize: 12, lineHeight: 1.55, whiteSpace: "pre" }}>{reference.content}</pre>
  </Card>;
}

function StrategyLadderTrace({ candidate, onObjectSelect }: { candidate?: LadderCandidate; onObjectSelect: (id: string) => void }) {
  if (!candidate) return <Card title="SMC strategy ladder" subtitle="frozen native-object research definitions"><EmptyState text="Loading the read-only candidate trace…" /></Card>;
  const trace = candidate.selected_trace;
  return <Card title="SMC strategy ladder" subtitle={`${candidate.strategy_id} · ${candidate.research_status} · execution disabled`}>
    <div className="instance-risk-notice amber"><b>{candidate.state}</b><br /><span className="dim">{candidate.next_required_event}</span></div>
    {trace ? <><div className="smc-ladder-meta"><span>Direction <b>{trace.direction}</b></span><span>Setup <code>{shortId(trace.setup_id)}</code></span></div><div className="smc-ladder-conditions">{trace.conditions.map((condition) => <button type="button" key={`${condition.key}-${condition.object_id ?? "none"}`} className={`smc-ladder-condition ${condition.status.toLowerCase()}`} onClick={() => condition.object_id && onObjectSelect(condition.object_id)} disabled={!condition.object_id}><span>{condition.label}</span><b>{condition.status.replace("_", " ")}</b><small>{condition.detail}{condition.bars_since !== undefined && condition.bars_since !== null ? ` · ${condition.bars_since} bars` : ""}</small></button>)}</div>{trace.invalidation_reason ? <div className="instance-risk-notice red">{trace.invalidation_reason}</div> : null}</> : <EmptyState text="No directional trace is available for this native closed-bar state." />}
  </Card>;
}

function SourceStrategyPanel({ evaluation, onObjectSelect }: { evaluation?: SMCSourceStrategyEvaluation | null; onObjectSelect: (id: string) => void }) {
  if (!evaluation) return <Card title="SMC strategy V1" subtitle="source-informed paper strategy"><EmptyState text="Loading the strategy decision…" /></Card>;
  const plan = evaluation.trade_plan;
  return <Card title="SMC strategy V1" subtitle={`${evaluation.version} · paper only`}>
    <div className={`instance-risk-notice ${evaluation.state === "ENTRY_READY" ? "amber" : "green"}`}>
      <b>{evaluation.model.label.toUpperCase()} · {evaluation.state}</b><br />
      <span className="dim">{evaluation.next_required_event}</span>
    </div>
    <div className="risk-list terminal" style={{ marginTop: 10 }}>
      <div className="risk-item"><span>Native route</span><b>{evaluation.selected_candidate_id ?? "Watching"}</b></div>
      <div className="risk-item"><span>Risk</span><b>{plan ? `${plan.risk_percent}% paper risk` : "No risk allocated"}</b></div>
      <div className="risk-item"><span>Entry / stop</span><b>{plan ? `${plan.entry} / ${plan.stop}` : "—"}</b></div>
      <div className="risk-item"><span>Target 1 · 50%</span><b>{plan ? `${plan.target_1} · ${plan.target_1_r.toFixed(2)}R` : "—"}</b></div>
      <div className="risk-item"><span>Target 2 · 50%</span><b>{plan ? `${plan.target_2} · ${plan.target_2_r.toFixed(2)}R` : "—"}</b></div>
    </div>
    {evaluation.ordered_condition_results.length ? <div className="smc-ladder-conditions" style={{ marginTop: 10 }}>{evaluation.ordered_condition_results.map((condition) => <button type="button" key={`${condition.key}-${condition.object_id ?? "none"}`} className={`smc-ladder-condition ${condition.status.toLowerCase()}`} onClick={() => condition.object_id && onObjectSelect(condition.object_id)} disabled={!condition.object_id}><span>{condition.label}</span><b>{condition.status.replace("_", " ")}</b><small>{condition.detail}</small></button>)}</div> : null}
    {evaluation.missing_conditions.length ? <div className="instance-risk-notice amber" style={{ marginTop: 10 }}><b>Missing conditions</b><br />{evaluation.missing_conditions.join(" · ")}</div> : null}
    <div className="dim" style={{ marginTop: 10, fontSize: 11 }}>Live execution is disabled. A strategy-ready state is a paper candidate, never an order.</div>
  </Card>;
}

function SMCPaperSidebar({ state, modelId, onRefresh }: { state?: SMCPaperState | null; modelId: string; onRefresh: () => Promise<boolean> }) {
  const [mode, setMode] = useState("signals_only");
  const [risk, setRisk] = useState("0.5");
  const [leverage, setLeverage] = useState("1");
  const [side, setSide] = useState("buy");
  const [orderType, setOrderType] = useState("market");
  const [quantity, setQuantity] = useState("0.001");
  const [limit, setLimit] = useState("");
  const [trigger, setTrigger] = useState("");
  const [stop, setStop] = useState("");
  const [target1, setTarget1] = useState("");
  const [target2, setTarget2] = useState("");
  const [resetPhrase, setResetPhrase] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!state) return;
    setMode(state.session.operating_mode); setRisk(String(state.session.risk_pct)); setLeverage(String(state.account.leverage));
  }, [state?.session.id, state?.session.operating_mode, state?.session.risk_pct, state?.account.leverage]);
  const act = async (task: () => Promise<unknown>, success: string) => {
    setBusy(true); setMessage(null);
    try { await task(); setMessage(success); await onRefresh(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "SMC paper action failed"); }
    finally { setBusy(false); }
  };
  if (!state) return <aside className="smc-watchlist" aria-label="SMC paper account"><EmptyState text="Loading isolated SMC paper account…" /></aside>;
  const account = state.account;
  return <aside className="smc-watchlist smc-account-sidebar" aria-label="SMC paper account and controls">
    <div className="smc-watchlist-head"><div><b>SMC PAPER ACCOUNT</b><span>isolated · USDT</span></div><Badge text="PAPER" tone="green" /></div>
    <div className="risk-list terminal" style={{ padding: 10 }}>
      {[["Balance", account.balance], ["Equity", account.equity], ["Available margin", account.available_margin], ["Used margin", account.used_margin], ["Open risk", account.open_risk], ["Unrealized P&L", account.unrealized_pnl]].map(([label, value]) => <div className="risk-item" key={String(label)}><span>{label}</span><b>{Number(value).toFixed(2)} USDT</b></div>)}
    </div>
    <div className="smc-account-controls">
      <Field label="Operating mode"><select value={mode} onChange={(event) => setMode(event.target.value)}><option value="signals_only">Signals only</option><option value="manual_approval">Manual approval</option><option value="automatic">Automatic paper</option></select></Field>
      <Field label="Risk per trade (%)"><input type="number" min="0.01" max="1" step="0.01" value={risk} onChange={(event) => setRisk(event.target.value)} /></Field>
      <button className="btn btn-primary" disabled={busy} type="button" onClick={() => act(() => apiPostJson("/research/smc/sessions/current/configuration", { operating_mode: mode, model_id: modelId, risk_pct: Number(risk) }), "Paper configuration saved")}>Apply configuration</button>
      <Field label="Paper leverage"><select value={leverage} onChange={(event) => setLeverage(event.target.value)}>{[1, 2, 3, 5, 10].map((row) => <option key={row} value={row}>{row}x</option>)}</select></Field>
      <button className="btn btn-soft" disabled={busy} type="button" onClick={() => act(() => apiPostJson("/research/smc/paper/leverage", { leverage: Number(leverage) }), "Paper leverage saved")}>Confirm leverage</button>
    </div>
    <details className="smc-account-ticket" open><summary>Manual paper order</summary><div className="smc-account-controls">
      <Field label="Side"><select value={side} onChange={(event) => setSide(event.target.value)}><option value="buy">Buy / Long</option><option value="sell">Sell / Short</option></select></Field>
      <Field label="Type"><select value={orderType} onChange={(event) => setOrderType(event.target.value)}><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option></select></Field>
      <Field label="Quantity"><input value={quantity} onChange={(event) => setQuantity(event.target.value)} inputMode="decimal" /></Field>
      {orderType === "limit" ? <Field label="Limit price"><input value={limit} onChange={(event) => setLimit(event.target.value)} inputMode="decimal" /></Field> : null}
      {orderType === "stop" ? <Field label="Trigger price"><input value={trigger} onChange={(event) => setTrigger(event.target.value)} inputMode="decimal" /></Field> : null}
      <Field label="Stop loss"><input value={stop} onChange={(event) => setStop(event.target.value)} inputMode="decimal" /></Field>
      <Field label="Target 1"><input value={target1} onChange={(event) => setTarget1(event.target.value)} inputMode="decimal" /></Field>
      <Field label="Target 2"><input value={target2} onChange={(event) => setTarget2(event.target.value)} inputMode="decimal" /></Field>
      <button className="btn btn-primary" disabled={busy} type="button" onClick={() => act(() => apiPostJson("/research/smc/paper/orders", { symbol: state.session.symbol, side, type: orderType, quantity: Number(quantity), limit_price: limit ? Number(limit) : null, trigger_price: trigger ? Number(trigger) : null, stop_loss: stop ? Number(stop) : null, target_1: target1 ? Number(target1) : null, target_2: target2 ? Number(target2) : null }, { "Idempotency-Key": `smc-manual-${Date.now()}` }), "Manual paper order accepted")}>Review & submit paper order</button>
    </div></details>
    <details className="smc-account-ticket"><summary>Reset SMC paper account</summary><div className="smc-account-controls"><p className="dim">Preserves prior session evidence. Type RESET SMC PAPER.</p><input aria-label="SMC reset confirmation" value={resetPhrase} onChange={(event) => setResetPhrase(event.target.value)} /><button className="btn btn-danger" type="button" disabled={busy || resetPhrase !== "RESET SMC PAPER"} onClick={() => act(() => apiPostJson("/research/smc/paper/reset", { confirmation: resetPhrase }), "SMC paper account reset")}>Reset SMC paper</button></div></details>
    {message ? <div className="instance-risk-notice amber" role="status" style={{ margin: 10 }}>{message}</div> : null}
    <p>PAPER ONLY · real exchange orders are structurally disabled.</p>
  </aside>;
}

function SMCTradingToolbar({ symbol, timeframe, chartFeed, live, lastPrice, reviewProgress, showObjects, showJump, autoFollowLatest, candidateId, candidates, modelId, models, onSymbolChange, onTimeframeChange, onFeedChange, onCandidateChange, onModelChange, onToggleObjects, onToggleJump, onFit, onLatest, onCompare, onAutoScale, onOpenSettings, onFullScreen }: {
  symbol: string; timeframe: string; live: boolean; lastPrice?: number; onSymbolChange: (symbol: string) => void;
  chartFeed: ChartFeed; reviewProgress: string; showObjects: boolean; showJump: boolean; autoFollowLatest: boolean;
  candidateId: string; candidates: LadderCandidate[]; onCandidateChange: (candidate: string) => void;
  modelId: string; models: SMCSourceModel[]; onModelChange: (model: string) => void;
  onTimeframeChange: (timeframe: string) => void; onFeedChange: (feed: ChartFeed) => void;
  onToggleObjects: () => void; onToggleJump: () => void; onFit: () => void; onLatest: () => void; onCompare: () => void; onAutoScale: () => void; onOpenSettings: () => void; onFullScreen: () => void;
}) {
  return <div className="smc-terminal-toolbar" aria-label="SMC chart toolbar">
    <div className="smc-terminal-market"><span className={`pulse-dot ${live ? "green" : "gold"}`} /><select aria-label="Chart market" value={symbol} onChange={(event) => onSymbolChange(event.target.value)}>{LIVE_SYMBOLS.map((row) => <option key={row}>{row}</option>)}</select></div>
    <div className="smc-timeframe-group" role="group" aria-label="Chart timeframe">{CHART_TIMEFRAMES.map((row) => <button key={row} type="button" className={row === timeframe ? "active" : ""} aria-pressed={row === timeframe} onClick={() => onTimeframeChange(row)}>{row}</button>)}</div>
    <select className="smc-toolbar-select" aria-label="Chart data source" value={chartFeed} onChange={(event) => onFeedChange(event.target.value as ChartFeed)}><option value="binance_usdm">Binance USDⓈ-M Futures</option><option value="checkpoint">Verified March checkpoint</option><option value="mexc_perpetual">MEXC perpetual · alternate</option><option value="kraken_spot">Kraken spot · alternate</option></select>
    <select className="smc-toolbar-select" aria-label="SMC entry model" value={modelId} onChange={(event) => onModelChange(event.target.value)}>{models.map((model) => <option key={model.id} value={model.id} disabled={model.status === "PARKED"}>{model.label} · {model.status === "PARKED" ? "Parked" : "Active"}</option>)}</select>
    <select className="smc-toolbar-select" aria-label="Frozen SMC research candidate" value={candidateId} onChange={(event) => onCandidateChange(event.target.value)}>{candidates.map((candidate) => <option key={candidate.strategy_id} value={candidate.strategy_id}>{candidate.strategy_id.replace("SMC_", "").replace(/_/g, " ")} · {candidate.state}</option>)}</select>
    <span className="smc-toolbar-mode">Candles</span><span className="smc-research-chip">SMC NATIVE V1 · RESEARCH</span>
    <div className="smc-terminal-actions"><span className="smc-live-quote">{lastPrice ? lastPrice.toLocaleString(undefined, { maximumFractionDigits: 4 }) : "Closed bars"}</span><button className={`btn ${showJump ? "btn-primary" : "btn-soft"}`} type="button" onClick={onToggleJump}>⌕ Time</button><button className={`btn ${showObjects ? "btn-primary" : "btn-soft"}`} type="button" onClick={onToggleObjects}>Objects</button><button className="btn btn-soft" type="button" onClick={onFit}>Fit visible structure</button><button className="btn btn-soft" type="button" onClick={onAutoScale}>Auto scale</button><button className={`btn ${autoFollowLatest ? "btn-soft" : "btn-primary"}`} type="button" onClick={onLatest}>{autoFollowLatest ? "Latest" : "Go to latest"}</button><button className="btn btn-soft" type="button" onClick={onCompare}>Compare</button><button className="btn btn-soft" type="button" onClick={onOpenSettings}>Settings</button><button className="btn btn-primary" type="button" onClick={onFullScreen}>Full screen</button><span className="smc-toolbar-progress">{reviewProgress}</span></div>
  </div>;
}

function IndicatorAndChartSettings({ lightChart, setLightChart, visibleBars, setVisibleBars, rightOffsetBars, setRightOffsetBars, liveRefreshMs, setLiveRefreshMs, setFitSignal }: {
  lightChart: boolean; setLightChart: (value: boolean) => void; visibleBars: number; setVisibleBars: (value: number) => void;
  rightOffsetBars: number; setRightOffsetBars: (value: number) => void; liveRefreshMs: number; setLiveRefreshMs: (value: number) => void; setFitSignal: Dispatch<SetStateAction<number>>;
}) {
  return <div className="smc-settings-layout">
    <Card title="Chart settings" subtitle="active display preferences — they never alter native SMC research state">
      <div className="form-grid smc-settings-grid">
        <Field label="Visible candles"><select value={visibleBars} onChange={(event) => setVisibleBars(Number(event.target.value))}><option value={100}>100 · close focus</option><option value={120}>120 · default intraday</option><option value={150}>150 · wider intraday</option><option value={240}>240 · extended view</option><option value={400}>400 · broad context</option></select></Field>
        <Field label="Right-edge space"><select value={rightOffsetBars} onChange={(event) => setRightOffsetBars(Number(event.target.value))}><option value={0}>None · last candle at edge</option><option value={6}>6 bars · compact</option><option value={12}>12 bars · standard</option><option value={24}>24 bars · extended</option><option value={48}>48 bars · planning room</option><option value={96}>96 bars · latest near centre</option><option value={160}>160 bars · wide future space</option></select></Field>
        <Field label="Live refresh cadence"><select value={liveRefreshMs} onChange={(event) => setLiveRefreshMs(Number(event.target.value))}><option value={2500}>Exchange cadence · about 2.5 seconds</option><option value={5000}>Every 5 seconds</option><option value={10000}>Every 10 seconds</option></select></Field>
        <Field label="Chart theme"><select value={lightChart ? "light" : "dark"} onChange={(event) => setLightChart(event.target.value === "light")}><option value="dark">Nexus dark</option><option value="light">Light</option></select></Field>
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}><button className="btn btn-primary" type="button" onClick={() => setFitSignal((value) => value + 1)}>Reset chart view</button><span className="dim" style={{ alignSelf: "center", fontSize: 12 }}>Wheel zooms · drag pans · hovering never moves the chart.</span></div>
      <div className="instance-risk-notice amber" style={{ marginTop: 12 }}><b>Premium / discount range.</b> The chart now selects a recent range automatically from the active timeframe (rather than using one fixed all-history range). This changes the display only; native SMC snapshot calculations remain unchanged.</div>
      <div className="instance-risk-notice amber" style={{ marginTop: 12 }}><b>Live display boundary.</b> A forming candle may move visually, but it is not supplied to the native SMC engine. Structure, zones, snapshots, and execution authority remain closed-bar-only.</div>
    </Card>
    <Card title="Pine indicator inputs" subtitle="immutable SMC PRO v2 reference defaults · shown for TradingView comparison">
      <div className="instance-risk-notice amber"><b>Read-only reference.</b> These are the original Pine defaults. Change them in TradingView when comparing the reference script; this Lab does not reconfigure the Pine source or native SMC model.</div>
      <div className="smc-reference-input-groups">{REFERENCE_INPUT_GROUPS.map((group) => <details key={group.title} open><summary>{group.title}<span>{group.inputs.length} inputs</span></summary><div className="smc-reference-inputs">{group.inputs.map((input) => <label key={input.label}><span>{input.label}</span><input value={input.value} readOnly aria-label={`${group.title}: ${input.label}`} /></label>)}</div></details>)}</div>
    </Card>
  </div>;
}

export default function NativeSMCVisualPage() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("5m");
  const [selectedId, setSelectedId] = useState("");
  const [selectedCandle, setSelectedCandle] = useState("");
  const [jumpValue, setJumpValue] = useState("");
  const [filters, setFilters] = useState(defaultFilters);
  const [lightChart, setLightChart] = useState(false);
  const [workspace, setWorkspace] = useState<"chart" | "pine" | "settings">("chart");
  const [chartFeed, setChartFeed] = useState<ChartFeed>("binance_usdm");
  const [fullChart, setFullChart] = useState(false);
  const [fitSignal, setFitSignal] = useState(0);
  const [visibleBars, setVisibleBars] = useState(() => defaultVisibleBars("5m"));
  const [rightOffsetBars, setRightOffsetBars] = useState(12);
  const [liveRefreshMs, setLiveRefreshMs] = useState(2_500);
  const [smcPanelCollapsed, setSmcPanelCollapsed] = useState(() => localStorage.getItem("tradexa.smc.smcPanelCollapsed") === "1");
  const [showObjects, setShowObjects] = useState(false);
  const [showJump, setShowJump] = useState(false);
  const [bottomTab, setBottomTab] = useState<"positions" | "orders" | "trades" | "setups" | "rejected" | "journal" | "sessions" | "connection" | "native_review">("positions");
  const [selectedCandidateId, setSelectedCandidateId] = useState("SMC_S1_PIVOT_REVERSAL");
  const [selectedModelId, setSelectedModelId] = useState("SMC_M1_SWEEP_REVERSAL");
  const [latestSignal, setLatestSignal] = useState(0);
  const [autoFollowLatest, setAutoFollowLatest] = useState(true);
  const [priceViewport, setPriceViewport] = useState<ChartPriceViewport>({ auto: true, scale: 1, offset: 0 });
  const [timeViewport, setTimeViewport] = useState<ChartTimeViewport | null>(null);
  const [olderCandles, setOlderCandles] = useState<NativeCandle[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [hasMoreHistory, setHasMoreHistory] = useState(true);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyPrepend, setHistoryPrepend] = useState({ version: 0, count: 0 });
  // The chart can emit several near-left-edge viewport updates for a single
  // drag. Keep one request in flight so those events cannot fetch duplicate
  // exchange pages before React has rendered `historyLoading`.
  const historyRequestRef = useRef(false);
  const [classification, setClassification] = useState<ReviewClassification>("CORRECT");
  const [reason, setReason] = useState("");
  const [expectedStructure, setExpectedStructure] = useState("");
  const [actualStructure, setActualStructure] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [selectedJournalId, setSelectedJournalId] = useState("");
  const [journalNote, setJournalNote] = useState("");
  const sample = useLive<ReviewSampleResponse>(`/research/smc/review-sample?symbol=${symbol}&timeframe=${timeframe}`, 15_000);
  const sourceModels = useLive<SMCSourceModelsResponse>("/research/smc/strategy-models", 600_000);
  const paper = useLive<SMCPaperState>("/research/smc/paper", 5_000);
  const journal = useLive<SMCJournalResponse>(`/research/smc/journal?symbol=${symbol}&timeframe=${timeframe}`, 5_000);
  const sessions = useLive<SMCSessionsResponse>("/research/smc/sessions", 15_000);
  // A fresh visual session deliberately opens on the newest working bars.
  // A review only becomes a navigation target after the user selects it.
  const selectedSample = sample.data?.sample.find((row) => row.object_id === selectedId);
  const focusedAt = chartFeed === "checkpoint" ? (selectedCandle || selectedSample?.timestamp || "") : "";
  const chartPath = chartFeed === "checkpoint"
    ? `/research/smc/chart?symbol=${symbol}&timeframe=${timeframe}&window=800&model_id=${encodeURIComponent(selectedModelId)}${focusedAt ? `&at=${encodeURIComponent(focusedAt)}` : ""}`
    // Keep a full recent history loaded behind the initial screen window.
    // `visibleBars` controls only the initial chart viewport; sending it to
    // the API used to discard all older candles, leaving nothing to drag back
    // into on a live chart.
    : `/research/smc/live-chart?symbol=${symbol}&timeframe=${timeframe}&venue=${chartFeed}&window=800&visible=800&model_id=${encodeURIComponent(selectedModelId)}`;
  const state = useLive<NativeState & { data_provenance?: DataProvenance }>(chartPath, chartFeed === "checkpoint" ? 5_000 : liveRefreshMs);
  const reviews = useLive<ReviewsResponse>(`/research/smc/reviews?symbol=${symbol}&timeframe=${timeframe}`, 15_000);
  const pineReference = useLive<PineReference>("/research/smc/pine-reference", 600_000);
  const sourceStrategy = useLive<SMCSourceStrategyEvaluation>(`/research/smc/strategy-v1/evaluate?symbol=${symbol}&timeframe=${timeframe}&model_id=${encodeURIComponent(selectedModelId)}${focusedAt ? `&at=${encodeURIComponent(focusedAt)}` : ""}`, 5_000);
  const data = state.data;
  const chartData = useMemo(() => data ? { ...data, candles: mergeCandles(olderCandles, data.candles) } : null, [data, olderCandles]);
  const reviewItems = sample.data?.sample ?? [];
  const reviewIndex = Math.max(0, reviewItems.findIndex((row) => row.object_id === selectedId));
  const selectedObjectId = selectedId || undefined;
  const selectedSnapshot = data?.snapshot_ledger.find((row) => row.candle_open === focusedAt) ?? data?.selected_snapshot ?? data?.snapshot;
  const selectedRow = data?.candles.find((row) => row.timestamp === (selectedSnapshot?.candle_open ?? focusedAt));
  const ladderCandidates = data?.strategy_ladder?.candidates ?? [];
  const selectedCandidate = ladderCandidates.find((candidate) => candidate.strategy_id === selectedCandidateId) ?? ladderCandidates[0];
  const highlightedObjectIds = useMemo(() => selectedCandidate?.selected_trace?.supporting_object_ids ?? [], [selectedCandidate]);
  const strategyEvaluation = chartFeed === "checkpoint" ? sourceStrategy.data : data?.source_strategy;
  const strategyTradePlan = strategyEvaluation?.trade_plan ?? null;
  const paperFillMarkers = useMemo(() => (paper.data?.activity ?? []).flatMap((row) => {
    if (row.kind !== "paper_fill" || !row.payload || typeof row.payload !== "object") return [];
    const payload = row.payload as Record<string, unknown>;
    const timestamp = typeof payload.candle_time === "string" ? payload.candle_time : null;
    const price = Number(payload.price);
    return timestamp && Number.isFinite(price) ? [{ timestamp, price,
      side: String(payload.side ?? ""), realized_pnl: Number(payload.realized_pnl ?? 0) }] : [];
  }), [paper.data?.activity]);
  const selectedJournal = journal.data?.journal.find((row) => row.journal_id === selectedJournalId) ?? null;
  const selectedReview = reviewItems[reviewIndex];
  const selectReview = useCallback((index: number) => {
    const item = reviewItems[index];
    if (!item) return;
    setSelectedId(item.object_id); setSelectedCandle(item.timestamp); setJumpValue(item.timestamp.slice(0, 16));
  }, [reviewItems]);
  const onSelectCandle = useCallback((value: string) => { setSelectedCandle(value); setJumpValue(value.slice(0, 16)); }, []);
  const setFilter = (key: keyof NativeSMCOverlayFilters) => setFilters((current) => ({ ...current, [key]: !current[key] }));
  const jumpToTime = () => { if (!jumpValue) return; setSelectedCandle(toUtc(jumpValue)); };
  const stepCandle = (direction: -1 | 1) => {
    const index = data?.candles.findIndex((row) => row.timestamp === (selectedSnapshot?.candle_open ?? focusedAt)) ?? -1;
    const next = data?.candles[index + direction];
    if (next) onSelectCandle(next.timestamp);
  };
  const goToLive = useCallback(() => {
    setAutoFollowLatest(true);
    setLatestSignal((value) => value + 1);
  }, []);
  useEffect(() => {
    historyRequestRef.current = false;
    setOlderCandles([]); setHasMoreHistory(true); setHistoryLoading(false); setHistoryError(null);
    setHistoryPrepend((current) => ({ version: current.version + 1, count: 0 }));
  }, [symbol, timeframe, chartFeed]);
  const requestOlderHistory = useCallback(async () => {
    if (chartFeed === "checkpoint" || historyRequestRef.current || historyLoading || !hasMoreHistory || !chartData?.candles.length) return;
    const before = chartData.candles[0].timestamp;
    historyRequestRef.current = true;
    setHistoryLoading(true); setHistoryError(null);
    try {
      const page = await apiGet<LiveHistoryPage>(`/research/smc/live-history?symbol=${symbol}&timeframe=${timeframe}&venue=${chartFeed}&before=${encodeURIComponent(before)}&limit=400`);
      const merged = mergeCandles(page.candles, olderCandles);
      const count = Math.max(0, merged.length - olderCandles.length);
      setOlderCandles(merged);
      setHasMoreHistory(page.has_more_history && count > 0);
      if (count > 0) setHistoryPrepend((current) => ({ version: current.version + 1, count }));
    } catch (error) {
      setHistoryError(error instanceof Error ? error.message : "Unable to load older exchange candles.");
    } finally { historyRequestRef.current = false; setHistoryLoading(false); }
  }, [chartFeed, chartData?.candles, hasMoreHistory, historyLoading, olderCandles, symbol, timeframe]);
  const handleViewportChange = useCallback((range: ChartTimeViewport) => {
    setTimeViewport(range);
    setAutoFollowLatest(range.end >= 99.2);
  }, []);
  const submitReview = async () => {
    if (!selectedObjectId || saving || !selectedReview) return;
    setSaving(true); setReviewError(null);
    try {
      await apiPostJson(`/research/smc/reviews?symbol=${symbol}&timeframe=${timeframe}`, {
        object_id: selectedObjectId, component: category(selectedReview.category), classification,
        reason: reason || null, expected_structure: expectedStructure || null, actual_structure: actualStructure || null,
        notes: notes || null, screenshot_timestamp: selectedSnapshot?.candle_open ?? null,
        selected_candle_timestamp: selectedSnapshot?.candle_open ?? null,
        visible_range_start: data?.candles[0]?.timestamp ?? null, visible_range_end: data?.candles[data.candles.length - 1]?.timestamp ?? null,
      });
      setReason(""); setExpectedStructure(""); setActualStructure(""); setNotes(""); await reviews.refetch();
    } catch (error) { setReviewError(error instanceof Error ? error.message : "Unable to save review evidence."); }
    finally { setSaving(false); }
  };
  const switchDataset = (nextSymbol: string, nextTimeframe = timeframe) => {
    // The attached visual-review checkpoint is BTCUSDT 5m only. Other views
    // should immediately use the explicit live venue rather than appear empty.
    if (chartFeed === "checkpoint" && (nextSymbol !== "BTCUSDT" || nextTimeframe !== "5m")) setChartFeed("binance_usdm");
    setSymbol(nextSymbol); setTimeframe(nextTimeframe); setSelectedId(""); setSelectedCandle(""); setJumpValue(""); setVisibleBars(defaultVisibleBars(nextTimeframe)); setTimeViewport(null); setPriceViewport({ auto: true, scale: 1, offset: 0 }); setAutoFollowLatest(true); setLatestSignal((value) => value + 1);
  };

  useEffect(() => {
    const isTyping = (target: EventTarget | null) => target instanceof HTMLInputElement || target instanceof HTMLSelectElement || target instanceof HTMLTextAreaElement || (target instanceof HTMLElement && target.isContentEditable);
    const onKeyDown = (event: KeyboardEvent) => {
      if (isTyping(event.target)) return;
      if (event.key === "Escape") { if (fullChart) setFullChart(false); else { setSelectedCandle(""); setSelectedId(""); } return; }
      if (event.key.toLowerCase() === "f") { event.preventDefault(); setFitSignal((value) => value + 1); setPriceViewport({ auto: true, scale: 1, offset: 0 }); return; }
      if (event.key.toLowerCase() === "l") { event.preventDefault(); goToLive(); return; }
      if (event.key === "ArrowLeft") { event.preventDefault(); event.shiftKey ? selectReview(Math.max(0, reviewIndex - 1)) : stepCandle(-1); }
      if (event.key === "ArrowRight") { event.preventDefault(); event.shiftKey ? selectReview(Math.min(reviewItems.length - 1, reviewIndex + 1)) : stepCandle(1); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullChart, reviewIndex, reviewItems.length, selectReview, goToLive]);

  useEffect(() => { localStorage.setItem("tradexa.smc.smcPanelCollapsed", smcPanelCollapsed ? "1" : "0"); }, [smcPanelCollapsed]);

  const adjustPriceViewport = useCallback((deltaY: number, verticalPan: boolean) => {
    setPriceViewport((current) => {
      const active = { ...current, auto: false };
      return verticalPan
        ? { ...active, offset: Math.max(-2, Math.min(2, active.offset + deltaY * 0.0025)) }
        : { ...active, scale: Math.max(0.2, Math.min(4, active.scale * Math.exp(deltaY * 0.008))) };
    });
  }, []);

  const chartSubtitle = data?.data_provenance
    ? `${data.data_provenance.venue} · ${data.data_provenance.market} · last closed ${at(data.data_provenance.last_closed_candle)}`
    : "native verified closed OHLCV";
  const liveDataStale = Boolean(chartFeed !== "checkpoint" &&
    (state.error || (data?.live_display?.connection_state && data.live_display.connection_state !== "SYNCHRONIZED")));
  const chartPanel = data ? <section className="smc-chart-surface" aria-label="Native SMC chart workspace">
    <div className="smc-chart-heading"><div><b>Nexus SMC chart</b><span>{chartSubtitle} · {data.candles.length} closed candles{data.forming_candle ? " + visual forming candle" : ""}</span></div><Badge text="CLOSED-BAR SMC" tone="green" /></div>
    {data.data_provenance ? <div className={`smc-live-strip ${liveDataStale ? "is-stale" : ""}`}><span className={`pulse-dot ${liveDataStale ? "gold" : "green"}`} /><span><b>{liveDataStale ? "Live feed stale" : "Live exchange feed"}</b> · observed {at(data.live_display?.observed_at)} · {liveDataStale ? "last confirmed price frozen" : "forming candle is display-only"}</span><span className="dim">Native SMC uses closed candles only</span></div> : <div className="smc-live-strip"><span className="pulse-dot gold" /><span><b>Verified March 2025 checkpoint</b> · frozen human-review evidence</span><span className="dim">Execution disabled</span></div>}
    <NativeSMCChartOverlay state={chartData ?? data} timeframe={timeframe} rightOffsetBars={rightOffsetBars} initialVisibleBars={visibleBars} filters={filters} selectedObjectId={selectedObjectId} highlightedObjectIds={highlightedObjectIds} onCandleSelect={onSelectCandle} fitContentSignal={fitSignal} latestSignal={latestSignal} centerTimestamp={selectedId ? selectedSnapshot?.candle_open : undefined} priceViewport={priceViewport} viewport={timeViewport} onViewportChange={handleViewportChange} onHistoryNearStart={requestOlderHistory} historyLoading={historyLoading} hasMoreHistory={hasMoreHistory} historicalMode={!autoFollowLatest} onGoLive={goToLive} prependedHistory={historyPrepend} onPriceAxisDrag={adjustPriceViewport} onResetPriceScale={() => setPriceViewport({ auto: true, scale: 1, offset: 0 })} lightMode={lightChart} liveDataStale={liveDataStale} tradePlan={strategyTradePlan} fillMarkers={paperFillMarkers} height={fullChart ? "calc(100vh - 250px)" : "min(68vh, 810px)"} />
    <div className="smc-chart-footer"><span><b>{autoFollowLatest ? "Live follow" : "Historical browse"}</b> · crosshair inspects closed candles without changing native SMC state{chartFeed !== "checkpoint" && !hasMoreHistory ? " · oldest page reached" : ""}{historyError ? ` · ${historyError}` : ""}</span><span>Drag chart to pan · wheel to zoom · hover for OHLCV · L / Latest returns to live · drag price scale to resize</span></div>
  </section> : null;

  const reviewTerminal = <><b>Frozen native-object review</b><div className="form-grid smc-review-fields"><Field label="Classification"><select value={classification} onChange={(event) => setClassification(event.target.value as ReviewClassification)}><option>CORRECT</option><option>INCORRECT</option><option>AMBIGUOUS</option></select></Field><Field label="Reason"><input value={reason} onChange={(event) => setReason(event.target.value)} /></Field><Field label="Expected"><input value={expectedStructure} onChange={(event) => setExpectedStructure(event.target.value)} /></Field><Field label="Native result"><input value={actualStructure} onChange={(event) => setActualStructure(event.target.value)} /></Field><Field label="Notes"><input value={notes} onChange={(event) => setNotes(event.target.value)} /></Field></div><button className="btn btn-primary" type="button" disabled={!selectedObjectId || saving || chartFeed !== "checkpoint"} onClick={submitReview}>{saving ? "Saving…" : "Save review evidence"}</button>{reviewError ? <div className="instance-risk-notice red" role="alert">{reviewError}</div> : null}</>;

  const terminalGrid = <div className={`smc-terminal-grid ${smcPanelCollapsed ? "panel-collapsed" : ""}`}>
    {!fullChart ? <SMCPaperSidebar state={paper.data} modelId={selectedModelId} onRefresh={paper.refetch} /> : null}
    {chartPanel}
    <aside className={`smc-state-panel ${smcPanelCollapsed ? "is-collapsed" : ""}`} aria-label="SMC state panel"><div className="smc-state-panel-head"><b>{smcPanelCollapsed ? "SMC" : "Native SMC state"}</b><button className="btn btn-soft" type="button" onClick={() => setSmcPanelCollapsed((value) => !value)}>{smcPanelCollapsed ? "‹" : "›"}</button></div>{!smcPanelCollapsed ? <><SourceStrategyPanel evaluation={chartFeed === "checkpoint" ? sourceStrategy.data : data?.source_strategy} onObjectSelect={setSelectedId} /><StrategyLadderTrace candidate={selectedCandidate} onObjectSelect={setSelectedId} /><VerdictPanel snapshot={selectedSnapshot} selectedObjectId={selectedObjectId} data={data ?? undefined} /><CandleInspector candle={selectedRow} snapshot={selectedSnapshot} data={data ?? undefined} /></> : null}</aside>
  </div>;

  const toRows = (rows: unknown[]) => rows as Record<string, unknown>[];
  const focusJournal = (row: SMCJournalRow) => {
    setSelectedJournalId(row.journal_id);
    if (row.signal_timestamp) { setSelectedCandle(row.signal_timestamp); setJumpValue(row.signal_timestamp.slice(0, 16)); }
    if (row.native_object_ids[0]) setSelectedId(row.native_object_ids[0]);
  };
  const journalPanel = <div className="smc-journal-panel"><div className="smc-table-actions"><b>SMC decision journal</b><button className="btn btn-soft" type="button" onClick={() => void apiDownload("/research/smc/journal/export?format=csv", "smc-journal.csv")}>Export CSV</button></div><EvidenceTable title="" rows={toRows(journal.data?.journal ?? [])} columns={[{ key: "signal_timestamp", label: "Signal" }, { key: "symbol", label: "Symbol" }, { key: "timeframe", label: "TF" }, { key: "direction", label: "Direction" }, { key: "status", label: "Status" }, { key: "rule_compliance", label: "Rules" }, { key: "net_pnl", label: "Net P&L" }]} onSelect={(row) => focusJournal(row as unknown as SMCJournalRow)} />{selectedJournal ? <aside className="smc-journal-detail" aria-label="Selected SMC journal evidence"><div><b>{selectedJournal.model_id}</b><span>{selectedJournal.proposal_id}</span></div><dl><dt>Data quality</dt><dd>{selectedJournal.data_quality}</dd><dt>Rule compliance</dt><dd>{selectedJournal.rule_compliance}</dd><dt>Native objects</dt><dd>{selectedJournal.native_object_ids.join(", ") || "—"}</dd><dt>Missing conditions</dt><dd>{selectedJournal.missing_conditions.join(", ") || "None"}</dd><dt>Plan</dt><dd>{selectedJournal.trade_plan ? `Entry ${selectedJournal.trade_plan.entry} · Stop ${selectedJournal.trade_plan.stop} · T1 ${selectedJournal.trade_plan.target_1} · T2 ${selectedJournal.trade_plan.target_2}` : "No executable plan"}</dd></dl><ol>{selectedJournal.ordered_conditions.map((condition) => <li key={condition.key}><b>{condition.status}</b> · {condition.label} — {condition.detail}</li>)}</ol>{selectedJournal.notes.map((note) => <p key={note.id}><b>Note · {at(note.created_at)}</b><br />{note.note}</p>)}<div className="smc-note-composer"><input aria-label="Append journal note" placeholder="Append a revision-safe note" value={journalNote} onChange={(event) => setJournalNote(event.target.value)} /><button className="btn btn-soft" type="button" disabled={!journalNote.trim()} onClick={() => void apiPostJson(`/research/smc/journal/${selectedJournal.journal_id}/notes`, { note: journalNote }).then(() => { setJournalNote(""); return journal.refetch(); })}>Append note</button></div></aside> : <p>Select a journal row to inspect its immutable conditions and chart evidence.</p>}</div>;
  const bottomContent = bottomTab === "positions" ? <EvidenceTable title="SMC paper positions" rows={toRows(paper.data?.positions ?? [])} columns={[{ key: "symbol", label: "Symbol" }, { key: "side", label: "Side" }, { key: "size", label: "Size" }, { key: "entry_price", label: "Entry" }, { key: "stop_loss", label: "Stop" }, { key: "take_profit", label: "T2" }, { key: "opened_at", label: "Opened" }]} />
    : bottomTab === "orders" ? <EvidenceTable title="SMC paper orders" rows={toRows(paper.data?.orders ?? [])} columns={[{ key: "symbol", label: "Symbol" }, { key: "side", label: "Side" }, { key: "type", label: "Type" }, { key: "quantity", label: "Qty" }, { key: "limit_price", label: "Limit" }, { key: "stop_price", label: "Trigger" }, { key: "status", label: "Status" }]} />
    : bottomTab === "trades" ? <EvidenceTable title="SMC paper fills" rows={toRows(paper.data?.trades ?? [])} columns={[{ key: "timestamp", label: "Time" }, { key: "symbol", label: "Symbol" }, { key: "side", label: "Side" }, { key: "quantity", label: "Qty" }, { key: "price", label: "Price" }, { key: "fee", label: "Fee" }, { key: "realized_pnl", label: "Realized" }]} />
    : bottomTab === "setups" ? <EvidenceTable title="Qualified and observed SMC setups" rows={toRows(paper.data?.candidates ?? [])} columns={[{ key: "created_at", label: "Detected" }, { key: "proposal_id", label: "Proposal" }, { key: "status", label: "Status" }, { key: "reason", label: "Reason" }]} />
    : bottomTab === "rejected" ? <EvidenceTable title="Rejected and paused SMC setups" rows={toRows((paper.data?.candidates ?? []).filter((row) => ["DATA_PAUSED", "REJECTED", "EXPIRED", "CONFLICT"].includes(row.status)))} columns={[{ key: "created_at", label: "Detected" }, { key: "proposal_id", label: "Proposal" }, { key: "status", label: "Status" }, { key: "reason", label: "Reason" }]} />
    : bottomTab === "journal" ? journalPanel
    : bottomTab === "sessions" ? <EvidenceTable title="SMC paper sessions" rows={toRows(sessions.data?.sessions ?? [])} columns={[{ key: "started_at", label: "Started" }, { key: "mode", label: "Mode" }, { key: "symbol", label: "Symbol" }, { key: "timeframe", label: "TF" }, { key: "operating_mode", label: "Execution" }, { key: "status", label: "Status" }, { key: "end_reason", label: "End reason" }]} />
    : bottomTab === "native_review" ? reviewTerminal
    : <div className="smc-connection-evidence"><b>{data?.live_display?.connection_state ?? "CHECKPOINT"}</b><span>{data?.live_display?.health_reason ?? "Verified historical checkpoint"}</span><span>Bid / Ask: {cell(data?.live_display?.bid)} / {cell(data?.live_display?.ask)}</span><span>Mark: {cell(data?.live_display?.mark)}</span><span>Quote age: {cell(data?.live_display?.quote_age_seconds)}s</span><span>Deviation: {cell(data?.live_display?.candle_quote_deviation_bps)} bps</span><span>{data?.live_display?.new_entries_paused ? "NEW PAPER ENTRIES PAUSED" : "CLOSED-BAR PAPER ENTRIES ELIGIBLE"}</span></div>;

  const chartWorkspace = state.error && !data ? <div className="instance-risk-notice red">{state.error}</div> : !data?.candles.length ? <EmptyState text={chartFeed === "checkpoint" ? "No verified closed-candle checkpoint is attached. Configure HUB_SMC_VISUAL_CHECKPOINT_PATH before reviewing native SMC." : "The selected live venue has not returned enough valid closed candles yet."} /> : <section className="smc-terminal-workspace">
    <SMCTradingToolbar symbol={symbol} timeframe={timeframe} chartFeed={chartFeed} live={Boolean(data.data_provenance)} lastPrice={data.live_display?.last_price} reviewProgress={chartFeed === "checkpoint" ? `${selectedId ? reviewIndex + 1 : 0}/${reviewItems.length || 0}` : "LIVE"} showObjects={showObjects} showJump={showJump} autoFollowLatest={autoFollowLatest} candidateId={selectedCandidate?.strategy_id ?? selectedCandidateId} candidates={ladderCandidates} modelId={selectedModelId} models={sourceModels.data?.models ?? []} onModelChange={setSelectedModelId} onCandidateChange={setSelectedCandidateId} onSymbolChange={switchDataset} onTimeframeChange={(value) => switchDataset(symbol, value)} onFeedChange={(feed) => { setChartFeed(feed); setSelectedCandle(""); setTimeViewport(null); goToLive(); }} onToggleObjects={() => setShowObjects((value) => !value)} onToggleJump={() => setShowJump((value) => !value)} onFit={() => { setTimeViewport(null); setFitSignal((value) => value + 1); setPriceViewport({ auto: true, scale: 1, offset: 0 }); }} onLatest={goToLive} onCompare={() => setWorkspace("pine")} onAutoScale={() => setPriceViewport({ auto: true, scale: 1, offset: 0 })} onOpenSettings={() => setWorkspace("settings")} onFullScreen={() => setFullChart(true)} />
    {showJump ? <div className="smc-compact-control-row"><label>Jump to UTC<input type="datetime-local" value={jumpValue} onChange={(event) => setJumpValue(event.target.value)} /></label><button className="btn btn-primary" type="button" onClick={jumpToTime}>Jump</button>{chartFeed === "checkpoint" ? <label>Review item<select value={selectedObjectId ?? ""} onChange={(event) => selectReview(reviewItems.findIndex((row) => row.object_id === event.target.value))}><option value="">Select review item</option>{reviewItems.map((row, index) => <option key={row.object_id} value={row.object_id}>{index + 1} · {category(row.category)} · {at(row.timestamp)}</option>)}</select></label> : null}<label>Right space<select value={rightOffsetBars} onChange={(event) => setRightOffsetBars(Number(event.target.value))}><option value={6}>6 bars</option><option value={12}>12 bars</option><option value={24}>24 bars</option><option value={48}>48 bars</option><option value={96}>96 bars</option><option value={160}>160 bars</option></select></label></div> : null}
    {showObjects ? <div className="smc-overlay-controls"><span className="dim">Visual objects</span>{([ ["Pivots", "pivots"], ["Internal", "internal"], ["Swing", "swing"], ["Structure", "structure"], ["Liquidity", "liquidity"], ["FVG", "fvg"], ["Order blocks", "orderBlocks"], ["Mitigated", "mitigated"], ["Labels", "labels"] ] as [string, keyof NativeSMCOverlayFilters][]).map(([label, key]) => <Toggle key={key} label={label} enabled={filters[key]} onClick={() => setFilter(key)} />)}<Toggle label="Light" enabled={lightChart} onClick={() => setLightChart((value) => !value)} /></div> : null}
    {terminalGrid}
    <section className="smc-bottom-terminal"><div className="smc-bottom-tabs">{([ ["positions", `Positions ${paper.data?.positions.length ?? 0}`], ["orders", `Orders ${paper.data?.orders.length ?? 0}`], ["trades", `Trades ${paper.data?.trades.length ?? 0}`], ["setups", `Setups ${paper.data?.candidates.length ?? 0}`], ["rejected", "Rejected"], ["journal", `Journal ${journal.data?.journal.length ?? 0}`], ["sessions", "Sessions"], ["connection", "Connection"], ["native_review", "Native review"] ] as const).map(([key, label]) => <button key={key} type="button" className={bottomTab === key ? "active" : ""} onClick={() => setBottomTab(key)}>{label}</button>)}</div><div className="smc-bottom-content">{bottomContent}</div></section>
    <footer className="smc-status-bar"><span>SMC_SOURCE_V1 · PAPER DRAFT</span><span>{symbol}</span><span>{timeframe}</span><span>{chartFeed === "checkpoint" ? "Verified March 2025" : "Live exchange display"}</span><span>Review {chartFeed === "checkpoint" ? `${reviewIndex + 1}/${reviewItems.length || 0}` : "—"}</span><span>LIVE EXECUTION DISABLED</span></footer>
  </section>;

  return <>
    <PageHeader title="SMC Strategy Lab" subtitle="Native closed-candle SMC · source-informed paper strategy workspace" actions={<><Badge text="SMC_SOURCE_V1 · PAPER" tone="purple" /> <Badge text="LIVE EXECUTION DISABLED" tone="red" /></>} />
    <div className="smc-workspace-tabs"><button className={`btn ${workspace === "chart" ? "btn-primary" : "btn-soft"}`} type="button" onClick={() => setWorkspace("chart")}>Chart terminal</button><button className={`btn ${workspace === "pine" ? "btn-primary" : "btn-soft"}`} type="button" onClick={() => setWorkspace("pine")}>Pine reference</button><button className={`btn ${workspace === "settings" ? "btn-primary" : "btn-soft"}`} type="button" onClick={() => setWorkspace("settings")}>Indicator & chart settings</button><span className="dim">Browser interactions only — no visual control can calculate SMC or create an order.</span></div>
    {workspace === "chart" ? chartWorkspace : workspace === "pine" ? <PineReferencePanel reference={pineReference.data} error={pineReference.error} /> : <IndicatorAndChartSettings lightChart={lightChart} setLightChart={setLightChart} visibleBars={visibleBars} setVisibleBars={(value) => { setVisibleBars(value); setTimeViewport(null); setFitSignal((signal) => signal + 1); }} rightOffsetBars={rightOffsetBars} setRightOffsetBars={setRightOffsetBars} liveRefreshMs={liveRefreshMs} setLiveRefreshMs={setLiveRefreshMs} setFitSignal={setFitSignal} />}
    {fullChart && chartPanel ? <div className="smc-fullscreen" role="dialog" aria-modal="true" aria-label="Full screen native SMC chart"><div className="smc-fullscreen-header"><div><span className="eyebrow">SMC RESEARCH TERMINAL</span><b>{symbol} · {timeframe} · {chartFeed === "checkpoint" ? "Verified checkpoint" : "Live exchange display"}</b></div><button className="btn btn-soft" type="button" onClick={() => setFullChart(false)}>Exit full screen · Esc</button></div>{terminalGrid}</div> : null}
  </>;
}
