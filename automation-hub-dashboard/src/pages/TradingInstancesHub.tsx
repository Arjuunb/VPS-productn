import SectionTabs from "../components/common/SectionTabs";
import TradingInstances from "./TradingInstances";
import Fleet from "./Bots";
import Health from "./BotHealth";
import Logs from "./Logs";

const tabs = [{ id: "instances", label: "Instances" }, { id: "fleet", label: "Fleet" }, { id: "workers", label: "Workers" }, { id: "activity", label: "Activity" }];

export default function TradingInstancesHub({ instanceId, tab }: { instanceId?: string; tab?: string }) {
  const active = instanceId ? "instances" : (tabs.some((item) => item.id === tab) ? tab : "instances");
  return <><SectionTabs tabs={tabs} active={active} />{active === "fleet" ? <Fleet /> : active === "workers" ? <Health /> : active === "activity" ? <Logs /> : <TradingInstances instanceId={instanceId} />}</>;
}
