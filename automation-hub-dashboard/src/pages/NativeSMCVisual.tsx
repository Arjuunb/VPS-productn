import { useCallback, useState } from "react";
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

export default function NativeSMCVisualPage() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [timeframe, setTimeframe] = useState("5m");
  const [selectedId, setSelectedId] = useState("");
  const [selectedCandle, setSelectedCandle] = useState("");
  const [jumpValue, setJumpValue] = useState("");
  const [filters, setFilters] = useState(defaultFilters);
  const [lightChart, setLightChart] = useState(false);
  const [fitSignal, setFitSignal] = useState(0);
  const [classification, setClassification] = useState<ReviewClassification>("CORRECT");
  const [reason, setReason] = useState("");
  const [expectedStructure, setExpectedStructure] = useState("");
  const [actualStructure, setActualStructure] = useState("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const sample = useLive<ReviewSampleResponse>(`/research/smc/review-sample?symbol=${symbol}&timeframe=${timeframe}`, 15_000);
  const selectedSample = sample.data?.sample.find((row) => row.object_id === selectedId) ?? sample.data?.sample[0];
  const focusedAt = selectedCandle || selectedSample?.timestamp || "";
  const state = useLive<NativeState>(`/research/smc/chart?symbol=${symbol}&timeframe=${timeframe}&window=800${focusedAt ? `&at=${encodeURIComponent(focusedAt)}` : ""}`, 5_000);
  const reviews = useLive<ReviewsResponse>(`/research/smc/reviews?symbol=${symbol}&timeframe=${timeframe}`, 15_000);
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
    setSymbol(nextSymbol); setTimeframe(nextTimeframe); setSelectedId(""); setSelectedCandle(""); setJumpValue("");
  };

  return <>
    <PageHeader title="Native SMC Visual Lab" subtitle="TradingView-style native structure verification · closed exchange candles only"
      actions={<><Badge text="SMC_NATIVE_V1_RESEARCH" tone="purple" /> <Badge text="EXECUTION DISABLED" tone="red" /></>} />
    <div className="instance-risk-notice amber" role="status"><b>Research visualisation only.</b> The browser renders backend objects; it cannot calculate SMC, create signals, or place paper/live orders.</div>
    <Card title="Compare mode" subtitle="jump, inspect, pan and zoom without changing native state">
      <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", alignItems: "end" }}>
        <Field label="Symbol"><select value={symbol} onChange={(event) => switchDataset(event.target.value)}><option>BTCUSDT</option><option>ETHUSDT</option><option>SOLUSDT</option></select></Field>
        <Field label="Timeframe"><select value={timeframe} onChange={(event) => switchDataset(symbol, event.target.value)}><option>5m</option><option>15m</option><option>1h</option></select></Field>
        <Field label="Jump to UTC time"><input type="datetime-local" value={jumpValue} onChange={(event) => setJumpValue(event.target.value)} /></Field>
        <button className="btn btn-primary" type="button" onClick={jumpToTime}>Jump to time</button>
        <Field label="Review item"><select value={selectedObjectId ?? ""} onChange={(event) => { const index = reviewItems.findIndex((row) => row.object_id === event.target.value); selectReview(index); }}><option value="">Select review item</option>{reviewItems.map((row, index) => <option key={row.object_id} value={row.object_id}>{index + 1} / {reviewItems.length} · {category(row.category)} · {at(row.timestamp)}</option>)}</select></Field>
        <button className="btn btn-soft" type="button" onClick={() => setFitSignal((value) => value + 1)}>Fit chart</button>
      </div>
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginTop: 10 }}>
        <span className="dim" style={{ padding: "6px 2px", fontSize: 11 }}>Render filters:</span>
        {([ ["Pivots", "pivots"], ["Internal", "internal"], ["Swing", "swing"], ["Structure", "structure"], ["Liquidity", "liquidity"], ["FVG", "fvg"], ["Order blocks", "orderBlocks"], ["Mitigated", "mitigated"], ["Labels", "labels"] ] as [string, keyof NativeSMCOverlayFilters][]).map(([label, key]) => <Toggle key={key} label={label} enabled={filters[key]} onClick={() => setFilter(key)} />)}
        <Toggle label="Light chart" enabled={lightChart} onClick={() => setLightChart((value) => !value)} />
      </div>
    </Card>
    {state.error ? <div className="instance-risk-notice red">{state.error}</div> : !data?.candles.length ? <EmptyState text="No verified closed-candle checkpoint is attached. Configure HUB_SMC_VISUAL_CHECKPOINT_PATH before reviewing native SMC." /> : <>
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(300px, 360px)", gap: 14, alignItems: "start" }}>
        <Card title="Native SMC chart" subtitle={`closed OHLCV · ${data.candles.length} loaded candles · mouse wheel zoom · drag to pan`}>
          <NativeSMCChartOverlay state={data} filters={filters} selectedObjectId={selectedObjectId} onCandleSelect={onSelectCandle} fitContentSignal={fitSignal} lightMode={lightChart} />
          <div className="risk-list terminal" style={{ marginTop: 8 }}><div className="risk-item"><span>Selected OHLC</span><b>{selectedRow ? `O ${selectedRow.open} · H ${selectedRow.high} · L ${selectedRow.low} · C ${selectedRow.close} · V ${selectedRow.volume}` : "Click a candle"}</b></div></div>
        </Card>
        <div style={{ display: "grid", gap: 14 }}>
          <VerdictPanel snapshot={selectedSnapshot} selectedObjectId={selectedObjectId} data={data} />
          <CandleInspector candle={selectedRow} snapshot={selectedSnapshot} data={data} />
        </div>
      </div>
      <Card title="Frozen 82-item review workflow" subtitle="deterministic sample · classifications are evaluation evidence only">
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
      </Card>
    </>}
  </>;
}
