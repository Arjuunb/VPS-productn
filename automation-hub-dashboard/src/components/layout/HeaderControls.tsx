import Icon from "../common/Icon";
import { useApp } from "../../app-context";
import { useLive } from "../../lib/api";

type Instance = {
  id: string; symbol: string; strategy_label: string; strategy_version: string;
  timeframe: string; state: string; market_data?: { market_data_status?: string };
};
type InstanceSnapshot = { instances: Instance[]; active_slots: number; max_active_slots: number };

/**
 * Header status is intentionally instance-derived.  The legacy autonomous
 * engine has a separate compatibility section in Settings and must never make
 * this bar imply that a global strategy or timeframe controls paper workers.
 */
export default function HeaderControls() {
  const app = useApp();
  const { data, error } = useLive<InstanceSnapshot>("/instances", 4000);
  const running = (data?.instances ?? []).filter((row) => row.state === "running");
  const lead = running[0];
  const healthy = running.length > 0 && running.every((row) => row.market_data?.market_data_status === "healthy");
  const dot = error ? "offline" : running.length ? (healthy ? "online" : "warn") : "warn";

  return <div className="hdr-controls" title={error ?? "Trading Instance status"}>
    <button className="hdr-chip" onClick={() => app.go("Trading Instances")} title="Open Trading Instances">
      <span className={`dot ${dot}`} /> <b>Paper</b>
    </button>
    <span className="hdr-static dot-label">
      <span className={`dot ${dot}`} /> <span className="hide-sm">{running.length} / {data?.max_active_slots ?? 1} instances running</span>
    </span>
    {lead ? <button className="hdr-chip" onClick={() => app.viewInstance(lead.id)} title="Open active instance details">
      <b>{lead.symbol} · {lead.strategy_label}</b><span className="dim">{lead.timeframe}</span>
    </button> : <button className="hdr-chip" onClick={() => app.go("Trading Instances")}><b>Create instance</b><Icon name="plus" size={12} /></button>}
    <button className="hdr-chip" onClick={() => app.go("Paper Trading")} title="Open Paper Trading terminal">
      <Icon name="bot" size={13} /><span className="hide-sm">Terminal</span>
    </button>
    <button className="hdr-chip hdr-gear" onClick={() => app.go("Settings")} title="Global account settings"><Icon name="settings" size={13} /></button>
  </div>;
}
