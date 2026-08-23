import { useState } from "react";
import { apiPostJson } from "../../lib/api";
import { useApp } from "../../app-context";
import Modal from "../common/Modal";

const PHRASE = "FACTORY RESET";

const DELETED = [
  "Trading Instances, workers and current runtime state",
  "paper positions, orders, balances, P&L and simulation sessions",
  "trade journal, decisions, memory and analytics history",
  "backtests, optimization, forward-validation and research results",
  "alerts, safe application logs, watchlists and dashboard preferences",
  "platform defaults, paper settings and regenerable caches",
];

const PRESERVED = [
  "your login/user account",
  ".env, API keys and secrets",
  "source code, database schema and migrations",
  "VPS/Docker deployment, domain and TLS certificates",
];

export default function FactoryResetSettings() {
  const app = useApp();
  const [stage, setStage] = useState<0 | 1 | 2>(0);
  const [typed, setTyped] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => {
    if (running) return;
    setStage(0); setTyped(""); setError(null);
  };
  const execute = async () => {
    if (typed !== PHRASE) return;
    setRunning(true); setError(null);
    try {
      await apiPostJson("/system/factory-reset", {
        confirmation: typed,
        final_confirmation: true,
      });
      setStage(0); setTyped("");
      app.toast("Factory Reset completed. Tradexa is in a clean stopped paper state.", "success");
      window.setTimeout(() => window.location.reload(), 1200);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Factory Reset failed";
      setError(message);
      app.toast(message, "error");
    } finally {
      setRunning(false);
    }
  };

  return <section className="settings-protected-action settings-danger-zone" aria-labelledby="factory-reset-title">
    <div>
      <div className="settings-danger-label">Danger Zone</div>
      <h3 id="factory-reset-title">Factory Reset</h3>
      <p>Return Tradexa to a fresh first-launch operational state. This permanently erases all trading and application history but preserves your account and deployment.</p>
    </div>
    <button className="btn btn-danger" type="button" onClick={() => setStage(1)}>Factory Reset…</button>

    <Modal open={stage > 0} title={stage === 1 ? "Factory Reset Tradexa?" : "Final confirmation"} onClose={close}>
      {stage === 1 ? <>
        <p><b>This action cannot be undone.</b> Tradexa will stop every worker and permanently delete:</p>
        <ul className="settings-confirm-list">{DELETED.map((item) => <li key={item}>{item}</li>)}</ul>
        <p><b>The following will be preserved:</b></p>
        <ul className="settings-confirm-list settings-preserved-list">{PRESERVED.map((item) => <li key={item}>{item}</li>)}</ul>
        <p className="dim">Live Trading will remain disabled. The clean application starts stopped in paper mode.</p>
        <div className="modal-actions">
          <button className="btn btn-soft" type="button" onClick={close}>Cancel</button>
          <button className="btn btn-danger" type="button" onClick={() => setStage(2)}>Continue</button>
        </div>
      </> : <>
        <p>Type <b>{PHRASE}</b> exactly, then press the final reset button.</p>
        <label className="field"><span>Confirmation phrase</span>
          <input autoFocus autoComplete="off" value={typed} disabled={running}
            onChange={(event) => setTyped(event.target.value)} placeholder={PHRASE} />
        </label>
        {error && <div className="banner red">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-soft" type="button" disabled={running} onClick={() => setStage(1)}>Back</button>
          <button className="btn btn-danger" type="button" disabled={running || typed !== PHRASE}
            onClick={() => void execute()}>{running ? "Factory Reset running…" : "Factory Reset — permanently delete"}</button>
        </div>
      </>}
    </Modal>
  </section>;
}
