import { useEffect, useRef, useState } from "react";
import { useLive } from "../../lib/api";
import type {
  NexusDecision,
  NexusInstance,
  NexusInstancesSnapshot,
  NexusPetState,
  NexusPetViewModel,
} from "./types";

const ACTIVE_STATES = new Set([
  "starting", "bootstrapping", "warming", "syncing", "ready", "running",
  "data_stale", "recovering", "paused",
]);
const ANALYSING_STATES = new Set(["starting", "bootstrapping", "warming", "syncing", "ready", "recovering"]);
const SIGNAL_THROTTLE_MS = 30_000;

type EventMarker = {
  instanceId: string;
  positionKey: string;
  trades: number;
  realizedPnl: number;
  decisionKey: string;
};

const titleCase = (value?: string | null): string => {
  if (!value) return "Not available";
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
};

function selectInstance(rows: NexusInstance[], selectedId: string | null): NexusInstance | null {
  const selected = rows.find((row) => row.id === selectedId);
  if (selected && selected.state !== "stopped" && selected.state !== "created") return selected;
  return rows.find((row) => row.state === "running")
    ?? rows.find((row) => ACTIVE_STATES.has(row.state))
    ?? rows.find((row) => row.state === "error")
    ?? selected
    ?? rows[0]
    ?? null;
}

function runtimeError(instance: NexusInstance | null): string | null {
  if (!instance) return null;
  return instance.last_error
    || instance.engine?.last_error
    || instance.engine?.stop_reason
    || null;
}

function baseState(snapshot: NexusInstancesSnapshot | null, requestError: string | null, instance: NexusInstance | null): NexusPetState {
  if (requestError || !snapshot) return "offline";
  const rows = snapshot.instances ?? [];
  const running = rows.filter((row) => row.state === "running");
  const errors = rows.filter((row) => row.state === "error");
  if (instance?.state === "error" || (!running.length && errors.length)) return "error";
  if (instance?.state === "paused") return "paused";
  if (instance && ANALYSING_STATES.has(instance.state)) return "analysing";
  const marketState = String(instance?.market_data?.market_data_status || snapshot.market_data_status || "").toLowerCase();
  if (instance?.state === "data_stale" || ["warning", "critical", "paused", "blocked"].includes(String(snapshot.global_risk_status || "").toLowerCase())
    || (snapshot.global_status === "critical" && running.length > 0)
    || ["stale", "disconnected", "error", "not_available"].includes(marketState)
    || (errors.length > 0 && running.length > 0)) return "warning";
  if (instance?.current_position) return "trade-open";
  if (instance?.state === "running") return "running";
  return "offline";
}

function labelFor(state: NexusPetState, instance: NexusInstance | null, requestError: string | null): string {
  if (requestError) return "Offline";
  if (state === "offline" && instance && ["stopped", "created"].includes(instance.state)) return "Stopped";
  const labels: Record<NexusPetState, string> = {
    running: "Running",
    analysing: "Analysing",
    "signal-found": "Signal found",
    "trade-open": "Trade open",
    "trade-win": "Trade closed in profit",
    "trade-loss": "Trade closed at a loss",
    paused: "Paused",
    warning: "Warning",
    error: "Error",
    offline: "Offline",
  };
  return labels[state];
}

function uptimeSeconds(instance: NexusInstance | null): number | null {
  if (!instance) return null;
  const startedAt = instance.engine?.started_at ?? instance.started_at;
  const parsed = startedAt ? Date.parse(startedAt) : Number.NaN;
  if (Number.isFinite(parsed)) return Math.max(0, (Date.now() - parsed) / 1000);
  const value = instance.engine?.uptime_s;
  return typeof value === "number" && Number.isFinite(value) ? Math.max(0, value) : null;
}

