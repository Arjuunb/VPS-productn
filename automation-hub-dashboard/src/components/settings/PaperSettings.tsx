import { useEffect, useState } from "react";
import { apiPostJson, useLive, type FillModelStatus, type PaperAccount } from "../../lib/api";
import { Field } from "../common/ui";
import Modal from "../common/Modal";
import { useApp } from "../../app-context";
import SettingsSection, { type SaveState } from "./SettingsSection";
import SettingsSaveBar from "./SettingsSaveBar";

type PlatformPaper = {
  paper_account_capital: number;
  total_allocated_capital: number;
  available_paper_capital: number;
  instances: Array<{
    id: string; symbol: string; strategy_label: string; timeframe: string;
    execution_mode: string; mode: string; starting_equity: number;
    execution?: { current_equity?: number; realized_pnl?: number; unrealized_pnl?: number };
    simulation_session?: { id: string; number: number; starting_balance: number; status: string };
  }>;
};

export default function PaperSettings() {
  const app = useApp();
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
  const [selectedInstanceId, setSelectedInstanceId] = useState("");
  const [confirmRestart, setConfirmRestart] = useState(false);
  const [restarting, setRestarting] = useState(false);

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
  const simulationInstances = (platform.data?.instances ?? []).filter((row) => row.execution_mode === "paper" && row.mode === "trading");
  const selectedInstance = simulationInstances.find((row) => row.id === selectedInstanceId) ?? simulationInstances[0];
  useEffect(() => {
    if (!selectedInstanceId && simulationInstances[0]) setSelectedInstanceId(simulationInstances[0].id);
  }, [selectedInstanceId, simulationInstances]);

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
  const restartSimulationAccount = async () => {
    if (!selectedInstance) return;
    setRestarting(true);
    setError(null);
    try {
      await apiPostJson(`/instances/${encodeURIComponent(selectedInstance.id)}/simulation-account/restart`, { confirm: true });
      setConfirmRestart(false);
      await platform.refetch();
      app.toast(`Simulation Session #${(selectedInstance.simulation_session?.number ?? 0) + 1} started with a fresh account`, "success");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Simulation account restart failed";
      setError(message);
      app.toast(message, "error");
    } finally {
      setRestarting(false);
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
    <section className="settings-protected-action" aria-labelledby="restart-simulation-account-title">
      <div>
        <h3 id="restart-simulation-account-title">Account</h3>
        <p>Start one Paper Trading account again from its configured capital. Previous trades, journal entries, strategy, risk settings and instance configuration remain available.</p>
      </div>
      <div className="form-grid-2">
        <Field label="Current Simulation Account">
          <select value={selectedInstance?.id ?? ""} disabled={!simulationInstances.length || restarting}
            onChange={(event) => setSelectedInstanceId(event.target.value)}>
            {!simulationInstances.length && <option value="">No Paper Trading instances</option>}
            {simulationInstances.map((row) => <option key={row.id} value={row.id}>
              {row.symbol} · {row.strategy_label} · {row.timeframe}
            </option>)}
          </select>
        </Field>
        <Field label="Current financial state">
          <input disabled value={selectedInstance
            ? `Session #${selectedInstance.simulation_session?.number ?? 1} · $${(selectedInstance.execution?.current_equity ?? selectedInstance.starting_equity).toLocaleString()}`
            : "—"} />
        </Field>
      </div>
      <button className="btn btn-danger" type="button" disabled={!selectedInstance || restarting}
        onClick={() => setConfirmRestart(true)}>
        {restarting ? "Restarting account…" : "Restart Simulation Account"}
      </button>
    </section>
    <p className="dim">Trading Instance capacity is stored in the platform database. Legacy account storage: {account.data?.storage ?? "—"}. The legacy reset does not change Trading Instance allocations.</p>
    <SettingsSaveBar state={state} error={error} onSave={() => void save()} onDiscard={() => { setModel(savedModel); setPlatformCapital(savedPlatformCapital); setState("saved"); setError(null); }} />
    <Modal open={confirmRestart} title="Restart Simulation Account?" onClose={() => { if (!restarting) setConfirmRestart(false); }}>
      <p>This will reset this paper account to <b>${(selectedInstance?.starting_equity ?? 0).toLocaleString()}</b>.</p>
      <p>The following current-session state will be cleared:</p>
      <ul className="settings-confirm-list">
        <li>open simulated positions</li>
        <li>pending simulated orders</li>
        <li>current realized and unrealized P&amp;L</li>
        <li>equity and drawdown state</li>
        <li>current simulation counters</li>
      </ul>
      <p className="dim">Your journal, previous trades, strategy and settings will be preserved in the completed session.</p>
      <div className="modal-actions">
        <button className="btn btn-soft" type="button" disabled={restarting} onClick={() => setConfirmRestart(false)}>Cancel</button>
        <button className="btn btn-danger" type="button" disabled={restarting} onClick={() => void restartSimulationAccount()}>
          {restarting ? "Restarting…" : "Restart Account"}
        </button>
      </div>
    </Modal>
  </SettingsSection>;
}
