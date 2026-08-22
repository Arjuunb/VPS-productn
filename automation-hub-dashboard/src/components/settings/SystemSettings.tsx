import { useLive } from "../../lib/api";
import { Badge } from "../common/ui";
import SettingsSection from "./SettingsSection";

export default function SystemSettings() {
  const health = useLive<any>("/health/bot", 5000); const storage = useLive<any>("/ops/storage", 15000); const instances = useLive<any>("/instances", 8000);
  return <SettingsSection title="System Health" description="Read-only runtime, worker, database and storage evidence." state="read-only"><div className="risk-list">
    <div className="risk-item"><span>Backend</span><Badge text={health.error ? "unavailable" : "online"} tone={health.error ? "red" : "green"} /></div>
    <div className="risk-item"><span>Legacy worker</span><b>{health.data?.engine?.running ? "Running" : "Stopped"}</b></div>
    <div className="risk-item"><span>Active Trading Instance workers</span><b>{instances.data?.active_slots ?? "—"}</b></div>
    <div className="risk-item"><span>Market-data service</span><b>{instances.data?.market_data_status ?? health.data?.data_source ?? "—"}</b></div>
    <div className="risk-item"><span>Storage durability</span><b>{storage.data?.durability ?? storage.data?.tier ?? (storage.data?.persistent ? "persistent" : "unverified")}</b></div>
    <div className="risk-item"><span>Recent runtime errors</span><b>{health.data?.errors?.length ?? 0}</b></div>
  </div></SettingsSection>;
}
