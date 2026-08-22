import { useLive } from "../../lib/api";
import { Badge } from "../common/ui";
import SettingsSection from "./SettingsSection";

export default function LiveTradingSettings() {
  const readiness = useLive<any>("/execution/readiness", 15000); const system = useLive<any>("/system/status", 8000);
  return <SettingsSection title="Live Trading" description="Readiness evidence only. Stage 1 preserves the live-trading safety lock." state="read-only"><div className="risk-list"><div className="risk-item"><span>Status</span><Badge text="LOCKED" tone="red" /></div><div className="risk-item"><span>Backend readiness</span><b>{readiness.data?.ready ? "Ready checks passed" : "Not ready"}</b></div><div className="risk-item"><span>Exchange connection</span><b>{system.data?.broker_connected ? "Connected" : "Not connected"}</b></div><div className="risk-item"><span>Configuration</span><b>{readiness.data?.configured ? "Configured (masked)" : "Incomplete"}</b></div></div><p className="dim">There is no enable-live control or hidden bypass in Settings.</p></SettingsSection>;
}
