import { useMemo } from "react";
import type { EChartsOption } from "echarts";
import EChart from "./EChart";

export type Direction = "bullish" | "bearish" | "neutral";

export interface NativeCandle { timestamp: string; open: number; high: number; low: number; close: number; volume: number }
export interface NativePivot { id: string; scope: "internal" | "swing"; kind: "high" | "low"; price: number; occurred_at: string; confirmed_at: string; strength: "strong" | "weak" }
export interface NativeEvent { id: string; direction: Direction; event_type?: string; scope?: string; level: number; occurred_at?: string; confirmed_at?: string; timestamp?: string; source_pivot_id?: string }
export interface NativeZone { id: string; direction: Direction; top?: number; bottom?: number; high?: number; low?: number; created_at: string; active: boolean; mitigated: boolean; mitigation_at?: string | null; source_pivot_id?: string; source_structure_id?: string }
export interface NativeProposal { id: string; setup_id: string; snapshot_id: string; direction: Direction; entry: number; stop: number; target: number; rr_ratio: number; execution_allowed: false; risk_status: string }
export interface NativeSnapshot {
  id: string; candle_open: string; candle_close: string; htf_bias: number; htf_ema: number | null;
  swing_bias: number; internal_bias: number; session: string;
  dealing_range: { high: number; low: number; equilibrium: number; area: string };
  price_action: { bullish_rejection: boolean; bearish_rejection: boolean; body: number; upper_wick: number; lower_wick: number };
  active_setup_id: string | null; setup_phase: string | null; next_required_event: string;
  latest_sweep_id: string | null; event_ids: string[]; active_fvg_ids: string[]; active_ob_ids: string[]; proposal_ids: string[];
}
export interface NativeSMCChartState {
  research_id: string; execution_allowed: false; candles: NativeCandle[]; pivots: NativePivot[]; events: NativeEvent[];
  fair_value_gaps: NativeZone[]; order_blocks: NativeZone[]; proposals: NativeProposal[];
  snapshot: NativeSnapshot | null; selected_snapshot: NativeSnapshot | null; snapshot_ledger: NativeSnapshot[];
  /** A provider-supplied open candle for display. Never contributes to SMC state. */
  forming_candle?: NativeCandle | null;
  live_display?: { is_forming: boolean; observed_at: string; refresh_interval_seconds: number; candle_closes_at: string | null; last_price: number; execution_uses_closed_bars_only: true };
}
export interface NativeSMCOverlayFilters {
  pivots: boolean; internal: boolean; swing: boolean; structure: boolean; liquidity: boolean;
  fvg: boolean; orderBlocks: boolean; mitigated: boolean; labels: boolean;
}

interface Props {
  state: NativeSMCChartState;
  /** The user-selected chart timeframe. Used only for display-range scaling. */
  timeframe?: string;
  /** Empty chart slots after the latest candle, for live-chart positioning. */
  rightOffsetBars?: number;
  filters: NativeSMCOverlayFilters;
  selectedObjectId?: string;
  onCandleSelect: (timestamp: string) => void;
  fitContentSignal: number;
  lightMode?: boolean;
  height?: number | string;
}

const shortId = (id: string) => `${id.slice(0, 10)}…`;
const colorFor = (direction: Direction) => direction === "bullish" ? "#21c77a" : direction === "bearish" ? "#ef5b5b" : "#9ca3af";
const timestamp = (value: string) => value.replace("T", " ").replace("+00:00", " UTC").slice(0, 23);

// Premium/discount is a visual aid in this Lab, not a new native-SMC input.
// Each value represents a comparable recent market horizon for its timeframe,
// rather than the all-history range held in a native research snapshot.
const DISPLAY_RANGE_BARS: Record<string, number> = {
  "1m": 180, "3m": 160, "5m": 144, "30m": 96,
  "1h": 72, "4h": 60, "1d": 60, "1w": 52,
};

const TIMEFRAME_MS: Record<string, number> = {
  "1m": 60_000, "3m": 3 * 60_000, "5m": 5 * 60_000, "30m": 30 * 60_000,
  "1h": 60 * 60_000, "4h": 4 * 60 * 60_000, "1d": 24 * 60 * 60_000, "1w": 7 * 24 * 60 * 60_000,
};

export interface DisplayDealingRange {
  high: number;
  low: number;
  equilibrium: number;
  start: string;
  end: string;
  bars: number;
}

