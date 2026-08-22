import SectionTabs from "../components/common/SectionTabs";
import Trades from "./Journal";
import Decisions from "./Decisions";
import Memory from "./Memory";

const tabs = [{ id: "trades", label: "Trades" }, { id: "decisions", label: "Decisions" }, { id: "memory", label: "Memory" }, { id: "notes", label: "Notes" }];
export default function JournalHub({ tab, focusId }: { tab?: string; focusId?: string }) {
  const active = tabs.some((item) => item.id === tab) ? tab : "trades";
  return <><SectionTabs tabs={tabs} active={active} />{active === "decisions" ? <Decisions focusId={focusId} /> : active === "memory" || active === "notes" ? <Memory /> : <Trades focusId={focusId} />}</>;
}
