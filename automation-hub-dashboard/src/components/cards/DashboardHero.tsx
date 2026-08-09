import Icon from "../common/Icon";
import Sparkline from "../chart/Sparkline";
import { Badge } from "../common/ui";
import { useLive } from "../../lib/api";

const money = (n: number) => `${n >= 0 ? "+" : "-"}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const usd = (n: number) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

/** Tradexa command-center hero — live account equity, P&L, engine state and a
 *  mini equity curve in one premium banner. Real backend data only. */
export default function DashboardHero() {
  const instances = useLive<any>("/instances", 2000);
  const snapshot = instances.data;
  const running = (snapshot?.instances ?? []).filter((row: any) => row.state === "running");
  const curve = (running[0]?.performance?.equity_curve ?? []).map((p: any) => p.equity);
  const pnl = snapshot?.today_pnl ?? 0;
  const exposure = snapshot?.max_global_risk_amount ? snapshot.current_global_risk_amount / snapshot.max_global_risk_amount * 100 : 0;
  const expTone = exposure >= 90 ? "red" : exposure >= 60 ? "amber" : "green";

  return (
    <div className="dash-hero">
      <div className="hero-left">
        <span className="hero-eyebrow">PAPER TRADING INSTANCES · SIMULATION</span>
        <div className="hero-equity">{usd(snapshot?.total_current_equity ?? 0)}</div>
        <div className="hero-row">
          <Badge text={`${money(pnl)} realized`} tone={pnl >= 0 ? "green" : "red"} />
          <span className="hero-dot" />
          <span className={running.length ? "pos" : "neg"} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 600 }}>
            <span className={`dot ${running.length ? "online" : "offline"}`} /> {running.length} instance{running.length === 1 ? "" : "s"} running
          </span>
          <span className="hero-dot" />
          <span className="dim">{running.map((row: any) => `${row.symbol} · ${row.strategy_label} · ${row.timeframe}`).join("  |  ") || "No active Trading Instance"}</span>
        </div>
      </div>

      <div className="hero-mid">
        <div className="hero-stat">
          <span className="hero-stat-label">Exposure</span>
          <b className={expTone === "green" ? "pos" : expTone === "amber" ? "amber" : "neg"}>{exposure.toFixed(0)}%</b>
          <span className="hero-bar"><span className="hero-bar-fill" style={{ width: `${Math.min(100, exposure)}%`, background: `var(--${expTone === "green" ? "green" : expTone === "amber" ? "gold" : "red"})` }} /></span>
        </div>
        <div className="hero-stat">
          <span className="hero-stat-label">Open positions</span>
          <b>{snapshot?.total_open_positions ?? 0}</b>
        </div>
        <div className="hero-stat">
          <span className="hero-stat-label">Allocated</span>
          <b>{usd(snapshot?.total_allocated_capital ?? 0)}</b>
          <span className="dim" style={{ fontSize: 10 }}>{snapshot?.active_slots ?? 0} / {snapshot?.max_active_slots ?? 1} slots</span>
        </div>
      </div>

      <div className="hero-right">
        <span className="hero-stat-label" style={{ display: "flex", alignItems: "center", gap: 5 }}><Icon name="chart" size={12} /> Equity</span>
        {curve.length > 1
          ? <div className="hero-spark"><Sparkline data={curve} color={pnl >= 0 ? "#22c55e" : "#ef4444"} height={56} /></div>
          : <span className="dim" style={{ fontSize: 12 }}>Awaiting trades…</span>}
      </div>
    </div>
  );
}
