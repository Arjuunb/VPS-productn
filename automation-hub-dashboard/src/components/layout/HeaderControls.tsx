import { useEffect, useRef, useState, type ReactNode } from "react";
import Icon from "../common/Icon";
import { useApp } from "../../app-context";
import { useLive } from "../../lib/api";

type Instance = {
  id: string; symbol: string; strategy_label: string; strategy_version: string;
  timeframe: string; state: string; risk_per_trade_pct?: number; capital_allocation?: number;
  sizing_mode?: string; entry_mode?: string; fill_model?: string;
  market_data?: { market_data_status?: string; data_source?: string };
};
type InstanceSnapshot = {
  instances: Instance[]; active_slots: number; max_active_slots: number;
  total_current_equity?: number; paper_account_capital?: number;
  market_data_status?: string;
};
type Options = { strategies: { key: string; label: string }[]; timeframes: string[] };

function usePopover<T extends HTMLElement>() {
  const [open, setOpen] = useState(false);
  const ref = useRef<T | null>(null);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => { if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false); };
    const key = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close); document.addEventListener("keydown", key);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", key); };
  }, [open]);
  return { open, setOpen, ref };
}

function Chip({ children, onClick, label, active }: { children: ReactNode; onClick: () => void; label: string; active?: boolean }) {
  return <button type="button" className={`hdr-chip ${active ? "open" : ""}`} aria-label={label} aria-haspopup="menu" aria-expanded={active} onClick={onClick}>
    {children}<Icon name="chevron" size={10} className="dim hdr-caret" />
  </button>;
}

/**
 * Original terminal header layout, deliberately sourced from the active
 * Trading Instance. It replaces the former legacy-engine controls without
 * allowing a global symbol, strategy, or timeframe to override a worker.
 */
