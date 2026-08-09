import { useEffect, useMemo, useState } from "react";
import AreaLine from "../components/chart/AreaLine";
import Card from "../components/common/Card";
import Icon from "../components/common/Icon";
import { Badge, Field, PageHeader, StatCard } from "../components/common/ui";
import { apiPost, apiPostJson, useLive } from "../lib/api";
import { useApp } from "../app-context";

type Metric = Record<string, any>;
type MarketData = { market_data_mode?: string; market_data_status?: string; last_market_data_timestamp?: string; last_processed_candle_timestamp?: string; market_data_age_seconds?: number | null; warmup_bars?: number; duplicate_candles?: number; missing_candles?: number; out_of_order_candles?: number; reconnect_attempt?: number; data_source?: string; freshness_thresholds_seconds?: { healthy_under: number; disconnected_over: number } };
type Instance = { id: string; symbol: string; strategy_key: string; strategy_label: string; strategy_version: string; timeframe: string; risk_per_trade_pct: number; capital_allocation: number; sizing_mode?: string; fixed_position_size?: number; fixed_quantity?: number; profit_reinvestment?: boolean; maximum_risk_amount?: number | null; minimum_equity?: number | null; starting_equity?: number; current_realized_equity?: number; entry_mode?: string; fill_model?: string; execution_mode?: string; market_data_mode?: string; mode: string; state: string; created_at: string; started_at?: string | null; stopped_at?: string | null; last_error?: string; metrics: Metric; performance?: Metric; execution?: Metric; risk?: Metric; engine?: Metric | null; market_data?: MarketData; current_position?: Metric | null; strategy_health?: Metric | null; last_decision?: Metric | null };
type InstancesResponse = { instances: Instance[]; max_active_slots: number; active_slots: number; total_instances?: number; max_global_risk_pct: number; max_global_risk_amount: number; current_global_risk_amount: number; paper_account_capital?: number; total_allocated_capital?: number; total_current_equity?: number; available_paper_capital?: number; today_pnl?: number; today_trades?: number; total_open_positions?: number; global_risk_status?: string; market_data_status?: string; global_status?: string; instance_counts?: Record<string, number> };
type Options = { symbols: string[]; timeframes: string[]; strategies: { key: string; label: string; versions: string[] }[]; execution_defaults: { position_sizing_mode?: string; entry_mode?: string; fill_model?: string; leverage?: number | null; max_open_positions?: number }; sizing_modes?: { key: string; label: string; implemented: boolean }[]; market_data_mode: string };

const noValue = (value: unknown) => value === undefined || value === null || value === "";
const money = (value: unknown) => noValue(value) ? "—" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(Number(value));
const signedMoney = (value: unknown) => noValue(value) ? "—" : `${Number(value) > 0 ? "+" : ""}${money(value)}`;
const pct = (value: unknown, digits = 2) => noValue(value) ? "—" : `${Number(value).toFixed(digits)}%`;
const number = (value: unknown, digits = 2) => noValue(value) ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: digits });
const timestamp = (value: unknown) => noValue(value) ? "—" : new Date(String(value)).toLocaleString();
const duration = (seconds: unknown) => {
  if (noValue(seconds)) return "—";
  const n = Math.max(0, Number(seconds));
  if (n < 60) return `${Math.floor(n)}s`;
  if (n < 3600) return `${Math.floor(n / 60)}m`;
  return `${Math.floor(n / 3600)}h ${Math.floor((n % 3600) / 60)}m`;
};
const titleCase = (value?: string) => value ? value.replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()) : "Not available";
const tone = (state?: string) => state === "running" || state === "healthy" ? "green" : state === "paused" || state === "warning" || state === "stale" || state === "warming_up" ? "amber" : state === "error" || state === "critical" || state === "disconnected" ? "red" : "default";

function Detail({ label, value, negative = false }: { label: string; value: React.ReactNode; negative?: boolean }) {
  return <div style={{ minWidth: 0, marginBottom: 8 }}><div className="dim" style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em" }}>{label}</div><div className={negative ? "neg" : ""} style={{ overflowWrap: "anywhere" }}>{value}</div></div>;
}

