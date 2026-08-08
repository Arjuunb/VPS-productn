"""Read-only production evidence collector for the performance investigation.

Run this *inside the app container*.  It deliberately never prints credentials,
prices, trade identifiers, or free-form log payloads, and it never calls the V2
market-data service because its integrity check is allowed to quarantine a bad
cache.  All SQLite connections in this module are explicitly read-only.

Example (from the VPS repository root)::

    docker compose exec -T app python scripts/trading_performance_forensics.py \
      --format markdown > CURRENT_VPS_TRADING_CONFIG.md

The resulting document is a point-in-time observation, not a deployment
configuration file and not a trading recommendation.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from data.market_data_v2 import TF_MS, normalize_symbol
from services.performance import summarize


ENV_KEYS = (
    "HUB_AUTO_STRATEGY", "HUB_AUTO_TIMEFRAME", "HUB_AUTO_SYMBOLS",
    "HUB_USE_LIVE_DATA", "HUB_MARKET_DATA_V2", "HUB_ENTRY_MODE",
    "HUB_FILL_MODEL", "HUB_MAX_DAILY_LOSS", "HUB_MAX_DRAWDOWN",
    "HUB_STRATEGY_HEALTH_GUARD",
)
RUNTIME_KEYS = (
    "auto_strategy", "engine_timeframe", "engine_symbols", "auto_symbols",
    "entry_mode", "risk_per_trade_pct", "max_open_positions",
    "max_daily_loss_pct", "max_drawdown_pct", "min_quality_score",
    "position_sizing_mode", "fixed_position_size", "symbol_selection_mode",
    "manual_symbol", "engine_desired_running", "trading_mode",
)


def _flag(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_env(key: str) -> str:
    """Only report a fixed allow-list.  Nothing credential-like is accepted."""
    return os.environ.get(key, "<unset>")


def _safe_json(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        if not path.exists():
            return {}, "not present"
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}, "not a JSON object"
        return {k: raw[k] for k in RUNTIME_KEYS if k in raw}, None
    except Exception as exc:  # noqa: BLE001 - observations should still finish
        return {}, f"unreadable ({type(exc).__name__})"


def effective_config(env: dict[str, str], overrides: dict[str, Any]) -> dict[str, Any]:
    """Mirror the precedence used during ``webhook_api`` boot.

    This intentionally captures only trading settings.  The final symbol source
    is shown separately because manual symbol selection takes precedence over
    the persisted automatic list, which in turn takes precedence over the env.
    """
    def pick(runtime: str, env_name: str, fallback: str) -> Any:
        return overrides.get(runtime, env.get(env_name, "<unset>") if env.get(env_name, "<unset>") != "<unset>" else fallback)

    mode = str(pick("symbol_selection_mode", "HUB_SYMBOL_SELECTION_MODE", "auto"))
    manual = str(pick("manual_symbol", "HUB_MANUAL_SYMBOL", "")).strip()
    auto = str(pick("auto_symbols", "HUB_AUTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT"))
    engine_symbols = str(pick("engine_symbols", "HUB_AUTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT"))
    if mode == "manual" and manual:
        symbols, source = [normalize_symbol(manual)], "runtime.manual_symbol"
    elif auto.strip():
        symbols = [normalize_symbol(v) for v in auto.split(",") if normalize_symbol(v)]
        source = "runtime.auto_symbols" if "auto_symbols" in overrides else "HUB_AUTO_SYMBOLS"
    else:
        symbols = [normalize_symbol(v) for v in engine_symbols.split(",") if normalize_symbol(v)]
        source = "runtime.engine_symbols" if "engine_symbols" in overrides else "HUB_AUTO_SYMBOLS/default"
    return {
        "strategy": pick("auto_strategy", "HUB_AUTO_STRATEGY", "brain"),
        "timeframe": pick("engine_timeframe", "HUB_AUTO_TIMEFRAME", "4h"),
        "entry_mode": pick("entry_mode", "HUB_ENTRY_MODE", "limit"),
        "symbols": symbols,
        "symbol_source": source,
        "symbol_selection_mode": mode,
        "risk_per_trade_pct": pick("risk_per_trade_pct", "HUB_RISK_PER_TRADE", "0.01"),
        "max_open_positions": pick("max_open_positions", "HUB_MAX_OPEN_POSITIONS", "3"),
        "max_daily_loss_pct": pick("max_daily_loss_pct", "HUB_MAX_DAILY_LOSS", "0"),
        "max_drawdown_pct": pick("max_drawdown_pct", "HUB_MAX_DRAWDOWN", "0.20"),
        "min_quality_score": pick("min_quality_score", "HUB_MIN_QUALITY_SCORE", "60"),
        "position_sizing_mode": pick("position_sizing_mode", "HUB_POSITION_SIZING_MODE", "auto"),
        "fixed_position_size": pick("fixed_position_size", "HUB_FIXED_POSITION_SIZE", "0"),
    }


def _readonly_connection(path: Path) -> sqlite3.Connection:
    # mode=ro avoids creating a cache database or journal file during an audit.
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _cache_path(root: Path, symbol: str) -> Path | None:
    safe = "".join(ch for ch in normalize_symbol(symbol) if ch.isalnum() or ch == "_")
    for asset in ("crypto", "stocks", "forex", "commodities"):
        candidate = root / asset / f"{safe}.sqlite3"
        if candidate.exists():
            return candidate
    return None


def inspect_v2_cache(root: Path, symbol: str, timeframe: str, now_ms: int | None = None) -> dict[str, Any]:
    """Inspect cache rows without running V2's mutating checksum/quarantine path."""
    now_ms = now_ms or int(datetime.now(timezone.utc).timestamp() * 1000)
    result: dict[str, Any] = {"symbol": normalize_symbol(symbol), "timeframe": timeframe,
                              "cache_path": None, "available": False}
    if timeframe not in TF_MS:
        result["reason"] = "unsupported timeframe"
        return result
    path = _cache_path(root, symbol)
    if not path:
        result.update({"reason": "Market Data V2 cache required", "bars": 0,
                       "indicator_history_ready": False, "get_bars_expected": "empty"})
        return result
    result["cache_path"] = str(path)
    try:
        with _readonly_connection(path) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "candles" not in tables:
                result.update({"reason": "candles table missing", "bars": 0,
                               "indicator_history_ready": False, "get_bars_expected": "empty"})
                return result
            rows = [int(row[0]) for row in db.execute(
                "SELECT open_time FROM candles WHERE timeframe=? ORDER BY rowid", (timeframe,))]
            ordered = sorted(rows)
            duplicates = len(rows) - len(set(rows))
            out_of_order = sum(1 for a, b in zip(rows, rows[1:]) if b < a)
            gap = TF_MS[timeframe]
            missing = sum(max(0, (b - a) // gap - 1) for a, b in zip(ordered, ordered[1:]))
            latest = ordered[-1] if ordered else None
            # 150 is the longest current built-in indicator warm-up requirement.
            result.update({
                "available": bool(rows) and not duplicates and not out_of_order,
                "bars": len(rows), "latest_candle": _iso_ms(latest),
                "age_seconds": round(max(0, now_ms - latest) / 1000, 1) if latest else None,
                "missing_bars": missing, "duplicate_bars": duplicates,
                "out_of_order_bars": out_of_order,
                "indicator_history_ready": len(rows) >= 150 and not duplicates and not out_of_order,
                "get_bars_expected": "enough history" if len(rows) >= 150 and not duplicates and not out_of_order else "not enough verified history",
            })
    except Exception as exc:  # noqa: BLE001
        result.update({"reason": f"cache unreadable ({type(exc).__name__})", "bars": 0,
                       "indicator_history_ready": False, "get_bars_expected": "error"})
    return result


def _iso_ms(value: int | None) -> str | None:
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat() if value is not None else None


def _sqlite_rows(path: Path, table: str) -> tuple[list[dict], str | None]:
    if not path.exists():
        return [], "not present"
    try:
        with _readonly_connection(path) as db:
            db.row_factory = sqlite3.Row
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            if not exists:
                return [], "table missing"
            return [dict(r) for r in db.execute(f"SELECT * FROM {table}")], None
    except Exception as exc:  # noqa: BLE001
        return [], f"unreadable ({type(exc).__name__})"


def _supabase_rows(table: str) -> tuple[list[dict], str | None]:
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not (url and key):
        return [], "not configured"
    try:
        from supabase import create_client
        client = create_client(url, key)
        data: list[dict] = []
        offset = 0
        while True:
            page = client.table(table).select("*").range(offset, offset + 999).execute().data or []
            data.extend(page)
            if len(page) < 1000:
                break
            offset += len(page)
        return data, None
    except Exception as exc:  # noqa: BLE001
        # The class, not server text, is enough for a non-secret operational report.
        return [], f"unavailable ({type(exc).__name__})"


def _starting_balance(account_path: Path) -> float | None:
    rows, _ = _sqlite_rows(account_path, "account_state")
    if not rows:
        return None
    row = rows[0]
    for key in ("starting_balance", "initial_balance", "initial_equity"):
        try:
            return float(row[key])
        except (KeyError, TypeError, ValueError):
            continue
    return None


def summarize_history(trades: Iterable[dict], starting_balance: float | None,
                      instances: Iterable[dict]) -> dict[str, Any]:
    """Aggregate by known attribution; never guess metadata absent from rows."""
    instance_by_id = {str(i.get("id") or ""): i for i in instances}
    closed = [dict(t) for t in trades if str(t.get("status", "")).lower() == "closed" and t.get("pnl") is not None]
    groups: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for trade in closed:
        iid = str(trade.get("instance_id") or "")
        inst = instance_by_id.get(iid, {})
        strategy = str(trade.get("strategy_id") or inst.get("strategy_key") or "")
        version = str(inst.get("strategy_version") or "")
        groups[(iid, strategy, version, str(trade.get("symbol") or ""))].append(trade)
    reports = []
    for (iid, strategy, version, symbol), items in sorted(groups.items(), key=lambda p: min(str(x.get("opened_at") or "") for x in p[1])):
        instance = instance_by_id.get(iid, {})
        initial = _number(instance.get("capital_allocation")) or starting_balance
        if initial is None:
            # Absolute balances cannot be reconstructed without a known opening capital.
            initial = 0.0
        metrics = summarize(items, initial)
        reports.append({
            "start": min((x.get("opened_at") for x in items if x.get("opened_at")), default=None),
            "end": max((x.get("closed_at") for x in items if x.get("closed_at")), default=None),
            "instance_id_present": bool(iid), "symbol": symbol or "<unknown>",
            "strategy": strategy or "UNATTRIBUTED LEGACY RUN",
            "strategy_version": version or None,
            "timeframe": instance.get("timeframe") if instance else None,
            "entry_mode": None, "fill_model": None,  # not persisted in the ledger schema
            "starting_balance": initial if (instance or starting_balance is not None) else None,
            "ending_balance": metrics["balance"] if (instance or starting_balance is not None) else None,
            "trades": metrics["trades"], "wins": metrics["wins"], "losses": metrics["losses"],
            "win_rate_pct": metrics["win_rate"], "profit_factor": metrics["profit_factor"],
            "average_r": round(sum(float(x.get("rr") or 0) for x in items) / len(items), 3) if items else None,
            "max_drawdown_pct": metrics["max_drawdown_pct"], "realized_pnl": metrics["realized_pnl"],
        })
    match = [r for r in reports if r["starting_balance"] is not None and r["ending_balance"] is not None
             and 400 <= float(r["starting_balance"]) <= 600 and float(r["ending_balance"]) >= 2_250]
    return {"closed_trades": len(closed), "groups": reports, "approx_500_to_2500_candidates": match}


def _number(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def collect() -> dict[str, Any]:
    env = {key: _safe_env(key) for key in ENV_KEYS}
    settings_path = Path(os.environ.get("HUB_SETTINGS_PATH", "/var/lib/tradexa/runtime_settings.json"))
    overrides, overrides_error = _safe_json(settings_path)
    effective = effective_config(env, overrides)
    root = Path(os.environ.get("HUB_MARKET_DATA_DIR", "/var/lib/tradexa/market_data"))
    v2_enabled = _flag(os.environ.get("HUB_MARKET_DATA_V2"))
    caches = [inspect_v2_cache(root, symbol, str(effective["timeframe"])) for symbol in effective["symbols"]] if v2_enabled else []

    # The production source of truth is Supabase when configured.  An audit
    # still inventories the local fallback ledger without showing any rows.
    supabase_trades, supabase_error = _supabase_rows("paper_trades")
    supabase_instances, instances_error = _supabase_rows("trading_instances")
    ledger_path = Path(os.environ.get("HUB_LEDGER_PATH", "/var/lib/tradexa/ledger.db"))
    local_trades, local_error = _sqlite_rows(ledger_path, "paper_trades")
    account_path = Path(os.environ.get("HUB_ACCOUNT_DB", "/var/lib/tradexa/account.db"))
    starting = _starting_balance(account_path)
    source = "supabase" if supabase_error is None else "local SQLite fallback"
    trades = supabase_trades if supabase_error is None else local_trades
    instances = supabase_instances if instances_error is None else []
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(), "environment": env,
        "runtime_settings_path": str(settings_path), "runtime_overrides": overrides,
        "runtime_overrides_note": overrides_error,
        "effective": effective, "market_data_v2_enabled": v2_enabled,
        "market_data_root": str(root), "v2_cache": caches,
        "history_source": source, "supabase_paper_trades_status": supabase_error or "connected",
        "supabase_instances_status": instances_error or "connected",
        "local_ledger_status": local_error or "readable", "history": summarize_history(trades, starting, instances),
        "limitations": [
            "No secrets, trade IDs, prices, free-form logs, or payloads are emitted.",
            "Entry mode and fill model are not columns in the legacy ledger; absent per-trade metadata is reported as unknown.",
            "The V2 cache scan is direct read-only SQLite inspection and does not invoke cache quarantine or fallback data.",
            "A timestamp/strategy replay requires actual historical candles for the candidate period; this collector does not fabricate them.",
        ],
    }


def markdown(data: dict[str, Any]) -> str:
    out = ["# Current VPS Trading Configuration and Evidence", "", f"Generated: `{data['generated_at']}`", "",
           "This is a read-only, non-secret forensic snapshot. Values labelled unknown are not stored by the applicable schema.", "",
           "## Environment allow-list", "", "| Setting | Value |", "| --- | --- |"]
    out.extend(f"| `{key}` | `{value}` |" for key, value in data["environment"].items())
    out += ["", "## Persisted overrides", "", f"Path: `{data['runtime_settings_path']}`", "", "| Key | Value |", "| --- | --- |"]
    if data["runtime_overrides"]:
        out.extend(f"| `{key}` | `{value}` |" for key, value in data["runtime_overrides"].items())
    else:
        out.append(f"| — | {data['runtime_overrides_note'] or 'none'} |")
    eff = data["effective"]
    out += ["", "## Effective engine configuration", "", "| Item | Effective value | Source / precedence |", "| --- | --- | --- |",
            f"| Strategy | `{eff['strategy']}` | runtime override, then environment/default |",
            f"| Timeframe | `{eff['timeframe']}` | runtime override, then environment/default |",
            f"| Entry mode | `{eff['entry_mode']}` | runtime override, then environment/default |",
            f"| Symbols | `{', '.join(eff['symbols']) or '<none>'}` | {eff['symbol_source']} |",
            f"| Position sizing | `{eff['position_sizing_mode']}` | runtime override, then environment/default |",
            f"| Risk / position cap | `{eff['risk_per_trade_pct']}` / `{eff['max_open_positions']}` | runtime override, then environment/default |",
            f"| Daily loss / drawdown | `{eff['max_daily_loss_pct']}` / `{eff['max_drawdown_pct']}` | runtime override, then environment/default |",
            f"| Quality threshold | `{eff['min_quality_score']}` | runtime override, then environment/default |"]
    out += ["", "## Market Data V2 health", "", f"Enabled: **{data['market_data_v2_enabled']}**", ""]
    if not data["market_data_v2_enabled"]:
        out.append("V2 is disabled; no V2 cache is required for the active path.")
    else:
        out += ["| Symbol | TF | Bars | Latest closed candle | Age (s) | Missing | Duplicate | Out of order | Indicator-ready | get_bars expectation |",
                "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |"]
        for r in data["v2_cache"]:
            out.append("| {symbol} | {timeframe} | {bars} | {latest} | {age} | {missing} | {dupes} | {order} | {ready} | {expect} |".format(
                symbol=r["symbol"], timeframe=r["timeframe"], bars=r.get("bars", 0), latest=r.get("latest_candle") or "—",
                age=r.get("age_seconds") if r.get("age_seconds") is not None else "—", missing=r.get("missing_bars", "—"),
                dupes=r.get("duplicate_bars", "—"), order=r.get("out_of_order_bars", "—"),
                ready=r.get("indicator_history_ready", False), expect=r.get("reason") or r.get("get_bars_expected", "—")))
    hist = data["history"]
    out += ["", "## Ledger / Supabase forensic inventory", "", f"Source selected: **{data['history_source']}**  ",
            f"Supabase paper trades: `{data['supabase_paper_trades_status']}`; instances: `{data['supabase_instances_status']}`; local ledger: `{data['local_ledger_status']}`.", "",
            f"Closed trade rows: **{hist['closed_trades']}**", "", "| Period | Pair | Strategy attribution | TF | Trades | W/L | Win rate | PF | Avg R | Max DD | Start → End balance | Entry / fill |",
            "| --- | --- | --- | --- | ---: | --- | ---: | ---: | ---: | ---: | --- | --- |"]
    if hist["groups"]:
        for r in hist["groups"]:
            balance = "unknown" if r["starting_balance"] is None else f"{r['starting_balance']} → {r['ending_balance']}"
            out.append(f"| {r['start'] or '—'} → {r['end'] or '—'} | {r['symbol']} | {r['strategy']} | {r['timeframe'] or 'unknown'} | {r['trades']} | {r['wins']}/{r['losses']} | {r['win_rate_pct']}% | {r['profit_factor']} | {r['average_r']} | {r['max_drawdown_pct']}% | {balance} | unknown / unknown |")
    else:
        out.append("| — | — | No closed trade evidence found | — | 0 | — | — | — | — | — | — | — |")
    if hist["approx_500_to_2500_candidates"]:
        out += ["", "Approximate 500 → 2,500 candidates were found above. Attribution remains evidence-only."]
    else:
        out += ["", "No group with a reconstructable opening balance near 500 and ending balance at or above 2,250 was found."]
    out += ["", "## Limitations", ""] + [f"- {item}" for item in data["limitations"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args(argv)
    data = collect()
    print(markdown(data) if args.format == "markdown" else json.dumps(data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
