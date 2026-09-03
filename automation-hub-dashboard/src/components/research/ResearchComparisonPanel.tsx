import { useLive } from "../../lib/api";

interface VariantRow {
  rank: number;
  strategy_id: string;
  sample_size: number;
  expectancy_r: number;
  profit_factor: number | null;
  max_drawdown_r: number;
  stability: number;
  validation_state: "INSUFFICIENT_SAMPLE" | "PROMISING" | "NO NET EDGE" | "HARMFUL";
}

interface Contribution {
  filter: string;
  before: { sample_size: number; expectancy_r: number; profit_factor: number | null };
  after: { sample_size: number; expectancy_r: number; profit_factor: number | null };
  blocked_winners: number;
  blocked_losers: number;
  verdict: "HELPFUL" | "NEUTRAL" | "HARMFUL" | "INSUFFICIENT_SAMPLE";
}

interface Comparison {
  execution_class: "SHADOW";
  minimum_validation_sample: number;
  variants: VariantRow[];
  filter_contributions: Contribution[];
  best_positive_cost_adjusted_rule: VariantRow | null;
}

interface ObservatoryStatus {
  state: string;
  error?: string | null;
  last_observation?: {
    candle_id?: string;
    decisions?: { variant: string; decision: { blocker: string } }[];
  };
}

const number = (value: number | null | undefined, digits = 2) =>
  value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);

export default function ResearchComparisonPanel({ engine }: { engine: "PA" | "SMC" }) {
  const comparison = useLive<Comparison>("/research/observatory/comparison", 10_000);
  const status = useLive<ObservatoryStatus>("/research/observatory/status", 5_000);
  const rows = (comparison.data?.variants ?? []).filter((row) =>
    engine === "PA" ? row.strategy_id.startsWith("PA_") : row.strategy_id.startsWith("SMC_"));
  const blockers = (status.data?.last_observation?.decisions ?? [])
    .filter((row) => engine === "PA" ? ["H", "I"].includes(row.variant) : !["H", "I"].includes(row.variant));
  const best = rows.find((row) => row.expectancy_r > 0) ?? null;

  return <section className="research-comparison" aria-label={`${engine} shadow research comparison`}>
    <div className="research-comparison-head">
      <div><span>OBSERVATIONAL · SHADOW ONLY</span><h2>{engine} rule evidence</h2></div>
      <b>{status.data?.state ?? "LOADING"}</b>
    </div>
    <div className="research-answer">
      <strong>{best ? best.strategy_id : "No positive cost-adjusted rule proven"}</strong>
      <span>{best ? `${number(best.expectancy_r)}R expectancy · n=${best.sample_size} · ${best.validation_state}` : `Minimum validation sample ${comparison.data?.minimum_validation_sample ?? 100}`}</span>
      <small>Ranking uses expectancy, profit factor, drawdown, stability and sample size—not win rate.</small>
    </div>
    {status.data?.error ? <div className="research-error">{status.data.error}</div> : null}
    <div className="research-table-wrap"><table><thead><tr><th>Rank</th><th>Variant</th><th>n</th><th>Net expectancy</th><th>PF</th><th>DD</th><th>Stability</th><th>Validation</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.strategy_id}><td>{row.rank}</td><td>{row.strategy_id}</td><td>{row.sample_size}</td><td>{number(row.expectancy_r)}R</td><td>{number(row.profit_factor)}</td><td>{number(row.max_drawdown_r)}R</td><td>{number(row.stability * 100, 0)}%</td><td><b className={`research-verdict ${row.validation_state.toLowerCase().replace(/_/g, "-")}`}>{row.validation_state}</b></td></tr>)}
      {!rows.length ? <tr><td colSpan={8}>No closed shadow outcomes yet. This is not evidence of an edge.</td></tr> : null}
    </tbody></table></div>
    <div className="research-blockers"><b>Why no trade?</b>{blockers.map((row) => <span key={row.variant}>{row.variant}: {row.decision.blocker}</span>)}{!blockers.length ? <span>Awaiting the first shared closed-candle observation.</span> : null}</div>
    {engine === "SMC" ? <div className="research-filters">{(comparison.data?.filter_contributions ?? []).map((row) => <article key={row.filter}><b>{row.filter}</b><span>{row.before.sample_size} → {row.after.sample_size} samples</span><span>{number(row.before.expectancy_r)}R → {number(row.after.expectancy_r)}R</span><span>Blocked {row.blocked_winners} winners / {row.blocked_losers} losers</span><strong>{row.verdict}</strong></article>)}</div> : null}
  </section>;
}
