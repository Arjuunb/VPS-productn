import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from "react";
import Card from "../components/common/Card";
import { Badge, EmptyState, Field, PageHeader } from "../components/common/ui";
import NativeSMCChartOverlay, {
  type NativeCandle, type NativeEvent, type NativePivot, type NativeProposal, type NativeSMCChartState,
  type NativeSMCOverlayFilters, type NativeSnapshot, type NativeZone,
} from "../components/chart/NativeSMCChartOverlay";
import { apiPostJson, useLive } from "../lib/api";

type ReviewClassification = "CORRECT" | "INCORRECT" | "AMBIGUOUS";
interface Setup { id: string; direction: "bullish" | "bearish"; phase: string; next_required_event: string; transitions: { id: string; timestamp: string; to_phase: string; reason: string; object_id?: string | null }[] }
interface NativeState extends NativeSMCChartState { pivots: NativePivot[]; events: NativeEvent[]; fair_value_gaps: NativeZone[]; order_blocks: NativeZone[]; proposals: NativeProposal[]; setups: Setup[] }
interface ReviewSampleItem { object_id: string; category: string; timestamp: string; setup_id?: string | null }
interface ReviewSampleResponse { sample: ReviewSampleItem[] }
interface Review { id: string; object_id: string; component: string; classification: ReviewClassification; reason?: string | null; notes?: string | null; selected_candle_timestamp?: string | null }
interface ReviewsResponse { reviews: Review[] }
interface PineReference { reference_id: string; status: string; language: string; sha256: string; execution_allowed: false; notice: string; content: string }
interface DataProvenance { mode: string; venue: string; market: string; observed_at: string; closed_candles_loaded: number; closed_candles_visible: number; last_closed_candle: string; forming_candle_excluded: boolean; execution_allowed: false }

type ReferenceInput = { label: string; value: string };
type ReferenceInputGroup = { title: string; inputs: ReferenceInput[] };
const LIVE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"];
const CHART_TIMEFRAMES = ["1m", "3m", "5m", "30m", "1h", "4h", "1d", "1w"];
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

const defaultFilters: NativeSMCOverlayFilters = { pivots: true, internal: true, swing: true, structure: true, liquidity: true, fvg: true, orderBlocks: true, mitigated: true, labels: true };
const shortId = (id?: string | null) => id ? `${id.slice(0, 10)}…` : "—";
const at = (value?: string | null) => value ? value.replace("T", " ").replace("+00:00", " UTC").slice(0, 23) : "—";
const bias = (value?: number) => value === 1 ? "Bullish" : value === -1 ? "Bearish" : "Neutral";
const category = (value: string) => value.split("_").join(" ").toUpperCase();
const toUtc = (value: string) => value ? new Date(value).toISOString() : "";

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

function SMCTradingToolbar({ symbol, timeframe, live, lastPrice, onSymbolChange, onTimeframeChange, onOpenSettings }: {
  symbol: string; timeframe: string; live: boolean; lastPrice?: number; onSymbolChange: (symbol: string) => void;
  onTimeframeChange: (timeframe: string) => void; onOpenSettings: () => void;
}) {
  return <div className="smc-terminal-toolbar" aria-label="SMC chart toolbar">
    <div className="smc-terminal-market"><span className="pulse-dot green" /><select aria-label="Chart market" value={symbol} onChange={(event) => onSymbolChange(event.target.value)}>{LIVE_SYMBOLS.map((row) => <option key={row}>{row}</option>)}</select><span className="dim">{live ? "Live exchange" : "Verified checkpoint"}</span></div>
    <div className="smc-timeframe-group" role="group" aria-label="Chart timeframe">{CHART_TIMEFRAMES.map((row) => <button key={row} type="button" className={row === timeframe ? "active" : ""} aria-pressed={row === timeframe} onClick={() => onTimeframeChange(row)}>{row}</button>)}</div>
    <div className="smc-terminal-actions"><span className="smc-live-quote">{lastPrice ? lastPrice.toLocaleString(undefined, { maximumFractionDigits: 4 }) : "Awaiting quote"}</span><span className="smc-research-chip">RESEARCH ONLY</span><button className="btn btn-soft" type="button" onClick={onOpenSettings}>Chart settings</button></div>
  </div>;
}

