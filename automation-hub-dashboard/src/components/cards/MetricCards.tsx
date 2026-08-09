import Icon from "../common/Icon";
import Sparkline from "../chart/Sparkline";
import { useLive } from "../../lib/api";

const money = (n: number | undefined) => `${(n ?? 0) >= 0 ? "+" : "-"}$${Math.abs(n ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

export default function MetricCards() {
  const instances = useLive<any>("/instances", 2000);
  const data = instances.data;
  const rows = data?.instances ?? [];
  const running = rows.filter((row: any) => row.state === "running");
  const signals = running.reduce((total: number, row: any) => total + Number(row.engine?.signals ?? 0), 0);
  const rejections = running.reduce((total: number, row: any) => total + Number(row.engine?.rejections ?? 0), 0);
  const curve = (running[0]?.performance?.equity_curve ?? []).map((point: any) => point.equity);

  const cards = [
    { key: "engine", label: "Active Instances", value: `${data?.active_slots ?? 0} / ${data?.max_active_slots ?? 1}`, sub: `${running.length} running workers`, color: running.length ? "#22c55e" : "#ef4444", icon: "bot", tone: running.length ? "green" : "" },
    { key: "open", label: "Open Positions", value: String(data?.total_open_positions ?? 0), sub: "instance-scoped", color: "#3b82f6", icon: "layers", tone: "" },
    { key: "signals", label: "Signals", value: String(signals), sub: "running instances", color: "#eab54f", icon: "target", tone: "" },
    { key: "rejections", label: "Rejections", value: String(rejections), sub: "instance gates", color: "#eab54f", icon: "shield", tone: "" },
  ];

  const realized = data?.today_pnl ?? 0;

  return (
    <div className="metric-row">
      {cards.map((m) => (
        <div className="metric-card" key={m.key}>
          <span className="metric-accent" style={{ background: m.color }} />
          <div className="metric-top">
            <span className="metric-label">{m.label}</span>
            <span className="metric-icon" style={{ background: m.color + "22", color: m.color }}><Icon name={m.icon} size={16} /></span>
          </div>
          <div className="metric-main">
            <span className="metric-value">{m.value}</span>
            <span className={`metric-sub ${m.tone === "green" ? "pos" : ""}`}>{m.sub}</span>
          </div>
        </div>
      ))}

      <div className="metric-card pnl-card">
        <span className="metric-accent" style={{ background: realized >= 0 ? "#22c55e" : "#ef4444" }} />
        <div className="metric-top">
          <span className="metric-label">Today's P&amp;L</span>
          <span className={`metric-icon ${realized >= 0 ? "pos" : "neg"}`}><Icon name="chart" size={16} /></span>
        </div>
        <div className="metric-main">
          <span className={`metric-value ${realized >= 0 ? "pos" : "neg"}`}>{money(realized)}</span>
        </div>
        {curve.length > 1 ? (
          <div className="metric-spark"><Sparkline data={curve} color={realized >= 0 ? "#22c55e" : "#ef4444"} height={34} /></div>
        ) : (
          <span className="metric-sub dim">Equity ${(data?.total_current_equity ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}</span>
        )}
      </div>
    </div>
  );
}
