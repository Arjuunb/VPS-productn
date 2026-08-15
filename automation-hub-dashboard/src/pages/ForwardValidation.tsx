import Card from "../components/common/Card";
import { Badge, PageHeader, StatCard } from "../components/common/ui";
import { useLive } from "../lib/api";

type EvidenceStatus = "REJECTED" | "RESEARCH ONLY" | "FORWARD PAPER ELIGIBLE";

interface Candidate {
  candidate_id: string;
  strategy: string;
  version: string;
  symbol: string;
  timeframe: string;
  combined_hash: string;
  evidence_status: EvidenceStatus;
  reason: string;
  test_trades: number;
  test_win_rate_pct: number;
  test_profit_factor: number;
  test_expectancy_r: number;
  test_net_r: number;
  walk_forward_positive_folds: number;
  walk_forward_folds: number;
}

interface ForwardValidationStatus {
  stage_status: string;
  verdict: string;
  validation_started_at: string | null;
  active_experiments: unknown[];
  candidate_counts: Record<EvidenceStatus, number>;
  candidates: Candidate[];
  historical_evidence: {
    exchange: string;
    instrument: string;
    timeframe: string;
    start_utc: string;
    end_utc: string;
    candles_per_symbol: number;
    symbols: string[];
    bundle_sha256: string;
    forward_venue: string;
    exact_venue_parity: string;
  };
  forward_evidence: {
    experiments: number;
    counted_candles: number;
    decisions: number;
    trades: number;
    note: string;
  };
  next_action: string;
}

const signedR = (value: number) => `${value >= 0 ? "+" : ""}${value.toFixed(3)}R`;
const statusTone = (status: EvidenceStatus) => status === "REJECTED" ? "red" : status === "RESEARCH ONLY" ? "amber" : "green";