export function deriveNexusPetViewModel(
  snapshot: NexusInstancesSnapshot | null,
  requestError: string | null,
  selectedId: string | null,
  transientState: NexusPetState | null = null,
): NexusPetViewModel {
  const instance = selectInstance(snapshot?.instances ?? [], selectedId);
  const state = transientState ?? baseState(snapshot, requestError, instance);
  const market = instance?.market_data?.market_data_status ?? snapshot?.market_data_status ?? null;
  const detail = requestError || runtimeError(instance);
  return {
    state,
    statusLabel: labelFor(state, instance, requestError),
    statusDetail: detail,
    instance,
    runningInstances: (snapshot?.instances ?? []).filter((row) => row.state === "running").length,
    maxActiveSlots: snapshot ? snapshot.max_active_slots : null,
    marketDataLabel: titleCase(market),
    openPositions: snapshot?.total_open_positions ?? null,
    currentRiskAmount: snapshot?.current_global_risk_amount ?? null,
    maxRiskAmount: snapshot?.max_global_risk_amount ?? null,
    uptimeSeconds: uptimeSeconds(instance),
    lastHeartbeat: instance?.engine?.last_heartbeat ?? null,
  };
}

function decisionKey(decision?: NexusDecision | null): string {
  if (!decision) return "";
  return String(decision.id || decision.created_at || decision.time || decision.ts || "");
}

function isLegitimateSignal(decision?: NexusDecision | null): boolean {
  if (!decision) return false;
  const outcome = String(decision.verdict || decision.decision || "").toLowerCase();
  const signal = String(decision.signal || decision.side || "").toLowerCase();
  return ["accepted", "executed", "allowed"].includes(outcome) && !["", "hold", "none"].includes(signal);
}

function eventMarker(instance: NexusInstance): EventMarker {
  const position = instance.current_position;
  return {
    instanceId: instance.id,
    positionKey: position ? String(position.id || position.opened_at || "open") : "",
    trades: Number(instance.metrics?.trades ?? 0),
    realizedPnl: Number(instance.metrics?.realized_pnl ?? 0),
    decisionKey: decisionKey(instance.last_decision),
  };
}

/**
 * Converts the authoritative /instances payload into one small, read-only pet
 * model. The transient reactions are derived only from real server changes;
 * the mascot never starts, stops, or otherwise mutates a trading worker.
 */
export function useNexusPetController(selectedId: string | null): NexusPetViewModel {
  const live = useLive<NexusInstancesSnapshot>("/instances", 4000);
  const [transientState, setTransientState] = useState<NexusPetState | null>(null);
  const previous = useRef<EventMarker | null>(null);
  const lastSignalAt = useRef(0);

  const stableModel = deriveNexusPetViewModel(live.data, live.error, selectedId);
  const instance = stableModel.instance;

  useEffect(() => {
    if (!instance) {
      previous.current = null;
      setTransientState(null);
      return;
    }
    const next = eventMarker(instance);
    const prior = previous.current;
    previous.current = next;
    if (!prior || prior.instanceId !== next.instanceId) return;

    let reaction: NexusPetState | null = null;
    let duration = 900;
    if (!prior.positionKey && next.positionKey) {
      reaction = "trade-open";
    } else if (!next.positionKey && next.trades > prior.trades) {
      const pnlChange = next.realizedPnl - prior.realizedPnl;
      if (pnlChange !== 0) {
        reaction = pnlChange > 0 ? "trade-win" : "trade-loss";
        duration = 1400;
      }
    } else if (next.decisionKey && next.decisionKey !== prior.decisionKey
      && isLegitimateSignal(instance.last_decision)
      && Date.now() - lastSignalAt.current >= SIGNAL_THROTTLE_MS) {
      reaction = "signal-found";
      lastSignalAt.current = Date.now();
    }
    if (!reaction) return;
    setTransientState(reaction);
    const timer = window.setTimeout(() => setTransientState(null), duration);
    return () => window.clearTimeout(timer);
  }, [instance]);

  return deriveNexusPetViewModel(live.data, live.error, selectedId, transientState);
}
