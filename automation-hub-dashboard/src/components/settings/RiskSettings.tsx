import { useEffect, useState } from "react";
import { apiPost, apiPostJson, useLive } from "../../lib/api";
import { Badge, Field } from "../common/ui";
import SettingsSection, { type SaveState } from "./SettingsSection";
import SettingsSaveBar from "./SettingsSaveBar";

type Platform = { max_active_slots: number; max_global_risk_pct: number; max_global_daily_loss_pct: number; max_instance_risk_per_trade_pct: number; global_risk_status?: string };
type Form = { max_active_slots: number; max_global_risk_pct: number; max_global_daily_loss_pct: number; max_instance_risk_per_trade_pct: number };
export default function RiskSettings() {
  const platform = useLive<Platform>("/instances", 5000); const controls = useLive<{ state: string }>("/controls/state", 3000);
  const [saved, setSaved] = useState<Form | null>(null); const [form, setForm] = useState<Form | null>(null); const [state, setState] = useState<SaveState>("saved"); const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (platform.data && !saved) { const next = { max_active_slots: platform.data.max_active_slots, max_global_risk_pct: platform.data.max_global_risk_pct, max_global_daily_loss_pct: platform.data.max_global_daily_loss_pct, max_instance_risk_per_trade_pct: platform.data.max_instance_risk_per_trade_pct }; setSaved(next); setForm(next); } }, [platform.data, saved]);
  const change = (patch: Partial<Form>) => { if (!form) return; setForm({ ...form, ...patch }); setState("dirty"); setError(null); };
  const save = async () => { if (!form) return; setState("saving"); try { await apiPostJson("/instances/platform", form); setSaved(form); setState("saved"); await platform.refetch(); } catch (err) { setError(err instanceof Error ? err.message : "Save failed"); setState("error"); } };
  const control = async (path: string) => { try { await apiPost(path); await controls.refetch(); } catch (err) { setError(err instanceof Error ? err.message : "Control failed"); setState("error"); } };
  return <SettingsSection title="Risk & Safety" description="Server-enforced platform ceilings and the existing stop/resume control." state={state}>
    {form && <div className="form-grid-2">
      <Field label="Maximum active Trading Instances"><input type="number" min="1" max="3" value={form.max_active_slots} onChange={(e) => change({ max_active_slots: Number(e.target.value) })} /></Field>
      <Field label="Instance risk-per-trade ceiling (%)"><input type="number" min="0.1" max="5" step="0.1" value={form.max_instance_risk_per_trade_pct * 100} onChange={(e) => change({ max_instance_risk_per_trade_pct: Number(e.target.value) / 100 })} /></Field>
      <Field label="Aggregate global open-risk limit (%)"><input type="number" min="0.1" max="100" step="0.1" value={form.max_global_risk_pct * 100} onChange={(e) => change({ max_global_risk_pct: Number(e.target.value) / 100 })} /></Field>
      <Field label="Global daily loss limit (%)"><input type="number" min="0.1" max="100" step="0.1" value={form.max_global_daily_loss_pct * 100} onChange={(e) => change({ max_global_daily_loss_pct: Number(e.target.value) / 100 })} /></Field>
    </div>}
    <div className="banner"><span>Current safety state: </span><Badge text={controls.data?.state ?? "unknown"} tone={controls.data?.state === "Active" ? "green" : "amber"} /> <span className="dim">Stops new automated trading; it does not close positions.</span></div>
    <div className="row-actions" style={{ justifyContent: "flex-start" }}><button className="btn btn-danger" type="button" onClick={() => void control("/controls/stop-all")}>Stop New Automated Trading</button><button className="btn btn-primary" type="button" onClick={() => void control("/controls/resume")}>Resume Automated Trading</button></div>
    <SettingsSaveBar state={state} error={error} onSave={() => void save()} onDiscard={() => { setForm(saved); setState("saved"); setError(null); }} />
  </SettingsSection>;
}
