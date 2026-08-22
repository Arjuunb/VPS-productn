import SectionTabs from "../components/common/SectionTabs";
import Portfolio from "./Portfolio";
import Allocation from "./Allocation";

const tabs = [{ id: "overview", label: "Overview" }, { id: "positions", label: "Positions" }, { id: "allocation", label: "Allocation" }, { id: "exposure", label: "Exposure" }, { id: "pnl", label: "P&L" }];
export default function PortfolioHub({ tab }: { tab?: string }) {
  const active = tabs.some((item) => item.id === tab) ? tab : "overview";
  return <><SectionTabs tabs={tabs} active={active} />{active === "allocation" ? <Allocation /> : <Portfolio />}</>;
}
