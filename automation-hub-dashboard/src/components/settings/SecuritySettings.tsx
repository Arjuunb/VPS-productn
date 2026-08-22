import { useState } from "react";
import { API_BASE, useLive } from "../../lib/api";
import { Field } from "../common/ui";
import SettingsSection, { type SaveState } from "./SettingsSection";

export default function SecuritySettings() {
  const auth = useLive<{ authenticated: boolean; user: string | null }>("/auth/status", 30000); const settings = useLive<any>("/settings", 30000);
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" }); const [message, setMessage] = useState(""); const [state, setState] = useState<SaveState>("saved");
  const edit = (patch: Partial<typeof pw>) => { setPw({ ...pw, ...patch }); setState("dirty"); setMessage(""); };
  const change = async () => { if (pw.next.length < 8 || pw.next !== pw.confirm) { setMessage("New passwords must match and contain at least 8 characters."); setState("error"); return; } setState("saving"); try { const res = await fetch(`${API_BASE}/auth/change-password`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current: pw.current, new: pw.next }) }); const body = await res.json(); setMessage(res.ok ? "Password changed." : body.error ?? "Password change failed."); setState(res.ok ? "saved" : "error"); if (res.ok) setPw({ current: "", next: "", confirm: "" }); } catch { setMessage("Password change failed."); setState("error"); } };
  const logout = async () => { await fetch(`${API_BASE}/auth/logout`, { method: "POST", credentials: "include" }).catch(() => null); window.location.href = "/login"; };
  return <SettingsSection title="Security" description="Existing authentication actions and masked credential status only." state={state}>
    <div className="risk-list"><div className="risk-item"><span>Signed in as</span><b>{auth.data?.user ?? "—"}</b></div><div className="risk-item"><span>Webhook control credential</span><b>{settings.data?.readonly?.webhook_secret_set ? "Configured (masked)" : "Not configured"}</b></div><div className="risk-item"><span>Telegram credential</span><b>{settings.data?.readonly?.telegram_configured ? "Configured (masked)" : "Not configured"}</b></div></div>
    <div className="form-grid-2"><Field label="Current password"><input type="password" value={pw.current} onChange={(e) => edit({ current: e.target.value })} /></Field><Field label="New password"><input type="password" value={pw.next} onChange={(e) => edit({ next: e.target.value })} /></Field><Field label="Confirm password"><input type="password" value={pw.confirm} onChange={(e) => edit({ confirm: e.target.value })} /></Field></div>
    <div className="row-actions" style={{ justifyContent: "flex-start" }}><button disabled={state === "saving"} className="btn btn-primary" onClick={() => void change()}>Change Password</button><button className="btn btn-danger" onClick={() => void logout()}>Log Out</button></div>{message && <p className={state === "error" ? "neg" : "dim"}>{message}</p>}
    <p className="dim">Session inventory and revocation are deferred. Environment secrets and private keys are never returned.</p>
  </SettingsSection>;
}
