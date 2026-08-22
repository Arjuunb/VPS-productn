import { useEffect, useState } from "react";
import { apiPostJson, useLive, type FillModelStatus, type PaperAccount } from "../../lib/api";
import { Field } from "../common/ui";
import SettingsSection, { type SaveState } from "./SettingsSection";
import SettingsSaveBar from "./SettingsSaveBar";

type PlatformPaper = {
  paper_account_capital: number;
  total_allocated_capital: number;
  available_paper_capital: number;
};

export default function PaperSettings() {
  const account = useLive<PaperAccount>("/paper/account", 8000);
  const fill = useLive<FillModelStatus>("/execution/fill-model", 8000);
  const platform = useLive<PlatformPaper>("/instances", 8000);
  const [model, setModel] = useState("realistic");
  const [savedModel, setSavedModel] = useState("realistic");
  const [platformCapital, setPlatformCapital] = useState("");
  const [savedPlatformCapital, setSavedPlatformCapital] = useState("");
  const [legacyResetCapital, setLegacyResetCapital] = useState("");
  const [state, setState] = useState<SaveState>("saved");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (fill.data && state === "saved") {
      setModel(fill.data.model);
      setSavedModel(fill.data.model);
    }
  }, [fill.data, state]);
  useEffect(() => {
    if (platform.data && !savedPlatformCapital) {
      const value = String(platform.data.paper_account_capital);
      setPlatformCapital(value);
      setSavedPlatformCapital(value);
    }
  }, [platform.data, savedPlatformCapital]);
  useEffect(() => {
    if (account.data && !legacyResetCapital) setLegacyResetCapital(String(account.data.initial_capital));
  }, [account.data, legacyResetCapital]);

  const markDirty = () => { setState("dirty"); setError(null); };
  const save = async () => {
    const amount = Number(platformCapital);
    if (!Number.isFinite(amount) || amount <= 0) {
      setError("Paper account capital must be greater than zero");
      setState("error");
      return;
    }
    setState("saving");
    try {
      if (model !== savedModel) await apiPostJson("/execution/fill-model", { model });
      if (platformCapital !== savedPlatformCapital) {
        await apiPostJson("/instances/platform", { paper_account_capital: amount });
      }
      setSavedModel(model);
      setSavedPlatformCapital(platformCapital);
      setState("saved");
      await Promise.all([fill.refetch(), platform.refetch()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
      setState("error");
    }
  };
  const resetCapital = async () => {
    const amount = Number(legacyResetCapital);
    if (!amount || !window.confirm("Reset the legacy paper account and its paper trade history to this starting capital? Trading Instances are not changed.")) return;
    try {
      await apiPostJson("/paper/initial-capital", { amount, confirm: true, reset_trades: true });
      await account.refetch();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Capital reset failed");
      setState("error");
    }
  };

  return <SettingsSection title="Paper Trading" description="Trading Instance paper capacity plus restart-persistent legacy paper execution settings." state={state}>
    <div className="form-grid-2">
      <Field label="Trading Instance paper account capital"><input type="number" min="1" value={platformCapital} onChange={(e) => { setPlatformCapital(e.target.value); markDirty(); }} /></Field>
      <Field label="Allocated / available capital"><input disabled value={`$${(platform.data?.total_allocated_capital ?? 0).toLocaleString()} / $${(platform.data?.available_paper_capital ?? 0).toLocaleString()}`} /></Field>
      <Field label="Legacy paper fill model"><select value={model} onChange={(e) => { setModel(e.target.value); markDirty(); }}><option value="realistic">Realistic</option><option value="perfect">Perfect (research comparison)</option></select></Field>
      <Field label="Current model details"><input disabled value={fill.data?.note ?? "Loading…"} /></Field>
      <Field label="Legacy paper starting capital (destructive reset)"><input type="number" min="1" value={legacyResetCapital} onChange={(e) => setLegacyResetCapital(e.target.value)} /></Field>
      <div style={{ display: "flex", alignItems: "flex-end" }}><button className="btn btn-warn" type="button" onClick={() => void resetCapital()}>Set &amp; reset legacy paper account</button></div>
    </div>
    <p className="dim">Trading Instance capacity is stored in the platform database. Legacy account storage: {account.data?.storage ?? "—"}. The legacy reset does not change Trading Instance allocations.</p>
    <SettingsSaveBar state={state} error={error} onSave={() => void save()} onDiscard={() => { setModel(savedModel); setPlatformCapital(savedPlatformCapital); setState("saved"); setError(null); }} />
  </SettingsSection>;
}