export function timeframeDisplayRange(candles: NativeCandle[], timeframe = "5m", anchorTimestamp?: string): DisplayDealingRange | null {
  if (!candles.length) return null;
  const requestedIndex = anchorTimestamp ? candles.findIndex((row) => row.timestamp === anchorTimestamp) : -1;
  const endIndex = requestedIndex >= 0 ? requestedIndex : candles.length - 1;
  const bars = Math.min(DISPLAY_RANGE_BARS[timeframe] ?? 120, endIndex + 1);
  const rangeCandles = candles.slice(endIndex - bars + 1, endIndex + 1);
  let high = Number.NEGATIVE_INFINITY;
  let low = Number.POSITIVE_INFINITY;
  for (const candle of rangeCandles) {
    high = Math.max(high, candle.high);
    low = Math.min(low, candle.low);
  }
  return { high, low, equilibrium: (high + low) / 2, start: rangeCandles[0].timestamp, end: rangeCandles[rangeCandles.length - 1].timestamp, bars: rangeCandles.length };
}

function futureChartSlots(lastTimestamp: string | undefined, timeframe: string, count: number): string[] {
  const start = lastTimestamp ? Date.parse(lastTimestamp) : Number.NaN;
  const step = TIMEFRAME_MS[timeframe] ?? TIMEFRAME_MS["5m"];
  if (!Number.isFinite(start) || count <= 0) return [];
  return Array.from({ length: count }, (_, index) => new Date(start + step * (index + 1)).toISOString());
}

