import { uptime } from "../../lib/api";
import { useApp } from "../../app-context";
import type { NexusPetViewModel } from "./types";

const money = (value: number | null): string => value === null
  ? "—"
  : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);

type Props = {
  model: NexusPetViewModel;
  onClose: () => void;
};

export default function NexusPetPopover({ model, onClose }: Props) {
  const app = useApp();
  const openPage = (page: string) => { onClose(); app.go(page); };
  const viewInstance = () => {
    if (!model.instance) return;
    onClose();
    app.viewInstance(model.instance.id);
  };

  return (
    <section className="nexus-pet-popover" role="dialog" aria-label="Nexus Engine status">
      <div className="nexus-pet-popover-head">
        <b>Nexus Engine</b>
        <span className={`nexus-pet-status nexus-pet-status-${model.state}`}>{model.statusLabel}</span>
      </div>

      {model.instance ? (
        <div className="nexus-pet-instance">
          <strong>{model.instance.symbol}</strong>
          <span>{model.instance.strategy_label} · {model.instance.timeframe}</span>
        </div>
      ) : <div className="nexus-pet-instance"><span>No active Trading Instance</span></div>}

      <dl className="nexus-pet-facts">
        <div><dt>Instances</dt><dd>{model.maxActiveSlots === null ? "—" : `${model.runningInstances} / ${model.maxActiveSlots}`}</dd></div>
        <div><dt>Market Data</dt><dd>{model.marketDataLabel}</dd></div>
        <div><dt>Open Trades</dt><dd>{model.openPositions ?? "—"}</dd></div>
        <div><dt>Risk</dt><dd>{money(model.currentRiskAmount)} / {money(model.maxRiskAmount)}</dd></div>
        <div><dt>Uptime</dt><dd>{model.uptimeSeconds === null ? "—" : uptime(model.uptimeSeconds)}</dd></div>
      </dl>

      {model.statusDetail && <p className="nexus-pet-error" role="status">{model.statusDetail}</p>}
      {model.lastHeartbeat && <p className="nexus-pet-heartbeat">Heartbeat {new Date(model.lastHeartbeat).toLocaleTimeString()}</p>}

      <div className="nexus-pet-links">
        {model.instance && <button type="button" onClick={viewInstance}>View Instance</button>}
        <button type="button" onClick={() => openPage("Bot Health")}>Bot Health</button>
        <button type="button" onClick={() => openPage("Logs")}>Logs</button>
      </div>
    </section>
  );
}