function SMCWatchlist({ symbol, activePrice, collapsed, onToggle, onSelect }: {
  symbol: string; activePrice?: number; collapsed: boolean; onToggle: () => void; onSelect: (symbol: string) => void;
}) {
  return <aside className={`smc-watchlist ${collapsed ? "is-collapsed" : ""}`} aria-label="Research market watchlist">
    <div className="smc-watchlist-head"><div><b>Market watchlist</b><span>supported research markets</span></div><button className="btn btn-soft" type="button" onClick={onToggle} aria-expanded={!collapsed}>{collapsed ? "Show" : "Hide"}</button></div>
    {!collapsed ? <><div className="smc-watchlist-columns"><span>Symbol</span><span>Last</span><span>Status</span></div><div className="smc-watchlist-rows">{LIVE_SYMBOLS.map((row) => {
      const selected = row === symbol;
      return <button type="button" className={selected ? "active" : ""} key={row} onClick={() => onSelect(row)}><span><i className={row.slice(0, 3).toLowerCase()}>{row.slice(0, 3)}</i>{row}</span><b>{selected && activePrice ? activePrice.toLocaleString(undefined, { maximumFractionDigits: 4 }) : "Select"}</b><em>{selected ? "Loaded" : "Open"}</em></button>;
    })}</div><p>Click a symbol to load its genuine live exchange display. Unselected rows never show invented prices.</p></> : null}
  </aside>;
}