function InstanceActions({ instance, action, compact = false }: { instance: Instance; action: (id: string, name: string) => Promise<void>; compact?: boolean }) {
  const cls = compact ? "btn btn-soft btn-sm" : "btn btn-soft btn-sm";
  return <div className="row-actions" style={{ justifyContent: "flex-start", gap: 6, flexWrap: "wrap" }}>
    {instance.state === "running" && <><button className={`${cls} btn-warn`} onClick={() => void action(instance.id, "pause")}>Pause</button><button className={`${cls} btn-danger`} onClick={() => void action(instance.id, "stop")}>Stop</button></>}
    {instance.state === "paused" && <><button className={`${cls} btn-primary`} onClick={() => void action(instance.id, "resume")}>Resume</button><button className={`${cls} btn-danger`} onClick={() => void action(instance.id, "stop")}>Stop</button></>}
    {instance.state !== "running" && instance.state !== "paused" && <button className={`${cls} btn-primary`} onClick={() => void action(instance.id, "start")}>Start</button>}
    <button className={cls} onClick={() => void action(instance.id, "restart")}>Restart</button>
  </div>;
}

export default function TradingInstancesPage({ instanceId }: { instanceId?: string }) {
  const app = useApp();
  const live = useLive<InstancesResponse>("/instances", 5000);
  const options = useLive<Options>("/instances/options", 30000);
  const [selected, setSelected] = useState<string | null>(instanceId ?? null);
  const [view, setView] = useState<"cards" | "table">("cards");
  const [range, setRange] = useState<"today" | "7d" | "30d" | "all">("all");
  const [filters, setFilters] = useState({ status: "", pair: "", strategy: "", timeframe: "", version: "", query: "", sort: "newest" });
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [form, setForm] = useState({ symbol: "", strategy: "", strategy_version: "", timeframe: "", risk: "0.5", capital: "1000", sizing_mode: "fixed_starting_equity_percent", fixed_quantity: "", profit_reinvestment: false, maximum_risk_amount: "", minimum_equity: "", entry_mode: "limit" });

  useEffect(() => {
    const firstStrategy = options.data?.strategies[0];
    setForm((current) => ({ ...current,
      symbol: current.symbol || options.data?.symbols[0] || "",
      strategy: current.strategy || firstStrategy?.key || "",
      strategy_version: current.strategy_version || firstStrategy?.versions[0] || "",
      timeframe: current.timeframe || options.data?.timeframes.find((x) => x === "5m") || options.data?.timeframes[0] || "",
      sizing_mode: current.sizing_mode || options.data?.execution_defaults.position_sizing_mode || "fixed_starting_equity_percent",
    }));
  }, [options.data]);

  const rows = live.data?.instances ?? [];
  const filtered = useMemo(() => {
    const query = filters.query.trim().toLowerCase();
    const output = rows.filter((row) => (!filters.status || row.state === filters.status)
      && (!filters.pair || row.symbol === filters.pair)
      && (!filters.strategy || row.strategy_key === filters.strategy)
      && (!filters.timeframe || row.timeframe === filters.timeframe)
      && (!filters.version || row.strategy_version === filters.version)
      && (!query || [row.symbol, row.strategy_label, row.strategy_version, row.timeframe, row.state].join(" ").toLowerCase().includes(query)));
    const val = (row: Instance, key: string): number | string => {
      if (key === "newest" || key === "oldest") return Date.parse(row.created_at || "") || 0;
      if (key === "risk") return row.risk_per_trade_pct || 0;
      if (key === "status") return row.state;
      return Number((row.performance ?? row.metrics)?.[key] ?? 0);
    };
    return output.sort((a, b) => {
      const left = val(a, filters.sort), right = val(b, filters.sort);
      if (typeof left === "string" || typeof right === "string") return String(left).localeCompare(String(right));
      const asc = filters.sort === "oldest" || filters.sort === "max_drawdown_pct" || filters.sort === "risk";
      return asc ? Number(left) - Number(right) : Number(right) - Number(left);
    });
  }, [rows, filters]);
  const active = filtered.filter((row) => row.state === "running");
  const inactive = (state: string) => filtered.filter((row) => row.state === state);
  const current = rows.find((row) => row.id === (instanceId ?? selected)) ?? rows[0];
  const formStrategy = options.data?.strategies.find((row) => row.key === form.strategy);

  const create = async () => {
    if (busy || !form.symbol || !form.strategy || !form.strategy_version || !form.timeframe) return;
    setCreateError(null);
    setBusy(true);
    try {
      const created = await apiPostJson<{ instance: Instance }>("/instances", { symbol: form.symbol, strategy: form.strategy, strategy_version: form.strategy_version, timeframe: form.timeframe, risk_per_trade_pct: Number(form.risk) / 100, capital_allocation: Number(form.capital), mode: "trading", sizing_mode: form.sizing_mode, fixed_quantity: Number(form.fixed_quantity || 0), profit_reinvestment: form.profit_reinvestment, maximum_risk_amount: form.maximum_risk_amount ? Number(form.maximum_risk_amount) : null, minimum_equity: form.minimum_equity ? Number(form.minimum_equity) : null, entry_mode: form.entry_mode, fill_model: "PerfectFill" });
      // Cards are rendered only from the authoritative GET /instances poll.
      // Never append the POST payload optimistically: creation may fail while
      // Supabase validates the durable per-instance market cursor.
      setSelected(created.instance.id);
      live.refetch();
      app.toast("Trading instance created — start it when ready", "success");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not create instance";
      setCreateError(message);
      app.toast(message, "error");
    }
    finally { setBusy(false); }
  };
  const action = async (id: string, name: string) => {
    try { await apiPost(`/instances/${id}/${name}`); live.refetch(); app.toast(`Instance ${name} completed`, "success"); }
    catch (error) { app.toast(error instanceof Error ? error.message : `Could not ${name} instance`, "error"); }
  };

  const marketLegend = current?.market_data?.freshness_thresholds_seconds;
  const curve = ((current?.performance ?? current?.metrics)?.equity_curve ?? []).filter((point: any) => {
    if (range === "all" || !point.t) return true;
    const age = Date.now() - Date.parse(point.t);
    return age <= (range === "today" ? 86400000 : range === "7d" ? 604800000 : 2592000000);
  });

  return <>
    <PageHeader title="Trading Instances" subtitle="independent pair + strategy workers · paper forward uses live closed candles only" />
    <div className="stat-row instance-overview">
      <StatCard label="Active slots" value={`${live.data?.active_slots ?? "—"} / ${live.data?.max_active_slots ?? "—"}`} sub="running paper workers" />
      <StatCard label="Total instances" value={String(live.data?.total_instances ?? rows.length)} sub={`running ${live.data?.instance_counts?.running ?? 0} · paused ${live.data?.instance_counts?.paused ?? 0} · stopped ${live.data?.instance_counts?.stopped ?? 0} · error ${live.data?.instance_counts?.error ?? 0}`} />
      <StatCard label="Allocated / available" value={`${money(live.data?.total_allocated_capital)} / ${money(live.data?.available_paper_capital)}`} sub={`current equity ${money(live.data?.total_current_equity)}`} />
      <StatCard label="Global open risk" value={`${money(live.data?.current_global_risk_amount)} / ${money(live.data?.max_global_risk_amount)}`} sub={titleCase(live.data?.global_risk_status)} />
      <StatCard label="Today" value={signedMoney(live.data?.today_pnl)} sub={`${live.data?.today_trades ?? "—"} opened or closed trades`} />
      <StatCard label="Platform" value={titleCase(live.data?.global_status)} sub={`market data ${titleCase(live.data?.market_data_status)}`} />
    </div>

    <div className="instances-control-grid">
      <Card className="instance-control-card" title="Create Trading Instance" subtitle="1 pair · 2 strategy · 3 version · 4 timeframe · 5 capital · 6 risk · 7 sizing · 8 entry · 9 review · 10 start">
        <div className="form-grid-2">
          <Field label="Pair"><select value={form.symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })}>{(options.data?.symbols ?? []).map((value) => <option key={value}>{value}</option>)}</select></Field>
          <Field label="Strategy"><select value={form.strategy} onChange={(e) => { const strategy = options.data?.strategies.find((row) => row.key === e.target.value); setForm({ ...form, strategy: e.target.value, strategy_version: strategy?.versions[0] ?? "" }); }}>{(options.data?.strategies ?? []).map((value) => <option key={value.key} value={value.key}>{value.label}</option>)}</select></Field>
          <Field label="Strategy version"><select value={form.strategy_version} onChange={(e) => setForm({ ...form, strategy_version: e.target.value })}>{(formStrategy?.versions ?? []).map((value) => <option key={value}>{value}</option>)}</select></Field>
          <Field label="Timeframe"><select value={form.timeframe} onChange={(e) => setForm({ ...form, timeframe: e.target.value })}>{(options.data?.timeframes ?? []).map((value) => <option key={value}>{value}</option>)}</select></Field>
          <Field label="Capital allocation"><input value={form.capital} onChange={(e) => setForm({ ...form, capital: e.target.value })} inputMode="decimal" /></Field>
          <Field label="Risk per trade (%)"><input value={form.risk} onChange={(e) => setForm({ ...form, risk: e.target.value })} inputMode="decimal" /></Field>
          <Field label="Sizing mode"><select value={form.sizing_mode} onChange={(e) => setForm({ ...form, sizing_mode: e.target.value })}>{(options.data?.sizing_modes ?? []).filter((value) => value.implemented).map((value) => <option key={value.key} value={value.key}>{value.label}</option>)}</select></Field>
          {form.sizing_mode === "fixed_quantity" && <Field label="Fixed quantity" hint="base-asset units on every trade"><input value={form.fixed_quantity} onChange={(e) => setForm({ ...form, fixed_quantity: e.target.value })} inputMode="decimal" /></Field>}
          {form.sizing_mode === "dynamic_current_equity_percent" && <Field label="Profit reinvestment" hint="losses always reduce risk"><select value={form.profit_reinvestment ? "on" : "off"} onChange={(e) => setForm({ ...form, profit_reinvestment: e.target.value === "on" })}><option value="off">Off — freeze upside risk</option><option value="on">On — compound realized profit</option></select></Field>}
          <Field label="Maximum risk amount" hint="optional hard cap per trade"><input value={form.maximum_risk_amount} placeholder="No additional cap" onChange={(e) => setForm({ ...form, maximum_risk_amount: e.target.value })} inputMode="decimal" /></Field>
          <Field label="Minimum equity" hint="optional risk-halt floor"><input value={form.minimum_equity} placeholder="No equity floor" onChange={(e) => setForm({ ...form, minimum_equity: e.target.value })} inputMode="decimal" /></Field>
          <Field label="Entry mode"><select value={form.entry_mode} onChange={(e) => setForm({ ...form, entry_mode: e.target.value })}><option value="limit">Limit</option><option value="market">Market</option></select></Field>
          <Field label="Fill model"><input readOnly value={options.data?.execution_defaults?.fill_model ?? "Not available"} /></Field>
          <Field label="Market data mode"><input readOnly value="Paper Forward — Live Only" /></Field>
        </div>
        <p className="dim" style={{ margin: "10px 0 0", fontSize: 12 }}>Execution defaults are server-owned and shown read-only. Historical replay cannot be created as a paper-trading instance.</p>
        {createError && <div className="instance-risk-notice red" role="alert" style={{ marginTop: 10 }}>{createError}</div>}
        <button className="btn btn-primary" disabled={busy || !form.symbol} aria-busy={busy} style={{ marginTop: 10 }} onClick={() => void create()}><Icon name="plus" size={14} /> {busy ? "Creating…" : "Create instance"}</button>
      </Card>
      <Card className="instance-filter-card" title="Filters & display" subtitle="instance-scoped records only">
        <div className="form-grid-2">
          <Field label="Search"><input placeholder="Pair, strategy, version…" value={filters.query} onChange={(e) => setFilters({ ...filters, query: e.target.value })} /></Field>
          <Field label="Sort"><select value={filters.sort} onChange={(e) => setFilters({ ...filters, sort: e.target.value })}>{[["newest", "Newest"], ["oldest", "Oldest"], ["net_pnl", "Net P&L"], ["profit_factor", "Profit Factor"], ["win_rate", "Win Rate"], ["expectancy", "Expectancy"], ["max_drawdown_pct", "Max Drawdown"], ["risk", "Risk"], ["status", "Status"]].map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
          <Field label="Status"><select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}><option value="">All</option>{["running", "paused", "stopped", "error"].map((value) => <option key={value}>{titleCase(value)}</option>)}</select></Field>
          <Field label="Pair"><select value={filters.pair} onChange={(e) => setFilters({ ...filters, pair: e.target.value })}><option value="">All</option>{[...new Set(rows.map((row) => row.symbol))].map((value) => <option key={value}>{value}</option>)}</select></Field>
          <Field label="Strategy"><select value={filters.strategy} onChange={(e) => setFilters({ ...filters, strategy: e.target.value })}><option value="">All</option>{[...new Map(rows.map((row) => [row.strategy_key, row.strategy_label])).entries()].map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></Field>
          <Field label="Timeframe"><select value={filters.timeframe} onChange={(e) => setFilters({ ...filters, timeframe: e.target.value })}><option value="">All</option>{[...new Set(rows.map((row) => row.timeframe))].map((value) => <option key={value}>{value}</option>)}</select></Field>
          <Field label="Strategy version"><select value={filters.version} onChange={(e) => setFilters({ ...filters, version: e.target.value })}><option value="">All</option>{[...new Set(rows.map((row) => row.strategy_version))].map((value) => <option key={value}>{value}</option>)}</select></Field>
          <Field label="View"><div className="row-actions" style={{ justifyContent: "flex-start", gap: 6 }}><button className={`btn btn-sm ${view === "cards" ? "btn-primary" : "btn-soft"}`} onClick={() => setView("cards")}>Cards</button><button className={`btn btn-sm ${view === "table" ? "btn-primary" : "btn-soft"}`} onClick={() => setView("table")}>Table</button></div></Field>
        </div>
      </Card>
    <Card className="instance-health-card" title="Market Data Health" subtitle="actual latest closed candles; source connectivity alone is never healthy">
      <div className="dim" style={{ marginBottom: 10, fontSize: 12 }}>Healthy &lt; {marketLegend ? `${duration(marketLegend.healthy_under)}` : "1.5× timeframe"} · Stale 1.5×–3× · Disconnected &gt; {marketLegend ? duration(marketLegend.disconnected_over) : "3× timeframe"} · Error = no usable source</div>
      <div className="tablewrap"><table className="data-table"><thead><tr><th>Pair</th><th>TF</th><th>Source</th><th>Last closed</th><th>Processed</th><th>Age</th><th>Status</th><th>Warm-up</th></tr></thead><tbody>
        {active.map((row) => <tr key={row.id}><td><b>{row.symbol}</b></td><td>{row.timeframe}</td><td>{row.market_data?.data_source ?? "Not available"}</td><td>{timestamp(row.market_data?.last_market_data_timestamp)}</td><td>{timestamp(row.market_data?.last_processed_candle_timestamp)}</td><td>{duration(row.market_data?.market_data_age_seconds)}</td><td><Badge text={titleCase(row.market_data?.market_data_status)} tone={tone(row.market_data?.market_data_status) as any} /></td><td>{row.market_data?.warmup_bars ?? "—"} / 150</td></tr>)}
        {!active.length && <tr><td colSpan={8} className="dim ta-center">No running trading instances match the current filters.</td></tr>}
      </tbody></table></div>
    </Card>
    </div>

    <Card className="instance-active-card" title="Active Trading Instances" subtitle="each card is a separate worker, ledger scope, strategy state, and paper execution account" right={<Badge text={`${active.length} running`} tone={active.length ? "green" : "default"} />}>
      {view === "table" ? <div className="tablewrap"><table className="data-table"><thead><tr><th>Pair</th><th>Strategy</th><th>Version</th><th>TF</th><th>State</th><th>Capital</th><th>Risk</th><th>P&L</th><th>Trades</th><th>WR</th><th>PF</th><th>Market data</th><th>Position</th><th>Health</th><th></th></tr></thead><tbody>
        {filtered.map((row) => <tr key={row.id}><td><button className="btn btn-link" onClick={() => setSelected(row.id)}>{row.symbol}</button></td><td>{row.strategy_label}</td><td>{row.strategy_version}</td><td>{row.timeframe}</td><td><Badge text={titleCase(row.state)} tone={tone(row.state) as any} /></td><td>{money(row.capital_allocation)}</td><td>{pct(row.risk_per_trade_pct * 100)}</td><td className={Number(row.performance?.net_pnl ?? row.metrics?.realized_pnl) < 0 ? "neg" : "pos"}>{signedMoney(row.performance?.net_pnl ?? row.metrics?.realized_pnl)}</td><td>{row.performance?.trades ?? row.metrics?.trades ?? "—"}</td><td>{pct(row.performance?.win_rate ?? row.metrics?.win_rate, 1)}</td><td>{number(row.performance?.profit_factor ?? row.metrics?.profit_factor)}</td><td>{titleCase(row.market_data?.market_data_status)}</td><td>{row.current_position ? `${row.current_position.side} ${row.current_position.symbol}` : "—"}</td><td>{row.strategy_health?.status ?? "Not available"}</td><td><InstanceActions instance={row} action={action} compact /></td></tr>)}
        {!filtered.length && <tr><td colSpan={15} className="dim ta-center">No instances match the current filters.</td></tr>}
      </tbody></table></div> : <div style={{ display: "grid", gap: 12 }}>
        {active.map((row) => <article key={row.id} className="instance-worker-row">
          <section className="instance-worker-column instance-identity"><Badge text={titleCase(row.state)} tone={tone(row.state) as any} /><h3>{row.symbol}</h3><div>{row.strategy_label}</div><div className="dim">{row.strategy_version} · Paper Forward</div><div className="instance-worker-actions"><InstanceActions instance={row} action={action} /></div><button className="btn btn-link" onClick={() => setSelected(row.id)}>Open details</button></section>
          <section className="instance-worker-column"><Detail label="Timeframe" value={row.timeframe} /><Detail label="Allocation / realized equity" value={`${money(row.capital_allocation)} / ${money(row.execution?.current_realized_equity)}`} /><Detail label="Risk % / next max risk" value={`${pct(row.risk_per_trade_pct * 100)} / ${money(row.execution?.next_trade_risk_amount)}`} /><Detail label="Next quantity" value={row.execution?.next_trade_quantity ?? "Calculated from the next valid stop"} /><Detail label="Sizing / entry" value={`${titleCase(row.sizing_mode ?? row.execution?.position_sizing_mode)} / ${titleCase(row.entry_mode ?? row.execution?.entry_mode)}`} /><Detail label="Risk basis / reinvest" value={`${money(row.execution?.risk_basis)} / ${row.profit_reinvestment ? "On" : "Off"}`} /></section>
          <section className="instance-worker-column"><Detail label="Source" value={row.market_data?.data_source ?? "Not available"} /><Detail label="Last closed / processed" value={`${timestamp(row.market_data?.last_market_data_timestamp)} / ${timestamp(row.market_data?.last_processed_candle_timestamp)}`} /><Detail label="Age / status" value={`${duration(row.market_data?.market_data_age_seconds)} / ${titleCase(row.market_data?.market_data_status)}`} /><Detail label="Warm-up" value={`${row.market_data?.warmup_bars ?? "—"} / 150`} /><Detail label="Duplicate / missing / out-of-order" value={`${row.market_data?.duplicate_candles ?? "—"} / ${row.market_data?.missing_candles ?? "—"} / ${row.market_data?.out_of_order_candles ?? "—"}`} /><Detail label="Reconnects" value={row.engine?.reconnect_attempt ?? "Not available"} /></section>
          <section className="instance-worker-column"><Detail label="Net P&L / return" value={`${signedMoney(row.performance?.net_pnl)} / ${pct(row.performance?.return_pct, 3)}`} /><Detail label="Trades / win rate" value={`${row.performance?.trades ?? "—"} / ${pct(row.performance?.win_rate, 1)}`} /><Detail label="Profit factor / average R" value={`${number(row.performance?.profit_factor)} / ${number(row.performance?.average_rr, 3)}R`} /><Detail label="Expectancy / max DD" value={`${money(row.performance?.expectancy)} / ${pct(row.performance?.max_drawdown_pct)}`} /><Detail label="Sharpe (per-trade R)" value={number(row.performance?.sharpe_ratio)} /><Detail label="Strategy health" value={row.strategy_health?.status ?? "Not available"} /></section>
          <section className="instance-worker-column"><Detail label="Uptime" value={row.engine?.started_at ? duration((Date.now() - Date.parse(row.engine.started_at)) / 1000) : "—"} /><Detail label="Last decision" value={timestamp(row.last_decision?.ts)} /><Detail label="Open positions / orders" value={`${row.engine?.open_positions ?? "—"} / ${row.execution?.pending_orders ?? "—"}`} /><Detail label="Open risk" value={`${money(row.risk?.open_risk_amount)} / ${pct(row.risk?.open_risk_pct, 3)}`} /><Detail label="Unrealized P&L" value={signedMoney(row.execution?.unrealized_pnl)} /><Detail label="Worker heartbeat" value={timestamp(row.engine?.last_heartbeat)} /></section>
        </article>)}
        {!active.length && <div className="dim ta-center" style={{ padding: 14 }}>No running instances match the current filters.</div>}
      </div>}
    </Card>

    {current && <div className="grid-2-eq">
      <Card title={`${current.symbol} performance`} subtitle="instance-only closed paper trades; never blended with another strategy or pair">
        <div className="row-actions" style={{ justifyContent: "flex-start", gap: 6, marginBottom: 8 }}>{(["today", "7d", "30d", "all"] as const).map((value) => <button key={value} className={`btn btn-sm ${range === value ? "btn-primary" : "btn-soft"}`} onClick={() => setRange(value)}>{value === "all" ? "All" : value.toUpperCase()}</button>)}</div>
        {curve.length > 1 ? <div className="chart-md"><AreaLine labels={curve.map((point: any) => point.t ? new Date(point.t).toLocaleDateString() : "Start")} series={[{ name: "Equity", data: curve.map((point: any) => Number(point.equity)), color: "#eab54f" }]} valueFormatter={(value) => money(value)} /></div> : <div className="dim ta-center" style={{ padding: 48 }}>Insufficient data</div>}
      </Card>
      <Card title={`${current.symbol} status`} subtitle="actual worker, decision, risk, and position state">
        {current.last_error && <div className="instance-risk-notice amber"><b>{titleCase(current.state)}</b><br />{current.last_error}</div>}
        <div className="form-grid-2"><Detail label="Last decision" value={current.last_decision ? `${titleCase(current.last_decision.decision)} · ${current.last_decision.side ?? "No side"}` : "Not available"} /><Detail label="Decision reason" value={current.last_decision?.reason ?? "Not available"} /><Detail label="Strategy health" value={current.strategy_health?.status ?? "Not available"} /><Detail label="Health sample" value={current.strategy_health?.sample_size ?? "Not available"} /><Detail label="Starting / realized equity" value={`${money(current.execution?.starting_equity)} / ${money(current.execution?.current_realized_equity)}`} /><Detail label="Unrealized / mark-to-market" value={`${signedMoney(current.execution?.unrealized_pnl)} / ${money(current.execution?.mark_to_market_equity)}`} /><Detail label="Gross P&L / fees / net" value={`${signedMoney(current.execution?.gross_realized_pnl)} / ${money(current.execution?.fees_paid)} / ${signedMoney(current.execution?.realized_pnl)}`} /><Detail label="Available capital" value={money(current.execution?.available_capital)} /><Detail label="Sizing / basis / next risk" value={`${titleCase(current.sizing_mode)} / ${money(current.execution?.risk_basis)} / ${money(current.execution?.next_trade_risk_amount)}`} /><Detail label="Risk cap / equity floor" value={`${money(current.maximum_risk_amount)} / ${money(current.minimum_equity)}`} /></div>
        {current.current_position ? <details style={{ marginTop: 8 }}><summary style={{ cursor: "pointer" }}>Open position · {current.current_position.side} {current.current_position.symbol}</summary><div className="form-grid-2" style={{ marginTop: 10 }}><Detail label="Entry / current" value={`${number(current.current_position.entry, 8)} / ${number(current.current_position.mark, 8)}`} /><Detail label="Stop / target" value={`${number(current.current_position.stop, 8)} / ${number(current.current_position.target, 8)}`} /><Detail label="Quantity / risk" value={`${number(current.current_position.size, 8)} / ${money(current.current_position.risk_amount)}`} /><Detail label="Current R / P&L" value={`${number(current.current_position.current_r, 3)}R / ${signedMoney(current.current_position.unrealized_pnl)}`} /><Detail label="Duration" value={duration(current.current_position.duration_seconds)} /></div></details> : <p className="dim" style={{ marginTop: 12 }}>No open position for this instance.</p>}
      </Card>
    </div>}

    {(["paused", "stopped", "error"] as const).map((state) => <details key={state} style={{ marginTop: 12 }}><summary className="card" style={{ cursor: "pointer", padding: 12 }}>{titleCase(state)} · {inactive(state).length}</summary>{inactive(state).map((row) => <Card key={row.id} title={`${row.symbol} · ${row.strategy_label}`} subtitle={`${row.strategy_version} · ${row.timeframe}`}><div className="grid-2-eq"><div>{row.last_error ? <div className="instance-risk-notice amber">{row.last_error}</div> : <p className="dim">No error reason recorded.</p>}</div><InstanceActions instance={row} action={action} /></div></Card>)}</details>)}
  </>;
}