export default function HeaderControls() {
  const app = useApp();
  const live = useLive<InstanceSnapshot>("/instances", 4000);
  const options = useLive<Options>("/instances/options", 30000);
  const account = usePopover<HTMLDivElement>();
  const strategy = usePopover<HTMLDivElement>();
  const timeframe = usePopover<HTMLDivElement>();
  const settings = usePopover<HTMLDivElement>();
  const running = (live.data?.instances ?? []).filter((item) => item.state === "running");
  const lead = running[0];
  const healthy = Boolean(lead && lead.market_data?.market_data_status === "healthy");
  const dot = live.error ? "offline" : lead ? (healthy ? "online" : "warn") : "warn";
  const engineLabel = live.error ? "Backend offline" : lead ? "Engine Running" : "Engine Stopped";
  const showInstance = () => lead ? app.viewInstance(lead.id) : app.go("Trading Instances");
  const money = (value?: number) => `$${Number(value ?? 0).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;

  return <div className="hdr-controls" title={live.error ?? "Active Trading Instance terminal"}>
    <div className="hdr-seg" ref={account.ref}>
      <Chip label="Trading account" active={account.open} onClick={() => account.setOpen(!account.open)}>
        <span className={`dot ${dot}`} /><b>Paper</b>
      </Chip>
      {account.open && <div className="hdr-pop" role="menu">
        <p className="hdr-pop-title">Trading account</p>
        <div className="hdr-kv">
          <span>Account</span><b>Paper Account</b>
          <span>Balance</span><b>{money(live.data?.total_current_equity)}</b>
          <span>Data venue</span><b>{lead?.market_data?.data_source ?? "—"}</b>
          <span>Connection</span><b className={healthy ? "pos" : "neg"}>{healthy ? "connected" : "waiting"}</b>
        </div>
        <div className="hdr-pop-sep" />
        <button className="hdr-item active" onClick={() => { account.setOpen(false); app.go("Paper Trading"); }}><span className="dot online" /> Paper Trading <span className="hdr-tag">current</span></button>
        <button className="hdr-item" onClick={() => { account.setOpen(false); app.go("Live Trading"); }}><span className="dot offline" /> Live Trading <span className="hdr-tag">gated</span></button>
      </div>}
    </div>

    <button className="hdr-static dot-label" onClick={showInstance} title="Open active Trading Instance">
      <span className={`dot ${dot}`} /><span className="hide-sm">{engineLabel}</span>
    </button>

    <div className="hdr-seg" ref={strategy.ref}>
      <Chip label="Active instance strategy" active={strategy.open} onClick={() => strategy.setOpen(!strategy.open)}><b>{lead?.strategy_label ?? "Strategy"}</b></Chip>
      {strategy.open && <div className="hdr-pop hdr-pop-wide" role="menu">
        <p className="hdr-pop-title">Strategy — active Trading Instance</p>
        {(options.data?.strategies ?? []).map((item) => {
          const active = item.label === lead?.strategy_label;
          return <button className={`hdr-item ${active ? "active" : ""}`} key={item.key} onClick={() => { strategy.setOpen(false); showInstance(); }}>
            <b>{item.label}</b>{active ? <span className="hdr-tag">active</span> : <span className="dim">view / create</span>}
          </button>;
        })}
        <p className="hdr-note">Strategy, version, and pair are immutable after creation to protect instance attribution. Create a new instance to compare a strategy.</p>
      </div>}
    </div>

    <div className="hdr-seg" ref={timeframe.ref}>
      <Chip label="Active instance timeframe" active={timeframe.open} onClick={() => timeframe.setOpen(!timeframe.open)}><b className="mono">{lead?.timeframe ?? "—"}</b></Chip>
      {timeframe.open && <div className="hdr-pop" role="menu">
        <p className="hdr-pop-title">Candle timeframe — active instance</p>
        <div className="tf-grid">{(options.data?.timeframes ?? ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"]).map((item) =>
          <button className={`tf-btn ${item === lead?.timeframe ? "active" : ""}`} key={item} onClick={() => { timeframe.setOpen(false); showInstance(); }}>{item}</button>)}</div>
        <p className="hdr-note">Timeframe is persisted with each instance. Open the instance to review or create another configuration.</p>
      </div>}
    </div>

    <div className="hdr-seg" ref={settings.ref}>
      <button type="button" className={`hdr-chip hdr-gear ${settings.open ? "open" : ""}`} aria-label="Instance settings terminal" aria-haspopup="dialog" aria-expanded={settings.open} onClick={() => settings.setOpen(!settings.open)}><Icon name="settings" size={13} /></button>
      {settings.open && <div className="hdr-pop hdr-pop-wide hdr-settings" role="dialog" aria-label="Instance settings terminal">
        <p className="hdr-pop-title">Engine settings — active Trading Instance</p>
        <p className="hdr-sect">General</p>
        <div className="hdr-kv">
          <span>Engine</span><b>{lead ? `${lead.symbol} · ${lead.strategy_label} · ${lead.timeframe}` : "No active instance"}</b>
          <span>Mode</span><b>paper forward (live closed candles)</b>
          <span>State</span><b className={lead ? "pos" : "neg"}>{lead?.state ?? "stopped"}</b>
        </div>
        <p className="hdr-sect">Execution</p>
        <div className="hdr-kv">
          <span>Order type</span><b>{lead?.entry_mode ?? "—"}</b>
          <span>Sizing</span><b>{lead?.sizing_mode ?? "—"}</b>
          <span>Fill model</span><b>{lead?.fill_model ?? "—"}</b>
        </div>
        <p className="hdr-sect">Risk</p>
        <div className="hdr-kv">
          <span>Capital allocation</span><b>{money(lead?.capital_allocation)}</b>
          <span>Risk per trade</span><b>{lead?.risk_per_trade_pct === undefined ? "—" : `${(lead.risk_per_trade_pct * 100).toFixed(2)}%`}</b>
          <span>Market data</span><b>{lead?.market_data?.data_source ?? "—"} · {lead?.market_data?.market_data_status ?? "—"}</b>
        </div>
        <div className="hdr-actions">
          <button className="btn btn-sm" onClick={() => { settings.setOpen(false); showInstance(); }}>Open instance</button>
          <button className="btn btn-ghost btn-sm" onClick={() => { settings.setOpen(false); app.go("Settings"); }}>Global settings</button>
        </div>
      </div>}
    </div>
  </div>;
}