function IndicatorAndChartSettings({ lightChart, setLightChart, visibleBars, setVisibleBars, rightOffsetBars, setRightOffsetBars, liveRefreshMs, setLiveRefreshMs, setFitSignal }: {
  lightChart: boolean; setLightChart: (value: boolean) => void; visibleBars: number; setVisibleBars: (value: number) => void;
  rightOffsetBars: number; setRightOffsetBars: (value: number) => void; liveRefreshMs: number; setLiveRefreshMs: (value: number) => void; setFitSignal: Dispatch<SetStateAction<number>>;
}) {
  return <div className="smc-settings-layout">
    <Card title="Chart settings" subtitle="active display preferences — they never alter native SMC research state">
      <div className="form-grid smc-settings-grid">
        <Field label="Live visible candles"><select value={visibleBars} onChange={(event) => setVisibleBars(Number(event.target.value))}><option value={120}>120 · tight intraday view</option><option value={240}>240 · standard view</option><option value={400}>400 · extended view</option><option value={800}>800 · full loaded window</option></select></Field>
        <Field label="Right-edge space"><select value={rightOffsetBars} onChange={(event) => setRightOffsetBars(Number(event.target.value))}><option value={0}>None · last candle at edge</option><option value={6}>6 bars · compact</option><option value={12}>12 bars · standard</option><option value={24}>24 bars · extended</option><option value={48}>48 bars · planning room</option></select></Field>
        <Field label="Live refresh cadence"><select value={liveRefreshMs} onChange={(event) => setLiveRefreshMs(Number(event.target.value))}><option value={3000}>About every 3 seconds</option><option value={5000}>Every 5 seconds</option><option value={10000}>Every 10 seconds</option></select></Field>
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
  const [chartFeed, setChartFeed] = useState<"checkpoint" | "mexc_perpetual" | "kraken_spot">("checkpoint");
  const [fullChart, setFullChart] = useState(false);
  const [fitSignal, setFitSignal] = useState(0);
  const [visibleBars, setVisibleBars] = useState(240);
  const [rightOffsetBars, setRightOffsetBars] = useState(12);
  const [liveRefreshMs, setLiveRefreshMs] = useState(3_000);
  const [watchlistCollapsed, setWatchlistCollapsed] = useState(false);
  const [classification, setClassification] = useState<ReviewClassification>("CORRECT");
  const [reason, setReason] = useState("");
  const [expectedStructure, setExpectedStructure] = useState("");
  const [actualStructure, setActualStructure] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const sample = useLive<ReviewSampleResponse>(`/research/smc/review-sample?symbol=${symbol}&timeframe=${timeframe}`, 15_000);
  const selectedSample = sample.data?.sample.find((row) => row.object_id === selectedId) ?? sample.data?.sample[0];
  const focusedAt = chartFeed === "checkpoint" ? (selectedCandle || selectedSample?.timestamp || "") : "";
  const chartPath = chartFeed === "checkpoint"
    ? `/research/smc/chart?symbol=${symbol}&timeframe=${timeframe}&window=800${focusedAt ? `&at=${encodeURIComponent(focusedAt)}` : ""}`
    // Keep a full recent history loaded behind the initial screen window.
    // `visibleBars` controls only the initial chart viewport; sending it to
    // the API used to discard all older candles, leaving nothing to drag back
    // into on a live chart.
    : `/research/smc/live-chart?symbol=${symbol}&timeframe=${timeframe}&venue=${chartFeed}&window=800&visible=800`;
  const state = useLive<NativeState & { data_provenance?: DataProvenance }>(chartPath, chartFeed === "checkpoint" ? 5_000 : liveRefreshMs);
  const reviews = useLive<ReviewsResponse>(`/research/smc/reviews?symbol=${symbol}&timeframe=${timeframe}`, 15_000);
  const pineReference = useLive<PineReference>("/research/smc/pine-reference", 600_000);
  const data = state.data;
  const reviewItems = sample.data?.sample ?? [];
  const reviewIndex = Math.max(0, reviewItems.findIndex((row) => row.object_id === (selectedId || selectedSample?.object_id)));
  const selectedObjectId = selectedId || selectedSample?.object_id;
  const selectedSnapshot = data?.snapshot_ledger.find((row) => row.candle_open === focusedAt) ?? data?.selected_snapshot ?? data?.snapshot;
  const selectedRow = data?.candles.find((row) => row.timestamp === (selectedSnapshot?.candle_open ?? focusedAt));
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
    if (chartFeed === "checkpoint" && (nextSymbol !== "BTCUSDT" || nextTimeframe !== "5m")) setChartFeed("mexc_perpetual");
    setSymbol(nextSymbol); setTimeframe(nextTimeframe); setSelectedId(""); setSelectedCandle(""); setJumpValue("");
  };

  useEffect(() => {
    if (!fullChart) return;
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") setFullChart(false); };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullChart]);

  const chartSubtitle = data?.data_provenance
    ? `${data.data_provenance.venue} · ${data.data_provenance.market} · last closed ${at(data.data_provenance.last_closed_candle)}`
    : "native verified closed OHLCV";
  const chartPanel = data ? <Card
    className="smc-chart-card"
    title="Nexus SMC chart"
    subtitle={`${chartSubtitle} · ${data.candles.length} visible closed candles${data.forming_candle ? " + forming candle" : ""}`}
    right={<div className="smc-chart-actions"><Badge text="CLOSED BARS" tone="green" /><button className="btn btn-soft" type="button" onClick={() => setFitSignal((value) => value + 1)}>Fit</button><button className="btn btn-primary" type="button" onClick={() => setFullChart((value) => !value)}>{fullChart ? "Exit full screen" : "Full screen"}</button></div>}
  >
    <SMCTradingToolbar symbol={symbol} timeframe={timeframe} live={Boolean(data.data_provenance)} lastPrice={data.live_display?.last_price} onSymbolChange={(value) => switchDataset(value)} onTimeframeChange={(value) => switchDataset(symbol, value)} onOpenSettings={() => setWorkspace("settings")} />
    {data.data_provenance ? <div className="smc-live-strip"><span className="pulse-dot green" /><span><b>Live price display</b> · observed {at(data.live_display?.observed_at)} · {data.live_display?.is_forming ? "forming candle moves every ~3 seconds" : "awaiting provider forming candle"}</span><span className="dim">SMC uses {data.data_provenance.closed_candles_loaded} confirmed candles only · execution disabled</span></div> : <div className="smc-live-strip"><span className="pulse-dot gold" /><span><b>Verified research checkpoint</b> · fixed March 2025 review evidence</span><span className="dim">Execution disabled</span></div>}
    <NativeSMCChartOverlay state={data} timeframe={timeframe} rightOffsetBars={rightOffsetBars} initialVisibleBars={visibleBars} filters={filters} selectedObjectId={selectedObjectId} onCandleSelect={onSelectCandle} fitContentSignal={fitSignal} lightMode={lightChart} height={fullChart ? "calc(100vh - 255px)" : "min(70vh, 780px)"} />
    <div className="smc-chart-footer"><span><b>{data.forming_candle ? "Live forming OHLC" : "Selected OHLC"}</b> {data.forming_candle ? `O ${data.forming_candle.open} · H ${data.forming_candle.high} · L ${data.forming_candle.low} · C ${data.forming_candle.close} · V ${data.forming_candle.volume}` : selectedRow ? `O ${selectedRow.open} · H ${selectedRow.high} · L ${selectedRow.low} · C ${selectedRow.close} · V ${selectedRow.volume}` : "Click a candle to inspect its confirmed state"}</span><span>Drag a blank chart area to pan · wheel: zoom · right space: {rightOffsetBars} bars · Esc: exit full screen</span></div>
  </Card> : null;

  return <>
    <PageHeader title="Native SMC Visual Lab" subtitle="TradingView-style live exchange display · native SMC remains confirmed-candle-only"
      actions={<><Badge text="SMC_NATIVE_V1_RESEARCH" tone="purple" /> <Badge text="EXECUTION DISABLED" tone="red" /></>} />
    <div className="instance-risk-notice amber" role="status"><b>Research visualisation only.</b> The browser renders backend objects; it cannot calculate SMC, create signals, or place paper/live orders.</div>
    <Card className="smc-workstation-card" title="Research workspace" subtitle="native chart objects and the immutable Pine reference are deliberately separate">
      <div className="smc-workspace-tabs">
        <button className={`btn ${workspace === "chart" ? "btn-primary" : "btn-soft"}`} type="button" onClick={() => setWorkspace("chart")}>Chart workspace</button>
        <button className={`btn ${workspace === "pine" ? "btn-primary" : "btn-soft"}`} type="button" onClick={() => setWorkspace("pine")}>Pine reference</button>
        <button className={`btn ${workspace === "settings" ? "btn-primary" : "btn-soft"}`} type="button" onClick={() => setWorkspace("settings")}>Indicator & chart settings</button>
        <span className="dim" style={{ padding: "7px 2px", fontSize: 11 }}>The chart renders native closed-candle state; Pine is shown read-only for side-by-side review.</span>
      </div>
      {workspace === "chart" ? <>
      <div className="smc-control-grid">
        <Field label="Chart feed"><select value={chartFeed} onChange={(event) => { setChartFeed(event.target.value as typeof chartFeed); setSelectedCandle(""); }}><option value="checkpoint">Verified March 2025 checkpoint · BTCUSDT 5m</option><option value="mexc_perpetual">Live MEXC perpetual · moving display / closed-bar SMC</option><option value="kraken_spot">Live Kraken spot · moving display / closed-bar SMC</option></select></Field>
        <Field label="Symbol"><select value={symbol} onChange={(event) => switchDataset(event.target.value)}><option>BTCUSDT</option><option>ETHUSDT</option><option>SOLUSDT</option></select></Field>
        <Field label="Timeframe"><select value={timeframe} onChange={(event) => switchDataset(symbol, event.target.value)}><option>1m</option><option>3m</option><option>5m</option><option>30m</option><option>1h</option><option>4h</option><option>1d</option><option>1w</option></select></Field>
        {chartFeed === "checkpoint" ? <><Field label="Jump to UTC time"><input type="datetime-local" value={jumpValue} onChange={(event) => setJumpValue(event.target.value)} /></Field>
        <button className="btn btn-primary" type="button" onClick={jumpToTime}>Jump to time</button>
        <Field label="Review item"><select value={selectedObjectId ?? ""} onChange={(event) => { const index = reviewItems.findIndex((row) => row.object_id === event.target.value); selectReview(index); }}><option value="">Select review item</option>{reviewItems.map((row, index) => <option key={row.object_id} value={row.object_id}>{index + 1} / {reviewItems.length} · {category(row.category)} · {at(row.timestamp)}</option>)}</select></Field></> : <div className="dim" style={{ alignSelf: "center", fontSize: 12 }}>Refreshes about every 3 seconds. The moving candle is visual-only; the native model receives fully closed candles only.</div>}
      </div>
      <div className="smc-overlay-controls">
        <span className="dim">Render filters:</span>
        {([ ["Pivots", "pivots"], ["Internal", "internal"], ["Swing", "swing"], ["Structure", "structure"], ["Liquidity", "liquidity"], ["FVG", "fvg"], ["Order blocks", "orderBlocks"], ["Mitigated", "mitigated"], ["Labels", "labels"] ] as [string, keyof NativeSMCOverlayFilters][]).map(([label, key]) => <Toggle key={key} label={label} enabled={filters[key]} onClick={() => setFilter(key)} />)}
        <Toggle label="Light chart" enabled={lightChart} onClick={() => setLightChart((value) => !value)} />
      </div>
      </> : workspace === "pine" ? <PineReferencePanel reference={pineReference.data} error={pineReference.error} /> : <IndicatorAndChartSettings lightChart={lightChart} setLightChart={setLightChart} visibleBars={visibleBars} setVisibleBars={setVisibleBars} rightOffsetBars={rightOffsetBars} setRightOffsetBars={setRightOffsetBars} liveRefreshMs={liveRefreshMs} setLiveRefreshMs={setLiveRefreshMs} setFitSignal={setFitSignal} />}
    </Card>
    {workspace === "chart" && (state.error ? <div className="instance-risk-notice red">{state.error}</div> : !data?.candles.length ? <EmptyState text={chartFeed === "checkpoint" ? "No verified closed-candle checkpoint is attached. Configure HUB_SMC_VISUAL_CHECKPOINT_PATH before reviewing native SMC." : "The selected live venue has not returned enough valid closed candles yet."} /> : <>
      {!fullChart ? <div className="smc-chart-layout">
        {chartPanel}
        <aside className="smc-analysis-rail">
          <SMCWatchlist symbol={symbol} activePrice={data.live_display?.last_price} collapsed={watchlistCollapsed} onToggle={() => setWatchlistCollapsed((value) => !value)} onSelect={(value) => switchDataset(value)} />
          <VerdictPanel snapshot={selectedSnapshot} selectedObjectId={selectedObjectId} data={data} />
          <CandleInspector candle={selectedRow} snapshot={selectedSnapshot} data={data} />
        </aside>
      </div> : null}
      {chartFeed === "checkpoint" ? <Card title="Frozen 82-item review workflow" subtitle="deterministic sample · classifications are evaluation evidence only">
        <div className="risk-list terminal" style={{ marginBottom: 12 }}><div className="risk-item"><span>Progress</span><b>{reviewItems.length ? `${reviewIndex + 1} / ${reviewItems.length}` : "No sample"}</b></div><div className="risk-item"><span>Selected item</span><b>{selectedReview ? `${category(selectedReview.category)} · ${shortId(selectedReview.object_id)} · ${at(selectedReview.timestamp)}` : "—"}</b></div></div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          <button className="btn btn-soft" type="button" disabled={reviewIndex <= 0} onClick={() => selectReview(reviewIndex - 1)}>Previous</button>
          <button className="btn btn-soft" type="button" onClick={() => stepCandle(-1)}>‹ Candle</button>
          <button className="btn btn-soft" type="button" onClick={() => stepCandle(1)}>Candle ›</button>
          <button className="btn btn-soft" type="button" disabled={reviewIndex >= reviewItems.length - 1} onClick={() => selectReview(reviewIndex + 1)}>Next</button>
        </div>
        <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))" }}>
          <Field label="Classification"><select value={classification} onChange={(event) => setClassification(event.target.value as ReviewClassification)}><option>CORRECT</option><option>INCORRECT</option><option>AMBIGUOUS</option></select></Field>
          <Field label="Reason"><input value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Why this classification?" /></Field>
          <Field label="Expected interpretation"><input value={expectedStructure} onChange={(event) => setExpectedStructure(event.target.value)} placeholder="What should be visible?" /></Field>
          <Field label="Native engine interpretation"><input value={actualStructure} onChange={(event) => setActualStructure(event.target.value)} placeholder="What did native SMC show?" /></Field>
          <Field label="Notes"><input value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Optional verification notes" /></Field>
        </div>
        <button className="btn btn-primary" type="button" disabled={!selectedObjectId || saving} onClick={submitReview}>{saving ? "Saving evidence…" : "Save review evidence"}</button>
        {reviewError ? <div className="instance-risk-notice red" role="alert" style={{ marginTop: 10 }}>{reviewError}</div> : null}
        <div className="tablewrap" style={{ marginTop: 14 }}><table className="data-table"><thead><tr><th>Object</th><th>Classification</th><th>Selected candle</th><th>Reason</th></tr></thead><tbody>{(reviews.data?.reviews ?? []).map((row) => <tr key={row.id}><td><code>{shortId(row.object_id)}</code></td><td><Badge text={row.classification} tone={row.classification === "CORRECT" ? "green" : row.classification === "INCORRECT" ? "red" : "amber"} /></td><td>{at(row.selected_candle_timestamp)}</td><td className="dim">{row.reason ?? row.notes ?? "—"}</td></tr>)}{!reviews.data?.reviews.length ? <tr><td colSpan={4} className="dim ta-center">No human classifications recorded.</td></tr> : null}</tbody></table></div>
      </Card> : <div className="instance-risk-notice amber"><b>Live view is not parity evidence.</b> Use the verified March 2025 checkpoint and its frozen review sample to record formal Pine-to-native comparisons. The live venue view is for current visual observation only.</div>}
    </>)}
    {fullChart && chartPanel ? <div className="smc-fullscreen" role="dialog" aria-modal="true" aria-label="Full screen native SMC chart"><div className="smc-fullscreen-header"><div><span className="eyebrow">RESEARCH VIEW</span><b>{symbol} · {timeframe} · {chartFeed === "checkpoint" ? "Verified checkpoint" : "Live exchange"}</b></div><button className="btn btn-soft" type="button" onClick={() => setFullChart(false)}>Exit full screen · Esc</button></div>{chartPanel}</div> : null}
  </>;
}
