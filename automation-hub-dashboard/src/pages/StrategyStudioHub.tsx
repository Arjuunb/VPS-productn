import SectionTabs from "../components/common/SectionTabs";
import Strategies from "./Strategies";
import Studio from "./StrategyStudio";
import GridDCA from "./GridDCA";

const tabs = [{ id: "strategies", label: "Strategies" }, { id: "builder", label: "Builder" }, { id: "grid-dca", label: "Grid & DCA" }, { id: "versions", label: "Versions" }];
export default function StrategyStudioHub({ tab }: { tab?: string }) {
  const active = tabs.some((item) => item.id === tab) ? tab : "strategies";
  return <><SectionTabs tabs={tabs} active={active} />{active === "builder" ? <Studio /> : active === "grid-dca" ? <GridDCA /> : <Strategies />}</>;
}
