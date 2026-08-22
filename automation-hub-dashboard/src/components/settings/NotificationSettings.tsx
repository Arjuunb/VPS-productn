import { useState } from "react";
import { apiPost, apiPostJson, useLive, type NotifStatus } from "../../lib/api";
import SettingsSection, { type SaveState } from "./SettingsSection";

export default function NotificationSettings() {
  const status = useLive<NotifStatus>("/notifications/status", 6000); const [message, setMessage] = useState(""); const [state, setState] = useState<SaveState>("saved");
  const toggle = async (key: "notify_trades" | "notify_risk") => { if (!status.data) return; setState("saving"); setMessage(""); try { await apiPostJson("/notifications", { [key]: !status.data[key] }); await status.refetch(); setState("saved"); } catch (err) { setMessage(err instanceof Error ? err.message : "Save failed"); setState("error"); } };
  const test = async () => { setState("saving"); try { const result = await apiPost<{ sent: boolean; configured: boolean }>("/notifications/test"); setMessage(result.sent ? "Telegram test sent." : result.configured ? "Telegram delivery failed." : "Telegram is not configured."); setState(result.sent ? "saved" : "error"); } catch (err) { setMessage(err instanceof Error ? err.message : "Test failed"); setState("error"); } };
  return <SettingsSection title="Notifications" description="Only the existing Telegram delivery channel is editable in Stage 1." state={state}>
    <div className="risk-list"><div className="risk-item"><span>Telegram credential</span><b>{status.data?.telegram_configured ? "Configured (masked)" : "Not configured"}</b></div><div className="risk-item"><span>Trade alerts</span><button disabled={state === "saving"} className="btn btn-soft btn-sm" onClick={() => void toggle("notify_trades")}>{status.data?.notify_trades ? "Enabled" : "Disabled"}</button></div><div className="risk-item"><span>Risk alerts</span><button disabled={state === "saving"} className="btn btn-soft btn-sm" onClick={() => void toggle("notify_risk")}>{status.data?.notify_risk ? "Enabled" : "Disabled"}</button></div></div>
    <div className="row-actions" style={{ justifyContent: "flex-start" }}><button className="btn btn-primary" disabled={!status.data?.telegram_configured} onClick={() => void test()}>Send Telegram Test</button></div>{message && <p className="dim">{message}</p>}
    <p className="dim">Email, Discord and webhook notification settings are deferred; no non-working controls are shown.</p>
  </SettingsSection>;
}
