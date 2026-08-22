import { useEffect, useState } from "react";
import { API_BASE } from "../../lib/api";
import { Field } from "../common/ui";
import SettingsSection, { type SaveState } from "./SettingsSection";
import SettingsSaveBar from "./SettingsSaveBar";

type General = { density: "comfortable" | "compact"; sidebar_default: "expanded" | "collapsed" };
const fallback: General = { density: "comfortable", sidebar_default: "expanded" };

export default function GeneralSettings() {
  const [saved, setSaved] = useState<General>(fallback);
  const [form, setForm] = useState<General>(fallback);
  const [state, setState] = useState<SaveState>("saved");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void fetch(`${API_BASE}/user/settings?ns=settings-center`, { credentials: "include" }).then(async (res) => {
    if (!res.ok) throw new Error("Could not load user settings");
    const body = await res.json(); const next = { ...fallback, ...(body.data?.general ?? {}) };
    setSaved(next); setForm(next); document.documentElement.dataset.density = next.density;
  }).catch((err) => { setError(String(err)); setState("error"); }); }, []);
  const change = (patch: Partial<General>) => { const next = { ...form, ...patch }; setForm(next); setState("dirty"); setError(null); };
  const save = async () => { setState("saving"); try {
    const current = await fetch(`${API_BASE}/user/settings?ns=settings-center`, { credentials: "include" }).then((res) => res.ok ? res.json() : ({ data: {} }));
    const res = await fetch(`${API_BASE}/user/settings`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ns: "settings-center", data: { ...(current.data ?? {}), general: form } }) });
    if (!res.ok) throw new Error("User preference save failed");
    setSaved(form); setState("saved"); document.documentElement.dataset.density = form.density;
    localStorage.setItem("hub.settings.general", JSON.stringify(form));
  } catch (err) { setError(err instanceof Error ? err.message : "Save failed"); setState("error"); } };
  return <SettingsSection title="General" description="Per-user interface preferences stored by your authenticated account." state={state}>
    <div className="form-grid-2">
      <Field label="Theme"><select disabled value="dark"><option value="dark">Dark (current supported theme)</option></select></Field>
      <Field label="Density"><select value={form.density} onChange={(e) => change({ density: e.target.value as General["density"] })}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></Field>
      <Field label="Sidebar default"><select value={form.sidebar_default} onChange={(e) => change({ sidebar_default: e.target.value as General["sidebar_default"] })}><option value="expanded">Expanded</option><option value="collapsed">Collapsed</option></select></Field>
      <Field label="Date and time"><input disabled value={Intl.DateTimeFormat().resolvedOptions().timeZone + " · browser locale"} /></Field>
    </div>
    <SettingsSaveBar state={state} error={error} onSave={() => void save()} onDiscard={() => { setForm(saved); setState("saved"); setError(null); }} />
  </SettingsSection>;
}
