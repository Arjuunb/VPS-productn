import SectionTabs from "../components/common/SectionTabs";
import Risk from "./RiskCenter";
import Health from "./BotHealth";
import Logs from "./Logs";
import Safety from "./SafetyCenter";

const tabs = [{ id: "risk", label: "Risk" }, { id: "health", label: "Health" }, { id: "workers", label: "Workers" }, { id: "logs", label: "Logs" }, { id: "safety", label: "Safety" }];
export default function RiskHealthHub({ tab }: { tab?: string }) {
  const active = tabs.some((item) => item.id === tab) ? tab : "risk";
  return <><SectionTabs tabs={tabs} active={active} />{active === "health" || active === "workers" ? <Health /> : active === "logs" ? <Logs /> : active === "safety" ? <Safety /> : <Risk />}</>;
}
