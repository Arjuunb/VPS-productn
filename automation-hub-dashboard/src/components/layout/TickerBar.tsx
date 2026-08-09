import { useLive } from "../../lib/api";

type Snapshot = {
  active_slots: number; max_active_slots: number; total_open_positions: number;
  current_global_risk_amount: number; max_global_risk_amount: number;
  market_data_status: string; instances: { symbol: string; strategy_label: string; timeframe: string; state: string }[];
};

/** Footer uses the same Trading Instance payload as the dashboard and detail UI. */
export default function TickerBar() {
  const { data } = useLive<Snapshot>("/instances", 4000);
  const running = (data?.instances ?? []).filter((row) => row.state === "running");
  const instances = running.map((row) => `${row.symbol} · ${row.strategy_label} · ${row.timeframe}`).join(" | ") || "No active instance";
  const items: [string, string][] = data ? [
    ["Mode", "PAPER (simulation)"], ["Instances", `${data.active_slots} / ${data.max_active_slots} running`],
    ["Market data", data.market_data_status], ["Open positions", String(data.total_open_positions)],
    ["Open risk", `$${data.current_global_risk_amount.toLocaleString()} / $${data.max_global_risk_amount.toLocaleString()}`],
    ["Active", instances],
  ] : [["System", "backend not reachable"]];
  return <footer className="ticker"><div className="ticker-items">{items.map(([k, v]) => <span className="ticker-item" key={k}><b>{k}</b><span className="ticker-price">{v}</span></span>)}</div><div className="ticker-meta"><span className={`dot ${running.length ? "online" : "offline"}`} /></div></footer>;
}