function chartOption(state: NativeSMCChartState, timeframe: string, rightOffsetBars: number, filters: NativeSMCOverlayFilters, selectedObjectId?: string, lightMode = false): EChartsOption {
  const closedCandles = state.candles;
  // The display candle intentionally remains outside the native model's
  // snapshots. It makes the chart feel live without turning an unclosed bar
  // into market-structure evidence.
  const hasFormingCandle = Boolean(state.forming_candle);
  const candles = hasFormingCandle ? [...closedCandles, state.forming_candle!] : closedCandles;
  const candleLabels = candles.map((row) => row.timestamp);
  const futureSlots = futureChartSlots(candleLabels[candleLabels.length - 1], timeframe, rightOffsetBars);
  const labels = [...candleLabels, ...futureSlots];
  const labelSet = new Set(candleLabels);
  const first = candleLabels[0];
  const last = candleLabels[candleLabels.length - 1];
  const selectedSnapshot = state.selected_snapshot ?? state.snapshot;
  // Do not reuse snapshot.dealing_range here: it is intentionally calculated
  // from the full engine history for native-state evidence. The chart overlay
  // needs a local, timeframe-aware viewing range instead.
  const range = timeframeDisplayRange(closedCandles, timeframe, selectedSnapshot?.candle_open);
  const rangeEnd = range?.end === closedCandles[closedCandles.length - 1]?.timestamp ? last : range?.end;
  const eventAt = (row: NativeEvent) => row.confirmed_at ?? row.timestamp;
  const inWindow = (value?: string | null) => Boolean(value && labelSet.has(value));
  const spanStart = (value: string) => labelSet.has(value) ? value : first;
  const spanEnd = (value?: string | null) => value && labelSet.has(value) ? value : last;

  const visiblePivots = filters.pivots ? state.pivots.filter((row) =>
    inWindow(row.occurred_at) && (row.scope === "internal" ? filters.internal : filters.swing),
  ) : [];
  const visibleEvents = state.events.filter((row) => {
    if (row.event_type) return filters.structure && (row.scope === "internal" ? filters.internal : filters.swing) && inWindow(eventAt(row));
    return filters.liquidity && inWindow(row.timestamp);
  });
  const zones = filters.fvg ? state.fair_value_gaps.filter((row) => (filters.mitigated || !row.mitigated) && row.created_at <= last && (!row.mitigation_at || row.mitigation_at >= first)) : [];
  const orderBlocks = filters.orderBlocks ? state.order_blocks.filter((row) => (filters.mitigated || !row.mitigated) && row.created_at <= last && (!row.mitigation_at || row.mitigation_at >= first)) : [];
  const fvgAreas = zones.map((row) => [{
    name: `${row.direction === "bullish" ? "Bull" : "Bear"} FVG ${shortId(row.id)}`,
    xAxis: spanStart(row.created_at), yAxis: row.bottom,
    itemStyle: { color: row.direction === "bullish" ? "rgba(34,197,94,.17)" : "rgba(239,91,91,.17)" },
  }, { xAxis: spanEnd(row.mitigation_at), yAxis: row.top }]);
  const obAreas = orderBlocks.map((row) => [{
    name: `${row.direction === "bullish" ? "Bull" : "Bear"} OB ${shortId(row.id)}`,
    xAxis: spanStart(row.created_at), yAxis: row.low,
    itemStyle: { color: row.direction === "bullish" ? "rgba(59,130,246,.14)" : "rgba(168,85,247,.14)" },
  }, { xAxis: spanEnd(row.mitigation_at), yAxis: row.high }]);
  const pivotData = visiblePivots.map((row) => ({
    name: `${row.strength === "strong" ? "Strong" : "Weak"} ${row.kind === "high" ? "High" : "Low"}`,
    value: [row.occurred_at, row.price],
    itemStyle: { color: row.scope === "swing" ? "#eab54f" : "#69b9ff", borderColor: row.id === selectedObjectId ? "#ffffff" : undefined, borderWidth: row.id === selectedObjectId ? 2 : 0 },
    metadata: `${row.id}\n${row.scope} ${row.kind} · ${row.strength}\nOccurred: ${timestamp(row.occurred_at)}\nConfirmed: ${timestamp(row.confirmed_at)}`,
  }));
  const structureData = visibleEvents.map((row) => ({
    name: row.event_type ? `${row.scope === "swing" ? "S" : "I"} ${row.event_type}` : `${row.direction === "bullish" ? "SSL swept" : "BSL swept"}`,
    value: [eventAt(row)!, row.level],
    itemStyle: { color: colorFor(row.direction), borderColor: row.id === selectedObjectId ? "#ffffff" : undefined, borderWidth: row.id === selectedObjectId ? 2 : 0 },
    metadata: `${row.id}\n${row.event_type ? `${row.scope} ${row.event_type}` : "Liquidity sweep"}\nLevel: ${row.level}\nConfirmed: ${timestamp(eventAt(row)!)}`,
  }));
  const proposalLines = state.proposals.flatMap((row) => [
    { yAxis: row.entry, name: `ENTRY ${shortId(row.id)}`, lineStyle: { color: "#65b7ff", width: 1.5 } },
    { yAxis: row.stop, name: "STOP LOSS", lineStyle: { color: "#ef5b5b", type: "dashed" } },
    { yAxis: row.target, name: "TAKE PROFIT", lineStyle: { color: "#21c77a", type: "dashed" } },
  ]);
  const rangeAreas = range ? [
    [{ name: `DISCOUNT · ${range.bars} bars`, xAxis: range.start, yAxis: range.low, itemStyle: { color: "rgba(34,197,94,.06)" } }, { xAxis: rangeEnd, yAxis: range.equilibrium }],
    [{ name: `PREMIUM · ${range.bars} bars`, xAxis: range.start, yAxis: range.equilibrium, itemStyle: { color: "rgba(239,91,91,.06)" } }, { xAxis: rangeEnd, yAxis: range.high }],
  ] : [];
  const lastCandle = candles[candles.length - 1];
  const canvas = lightMode ? "#ffffff" : "#101216";
  const axis = lightMode ? "#d1d5db" : "#2a2f38";
  const text = lightMode ? "#344054" : "#98a2b3";

  return {
    animation: true,
    animationDurationUpdate: 220,
    animationEasingUpdate: "linear",
    backgroundColor: canvas,
    axisPointer: {
      link: [{ xAxisIndex: [0, 1] }],
      snap: true,
      animation: false,
      label: { backgroundColor: lightMode ? "#e9edf3" : "#171a20", color: lightMode ? "#111827" : "#e5e7eb" },
    },
    tooltip: {
      trigger: "axis",
      triggerOn: "mousemove",
      showDelay: 90,
      hideDelay: 120,
      transitionDuration: 0.08,
      confine: true,
      axisPointer: { type: "cross" },
      formatter: (params: any) => {
        const custom = (params as any[]).find((row) => row.data?.metadata);
        if (custom?.data?.metadata) return custom.data.metadata.split("\n").join("<br/>");
        const candle = (params as any[]).find((row) => row.seriesName === "Market candles");
        const value = Array.isArray(candle?.data) ? candle.data : candle?.data?.value as number[] | undefined;
        const status = candle?.data?.forming ? "FORMING · visual only" : "CLOSED · native SMC eligible";
        return value ? `${candle.axisValue}<br/><b>${status}</b><br/>O ${value[0]} · H ${value[3]} · L ${value[2]} · C ${value[1]}` : "";
      },
    },
    grid: [
      { left: 58, right: 74, top: 32, height: "60%" },
      { left: 58, right: 74, top: "76%", height: "12%" },
    ],
    xAxis: [
      { id: "smc-price-x", type: "category", data: labels, boundaryGap: true, axisLine: { lineStyle: { color: axis } }, axisLabel: { show: false } },
      { id: "smc-volume-x", type: "category", gridIndex: 1, data: labels, boundaryGap: true, axisLine: { lineStyle: { color: axis } }, axisLabel: { color: text, formatter: (value: string) => value.slice(5, 16), fontSize: 10 } },
    ],
    yAxis: [
      { id: "smc-price-y", scale: true, position: "right", axisLine: { lineStyle: { color: axis } }, axisLabel: { color: text, fontSize: 10 }, splitLine: { lineStyle: { color: lightMode ? "#edf0f5" : "#1d222b" } } },
      { id: "smc-volume-y", gridIndex: 1, position: "right", axisLabel: { color: text, fontSize: 10 }, splitLine: { show: false } },
    ],
    dataZoom: [
      // Drag-pan only activates while the pointer is pressed; ordinary hover
      // continues to be a stable crosshair inspection action.
      { id: "smc-inside-zoom", type: "inside", xAxisIndex: [0, 1], zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false, preventDefaultMouseMove: true, cursorGrab: "grab", cursorGrabbing: "grabbing" },
      { id: "smc-slider-zoom", type: "slider", xAxisIndex: [0, 1], bottom: "2%", height: 16, borderColor: axis, fillerColor: "rgba(105,185,255,.14)", handleStyle: { color: "#69b9ff" }, textStyle: { color: text } },
    ],
    series: [
      {
        id: "smc-candles", type: "candlestick", name: "Market candles", xAxisIndex: 0, yAxisIndex: 0,
        data: [...candles.map((row, index) => ({
          value: [row.open, row.close, row.low, row.high],
          forming: hasFormingCandle && index === candles.length - 1,
          itemStyle: hasFormingCandle && index === candles.length - 1 ? { opacity: 0.78, borderWidth: 2 } : undefined,
        })), ...futureSlots.map(() => "-")], barMaxWidth: 18,
        itemStyle: { color: "#089981", color0: "#f23645", borderColor: "#089981", borderColor0: "#f23645" },
        markArea: { silent: true, label: { color: "#bac4d5", fontSize: 10 }, data: [...rangeAreas, ...fvgAreas, ...obAreas] },
        markLine: { silent: true, symbol: "none", label: { color: "#cfd6e4", fontSize: 10 }, data: [
          ...(range ? [
            { yAxis: range.high, name: `${timeframe} range high`, lineStyle: { color: "#ef5b5b", type: "dotted" } },
            { yAxis: range.equilibrium, name: `${timeframe} equilibrium ${range.equilibrium}`, lineStyle: { color: "#eab54f", type: "dashed" } },
            { yAxis: range.low, name: `${timeframe} range low`, lineStyle: { color: "#21c77a", type: "dotted" } },
          ] : []),
          ...(lastCandle ? [{ yAxis: lastCandle.close, name: `${hasFormingCandle ? "LIVE" : "LAST"} ${lastCandle.close}`, lineStyle: { color: lastCandle.close >= lastCandle.open ? "#21c77a" : "#ef5b5b", width: hasFormingCandle ? 1.5 : 1 } }] : []),
          ...proposalLines,
        ] },
      },
      { id: "smc-volume", type: "bar", name: "Volume", xAxisIndex: 1, yAxisIndex: 1, data: [...candles.map((row, index) => ({ value: row.volume, itemStyle: { color: row.close >= row.open ? "rgba(8,153,129,.70)" : "rgba(242,54,69,.70)", opacity: hasFormingCandle && index === candles.length - 1 ? 0.72 : 1 } })), ...futureSlots.map(() => "-")], barMaxWidth: 18 },
      { id: "smc-pivots", type: "scatter", name: "Native pivots", xAxisIndex: 0, yAxisIndex: 0, data: pivotData as any[], symbolSize: 8, label: { show: filters.labels, formatter: (row: any) => row.data.name, color: "#d7deea", fontSize: 9, position: "top" }, z: 8 },
      { id: "smc-structure", type: "scatter", name: "Native structure", xAxisIndex: 0, yAxisIndex: 0, data: structureData as any[], symbol: "diamond", symbolSize: 11, label: { show: filters.labels, formatter: (row: any) => row.data.name, color: "#d7deea", fontSize: 9, position: "bottom" }, z: 9 },
    ],
  } as EChartsOption;
}

export default function NativeSMCChartOverlay({ state, timeframe = "5m", rightOffsetBars = 12, filters, selectedObjectId, onCandleSelect, fitContentSignal, lightMode = false, height = 700 }: Props) {
  const option = useMemo(() => chartOption(state, timeframe, rightOffsetBars, filters, selectedObjectId, lightMode), [state, timeframe, rightOffsetBars, filters, selectedObjectId, lightMode]);
  const events = useMemo(() => ({
    click: (event: any) => {
      if (event?.seriesName !== "Market candles" || typeof event.dataIndex !== "number") return;
      // There is intentionally no state snapshot for the still-forming candle.
      if (event.dataIndex >= state.candles.length) return;
      const candle = state.candles[event.dataIndex];
      if (candle) onCandleSelect(candle.timestamp);
    },
  }), [onCandleSelect, state.candles]);
  return <EChart option={option} height={height} onEvents={events} preserveInteraction fitContentSignal={fitContentSignal} style={{ borderRadius: 8 }} />;
}