export default function ForwardValidationPage() {
  const live = useLive<ForwardValidationStatus>("/forward-validation", 15000);
  const data = live.data;

  return (
    <>
      <PageHeader
        title="Forward Validation"
        subtitle="immutable evidence gate · real forward data only · ordinary paper trades are excluded"
        actions={<Badge text={data?.verdict ?? "EVIDENCE LOADING"} tone={data ? "amber" : "default"} />}
      />

      {live.error ? (
        <div className="instance-risk-notice red" role="alert">
          <b>Forward-validation evidence is unavailable.</b><br />
          {live.error}<br />
          <span className="dim">No eligibility or forward-performance claim can be made while this endpoint is unavailable.</span>
        </div>
      ) : null}

      <div className="metric-row">
        <StatCard label="Eligible candidates" value={String(data?.candidate_counts["FORWARD PAPER ELIGIBLE"] ?? 0)} sub="required to start" tone="red" />
        <StatCard label="Research only" value={String(data?.candidate_counts["RESEARCH ONLY"] ?? 0)} sub="not eligible" tone="amber" />
        <StatCard label="Rejected" value={String(data?.candidate_counts.REJECTED ?? 0)} sub="untouched-test failure" tone="red" />
        <StatCard label="Active experiments" value={String(data?.active_experiments.length ?? 0)} sub="isolated evidence workers" />
        <StatCard label="Forward trades" value={String(data?.forward_evidence.trades ?? 0)} sub="counted evidence only" />
      </div>

      <Card
        title="Evidence gate"
        subtitle="Stage 2 stopped correctly because no frozen candidate met the entry standard"
        right={<Badge text={data?.stage_status ?? "LOADING"} tone="red" />}
      >
        <div className="instance-risk-notice red" role="status" style={{ margin: 0 }}>
          <b>No strategy may enter forward-paper validation.</b><br />
          Supertrend and Donchian failed the pooled untouched test. Decision Brain remains research-only because its pooled test was negative, its sample was small, and exact Kraken venue parity was not established.
        </div>
        <p className="dim" style={{ marginBottom: 0 }}>{data?.next_action ?? "Loading the frozen evidence decision…"}</p>
      </Card>

      <div className="grid-2-eq">
        <Card title="Historical baseline" subtitle="immutable real-exchange evidence used for the gate">
          {data ? (
            <div className="risk-list terminal">
              <div className="risk-item"><span className="dim">Dataset</span><b>{data.historical_evidence.exchange} · {data.historical_evidence.instrument}</b></div>
              <div className="risk-item"><span className="dim">Symbols / timeframe</span><b>{data.historical_evidence.symbols.join(", ")} · {data.historical_evidence.timeframe}</b></div>
              <div className="risk-item"><span className="dim">Coverage</span><b>{data.historical_evidence.start_utc.slice(0, 10)} → {data.historical_evidence.end_utc.slice(0, 10)}</b></div>
              <div className="risk-item"><span className="dim">Candles per symbol</span><b>{data.historical_evidence.candles_per_symbol.toLocaleString()}</b></div>
              <div className="risk-item"><span className="dim">Forward venue</span><b>{data.historical_evidence.forward_venue}</b></div>
              <div className="risk-item"><span className="dim">Exact venue parity</span><b className="neg">{data.historical_evidence.exact_venue_parity}</b></div>
              <div className="risk-item"><span className="dim">Evidence bundle</span><code title={data.historical_evidence.bundle_sha256}>{data.historical_evidence.bundle_sha256.slice(0, 16)}…</code></div>
            </div>
          ) : <p className="dim">Loading baseline…</p>}
        </Card>

        <Card title="Forward evidence" subtitle="starts only after an explicit immutable experiment boundary">
          <div className="risk-list terminal">
            <div className="risk-item"><span className="dim">Started at</span><b>{data?.validation_started_at ?? "Not started"}</b></div>
            <div className="risk-item"><span className="dim">Experiments</span><b>{data?.forward_evidence.experiments ?? 0}</b></div>
            <div className="risk-item"><span className="dim">Counted closed candles</span><b>{data?.forward_evidence.counted_candles ?? 0}</b></div>
            <div className="risk-item"><span className="dim">Recorded decisions</span><b>{data?.forward_evidence.decisions ?? 0}</b></div>
            <div className="risk-item"><span className="dim">Closed trades</span><b>{data?.forward_evidence.trades ?? 0}</b></div>
          </div>
          <p className="dim" style={{ marginBottom: 0 }}>{data?.forward_evidence.note ?? "No forward evidence loaded."}</p>
        </Card>
      </div>

      <Card title="Frozen candidate versions" subtitle="hashes, symbol, timeframe and historical out-of-sample result cannot be changed during an experiment">
        <div className="tablewrap">
          <table className="data-table">
            <thead><tr><th>Strategy</th><th>Version</th><th>Market</th><th>Status</th><th>Test trades</th><th>WR</th><th>PF</th><th>Expectancy</th><th>Net</th><th>Walk-forward</th><th>Fingerprint</th></tr></thead>
            <tbody>
              {(data?.candidates ?? []).map((row) => (
                <tr key={row.candidate_id}>
                  <td><b>{row.strategy}</b><div className="dim" title={row.reason}>{row.reason}</div></td>
                  <td>{row.version}</td>
                  <td>{row.symbol} · {row.timeframe}</td>
                  <td><Badge text={row.evidence_status} tone={statusTone(row.evidence_status)} /></td>
                  <td>{row.test_trades}</td>
                  <td>{row.test_win_rate_pct.toFixed(2)}%</td>
                  <td className={row.test_profit_factor >= 1 ? "pos" : "neg"}>{row.test_profit_factor.toFixed(3)}</td>
                  <td className={row.test_expectancy_r >= 0 ? "pos" : "neg"}>{signedR(row.test_expectancy_r)}</td>
                  <td className={row.test_net_r >= 0 ? "pos" : "neg"}>{signedR(row.test_net_r)}</td>
                  <td>{row.walk_forward_positive_folds}/{row.walk_forward_folds} positive</td>
                  <td><code title={row.combined_hash}>{row.combined_hash.slice(0, 10)}…</code></td>
                </tr>
              ))}
              {!data?.candidates.length ? <tr><td colSpan={11} className="dim ta-center">Loading frozen candidates…</td></tr> : null}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
