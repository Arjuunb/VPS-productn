import { useState } from "react";
import Card from "../components/common/Card";
import Icon from "../components/common/Icon";
import { Badge, Field, PageHeader, StatCard } from "../components/common/ui";
import { apiPost, apiPostJson, useLive } from "../lib/api";
import { useApp } from "../app-context";

type Instance = { id: string; symbol: string; strategy_key: string; strategy_label: string; strategy_version: string; timeframe: string; risk_per_trade_pct: number; capital_allocation: number; mode: string; state: string; metrics?: any; engine?: any };
type InstancesResponse = { instances: Instance[]; max_active_slots: number; active_slots: number; max_global_risk_pct: number; max_global_risk_amount: number; current_global_risk_amount: number };
type Strategy = { key: string; label: string };

const money = (v?: number) => `$${(v ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
const tone = (state: string) => state === "running" ? "green" : state === "paused" ? "amber" : state === "error" ? "red" : "default";

export default function TradingInstancesPage({ instanceId }: { instanceId?: string }) {
  const app = useApp();
  const data = useLive<InstancesResponse>("/instances", 5000);
  const strategies = useLive<{ strategies: Strategy[] }>("/strategy/list", 15000);
  const [selected, setSelected] = useState<string | null>(instanceId ?? null);
  const [form, setForm] = useState({ symbol: "BTCUSDT", strategy: "brain", strategy_version: "builtin-1", timeframe: "5m", risk: "0.5", capital: "1000", mode: "trading" });
  const [busy, setBusy] = useState(false);
  const create = async () => {
    setBusy(true);
    try {
      await apiPostJson("/instances", { symbol: form.symbol, strategy: form.strategy, strategy_version: form.strategy_version, timeframe: form.timeframe, risk_per_trade_pct: Number(form.risk) / 100, capital_allocation: Number(form.capital), mode: form.mode });
      app.toast("Trading instance created — start it when ready", "success"); data.refetch();
    } catch (e) { app.toast(e instanceof Error ? e.message : "Could not create instance", "error"); }
    finally { setBusy(false); }
  };
  const action = async (id: string, name: string) => {
    try { await apiPost(`/instances/${id}/${name}`); data.refetch(); app.toast(`Instance ${name} completed`, "success"); }
    catch (e) { app.toast(e instanceof Error ? e.message : `Could not ${name} instance`, "error"); }
  };
  const slots = async (value: number) => {
    try { await apiPostJson("/instances/platform", { max_active_slots: value }); data.refetch(); app.toast(`Active trading slots set to ${value}`, "success"); }
    catch (e) { app.toast(e instanceof Error ? e.message : "Could not update active slots", "error"); }
  };
  const autoSelect = async () => {
    try { const result = await apiPost<{ selected: Instance }>("/instances/auto-select"); app.viewInstance(result.selected.id); data.refetch(); app.toast("Best measured instance started", "success"); }
    catch (e) { app.toast(e instanceof Error ? e.message : "No eligible measured instance", "error"); }
  };
  const rows = data.data?.instances ?? [];
  const current = rows.find((x) => x.id === (instanceId ?? selected)) ?? rows[0];
  return <>
    <PageHeader title="Trading Instances" subtitle="isolated pair + strategy + version + capital · paper only" />
    <div className="stat-row">
      <StatCard label="Active slots" value={`${data.data?.active_slots ?? 0} / ${data.data?.max_active_slots ?? 1}`} sub="independent running engines" />
      <StatCard label="Instances" value={String(rows.length)} sub="separate histories and P&L" />
      <StatCard label="Global risk" value={`${money(data.data?.current_global_risk_amount)} / ${money(data.data?.max_global_risk_amount)}`} sub={`${((data.data?.max_global_risk_pct ?? 0.02) * 100).toFixed(1)}% account limit`} />
    </div>
    <div className="grid-2-eq">
      <Card title="Create Trading Instance" subtitle="one pair, one strategy version, one isolated paper allocation">
        <div className="form-grid-2">
          <Field label="Pair"><input value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value.toUpperCase() })} /></Field>
          <Field label="Strategy"><select value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })}>{(strategies.data?.strategies ?? []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select></Field>
          <Field label="Strategy version"><input value={form.strategy_version} onChange={(e) => setForm({ ...form, strategy_version: e.target.value })} /></Field>
          <Field label="Timeframe"><select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>{["1m", "5m", "15m", "1h", "4h"].map((x) => <option key={x}>{x}</option>)}</select></Field>
          <Field label="Risk per trade (%)"><input value={form.risk} onChange={(e) => setForm({ ...form, risk: e.target.value })} inputMode="decimal" /></Field>
          <Field label="Paper capital allocation"><input value={form.capital} onChange={(e) => setForm({ ...form, capital: e.target.value })} inputMode="decimal" /></Field>
          <Field label="Mode"><select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}><option value="trading">Trading (paper execution)</option><option value="research">Research (signals only)</option></select></Field>
        </div>
        <button className="btn btn-primary" disabled={busy} style={{ marginTop: 10 }} onClick={create}><Icon name="plus" size={14} /> {busy ? "Creating…" : "Create instance"}</button>
      </Card>
      <Card title="Research vs Trading" subtitle="research compares ideas; only approved trading instances execute paper orders">
        <p className="dim">Every instance has isolated execution, indicator state, positions, logs, trade history, statistics, strategy-health review, and P&amp;L. Global account risk remains enforced before any entry.</p>
        <div className="row-actions" style={{ justifyContent: "flex-start", gap: 8, marginTop: 10 }}>
          {[1, 2, 3].map((value) => <button key={value} className={`btn btn-sm ${data.data?.max_active_slots === value ? "btn-primary" : "btn-soft"}`} onClick={() => void slots(value)}>{value} slot{value > 1 ? "s" : ""}</button>)}
          <button className="btn btn-soft btn-sm" onClick={() => void autoSelect()}>Auto-select measured winner</button>
        </div>
      </Card>
    </div>
    <Card title="Pair + Strategy Leaderboard" subtitle="ranked from each instance’s own closed paper trades">
      <div className="tablewrap"><table className="data-table"><thead><tr><th>Pair</th><th>Strategy / Version</th><th>State</th><th>Trades</th><th>Win rate</th><th>Profit factor</th><th>Net profit</th><th></th></tr></thead><tbody>
        {rows.map((r) => <tr key={r.id} onClick={() => { setSelected(r.id); app.viewInstance(r.id); }} style={{ cursor: "pointer" }}><td><b>{r.symbol}</b></td><td>{r.strategy_label} <span className="dim">{r.strategy_version}</span></td><td><Badge text={r.state} tone={tone(r.state) as any} /></td><td>{r.metrics?.trades ?? 0}</td><td>{r.metrics?.win_rate ?? 0}%</td><td>{r.metrics?.profit_factor ?? 0}</td><td className={(r.metrics?.realized_pnl ?? 0) >= 0 ? "pos" : "neg"}>{money(r.metrics?.realized_pnl)}</td><td><button className="btn btn-soft btn-sm" onClick={(e) => { e.stopPropagation(); void action(r.id, r.state === "running" ? "stop" : "start"); }}>{r.state === "running" ? "Stop" : "Start"}</button></td></tr>)}
        {!rows.length && <tr><td colSpan={8} className="dim ta-center">No instances yet. Create one above; the legacy paper engine remains separate during migration.</td></tr>}
      </tbody></table></div>
    </Card>
    {current && <Card title={`${current.symbol} · ${current.strategy_label} ${current.strategy_version}`} subtitle="instance detail · isolated performance and health">
      <div className="stat-row"><StatCard label="Capital" value={money(current.capital_allocation)} /><StatCard label="Risk" value={`${(current.risk_per_trade_pct * 100).toFixed(2)}%`} /><StatCard label="Max drawdown" value={`${current.metrics?.max_drawdown_pct ?? 0}%`} /><StatCard label="Strategy health" value={current.metrics?.strategy_health?.status ?? "Healthy"} /></div>
      <div className="stat-row"><StatCard label="Profit factor" value={String(current.metrics?.profit_factor ?? 0)} /><StatCard label="Average RR" value={String(current.metrics?.average_rr ?? 0)} /><StatCard label="Open positions" value={String(current.engine?.open_positions ?? 0)} /><StatCard label="Last heartbeat" value={current.engine?.last_heartbeat ? new Date(current.engine.last_heartbeat).toLocaleTimeString() : "—"} /></div>
      <div className="row-actions" style={{ justifyContent: "flex-start", gap: 8, marginTop: 10 }}><button className="btn btn-warn" onClick={() => void action(current.id, "pause")}>Pause</button><button className="btn btn-primary" onClick={() => void action(current.id, "resume")}>Resume</button><button className="btn btn-soft" onClick={() => void action(current.id, "restart")}>Restart</button><button className="btn btn-danger" onClick={() => void action(current.id, "stop")}>Stop</button></div>
    </Card>}
  </>;
}
