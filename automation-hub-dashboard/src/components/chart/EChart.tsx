import { useEffect, useRef } from "react";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";

interface EChartProps {
  option: EChartsOption;
  height?: number | string;
  className?: string;
  style?: React.CSSProperties;
  /** Optional renderer events for inspection-only chart interactions. */
  onEvents?: Record<string, (event: unknown) => void>;
  /** Preserve chart zoom/pan state across polling refreshes. */
  preserveInteraction?: boolean;
  /** Increment to reset a zoomable chart to its full loaded window. */
  fitContentSignal?: number;
}

/**
 * Reusable Apache ECharts wrapper.
 * - Inits once, updates option reactively.
 * - Resizes with its container via ResizeObserver.
 * - Disposes on unmount (no leaks, no console errors).
 */
export default function EChart({ option, height = "100%", className, style, onEvents, preserveInteraction = false, fitContentSignal }: EChartProps) {
  const elRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!elRef.current) return;
    const chart = echarts.init(elRef.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;

    const ro = new ResizeObserver(() => chart.resize());
    ro.observe(elRef.current);

    return () => {
      ro.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current) return;
    if (preserveInteraction) chartRef.current.setOption(option, { notMerge: false, lazyUpdate: true });
    else chartRef.current.setOption(option, true);
  }, [option, preserveInteraction]);

  useEffect(() => {
    if (fitContentSignal === undefined) return;
    chartRef.current?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
  }, [fitContentSignal]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onEvents) return;
    for (const [event, handler] of Object.entries(onEvents)) chart.on(event, handler);
    return () => {
      for (const [event, handler] of Object.entries(onEvents)) chart.off(event, handler);
    };
  }, [onEvents]);

  return (
    <div
      ref={elRef}
      className={className}
      style={{ width: "100%", height, ...style }}
    />
  );
}
