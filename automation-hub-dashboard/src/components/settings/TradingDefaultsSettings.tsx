import { useEffect, useState } from "react";
import { apiPostJson, useLive } from "../../lib/api";
import { Field } from "../common/ui";
import SettingsSection, { type SaveState } from "./SettingsSection";
import SettingsSaveBar from "./SettingsSaveBar";

type Defaults = { default_symbol: string; default_timeframe: string; default_strategy: string; default_capital: number; default_risk_per_trade_pct: number; default_max_open_positions: number; default_entry_mode: string; default_fill_model: string };
type Options = { symbols: string[]; timeframes: string[]; strategies: Array<{ key: string; label: string }>; fill_models: Array<{ key: string; label: string }>; platform_defaults: Defaults };

export default function TradingDefaultsSettings() {
  const options = useLive<Options>("/instances/options", 30000);
  const [saved, setSaved] = useState<Defaults | null>(null); const [form, setForm] = useState<Defaults | null>(null);
  const [state, setState] = useState<SaveState>("saved"); const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (options.data && !saved) { setSaved(options.data.platform_defaults); setForm(options.data.platform_defaults); } }, [options.data, saved]);
  const change = (patch: Partial<Defaults>) => { if (!form) return; setForm({ ...form, ...patch }); setState("dirty"); setError(null); };
  const save = async () => { if (!form) return; setState("saving"); try { await apiPostJson("/instances/platform", form); setSaved(form); setState("saved"); await options.refetch(); } catch (err) { setError(err instanceof Error ? err.message : "Save failed"); setState("error"); } };
  return <SettingsSection title="Trading Defaults" description="Platform defaults stored in the Trading Instance database." state={state}>
    <div className="banner"><b>These defaults are applied only when creating a new Trading Instance.</b> Existing instances are not modified.</div>
    {!form ? <p className="dim">Loading defaults…</p> : <div className="form-grid-2">
      <Field label="Default symbol"><select value={form.default_symbol} onChange={(e) => change({ default_symbol: e.target.value })}>{(options.data?.symbols ?? []).map((value) => <option key={value}>{value}</option>)}</select></Field>
      <Field label="Default timeframe"><select value={form.default_timeframe} onChange={(e) => change({ default_timeframe: e.target.value })}>{(options.data?.timeframes ?? []).map((value) => <option key={value}>{value}</option>)}</select></Field>
      <Field label="Default strategy"><select value={form.default_strategy} onChange={(e) => change({ default_strategy: e.target.value })}>{(options.data?.strategies ?? []).map((value) => <option key={value.key} value={value.key}>{value.label}</option>)}</select></Field>
      <Field label="Default capital"><input type="number" min="1" value={form.default_capital} onChange={(e) => change({ default_capital: Number(e.target.value) })} /></Field>
      <Field label="Default risk per trade (%)"><input type="number" min="0.1" max="5" step="0.1" value={form.default_risk_per_trade_pct * 100} onChange={(e) => change({ default_risk_per_trade_pct: Number(e.target.value) / 100 })} /></Field>
      <Field label="Default max open positions"><input type="number" min="1" max="50" value={form.default_max_open_positions} onChange={(e) => change({ default_max_open_positions: Number(e.target.value) })} /></Field>
      <Field label="Default entry mode"><select value={form.default_entry_mode} onChange={(e) => change({ default_entry_mode: e.target.value })}><option value="limit">Limit</option><option value="market">Market</option></select></Field>
      <Field label="Default paper fill model"><select value={form.default_fill_model} onChange={(e) => change({ default_fill_model: e.target.value })}>{(options.data?.fill_models ?? []).map((value) => <option key={value.key} value={value.key}>{value.label}</option>)}</select></Field>
      <Field label="Instrument type"><input disabled value="Spot (only supported Trading Instance market)" /></Field>
    </div>}
    <SettingsSaveBar state={state} error={error || options.error} onSave={() => void save()} onDiscard={() => { setForm(saved); setState("saved"); setError(null); }} />
  </SettingsSection>;
}
