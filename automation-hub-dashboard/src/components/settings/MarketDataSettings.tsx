import { useState } from "react";
import { apiPostJson, useLive } from "../../lib/api";
import { useApp } from "../../app-context";
import { Badge, Field } from "../common/ui";
import SettingsSection from "./SettingsSection";

type TestResult = { ok: boolean; provider: string; market: string; latency_ms: number; last_price_timestamp: string; last_price: number };
export default function MarketDataSettings() {
  const app = useApp();
  const [symbol, setSymbol] = useState("BTCUSDT"); const [timeframe, setTimeframe] = useState("5m");
  const [testing, setTesting] = useState(false); const [result, setResult] = useState<TestResult | null>(null); const [error, setError] = useState<string | null>(null);
  const providers = useLive<{ providers: Array<{ name: string; current_availability: string; last_successful_request?: string }> }>("/market-data/providers", 15000);
  const status = useLive<any>(`/market-data/status?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}`, 10000);
  const options = useLive<{ execution_defaults?: { exchange?: string; instrument_type?: string } }>("/instances/options", 30000);
  const instances = useLive<{ market_data_status?: string }>("/instances", 10000);
  const test = async () => { setTesting(true); setError(null); try { setResult(await apiPostJson<TestResult>("/market-data/test-connection", { symbol, timeframe })); } catch (err) { setResult(null); setError(err instanceof Error ? err.message : "Connection failed"); } finally { setTesting(false); } };
  return <SettingsSection title="Market Data" description="Read-only provider and cache status. Trading Instances currently use provider-backed spot candles." state="read-only">
    <div className="form-grid-2"><Field label="Test symbol"><input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} /></Field><Field label="Timeframe"><select value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>{["1m", "3m", "5m", "15m", "1h", "4h", "1d"].map((value) => <option key={value}>{value}</option>)}</select></Field></div>
    <div className="risk-list">
      <div className="risk-item"><span>Trading Instance provider</span><b>{options.data?.execution_defaults?.exchange ?? "—"}</b></div>
      <div className="risk-item"><span>Trading Instance market</span><b>{options.data?.execution_defaults?.instrument_type ?? "spot"}</b></div>
      <div className="risk-item"><span>Trading Instance feed status</span><b>{instances.data?.market_data_status ?? "—"}</b></div>
      <div className="risk-item"><span>Research cache provider / market</span><b>{status.data?.metadata?.provider ?? "—"} / {status.data?.asset_class ?? "—"}</b></div>
      <div className="risk-item"><span>Research cache status</span><Badge text={status.data?.available ? "available" : "download required"} tone={status.data?.available ? "green" : "amber"} /></div>
      <div className="risk-item"><span>Last cached update</span><b>{status.data?.last_candle ?? status.data?.metadata?.last_updated ?? "—"}</b></div>
      <div className="risk-item"><span>Data quality / integrity</span><b>{status.data?.quality_score ?? 0}/100 · {status.data?.integrity?.status ?? "unavailable"}</b></div>
      <div className="risk-item"><span>Registered providers</span><b>{(providers.data?.providers ?? []).map((row) => row.name).join(", ") || "—"}</b></div>
    </div>
    <div className="row-actions" style={{ justifyContent: "flex-start", marginTop: 12 }}><button className="btn btn-primary" type="button" disabled={testing} onClick={() => void test()}>{testing ? "Testing…" : "Test Connection"}</button><button className="btn btn-soft" type="button" onClick={() => app.go("Market Data")}>Open download &amp; repair tools</button></div>
    {result && <div className="banner"><b>Connected:</b> {result.provider} · {result.market} · {result.latency_ms}ms · latest {result.last_price_timestamp} · {result.last_price}</div>}
    {error && <div className="banner neg"><b>Connection failed:</b> {error}</div>}
  </SettingsSection>;
}
