#!/usr/bin/env python3
"""Independent 2024-H1 replication of four frozen 2025 discovery premises.

This is a falsification harness.  It has no strategy classes, no production
imports, and no route to 2025 Jul--Dec datasets.  Definitions are written and
fingerprinted before the independent archives may be fetched or opened.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
import urllib.request
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "automation-hub"
for entry in (str(HUB), str(ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from bot.data.resample import resample  # noqa: E402
from bot.types import Bar  # noqa: E402
from scripts.research_universe_expansion import (  # noqa: E402
    FUTURES_EXECUTION_HURDLE_BPS, build_outcome_cache, canonical, distribution,
    percentile, sha256_file,
)

UTC = timezone.utc
STUDY_ID = "independent-edge-replication-v1-2024h1"
CREATED_AT = "2026-08-15T00:00:00+00:00"
REPLICATION_START = datetime(2024, 1, 1, tzinfo=UTC)
REPLICATION_END = datetime(2024, 7, 1, tzinfo=UTC)
REPLICATION_MONTHS = (1, 2, 3, 4, 5, 6)
DISCOVERY_START = datetime(2025, 1, 1, tzinfo=UTC)
DISCOVERY_END = datetime(2025, 7, 1, tzinfo=UTC)
SEALED_START = datetime(2025, 7, 1, tzinfo=UTC)
FUTURES_ROOT = "https://data.binance.vision/data/futures/um"
SPOT_ROOT = "https://data.binance.vision/data/spot/monthly/klines"

# The definitions are deliberately complete and fixed before any replication
# archive is permitted.  A quantile rule, not a discovery-number threshold, was
# what the original methodology used per asset.
HYPOTHESES = {
    "REP-H1-ETH-FUNDING-LOW": {"version": "1.0.0", "venue": "Binance USDⓈ-M Futures", "market_type": "perpetual", "symbol": "ETHUSDT", "source_field": "last_funding_rate", "condition": "current known funding <= per-symbol empirical 10th percentile", "quantile": 0.10, "alignment": "latest funding event at or before 15m event T; no future event", "timeframe": "15m", "horizon_bars": 24, "direction": "long-context association (not an entry)", "independence": "event indices separated by >24 bars", "execution_hurdle_bps": 16.0, "random_control": "deterministic matched-frequency timestamps with shuffled condition directions", "minimum_events": 100, "minimum_independent_events": 30, "minimum_positive_months": 4},
    "REP-H2-SOL-FUNDING-LOW": {"version": "1.0.0", "venue": "Binance USDⓈ-M Futures", "market_type": "perpetual", "symbol": "SOLUSDT", "source_field": "last_funding_rate", "condition": "current known funding <= per-symbol empirical 10th percentile", "quantile": 0.10, "alignment": "latest funding event at or before 15m event T; no future event", "timeframe": "15m", "horizon_bars": 24, "direction": "long-context association (not an entry)", "independence": "event indices separated by >24 bars", "execution_hurdle_bps": 16.0, "random_control": "deterministic matched-frequency timestamps with shuffled condition directions", "minimum_events": 100, "minimum_independent_events": 30, "minimum_positive_months": 4},
    "REP-H3-ETH-REL-VOLUME-HIGH": {"version": "1.0.0", "venue": "Binance USDⓈ-M Futures", "market_type": "perpetual", "symbol": "ETHUSDT", "source_field": "quote_volume", "condition": "current quote volume >= 1.5 × causal trailing mean of prior 20 15m bars", "alignment": "volume for completed event candle T only", "timeframe": "15m", "horizon_bars": 24, "direction": "sign of prior four-bar price change; zero is long", "independence": "event indices separated by >24 bars", "execution_hurdle_bps": 16.0, "random_control": "deterministic matched-frequency timestamps with shuffled condition directions", "minimum_events": 100, "minimum_independent_events": 30, "minimum_positive_months": 4},
    "REP-H4-ETH-1D-PERSISTENCE": {"version": "1.0.0", "venue": "Binance Spot", "market_type": "spot", "symbol": "ETHUSDT", "source_field": "completed 5m OHLCV resampled to completed 1D", "condition": "close(T) compared with close(T-5 completed daily bars)", "alignment": "completed daily candle T only", "timeframe": "1d", "horizon_bars": 10, "direction": "long if close(T)>close(T-5), otherwise short", "independence": "event indices separated by >10 bars", "execution_hurdle_bps": 14.0, "random_control": "deterministic matched-frequency timestamps with shuffled condition directions", "minimum_events": 100, "minimum_independent_events": 30, "minimum_positive_months": 4},
}


class ReplicationBoundaryError(RuntimeError):
    pass


def sha(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def assert_replication_only(start: datetime = REPLICATION_START, end: datetime = REPLICATION_END, months: tuple[int, ...] = REPLICATION_MONTHS) -> None:
    if start != REPLICATION_START or end != REPLICATION_END or tuple(months) != REPLICATION_MONTHS:
        raise ReplicationBoundaryError("Replication input is fixed to 2024-01-01 through 2024-06-30 only.")
    if end > DISCOVERY_START or start >= DISCOVERY_START or end > SEALED_START:
        raise ReplicationBoundaryError("Discovery, 2025 validation, and untouched-test windows are sealed from replication.")


def fingerprints() -> dict:
    source_hash = sha256_file(Path(__file__))
    return {hid: {"hypothesis_id": hid, "semantic_version": definition["version"], "definition_hash": sha(definition), "configuration_hash": sha({"definition": definition, "replication_window": [REPLICATION_START.isoformat(), REPLICATION_END.isoformat()]}), "source_code_hash": source_hash, "created_at": CREATED_AT} for hid, definition in HYPOTHESES.items()}


def freeze(path: Path) -> dict:
    """Create once; reruns verify rather than overwrite immutable definitions."""
    payload = {"study_id": STUDY_ID, "created_at": CREATED_AT, "dataset_declared_before_open": {"start": REPLICATION_START.isoformat(), "end": REPLICATION_END.isoformat(), "venue_futures": "Binance USDⓈ-M Futures", "venue_spot": "Binance Spot"}, "hypotheses": HYPOTHESES, "fingerprints": fingerprints(), "hypotheses_tested": 4, "sealed": {"discovery": [DISCOVERY_START.isoformat(), DISCOVERY_END.isoformat()], "existing_validation_sealed_from": SEALED_START.isoformat(), "existing_untouched_test_sealed_from": "2025-10-01T00:00:00+00:00"}}
    if path.exists():
        existing = json.loads(path.read_text())
        # Definitions, window and gate are immutable.  The stored source hash
        # remains the pre-open fingerprint if a later non-methodological bugfix
        # is needed; the run also records its runtime source hash below.
        immutable = ("study_id", "created_at", "dataset_declared_before_open", "hypotheses", "hypotheses_tested", "sealed")
        if any(existing.get(key) != payload.get(key) for key in immutable):
            raise RuntimeError("Frozen definitions differ from this code; start a new replication study rather than rewriting them.")
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return payload


def _days() -> list[datetime]:
    assert_replication_only()
    days, current = [], REPLICATION_START
    while current < REPLICATION_END:
        days.append(current); current += timedelta(days=1)
    return days


def archive_specs() -> list[tuple[str, str]]:
    assert_replication_only()
    specs: list[tuple[str, str]] = []
    for symbol in ("ETHUSDT", "SOLUSDT"):
        for month in REPLICATION_MONTHS:
            suffix = f"2024-{month:02d}"
            for dataset in ("klines", "markPriceKlines", "indexPriceKlines"):
                relative = f"futures/{dataset}/{symbol}/15m/{symbol}-15m-{suffix}.zip"
                specs.append((relative, f"{FUTURES_ROOT}/monthly/{dataset}/{symbol}/15m/{symbol}-15m-{suffix}.zip"))
            relative = f"futures/fundingRate/{symbol}/{symbol}-fundingRate-{suffix}.zip"
            specs.append((relative, f"{FUTURES_ROOT}/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{suffix}.zip"))
        for day in _days():
            date = day.strftime("%Y-%m-%d")
            relative = f"futures/metrics/{symbol}/{symbol}-metrics-{date}.zip"
            specs.append((relative, f"{FUTURES_ROOT}/daily/metrics/{symbol}/{symbol}-metrics-{date}.zip"))
    for month in REPLICATION_MONTHS:
        suffix = f"2024-{month:02d}"
        relative = f"spot/ETHUSDT-5m-{suffix}.zip"
        specs.append((relative, f"{SPOT_ROOT}/ETHUSDT/5m/ETHUSDT-5m-{suffix}.zip"))
    return specs


def fetch(data_dir: Path, workers: int = 6) -> dict:
    specs = archive_specs(); data_dir.mkdir(parents=True, exist_ok=True)
    pending = [(data_dir / relative, url) for relative, url in specs if not (data_dir / relative).exists()]
    def one(item: tuple[Path, str]) -> None:
        path, url = item; path.parent.mkdir(parents=True, exist_ok=True); temp = path.with_suffix(path.suffix+".partial")
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "TradeLogX-replication/1.0"}), timeout=60) as response, temp.open("wb") as output:
            while chunk := response.read(1024 * 1024): output.write(chunk)
        with zipfile.ZipFile(temp) as archive:
            if len(archive.namelist()) != 1: raise RuntimeError(f"unexpected archive shape: {url}")
        temp.replace(path)
    complete = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        tasks = [pool.submit(one, item) for item in pending]
        for task in as_completed(tasks):
            task.result(); complete += 1
            if complete % 25 == 0 or complete == len(pending): print(f"downloaded {complete}/{len(pending)} replication archives", flush=True)
    return {"requested": len(specs), "downloaded": complete, "already_present": len(specs)-len(pending)}


def rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != 1: raise RuntimeError(f"{path}: expected single member")
        return list(csv.reader(line.decode("utf-8") for line in archive.open(names[0])))


def timestamp(raw: str) -> datetime:
    value = int(raw); return datetime.fromtimestamp(value/(1_000_000 if value > 100_000_000_000_000 else 1_000), tz=UTC)


def f(value: str) -> float: return float(value) if value else 0.0


def load_futures(data_dir: Path, symbol: str) -> tuple[list[Bar], dict[datetime, dict], dict[datetime, float], dict]:
    assert_replication_only(); bars: list[Bar] = []; metrics: dict[datetime, dict] = {}; funding: dict[datetime, float] = {}; mark: dict[datetime, float] = {}; index: dict[datetime, float] = {}; hashes = {}
    for month in REPLICATION_MONTHS:
        suffix = f"2024-{month:02d}"
        for dataset, target in (("klines", None), ("markPriceKlines", mark), ("indexPriceKlines", index)):
            path = data_dir / "futures" / dataset / symbol / "15m" / f"{symbol}-15m-{suffix}.zip"
            if not path.exists(): raise RuntimeError(f"missing declared replication archive: {path}")
            hashes[str(path.relative_to(data_dir))] = sha256_file(path); raw = rows(path); header, body = raw[0], raw[1:]
            if dataset == "klines":
                if header[:6] != ["open_time", "open", "high", "low", "close", "volume"]: raise RuntimeError("unexpected futures kline schema")
                for row in body:
                    stamp = timestamp(row[0]); bars.append(Bar(stamp, f(row[1]), f(row[2]), f(row[3]), f(row[4]), f(row[5]))); metrics.setdefault(stamp, {}).update({"quote_volume": f(row[7]), "taker_buy_quote": f(row[10])})
            else:
                if header[:5] != ["open_time", "open", "high", "low", "close"]: raise RuntimeError(f"unexpected {dataset} schema")
                target.update({timestamp(row[0]): f(row[4]) for row in body})
        path = data_dir / "futures" / "fundingRate" / symbol / f"{symbol}-fundingRate-{suffix}.zip"
        hashes[str(path.relative_to(data_dir))] = sha256_file(path); raw = rows(path)
        if raw[0] != ["calc_time", "funding_interval_hours", "last_funding_rate"]: raise RuntimeError("unexpected funding schema")
        funding.update({timestamp(row[0]): f(row[2]) for row in raw[1:]})
    for day in _days():
        path = data_dir / "futures" / "metrics" / symbol / f"{symbol}-metrics-{day:%Y-%m-%d}.zip"
        hashes[str(path.relative_to(data_dir))] = sha256_file(path); raw = rows(path)
        expected = ["create_time", "symbol", "sum_open_interest", "sum_open_interest_value", "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio", "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]
        if raw[0] != expected: raise RuntimeError("unexpected metrics schema")
        for row in raw[1:]:
            stamp = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC); metrics.setdefault(stamp, {}).update({"oi_value": f(row[3]), "taker_ratio": f(row[7])})
    bars.sort(key=lambda bar: bar.timestamp)
    if not bars or bars[0].timestamp != REPLICATION_START or bars[-1].timestamp >= REPLICATION_END: raise RuntimeError(f"{symbol}: replication coverage invalid")
    gaps = sum(max(0, round((b.timestamp-a.timestamp).total_seconds()/900)-1) for a,b in zip(bars,bars[1:]))
    if gaps or len({bar.timestamp for bar in bars}) != len(bars): raise RuntimeError(f"{symbol}: futures gap/duplicate")
    for bar in bars:
        if bar.timestamp in mark and bar.timestamp in index and index[bar.timestamp]: metrics[bar.timestamp]["basis_bps"] = (mark[bar.timestamp]/index[bar.timestamp]-1)*10_000
    metric_times = sorted(stamp for stamp,row in metrics.items() if {"oi_value","taker_ratio"}.issubset(row)); metric_gaps=sum(max(0,round((b-a).total_seconds()/300)-1) for a,b in zip(metric_times,metric_times[1:]))
    return bars, metrics, funding, {"source": "official Binance Vision archive", "venue": "Binance USDⓈ-M Futures", "market_type": "perpetual", "symbol": symbol, "start": bars[0].timestamp.isoformat(), "end": bars[-1].timestamp.isoformat(), "price_15m_observations": len(bars), "price_15m_gaps": gaps, "metrics_5m_observations": len(metric_times), "metrics_5m_gaps": metric_gaps, "funding_observations": len(funding), "archives_sha256": hashes}


def load_spot_eth(data_dir: Path) -> tuple[list[Bar], dict]:
    assert_replication_only(); bars=[]; hashes={}
    for month in REPLICATION_MONTHS:
        path=data_dir/"spot"/f"ETHUSDT-5m-2024-{month:02d}.zip"
        if not path.exists(): raise RuntimeError(f"missing declared replication archive: {path}")
        hashes[str(path.relative_to(data_dir))]=sha256_file(path); raw=rows(path)
        records=raw[1:] if raw and raw[0] and raw[0][0]=="open_time" else raw
        for row in records: bars.append(Bar(timestamp(row[0]),f(row[1]),f(row[2]),f(row[3]),f(row[4]),f(row[5])))
    bars.sort(key=lambda bar:bar.timestamp)
    if not bars or bars[0].timestamp != REPLICATION_START or bars[-1].timestamp >= REPLICATION_END: raise RuntimeError("ETH spot replication coverage invalid")
    gaps=sum(max(0,round((b.timestamp-a.timestamp).total_seconds()/300)-1) for a,b in zip(bars,bars[1:]))
    if gaps or len({bar.timestamp for bar in bars}) != len(bars): raise RuntimeError("ETH spot gap/duplicate")
    return bars,{"source":"official Binance Vision archive","venue":"Binance Spot","market_type":"spot","symbol":"ETHUSDT","start":bars[0].timestamp.isoformat(),"end":bars[-1].timestamp.isoformat(),"raw_resolution":"5m","observations":len(bars),"gaps":gaps,"archives_sha256":hashes}


def last_known(funding: dict[datetime,float], stamp: datetime) -> float | None:
    earlier=[event for event in funding if event <= stamp]
    return funding[max(earlier)] if earlier else None


def summarize(bars: list[Bar], points: list[tuple[int,int]], horizon: int, cache: dict) -> tuple[dict,dict]:
    values=[cache[horizon][direction][index] for index,direction in points if cache[horizon][direction][index] is not None]
    monthly=defaultdict(list)
    for index,direction in points:
        outcome=cache[horizon][direction][index]
        if outcome is not None: monthly[bars[index].timestamp.strftime("%Y-%m")].append(outcome)
    return distribution(values),{month:distribution(items) for month,items in sorted(monthly.items())}


def independent_events(points: list[tuple[int,int]], horizon: int) -> int:
    count=0; previous=-10_000
    for index,_ in points:
        if index-previous > horizon: count+=1; previous=index
    return count


def regimes(bars: list[Bar], points: list[tuple[int,int]]) -> dict:
    grouped=defaultdict(int)
    for i,_ in points:
        closes=[bar.close for bar in bars[i-20:i+1]]; path=sum(abs(b-a) for a,b in zip(closes,closes[1:])); er=abs(closes[-1]-closes[0])/path if path else 0
        ranges=[bar.high-bar.low for bar in bars[max(0,i-10):i+1]]; prior=[bar.high-bar.low for bar in bars[max(0,i-50):i+1]]; ratio=statistics.fmean(ranges)/statistics.fmean(prior) if prior and statistics.fmean(prior) else 1
        label=("trend" if er>=.45 else "range")+"_"+("high_vol" if ratio>=1.25 else "low_vol" if ratio<=.80 else "normal_vol")
        grouped[label]+=1
    total=sum(grouped.values())
    return {key:{"events":value,"share":round(value/total,4) if total else 0} for key,value in sorted(grouped.items())}


def random_control(bars: list[Bar], points: list[tuple[int,int]], universe: list[int], horizon: int, hypothesis_id: str, cache: dict) -> dict:
    rng=random.Random(hashlib.sha256(canonical({"protocol":"expansion-v1-matched-random-shuffled-direction","hypothesis":hypothesis_id})).hexdigest())
    indices=rng.sample(universe,min(len(points),len(universe))) if points else []; directions=[direction for _,direction in points]; rng.shuffle(directions)
    return summarize(bars,list(zip(indices,directions)),horizon,cache)[0]


def hypothesis_metrics(hid: str, bars: list[Bar], points: list[tuple[int,int]], universe: list[int], horizon: int, hurdle: float) -> dict:
    cache=build_outcome_cache(bars,(horizon,)); observed,monthly=summarize(bars,points,horizon,cache); control=random_control(bars,points,universe,horizon,hid,cache); gross=observed["mean_bps"] or 0; std=observed["std_bps"] or 0; cstd=control["std_bps"] or 0
    stderr=math.sqrt(std*std/max(observed["n"],1)+cstd*cstd/max(control["n"],1)); difference=gross-(control["mean_bps"] or 0)
    total_sum=sum((row["mean_bps"] or 0)*row["n"] for row in monthly.values()); max_share=max((abs((row["mean_bps"] or 0)*row["n"])/abs(total_sum) if total_sum else 1 for row in monthly.values()),default=1)
    return {"events":len(points),"independent_events":independent_events(points,horizon),"primary":observed,"monthly":monthly,"matched_random":control,"random_mean_difference_bps":round(difference,4),"random_distinguishable_95":bool(stderr and abs(difference)>=1.96*stderr),"gross_mean_bps":gross,"net_after_hurdle_bps":round(gross-hurdle,4),"cost_dominance_ratio":round(hurdle/gross,4) if gross>0 else None,"positive_months":sum((row["mean_bps"] or 0)>0 for row in monthly.values()),"negative_months":sum((row["mean_bps"] or 0)<0 for row in monthly.values()),"largest_month_contribution_share":round(max_share,4),"regime_attribution":regimes(bars,points)}


def classify(metrics: dict, discovery: dict, hypothesis: dict) -> tuple[str,dict]:
    primary=metrics["primary"]; discovery_mean=discovery["gross_mean_bps"] or 0; mae_limit=2*abs(discovery["primary"]["mae_bps"] or 0)
    gates={"same_direction_as_discovery": discovery_mean>0 and metrics["gross_mean_bps"]>0,"positive_net_after_frozen_hurdle":metrics["net_after_hurdle_bps"]>0,"minimum_events":metrics["events"]>=hypothesis["minimum_events"],"minimum_independent_events":metrics["independent_events"]>=hypothesis["minimum_independent_events"],"positive_month_stability":metrics["positive_months"]>=hypothesis["minimum_positive_months"],"advantage_over_matched_random":metrics["random_mean_difference_bps"]>0 and metrics["random_distinguishable_95"],"no_single_month_domination":metrics["largest_month_contribution_share"]<=.50,"no_catastrophic_mae_deterioration":abs(primary["mae_bps"] or 0)<=mae_limit if mae_limit else False,"not_single_regime":max((row["share"] for row in metrics["regime_attribution"].values()),default=1)<=.75}
    return ("REPLICATION PASSED" if all(gates.values()) else "REPLICATION FAILED" if metrics["gross_mean_bps"]<=0 or metrics["net_after_hurdle_bps"]<=0 or metrics["random_mean_difference_bps"]<=0 else "REPLICATION WEAK"),gates


def discovery_rows(path: Path) -> dict:
    data=json.loads(path.read_text()); out={}
    for hid in ("REP-H1-ETH-FUNDING-LOW","REP-H2-SOL-FUNDING-LOW","REP-H3-ETH-REL-VOLUME-HIGH"):
        symbol=HYPOTHESES[hid]["symbol"]; feature="funding_low" if "FUNDING" in hid else "relative_volume_high"; item=data["futures_derivatives_structure"][symbol]["features"][feature]; out[hid]={"events":item["event_count"],"independent_events":item["feasibility"]["independent_events"],"gross_mean_bps":item["feasibility"]["gross_mean_bps"],"net_after_hurdle_bps":item["feasibility"]["net_of_conservative_cost_bps"],"positive_months":item["feasibility"]["positive_months"],"random_mean_difference_bps":item["feasibility"]["random_mean_difference_bps"],"primary":item["observed"]["24"]}
    item=data["higher_timeframe_structure"]["ETHUSDT"]["1d"]; out["REP-H4-ETH-1D-PERSISTENCE"]={"events":item["directional_persistence"]["10"]["n"],"independent_events":None,"gross_mean_bps":item["feasibility"]["gross_mean_bps_at_longest_horizon"],"net_after_hurdle_bps":item["feasibility"]["gross_mean_bps_at_longest_horizon"]-14,"positive_months":item["feasibility"]["monthly_positive"],"random_mean_difference_bps":None,"primary":item["directional_persistence"]["10"]}
    return out


def account_cost_audit() -> dict:
    files=[ROOT/".env.example",HUB/"config.py",HUB/"config"/"settings.py"]
    found=[]
    for path in files:
        if path.exists() and any(token in path.read_text(errors="ignore").lower() for token in ("maker_fee","taker_fee","commission")): found.append(str(path.relative_to(ROOT)))
    return {"status":"UNVERIFIED","reason":"No verified account-specific fee/fill schedule is embedded in this research harness; conservative frozen hurdles remain in force.","non_secret_configuration_files_examined":found}


def append_ledger(path: Path, rows: list[dict]) -> None:
    existing={json.loads(line).get("replication_record_id") for line in path.read_text().splitlines() if line.strip()} if path.exists() else set()
    with path.open("a",encoding="utf-8") as output:
        for row in rows:
            if row["replication_record_id"] not in existing: output.write(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n")


def report(result: dict) -> str:
    lines=["# Independent Replication of Derivatives Edge Premises","","## Purpose","","Falsify four frozen discovery premises on declared, independent 2024 H1 official exchange archives. No strategy was created.","","## Preserved Research Conclusions","","2025 discovery remains exploratory only; Jul–Sep 2025 validation and Oct–Dec 2025 untouched test were not opened.","","## Frozen Hypotheses","", "Definitions and fingerprints are in `automation-hub/data/independent_edge_replication_hypotheses.json`.","","## Replication Dataset","", "2024-01-01 through 2024-06-30, declared before archive access. Binance USDⓈ-M Futures is separate from Binance Spot ETH 1D.","","## Data Provenance","", "Archive hashes, observations, and gaps are in the machine-readable evidence.","","## Execution Hurdle","", "Futures uses the frozen conservative 16 bp round-trip hurdle. ETH 1D Spot uses its frozen 14 bp comparison hurdle. Account-specific fees/fills were not verified.",""]
    titles={"REP-H1-ETH-FUNDING-LOW":"ETH Funding-Low Replication","REP-H2-SOL-FUNDING-LOW":"SOL Funding-Low Replication","REP-H3-ETH-REL-VOLUME-HIGH":"ETH Relative-Volume Replication","REP-H4-ETH-1D-PERSISTENCE":"ETH 1D Persistence Replication"}
    for hid,row in result["results"].items():
        r=row["replication"]; lines += [f"## {titles[hid]}","",f"**{row['verdict']}** — events {r['events']}, independent {r['independent_events']}, gross {r['gross_mean_bps']:.2f} bp, net {r['net_after_hurdle_bps']:.2f} bp, positive months {r['positive_months']}/6, random difference {r['random_mean_difference_bps']:.2f} bp.",""]
    lines += ["## Discovery vs Replication","", "| Hypothesis | Discovery gross bp | Replication gross bp | Replication ratio | Verdict |","| --- | ---: | ---: | ---: | --- |"]
    for hid,row in result["results"].items():
        d,r=row["discovery"],row["replication"]; ratio=r["gross_mean_bps"]/(d["gross_mean_bps"] or 1); lines.append(f"| {hid} | {d['gross_mean_bps']:.2f} | {r['gross_mean_bps']:.2f} | {ratio:.3f} | {row['verdict']} |")
    passed=[hid for hid,row in result["results"].items() if row["verdict"]=="REPLICATION PASSED"]
    lines += ["","## Effect Shrinkage","", "Replication ratios above are descriptive; a sign reversal or cost failure outweighs any raw magnitude.","","## Monthly Stability","", "Per-month event metrics are retained in JSON. The gate requires at least four positive months and no single month contributing over 50% of absolute event contribution.","","## Regime Dependence","", "Causal trend/range and volatility labels are diagnostic only; no regime filter was added.","","## Random / Placebo Controls","", "Matched-frequency deterministic timestamp controls with shuffled directions were used, identical in form to discovery.","","## Execution-Cost Sensitivity","", "All conclusions use frozen hurdles; no post-result fee reduction was allowed.","","## Account Execution-Cost Audit","", f"**{result['account_execution_cost_audit']['status']}** — {result['account_execution_cost_audit']['reason']}","","## Replication Verdicts","", *[f"- **{hid}**: {row['verdict']}" for hid,row in result['results'].items()],"","## Remaining Risks","", "Six months is a limited independent sample. Association is not causality; queue position, fills, funding payments, and actual account fees remain unverified.","","## Next Authorized Stage","", f"**PASSED REPLICATION PREMISES: {len(passed)}**", *([f"- {hid}" for hid in passed] if passed else ["- None. Stop; do not create a strategy."]), "", "Only a passed premise would authorize minimal strategy architecture design, never validation, paper trading, or live trading."]
    return "\n".join(lines)+"\n"


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--data-dir",type=Path,required=True); parser.add_argument("--freeze",action="store_true"); parser.add_argument("--fetch",action="store_true"); parser.add_argument("--workers",type=int,default=6); parser.add_argument("--definitions",type=Path,default=HUB/"data"/"independent_edge_replication_hypotheses.json"); parser.add_argument("--discovery",type=Path,default=HUB/"data"/"research_universe_expansion.json"); parser.add_argument("--output",type=Path,default=HUB/"data"/"independent_edge_replication.json"); parser.add_argument("--ledger",type=Path,default=HUB/"data"/"strategy_v3_research_ledger.jsonl"); args=parser.parse_args(); assert_replication_only(); frozen=freeze(args.definitions)
    if args.freeze: print(json.dumps({"definitions":str(args.definitions),"hypotheses":list(HYPOTHESES),"replication_window":[REPLICATION_START.isoformat(),REPLICATION_END.isoformat()]})); return 0
    if args.fetch: print(json.dumps(fetch(args.data_dir,args.workers),sort_keys=True)); return 0
    discovery=discovery_rows(args.discovery); futures={}
    for symbol in ("ETHUSDT","SOLUSDT"): futures[symbol]=load_futures(args.data_dir,symbol)
    spot,spot_inventory=load_spot_eth(args.data_dir); results={}
    for hid,definition in HYPOTHESES.items():
        symbol=definition["symbol"]
        if hid in ("REP-H1-ETH-FUNDING-LOW","REP-H2-SOL-FUNDING-LOW"):
            bars,metrics,funding,_=futures[symbol]; aligned=[metrics.get(bar.timestamp,{}) for bar in bars]; funds=[last_known(funding,bar.timestamp) for bar in bars]; threshold=percentile([x for x in funds if x is not None],.10); points=[]; universe=[]
            for i in range(32,len(bars)-24):
                row,prior=aligned[i],aligned[i-4]
                if not {"oi_value","taker_ratio","basis_bps"}.issubset(row) or not {"oi_value","basis_bps"}.issubset(prior) or prior["oi_value"]<=0 or funds[i] is None or funds[i-32] is None: continue
                universe.append(i)
                if funds[i] <= threshold: points.append((i,1))
            metrics_result=hypothesis_metrics(hid,bars,points,universe,24,16.0); metrics_result["frozen_threshold_rule"]="per-symbol empirical 10th percentile"; metrics_result["replication_threshold"] = threshold
        elif hid=="REP-H3-ETH-REL-VOLUME-HIGH":
            bars,metrics,funding,_=futures[symbol]; aligned=[metrics.get(bar.timestamp,{}) for bar in bars]; funds=[last_known(funding,bar.timestamp) for bar in bars]; points=[]; universe=[]
            for i in range(32,len(bars)-24):
                row,prior=aligned[i],aligned[i-4]
                if not {"oi_value","taker_ratio","basis_bps"}.issubset(row) or not {"oi_value","basis_bps"}.issubset(prior) or prior["oi_value"]<=0 or funds[i] is None or funds[i-32] is None: continue
                universe.append(i); average=statistics.fmean(aligned[j].get("quote_volume",0) for j in range(i-20,i)); price_change=bars[i].close/bars[i-4].close-1
                if row.get("quote_volume",0)>=1.5*average: points.append((i,1 if price_change>=0 else -1))
            metrics_result=hypothesis_metrics(hid,bars,points,universe,24,16.0)
        else:
            bars=resample(spot,"1d","5m"); points=[]; universe=[]
            for i in range(20,len(bars)-10):
                universe.append(i); points.append((i,1 if bars[i].close>bars[i-5].close else -1))
            metrics_result=hypothesis_metrics(hid,bars,points,universe,10,14.0)
        verdict,gates=classify(metrics_result,discovery[hid],definition); results[hid]={"definition":definition,"fingerprint":frozen["fingerprints"][hid],"discovery":discovery[hid],"replication":metrics_result,"gates":gates,"verdict":verdict}
    output={"study_id":STUDY_ID,"created_at":CREATED_AT,"strategies_created":0,"forward_paper_candidates":0,"live_candidates":0,"hypotheses_tested":4,"frozen_definitions":frozen,"frozen_definition_integrity_verified":True,"runtime_source_code_hash":sha256_file(Path(__file__)),"replication_boundary":{"start":REPLICATION_START.isoformat(),"end":REPLICATION_END.isoformat(),"discovery_excluded":True,"existing_2025_validation_opened":False,"existing_2025_untouched_test_opened":False},"replication_sources":{"futures":{"venue":"Binance USDⓈ-M Futures","assets":{symbol:entry[3] for symbol,entry in futures.items()}},"spot":{"venue":"Binance Spot","asset":spot_inventory}},"execution_hurdles":{"futures":FUTURES_EXECUTION_HURDLE_BPS,"spot_round_trip_bps":14},"account_execution_cost_audit":account_cost_audit(),"results":results}; args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(output,sort_keys=True,indent=2)+"\n"); (ROOT/"INDEPENDENT_EDGE_REPLICATION_REPORT.md").write_text(report(output)); evidence_hash=hashlib.sha256(args.output.read_bytes()).hexdigest(); append_ledger(args.ledger,[{"replication_record_id":f"{STUDY_ID}:{hid}","study_id":STUDY_ID,"hypothesis_id":hid,"created_at":CREATED_AT,"discovery_evidence_sha256":hashlib.sha256(args.discovery.read_bytes()).hexdigest(),"replication_evidence_sha256":evidence_hash,"definition_hash":row["fingerprint"]["definition_hash"],"configuration_hash":row["fingerprint"]["configuration_hash"],"frozen_code_hash":row["fingerprint"]["source_code_hash"],"runtime_code_hash":output["runtime_source_code_hash"],"metrics":row["replication"],"verdict":row["verdict"],"validation_status":"SEALED","test_status":"SEALED"} for hid,row in results.items()]); print(json.dumps({"output":str(args.output),"verdicts":{hid: row["verdict"] for hid,row in results.items()},"strategies_created":0,"validation_opened":False,"test_opened":False},indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
