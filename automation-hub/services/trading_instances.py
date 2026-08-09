"""Instance-first paper trading.

Each TradingInstance owns its engine, strategy state, paper execution view and
ledger scope.  The legacy engine remains available for backwards compatibility;
instances never share its mutable strategy or trade history.
"""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Callable, Optional

from data.ledger import Ledger, SqliteLedger
from data.tenant_scope import ensure_column
from execution.paper_engine import FillResult, PaperExecutionEngine
from services.auto_engine import AutoStrategyEngine
from services.controls import TradingControl
from services.performance import summarize
from services.signal_pipeline import SignalPipeline
from services.strategy_health import StrategyHealthMonitor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id() -> str:
    return uuid.uuid4().hex


_TIMEFRAME_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "1d": 86400, "1w": 604800,
}


def _age_seconds(value: object) -> Optional[int]:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
    except (TypeError, ValueError):
        return None


def _market_health(market: dict, *, timeframe: str, worker_state: str) -> dict:
    """Classify candle freshness on the server, from its actual timestamp.

    A transport being connected is deliberately not sufficient.  This makes
    the UI's Healthy/Stale/Disconnected legend truthful after browser refresh
    and also when the worker has stopped and only persisted state remains.
    """
    out = dict(market)
    # Provider OHLCV uses candle-open timestamps. Freshness starts when that
    # candle closes, not at its open, so a newly closed 5m candle reads 0s old.
    raw_age = _age_seconds(out.get("last_market_data_timestamp"))
    interval = _TIMEFRAME_SECONDS.get(timeframe, 300)
    age = max(0, raw_age - interval) if raw_age is not None else None
    out["market_data_age_seconds"] = age
    raw = str(out.get("market_data_status") or "").lower()
    if worker_state in ("error",) or raw == "error":
        state = "error"
    elif worker_state in ("stopped",) and raw not in ("healthy", "stale", "disconnected"):
        state = "stopped"
    elif raw == "warming_up" or int(out.get("warmup_bars") or 0) < 150:
        state = "warming_up"
    elif age is None:
        state = "error" if raw in ("failed", "error") else "disconnected"
    else:
        state = "healthy" if age < interval * 1.5 else "stale" if age <= interval * 3 else "disconnected"
    out["market_data_status"] = state
    out["freshness_thresholds_seconds"] = {
        "healthy_under": int(_TIMEFRAME_SECONDS.get(timeframe, 300) * 1.5),
        "disconnected_over": int(_TIMEFRAME_SECONDS.get(timeframe, 300) * 3),
    }
    return out


@dataclass
class TradingInstance:
    id: str
    symbol: str
    strategy_key: str
    strategy_label: str
    strategy_version: str
    timeframe: str
    risk_per_trade_pct: float
    capital_allocation: float
    max_open_positions: int = 3
    # These execution values are persisted with the worker; legacy autonomous
    # engine settings are never consulted when an instance starts or restores.
    sizing_mode: str = "auto"
    fixed_position_size: float = 0.0
    entry_mode: str = "limit"
    fill_model: str = "PerfectFill"
    execution_mode: str = "paper"
    mode: str = "trading"              # trading | research (paper only)
    # Trading instances are always forward paper. Research remains the only
    # instance mode allowed to consume a historical replay.
    market_data_mode: str = "paper_forward"
    state: str = "stopped"             # running | paused | stopped | error
    desired_running: bool = False
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    updated_at: str = field(default_factory=_now)
    last_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class InstanceLedger:
    """Tag every record and constrain every read to one instance."""
    def __init__(self, ledger: Ledger, instance_id: str):
        self._ledger, self.instance_id = ledger, instance_id

    def __getattr__(self, name):
        return getattr(self._ledger, name)

    def insert_webhook_event(self, **kw):
        return self._ledger.insert_webhook_event(**kw, instance_id=self.instance_id)

    def webhook_seen(self, alert_id: str, since_iso: str) -> bool:
        return any(e.get("alert_id") == alert_id and e.get("instance_id") == self.instance_id
                   and e.get("received_at", "") >= since_iso and e.get("status") != "rejected"
                   for e in self._ledger.get_webhook_events(1000))

    def get_webhook_events(self, limit=500):
        return [e for e in self._ledger.get_webhook_events(max(limit * 5, 500))
                if e.get("instance_id") == self.instance_id][:limit]

    def open_position(self, **kw):
        return self._ledger.open_position(**kw, instance_id=self.instance_id)

    def get_positions(self, status=None):
        return [p for p in self._ledger.get_positions(status)
                if p.get("instance_id") == self.instance_id]

    def update_position_stop(self, *, symbol, stop):
        return self._ledger.update_position_stop(symbol=symbol, stop=stop, instance_id=self.instance_id)

    def record_paper_trade(self, trade):
        row = dict(trade); row["instance_id"] = self.instance_id
        return self._ledger.record_paper_trade(row)

    def get_paper_trades(self):
        return [t for t in self._ledger.get_paper_trades() if t.get("instance_id") == self.instance_id]

    def log(self, *, level, stage, message, symbol=""):
        return self._ledger.log(level=level, stage=stage, message=message, symbol=symbol,
                                instance_id=self.instance_id)

    def get_logs(self, limit=200):
        return [r for r in self._ledger.get_logs(max(limit * 5, 500))
                if r.get("instance_id") == self.instance_id][:limit]

    def add_alert(self, *, severity, category, title, detail=""):
        return self._ledger.add_alert(severity=severity, category=category, title=title,
                                      detail=detail, instance_id=self.instance_id)


class ResearchExecutionEngine(PaperExecutionEngine):
    """Signal-only execution adapter used by research instances.

    Research workers must exercise the same strategy and risk path as trading
    workers, but they are not allowed to create positions or orders.  Returning
    a normal rejected fill makes that boundary explicit in the decision log
    without pretending that a simulated order was placed.
    """
    def open(self, *, symbol: str, side: str, size: float, entry: float,
             stop: Optional[float], alert_id: str = "", maker: bool = False) -> FillResult:
        return FillResult("rejected", symbol, side.lower(), 0.0, entry)


_LOCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS trading_instances (
 id TEXT PRIMARY KEY, symbol TEXT NOT NULL, strategy_key TEXT NOT NULL,
 strategy_label TEXT NOT NULL, strategy_version TEXT NOT NULL, timeframe TEXT NOT NULL,
 risk_per_trade_pct REAL NOT NULL, capital_allocation REAL NOT NULL,
 max_open_positions INTEGER NOT NULL DEFAULT 3,
 sizing_mode TEXT NOT NULL DEFAULT 'auto', fixed_position_size REAL NOT NULL DEFAULT 0,
 entry_mode TEXT NOT NULL DEFAULT 'limit', fill_model TEXT NOT NULL DEFAULT 'PerfectFill',
 execution_mode TEXT NOT NULL DEFAULT 'paper',
 mode TEXT NOT NULL, market_data_mode TEXT NOT NULL DEFAULT 'paper_forward', state TEXT NOT NULL, desired_running INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, started_at TEXT, stopped_at TEXT, updated_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS instance_metrics (
 instance_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instance_engine_logs (
 id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, ts TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instance_market_state (
 instance_id TEXT PRIMARY KEY, last_processed_candle_timestamp TEXT,
 market_data_mode TEXT NOT NULL, market_data_status TEXT NOT NULL DEFAULT 'stopped',
 last_market_data_timestamp TEXT, data_source TEXT, warmup_bars INTEGER NOT NULL DEFAULT 0,
 duplicate_candles INTEGER NOT NULL DEFAULT 0, missing_candles INTEGER NOT NULL DEFAULT 0,
 out_of_order_candles INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trading_instance_platform_settings (
 id TEXT PRIMARY KEY, max_active_slots INTEGER NOT NULL DEFAULT 1,
 max_global_risk_pct REAL NOT NULL DEFAULT 0.02,
 max_global_daily_loss_pct REAL NOT NULL DEFAULT 0.05,
 paper_account_capital REAL NOT NULL DEFAULT 10000, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_instance_symbol_strategy ON trading_instances(symbol, strategy_key, strategy_version);
"""


class InstanceStore:
    """SQLite in development; Supabase tables in production after the additive migration."""
    def __init__(self, ledger: Ledger):
        self.ledger = ledger
        self.remote = not isinstance(ledger, SqliteLedger)
        self.available = True
        self.error = ""
        if not self.remote:
            with ledger._lock:
                ledger._c.executescript(_LOCAL_SCHEMA)
                ensure_column(ledger._c, "trading_instance_platform_settings",
                              "max_global_daily_loss_pct", "REAL NOT NULL DEFAULT 0.05")
                ensure_column(ledger._c, "trading_instances", "market_data_mode",
                              "TEXT NOT NULL DEFAULT 'paper_forward'")
                for name, definition in (
                    ("sizing_mode", "TEXT NOT NULL DEFAULT 'auto'"),
                    ("fixed_position_size", "REAL NOT NULL DEFAULT 0"),
                    ("entry_mode", "TEXT NOT NULL DEFAULT 'limit'"),
                    ("fill_model", "TEXT NOT NULL DEFAULT 'PerfectFill'"),
                    ("execution_mode", "TEXT NOT NULL DEFAULT 'paper'"),
                    ("max_open_positions", "INTEGER NOT NULL DEFAULT 3"),
                    ("started_at", "TEXT"),
                    ("stopped_at", "TEXT"),
                ):
                    ensure_column(ledger._c, "trading_instances", name, definition)
                ensure_column(ledger._c, "trading_instance_platform_settings",
                              "paper_account_capital", "REAL NOT NULL DEFAULT 10000")
                ledger._c.commit()

    def _table(self, name):
        return self.ledger._db.table(name)

    def create(self, instance: TradingInstance) -> None:
        if not self.available:
            raise RuntimeError(self.error or "Trading instance storage is not available")
        row = instance.to_dict()
        if self.remote:
            self._table("trading_instances").insert(row).execute()
        else:
            with self.ledger._lock:
                self.ledger._c.execute("""INSERT INTO trading_instances
                (id,symbol,strategy_key,strategy_label,strategy_version,timeframe,risk_per_trade_pct,capital_allocation,max_open_positions,sizing_mode,fixed_position_size,entry_mode,fill_model,execution_mode,mode,market_data_mode,state,desired_running,created_at,started_at,stopped_at,updated_at,last_error)
                VALUES (:id,:symbol,:strategy_key,:strategy_label,:strategy_version,:timeframe,:risk_per_trade_pct,:capital_allocation,:max_open_positions,:sizing_mode,:fixed_position_size,:entry_mode,:fill_model,:execution_mode,:mode,:market_data_mode,:state,:desired_running,:created_at,:started_at,:stopped_at,:updated_at,:last_error)""", row)
                self.ledger._c.commit()

    def list(self) -> list[TradingInstance]:
        if self.remote:
            try:
                rows = self._table("trading_instances").select("*").order("created_at", desc=True).execute().data
            except Exception as exc:
                self.available = False
                self.error = "Trading instance tables are not installed in Supabase; run data/trading_instances_schema.sql"
                return []
        else:
            with self.ledger._lock:
                rows = [dict(r) for r in self.ledger._c.execute("SELECT * FROM trading_instances ORDER BY created_at DESC")]
        return [TradingInstance(**{**r, "desired_running": bool(r.get("desired_running"))}) for r in rows]

    def save(self, instance: TradingInstance) -> None:
        instance.updated_at = _now()
        row = instance.to_dict()
        if self.remote:
            self._table("trading_instances").update(row).eq("id", instance.id).execute()
        else:
            with self.ledger._lock:
                self.ledger._c.execute(
                    """UPDATE trading_instances
                    SET symbol=:symbol, strategy_key=:strategy_key, strategy_label=:strategy_label,
                        strategy_version=:strategy_version, timeframe=:timeframe,
                        risk_per_trade_pct=:risk_per_trade_pct, capital_allocation=:capital_allocation,
                        max_open_positions=:max_open_positions,
                        sizing_mode=:sizing_mode, fixed_position_size=:fixed_position_size,
                        entry_mode=:entry_mode, fill_model=:fill_model, execution_mode=:execution_mode,
                        market_data_mode=:market_data_mode, state=:state, desired_running=:desired_running,
                        started_at=:started_at, stopped_at=:stopped_at,
                        updated_at=:updated_at, last_error=:last_error
                    WHERE id=:id""", row)
                self.ledger._c.commit()

    def market_state(self, instance_id: str) -> dict:
        defaults = {"instance_id": instance_id, "last_processed_candle_timestamp": None,
                    "market_data_mode": "paper_forward", "market_data_status": "stopped",
                    "last_market_data_timestamp": None, "data_source": None,
                    "warmup_bars": 0, "duplicate_candles": 0, "missing_candles": 0,
                    "out_of_order_candles": 0}
        try:
            if self.remote:
                rows = self._table("instance_market_state").select("*").eq("instance_id", instance_id).execute().data
                return {**defaults, **(rows[0] if rows else {})}
            with self.ledger._lock:
                row = self.ledger._c.execute("SELECT * FROM instance_market_state WHERE instance_id=?", (instance_id,)).fetchone()
            return {**defaults, **(dict(row) if row else {})}
        except Exception as exc:
            self.available = False
            self.error = "Trading Instance market-state migration is not installed; run data/trading_instances_schema.sql"
            raise RuntimeError(self.error) from exc

    def save_market_state(self, instance_id: str, **values) -> None:
        row = {"instance_id": instance_id, "last_processed_candle_timestamp": None,
               "market_data_mode": "paper_forward", "market_data_status": "warming_up",
               "last_market_data_timestamp": None, "data_source": None,
               "warmup_bars": 0, "duplicate_candles": 0, "missing_candles": 0,
               "out_of_order_candles": 0, "updated_at": _now(), **values}
        if self.remote:
            self._table("instance_market_state").upsert(row).execute()
        else:
            with self.ledger._lock:
                self.ledger._c.execute("""INSERT OR REPLACE INTO instance_market_state
                (instance_id,last_processed_candle_timestamp,market_data_mode,market_data_status,last_market_data_timestamp,data_source,warmup_bars,duplicate_candles,missing_candles,out_of_order_candles,updated_at)
                VALUES (:instance_id,:last_processed_candle_timestamp,:market_data_mode,:market_data_status,:last_market_data_timestamp,:data_source,:warmup_bars,:duplicate_candles,:missing_candles,:out_of_order_candles,:updated_at)""", row)
                self.ledger._c.commit()

    def save_metrics(self, instance_id: str, metrics: dict) -> None:
        row = {"instance_id": instance_id, "data_json": json.dumps(metrics), "updated_at": _now()}
        if self.remote:
            # PostgREST accepts a Python dict for JSONB.  A JSON *string* is a
            # JSON string value, not the metrics object, and would make the
            # remote data unusable for future analytics queries.
            self._table("instance_metrics").upsert({**row, "data_json": metrics}).execute()
        else:
            with self.ledger._lock:
                self.ledger._c.execute("INSERT OR REPLACE INTO instance_metrics(instance_id,data_json,updated_at) VALUES (:instance_id,:data_json,:updated_at)", row)
                self.ledger._c.commit()

    def platform_settings(self) -> dict:
        defaults = {"max_active_slots": 1, "max_global_risk_pct": 0.02,
                    "max_global_daily_loss_pct": 0.05, "paper_account_capital": None}
        if self.remote:
            try:
                rows = self._table("trading_instance_platform_settings").select("*").eq("id", "default").execute().data
                return {**defaults, **(rows[0] if rows else {})}
            except Exception as exc:
                self.available = False
                self.error = "Trading instance tables are not installed in Supabase; run data/trading_instances_schema.sql"
                return defaults
        with self.ledger._lock:
            row = self.ledger._c.execute("SELECT * FROM trading_instance_platform_settings WHERE id='default'").fetchone()
        return {**defaults, **(dict(row) if row else {})}

    def save_platform_settings(self, *, max_active_slots: int, max_global_risk_pct: float,
                               max_global_daily_loss_pct: float, paper_account_capital: float) -> None:
        row = {"id": "default", "max_active_slots": max_active_slots,
               "max_global_risk_pct": max_global_risk_pct,
               "max_global_daily_loss_pct": max_global_daily_loss_pct,
               "paper_account_capital": paper_account_capital, "updated_at": _now()}
        if self.remote:
            self._table("trading_instance_platform_settings").upsert(row).execute()
        else:
            with self.ledger._lock:
                self.ledger._c.execute("""INSERT OR REPLACE INTO trading_instance_platform_settings
                    (id,max_active_slots,max_global_risk_pct,max_global_daily_loss_pct,paper_account_capital,updated_at)
                    VALUES (:id,:max_active_slots,:max_global_risk_pct,:max_global_daily_loss_pct,:paper_account_capital,:updated_at)""", row)
                self.ledger._c.commit()


class TradingInstanceManager:
    def __init__(self, ledger: Ledger, *, strategy_factory: Callable[[str, str], object],
                 live: bool, live_poll_s: float, fetcher=None, max_slots: int = 1,
                 max_global_risk_pct: float = 0.02, max_global_daily_loss_pct: float = 0.05,
                 paper_account_capital: float = 10_000.0, decision_store=None):
        self.ledger, self.store = ledger, InstanceStore(ledger)
        self.strategy_factory, self.live, self.live_poll_s, self.fetcher = strategy_factory, live, live_poll_s, fetcher
        self.decision_store = decision_store
        # Forward trading intentionally does not inherit HUB_USE_LIVE_DATA. It
        # always uses the strict provider-only adapter; a missing provider is a
        # fail-closed market-data error, never a replay fallback.
        from data.forward_market_data import fetch_forward_bars
        self.forward_fetcher = fetch_forward_bars
        configured = self.store.platform_settings() if self.store.available else {}
        self.max_slots = min(3, max(1, int(configured.get("max_active_slots", max_slots))))
        self.max_global_risk_pct = min(1.0, max(0.001, float(configured.get("max_global_risk_pct", max_global_risk_pct))))
        self.max_global_daily_loss_pct = min(1.0, max(0.001, float(configured.get("max_global_daily_loss_pct", max_global_daily_loss_pct))))
        configured_capital = configured.get("paper_account_capital")
        self.paper_account_capital = max(1.0, float(configured_capital if configured_capital is not None else paper_account_capital))
        self._instances: dict[str, TradingInstance] = {i.id: i for i in self.store.list()}
        self._runtime: dict[str, tuple[AutoStrategyEngine, PaperExecutionEngine, SignalPipeline, TradingControl]] = {}
        self._metric_fingerprints: dict[str, tuple] = {}
        self._lock = threading.RLock()

    def create(self, *, symbol: str, strategy_key: str, strategy_label: str, strategy_version: str,
               timeframe: str, risk_per_trade_pct: float, capital_allocation: float, mode: str = "trading",
               max_open_positions: int = 3,
               sizing_mode: str = "auto", fixed_position_size: float = 0.0,
               entry_mode: str = "limit", fill_model: str = "PerfectFill") -> TradingInstance:
        if mode not in ("trading", "research"):
            raise ValueError("mode must be trading or research")
        if sizing_mode not in ("auto", "fixed"):
            raise ValueError("sizing_mode must be 'auto' or 'fixed'")
        if sizing_mode == "fixed" and fixed_position_size <= 0:
            raise ValueError("fixed_position_size must be greater than zero for fixed sizing")
        if entry_mode not in ("limit", "market"):
            raise ValueError("entry_mode must be 'limit' or 'market'")
        if fill_model != "PerfectFill":
            raise ValueError("PerfectFill is the only currently supported paper fill model")
        if not 1 <= int(max_open_positions) <= 50:
            raise ValueError("max_open_positions must be between 1 and 50")
        if mode == "trading":
            duplicate = next((item for item in self._instances.values()
                              if item.mode == "trading" and item.symbol == symbol.upper()
                              and item.strategy_key == strategy_key
                              and item.strategy_version == (strategy_version or "builtin-1")
                              and item.timeframe == timeframe and item.state in ("running", "paused", "reconnecting", "starting")), None)
            if duplicate is not None:
                raise ValueError("This Trading Instance is already active")
            allocated = sum(item.capital_allocation for item in self._instances.values() if item.mode == "trading")
            if allocated + capital_allocation > self.paper_account_capital + 1e-9:
                available = max(0.0, self.paper_account_capital - allocated)
                raise ValueError(f"Capital allocation exceeds paper account capacity; available {available:.2f}")
        inst = TradingInstance(id=_id(), symbol=symbol.upper(), strategy_key=strategy_key,
                               strategy_label=strategy_label, strategy_version=strategy_version or "builtin-1",
                               timeframe=timeframe, risk_per_trade_pct=risk_per_trade_pct,
                               capital_allocation=capital_allocation, max_open_positions=int(max_open_positions),
                               sizing_mode=sizing_mode,
                               fixed_position_size=fixed_position_size, entry_mode=entry_mode,
                               fill_model=fill_model, mode=mode,
                               market_data_mode="paper_forward" if mode == "trading" else "replay")
        self.store.create(inst); self._instances[inst.id] = inst
        return inst

    def _global_guard(self, instance_id: str, symbol: str, entry: float, stop: float, size: float) -> tuple[bool, str]:
        # Query the scoped records rather than only in-memory workers. An
        # operator may have stopped an instance while it still owns a position;
        # that risk must remain included until the position is closed.
        all_positions = [p for p in self.ledger.get_positions("open")
                         if p.get("instance_id") in self._instances]
        risk = sum(abs(float(p.get("entry", 0)) - float(p.get("stop") or p.get("entry", 0))) * float(p.get("size", 0)) for p in all_positions)
        next_risk = abs(entry - stop) * size
        allocated_capital = sum(i.capital_allocation for i in self._instances.values() if i.mode == "trading")
        capital = allocated_capital or 1.0
        today = datetime.now(timezone.utc).date().isoformat()
        daily_pnl = sum(float(t.get("pnl") or 0) for t in self.ledger.get_paper_trades()
                        if t.get("instance_id") in self._instances
                        and str(t.get("closed_at") or "").startswith(today))
        if daily_pnl <= -(capital * self.max_global_daily_loss_pct):
            return False, "Global daily loss limit reached"
        if risk + next_risk > capital * self.max_global_risk_pct:
            return False, f"Global account risk exceeded ({risk + next_risk:.2f} > {capital * self.max_global_risk_pct:.2f})"
        return True, "global risk within limit"

    def start(self, instance_id: str) -> TradingInstance:
        with self._lock:
            inst = self._instances[instance_id]
            if instance_id in self._runtime and self._runtime[instance_id][0].running:
                return inst
            active_trading = sum(1 for key, runtime in self._runtime.items()
                                 if runtime[0].running and self._instances[key].mode == "trading")
            if inst.mode == "trading" and active_trading >= self.max_slots:
                raise ValueError(f"Maximum active trading slots reached ({self.max_slots})")
            scoped = InstanceLedger(self.ledger, instance_id)
            controls = TradingControl()
            engine_type = ResearchExecutionEngine if inst.mode == "research" else PaperExecutionEngine
            paper = engine_type(scoped, inst.capital_allocation)
            # Ledger rows retain the immutable instance identity and a stable
            # strategy/version attribution without borrowing legacy state.
            paper.strategy_id = f"{inst.strategy_key}:{inst.strategy_version}"
            pipeline = SignalPipeline(scoped, paper, controls, equity=inst.capital_allocation,
                                      risk_per_trade_pct=inst.risk_per_trade_pct, exposure_limit_pct=0.05,
                                      max_open_positions=inst.max_open_positions,
                                      position_sizing_mode=inst.sizing_mode,
                                      fixed_position_size=inst.fixed_position_size)
            pipeline.global_entry_guard = lambda **kw: self._global_guard(instance_id, **kw)
            forward = inst.mode == "trading"
            market = self.store.market_state(instance_id) if forward else {}
            if forward:
                self.store.save_market_state(instance_id,
                    last_processed_candle_timestamp=market.get("last_processed_candle_timestamp"),
                    market_data_mode="paper_forward", market_data_status="warming_up",
                    last_market_data_timestamp=market.get("last_market_data_timestamp"),
                    data_source=market.get("data_source"), warmup_bars=int(market.get("warmup_bars") or 0),
                    duplicate_candles=int(market.get("duplicate_candles") or 0),
                    missing_candles=int(market.get("missing_candles") or 0),
                    out_of_order_candles=int(market.get("out_of_order_candles") or 0))
            def checkpoint(timestamp: str) -> None:
                self.store.save_market_state(instance_id,
                    last_processed_candle_timestamp=timestamp,
                    market_data_mode="paper_forward", market_data_status="healthy",
                    last_market_data_timestamp=timestamp, data_source="live provider",
                    warmup_bars=150)
            engine = AutoStrategyEngine(pipeline, paper, scoped, symbols=[inst.symbol], timeframe=inst.timeframe,
                                        strategy_factory=lambda symbol: self.strategy_factory(inst.strategy_key, symbol),
                                        live=forward, live_poll_s=self.live_poll_s,
                                        fetcher=self.forward_fetcher if forward else self.fetcher,
                                        initial_last_processed_candle=market.get("last_processed_candle_timestamp"),
                                        candle_checkpoint=checkpoint if forward else None,
                                        entry_mode=inst.entry_mode)
            engine.strategy_label = f"{inst.strategy_label} {inst.strategy_version}"
            engine.decisions = self.decision_store
            engine.start(); self._runtime[instance_id] = (engine, paper, pipeline, controls)
            inst.state, inst.desired_running, inst.last_error = "running", True, ""
            inst.started_at, inst.stopped_at = _now(), None
            self.store.save(inst); return inst

    def stop(self, instance_id: str) -> TradingInstance:
        inst = self._instances[instance_id]
        runtime = self._runtime.get(instance_id)
        if runtime: runtime[0].stop("Stopped by instance operator")
        inst.state, inst.desired_running, inst.stopped_at = "stopped", False, _now(); self.store.save(inst); return inst

    def restart(self, instance_id: str) -> TradingInstance:
        self.stop(instance_id)
        return self.start(instance_id)

    def pause(self, instance_id: str) -> TradingInstance:
        inst = self._instances[instance_id]; runtime = self._runtime.get(instance_id)
        if runtime: runtime[3].pause_all()
        inst.state, inst.desired_running = "paused", False; self.store.save(inst); return inst

    def resume(self, instance_id: str) -> TradingInstance:
        inst = self._instances[instance_id]; runtime = self._runtime.get(instance_id)
        if runtime:
            runtime[3].resume()
            inst.state, inst.desired_running = "running", True
            self.store.save(inst)
            return inst
        return self.start(instance_id)

    def update_configuration(self, instance_id: str, *, capital_allocation: float | None = None,
                             risk_per_trade_pct: float | None = None, sizing_mode: str | None = None,
                             fixed_position_size: float | None = None, entry_mode: str | None = None,
                             max_open_positions: int | None = None,
                             strategy_key: str | None = None, strategy_label: str | None = None,
                             strategy_version: str | None = None, timeframe: str | None = None) -> TradingInstance:
        """Persist execution configuration and safely rebuild an active worker.

        Strategy/timeframe changes cannot mutate a running strategy object in
        place. The manager therefore refuses edits while a position is open,
        stops the worker, persists one authoritative configuration, and
        restores its prior running/paused lifecycle state.
        """
        with self._lock:
            inst = self._instances[instance_id]
            open_positions = InstanceLedger(self.ledger, instance_id).get_positions("open")
            prior_state = inst.state
            had_runtime = instance_id in self._runtime
            candidate_capital = float(capital_allocation if capital_allocation is not None else inst.capital_allocation)
            if candidate_capital <= 0:
                raise ValueError("capital_allocation must be greater than zero")
            if risk_per_trade_pct is not None and not 0 < float(risk_per_trade_pct) <= 0.05:
                raise ValueError("risk_per_trade_pct must be in (0, 0.05]")
            candidate_mode = sizing_mode if sizing_mode is not None else inst.sizing_mode
            if candidate_mode not in ("auto", "fixed"):
                raise ValueError("sizing_mode must be 'auto' or 'fixed'")
            candidate_fixed = float(fixed_position_size if fixed_position_size is not None else inst.fixed_position_size)
            if candidate_fixed < 0 or (candidate_mode == "fixed" and candidate_fixed <= 0):
                raise ValueError("fixed_position_size must be greater than zero for fixed sizing")
            candidate_entry = entry_mode if entry_mode is not None else inst.entry_mode
            if candidate_entry not in ("limit", "market"):
                raise ValueError("entry_mode must be 'limit' or 'market'")
            candidate_max = int(max_open_positions if max_open_positions is not None else inst.max_open_positions)
            if not 1 <= candidate_max <= 50:
                raise ValueError("max_open_positions must be between 1 and 50")
            allocated_elsewhere = sum(item.capital_allocation for key, item in self._instances.items()
                                      if key != instance_id and item.mode == "trading")
            if inst.mode == "trading" and allocated_elsewhere + candidate_capital > self.paper_account_capital + 1e-9:
                raise ValueError("Capital allocation exceeds paper account capacity")
            rebuild_required = any(value is not None for value in (
                capital_allocation, sizing_mode, fixed_position_size, entry_mode,
                strategy_key, strategy_label, strategy_version, timeframe,
            ))
            if rebuild_required and open_positions:
                raise ValueError("Close the instance's open position before changing execution configuration")
            if len(open_positions) > candidate_max:
                raise ValueError("max_open_positions cannot be below the instance's current open-position count")
            if had_runtime and rebuild_required:
                self.stop(instance_id)
            inst.capital_allocation = candidate_capital
            inst.risk_per_trade_pct = float(risk_per_trade_pct if risk_per_trade_pct is not None else inst.risk_per_trade_pct)
            inst.max_open_positions = candidate_max
            inst.sizing_mode, inst.fixed_position_size, inst.entry_mode = candidate_mode, candidate_fixed, candidate_entry
            inst.strategy_key = strategy_key or inst.strategy_key
            inst.strategy_label = strategy_label or inst.strategy_label
            inst.strategy_version = strategy_version or inst.strategy_version
            inst.timeframe = timeframe or inst.timeframe
            inst.last_error = ""
            self.store.save(inst)
            if rebuild_required and prior_state in ("running", "reconnecting", "starting"):
                self.start(instance_id)
            elif rebuild_required and prior_state == "paused":
                self.start(instance_id)
                self.pause(instance_id)
            elif had_runtime:
                # Risk and position-cap changes affect future entries only and
                # can be applied atomically without interrupting market data.
                pipeline = self._runtime[instance_id][2]
                pipeline.risk_per_trade_pct = inst.risk_per_trade_pct
                pipeline.max_open_positions = inst.max_open_positions
            self.ledger.log(level="info", stage="instance",
                            message=(f"Instance configuration updated: {inst.symbol} "
                                     f"{inst.strategy_label} {inst.strategy_version} {inst.timeframe}"),
                            symbol=inst.symbol, instance_id=inst.id)
            return inst

    def configure(self, *, max_active_slots: int | None = None,
                  max_global_risk_pct: float | None = None,
                  max_global_daily_loss_pct: float | None = None,
                  paper_account_capital: float | None = None) -> dict:
        if max_active_slots is not None:
            if not 1 <= int(max_active_slots) <= 3:
                raise ValueError("max_active_slots must be between 1 and 3")
            running = sum(1 for key, r in self._runtime.items()
                          if r[0].running and self._instances[key].mode == "trading")
            if int(max_active_slots) < running:
                raise ValueError("Stop or pause instances before reducing active slots below the running count")
            self.max_slots = int(max_active_slots)
        if max_global_risk_pct is not None:
            if not 0.001 <= float(max_global_risk_pct) <= 1:
                raise ValueError("max_global_risk_pct must be between 0.001 and 1")
            self.max_global_risk_pct = float(max_global_risk_pct)
        if max_global_daily_loss_pct is not None:
            if not 0.001 <= float(max_global_daily_loss_pct) <= 1:
                raise ValueError("max_global_daily_loss_pct must be between 0.001 and 1")
            self.max_global_daily_loss_pct = float(max_global_daily_loss_pct)
        if paper_account_capital is not None:
            if float(paper_account_capital) <= 0:
                raise ValueError("paper_account_capital must be greater than zero")
            allocated = sum(item.capital_allocation for item in self._instances.values() if item.mode == "trading")
            if float(paper_account_capital) < allocated:
                raise ValueError("paper_account_capital cannot be below existing allocated capital")
            self.paper_account_capital = float(paper_account_capital)
        self.store.save_platform_settings(max_active_slots=self.max_slots,
                                          max_global_risk_pct=self.max_global_risk_pct,
                                          max_global_daily_loss_pct=self.max_global_daily_loss_pct,
                                          paper_account_capital=self.paper_account_capital)
        return self.platform_status()

    def platform_status(self) -> dict:
        active = sum(1 for key, r in self._runtime.items()
                     if r[0].running and self._instances[key].mode == "trading")
        open_positions = [p for p in self.ledger.get_positions("open")
                          if p.get("instance_id") in self._instances]
        used_risk = sum(abs(float(p.get("entry", 0)) - float(p.get("stop") or p.get("entry", 0)))
                        * float(p.get("size", 0)) for p in open_positions)
        allocated_capital = sum(i.capital_allocation for i in self._instances.values() if i.mode == "trading")
        capital = allocated_capital or 1.0
        today = datetime.now(timezone.utc).date().isoformat()
        instance_ids = set(self._instances)
        instance_trades = [t for t in self.ledger.get_paper_trades() if t.get("instance_id") in instance_ids]
        today_closed = [t for t in instance_trades if str(t.get("closed_at") or "").startswith(today)]
        today_pnl = sum(float(t.get("pnl") or 0) for t in today_closed)
        runtime_states = [self.status(i) for i in self._instances]
        unrealized = sum(float((row.get("current_position") or {}).get("unrealized_pnl") or 0)
                         for row in runtime_states)
        total_equity = sum(float((row.get("execution") or {}).get("current_equity") or 0)
                           for row in runtime_states)
        available_capital = sum(float((row.get("execution") or {}).get("available_capital") or 0)
                                for row in runtime_states)
        account_available = max(0.0, self.paper_account_capital - allocated_capital)
        opened_or_closed_today = {str(t.get("id")) for t in instance_trades
                                  if str(t.get("opened_at") or "").startswith(today)
                                  or str(t.get("closed_at") or "").startswith(today)}
        risk_limit = capital * self.max_global_risk_pct
        daily_limit = capital * self.max_global_daily_loss_pct
        if today_pnl <= -daily_limit:
            risk_status, risk_message = "daily_loss_limit_reached", "Daily loss limit reached — new entries are paused"
        elif risk_limit > 0 and used_risk / risk_limit >= 0.9:
            risk_status, risk_message = "warning", "Risk capacity almost reached"
        else:
            risk_status, risk_message = "healthy", "Global risk within limits"
        market_states = [str((row.get("market_data") or {}).get("market_data_status") or "")
                         for row in runtime_states if row.get("mode") == "trading" and row.get("state") == "running"]
        worker_errors = sum(1 for row in runtime_states if row.get("state") == "error")
        if not self.store.available or worker_errors or any(s in ("error", "disconnected") for s in market_states):
            global_status = "critical"
        elif risk_status != "healthy" or any(s in ("stale", "warming_up") for s in market_states):
            global_status = "warning"
        else:
            global_status = "healthy"
        return {"max_active_slots": self.max_slots, "active_slots": active,
                "max_global_risk_pct": self.max_global_risk_pct,
                "max_global_daily_loss_pct": self.max_global_daily_loss_pct,
                "max_global_risk_amount": round(allocated_capital * self.max_global_risk_pct, 2),
                "current_global_risk_amount": round(used_risk, 2),
                "total_open_positions": len(open_positions),
                "total_instances": len(self._instances),
                "instance_counts": {state: sum(1 for row in runtime_states if row.get("state") == state)
                                    for state in ("running", "paused", "stopped", "error")},
                "total_allocated_capital": round(allocated_capital, 2),
                "paper_account_capital": round(self.paper_account_capital, 2),
                "total_current_equity": round(total_equity, 2),
                # Allocation capacity is account-level.  Per-worker free cash
                # remains separately visible for execution diagnostics.
                "available_paper_capital": round(account_available, 2),
                "worker_available_capital": round(available_capital, 2),
                "today_pnl": round(today_pnl + unrealized, 2),
                "today_realized_pnl": round(today_pnl, 2),
                "today_unrealized_pnl": round(unrealized, 2),
                "today_trades": len(opened_or_closed_today),
                "global_risk_status": risk_status,
                "global_risk_message": risk_message,
                "market_data_status": ("not_available" if not market_states else
                                       "critical" if any(s in ("error", "disconnected") for s in market_states) else
                                       "warning" if any(s in ("stale", "warming_up") for s in market_states) else "healthy"),
                "global_status": global_status}

    def restore_desired_instances(self) -> list[str]:
        """Restore independently desired workers after an application restart.

        A browser session is deliberately irrelevant here: the persisted
        server-owned desired state decides whether an instance resumes.  Older
        deployments could persist more desired workers than the current
        platform slot limit.  Restore the earliest requested workers only and
        leave the remainder visibly paused for an operator to start after
        freeing a slot; never silently exceed the account-level limit.
        """
        restored: list[str] = []
        restored_trading = 0
        for inst in sorted(self._instances.values(), key=lambda item: item.created_at):
            if not inst.desired_running:
                continue
            if inst.mode == "trading" and restored_trading >= self.max_slots:
                inst.state = "paused"
                inst.desired_running = False
                inst.last_error = (
                    f"Not restored: maximum active trading slots reached ({self.max_slots}). "
                    "Stop another instance before starting this one."
                )
                inst.stopped_at = _now()
                self.store.save(inst)
                continue
            try:
                self.start(inst.id)
                restored.append(inst.id)
                if inst.mode == "trading":
                    restored_trading += 1
            except Exception as exc:  # one broken instance cannot block others
                inst.state, inst.last_error, inst.desired_running = "error", str(exc)[:500], False
                self.store.save(inst)
        return restored

    def metrics(self, instance_id: str) -> dict:
        inst = self._instances[instance_id]; runtime = self._runtime.get(instance_id)
        trades = runtime[1].history() if runtime else [t for t in self.ledger.get_paper_trades() if t.get("instance_id") == instance_id and t.get("status") == "closed"]
        out = summarize(trades, inst.capital_allocation)
        equity, peak = inst.capital_allocation, inst.capital_allocation
        for trade in sorted(trades, key=lambda item: item.get("closed_at") or ""):
            equity += float(trade.get("pnl") or 0)
            peak = max(peak, equity)
        current_drawdown_pct = round(((peak - equity) / peak * 100) if peak > 0 else 0.0, 2)
        rr = [float(t.get("rr") or 0) for t in trades if t.get("rr") is not None]
        durations = []
        for trade in trades:
            try:
                opened = datetime.fromisoformat(str(trade["opened_at"]).replace("Z", "+00:00"))
                closed = datetime.fromisoformat(str(trade["closed_at"]).replace("Z", "+00:00"))
                durations.append((closed - opened).total_seconds())
            except (KeyError, TypeError, ValueError):
                continue
        out.update({
            "average_rr": round(sum(rr) / len(rr), 3) if rr else 0.0,
            "fees": round(runtime[1].fees_paid(), 8) if runtime else 0.0,
            "slippage": 0.0,  # execution-quality captures this when a non-perfect fill model is configured
            "average_trade_duration_seconds": round(sum(durations) / len(durations), 1) if durations else 0.0,
            "consecutive_wins": self._current_streak(trades, positive=True),
            "consecutive_losses": self._current_streak(trades, positive=False),
        })
        health = StrategyHealthMonitor().evaluate(trades).to_dict()
        out.update({"instance_id": instance_id, "strategy_health": health,
                    "current_drawdown_pct": current_drawdown_pct})
        # The dashboard polls status frequently. Persist only when the isolated
        # trade record changed, not on every page refresh/poll interval.
        last = trades[-1] if trades else {}
        fingerprint = (len(trades), last.get("id"), last.get("pnl"), last.get("closed_at"))
        if self._metric_fingerprints.get(instance_id) != fingerprint:
            self.store.save_metrics(instance_id, out)
            self._metric_fingerprints[instance_id] = fingerprint
        return out

    @staticmethod
    def _current_streak(trades: list[dict], *, positive: bool) -> int:
        count = 0
        for trade in sorted(trades, key=lambda item: item.get("closed_at") or "", reverse=True):
            pnl = float(trade.get("pnl") or 0)
            if (pnl > 0) == positive and pnl != 0:
                count += 1
            else:
                break
        return count

    def status(self, instance_id: str) -> dict:
        inst = self._instances[instance_id]; runtime = self._runtime.get(instance_id)
        engine = runtime[0].status() if runtime else None
        market = self.store.market_state(instance_id) if inst.mode == "trading" else {
            "market_data_mode": "replay", "market_data_status": "research_replay"}
        current_position = None
        if engine is not None:
            positions = runtime[1].positions()
            position = positions[0] if positions else None
            if position:
                mark = engine.get("last_prices", {}).get(position["symbol"])
                unrealized = None
                if mark is not None:
                    direction = position.get("side")
                    unrealized = ((float(mark) - float(position["entry"])) * float(position["size"])
                                  if direction == "long"
                                  else (float(position["entry"]) - float(mark)) * float(position["size"]))
                current_position = {
                    "symbol": position["symbol"], "side": position.get("side"),
                    "size": position.get("size"), "entry": position.get("entry"),
                    "stop": position.get("stop"), "target": runtime[0]._targets.get(position["symbol"]),
                    "mark": mark, "unrealized_pnl": round(unrealized, 2) if unrealized is not None else None,
                }
                entry, stop = float(position.get("entry") or 0), position.get("stop")
                if mark is not None and stop is not None and entry != float(stop):
                    direction = 1 if position.get("side") == "long" else -1
                    current_position["current_r"] = round(direction * (float(mark) - entry) / abs(entry - float(stop)), 3)
                else:
                    current_position["current_r"] = None
                current_position["risk_amount"] = round(abs(entry - float(stop or entry)) * float(position.get("size") or 0), 2)
                current_position["opened_at"] = position.get("opened_at")
                current_position["duration_seconds"] = _age_seconds(position.get("opened_at"))
            engine = {**engine, "open_positions": len(runtime[1].positions()),
                      "paper_balance": runtime[1].balance()}
            if inst.mode == "trading":
                market = {**market,
                          "market_data_mode": "paper_forward",
                          "market_data_status": engine.get("market_data_status", market["market_data_status"]),
                          "last_market_data_timestamp": engine.get("last_closed_candle") or market.get("last_market_data_timestamp"),
                          "last_processed_candle_timestamp": engine.get("last_processed_candle_timestamp") or market.get("last_processed_candle_timestamp"),
                          "data_source": engine.get("data_source") or market.get("data_source"),
                          "warmup_bars": engine.get("warmup_bars", market.get("warmup_bars", 0)),
                          "duplicate_candles": engine.get("duplicate_candles_ignored", market.get("duplicate_candles", 0)),
                          "missing_candles": engine.get("missing_candles", market.get("missing_candles", 0)),
                          "out_of_order_candles": engine.get("out_of_order_candles", market.get("out_of_order_candles", 0))}
        state = inst.state if engine is None else ("paused" if inst.state == "paused" else engine.get("lifecycle_state", inst.state))
        if inst.mode == "trading":
            market = _market_health(market, timeframe=inst.timeframe, worker_state=state)
        if engine and state in ("stopped", "error") and inst.state != state:
            # Persist terminal worker state so a stale UI can never claim a
            # dead engine is live. Errors require explicit operator recovery;
            # a clean replay completion likewise must not restart on deploy.
            inst.state = state
            inst.last_error = engine.get("last_error") or engine.get("stop_reason") or ""
            inst.desired_running = False
            self.store.save(inst)
        if inst.mode == "trading" and engine and state in ("stopped", "error"):
            # Preserve a terminal data diagnosis for the dashboard after the
            # worker object disappears on a later process restart.
            self.store.save_market_state(instance_id,
                last_processed_candle_timestamp=market.get("last_processed_candle_timestamp"),
                market_data_mode="paper_forward",
                market_data_status=engine.get("market_data_status", "error"),
                last_market_data_timestamp=market.get("last_market_data_timestamp"),
                data_source=market.get("data_source"), warmup_bars=int(market.get("warmup_bars") or 0),
                duplicate_candles=int(market.get("duplicate_candles") or 0),
                missing_candles=int(market.get("missing_candles") or 0),
                out_of_order_candles=int(market.get("out_of_order_candles") or 0))
        metrics = self.metrics(instance_id)
        execution: dict = {
            "current_equity": metrics.get("balance"),
            "available_capital": None,
            "realized_pnl": metrics.get("realized_pnl"),
            "unrealized_pnl": None,
            "return_pct": round(float(metrics.get("realized_pnl") or 0) / max(inst.capital_allocation, 1.0) * 100, 3),
            "entry_mode": None,
            "fill_model": None,
            "position_sizing_mode": None,
            "fixed_position_size": None,
            "max_open_positions": None,
            "leverage": None,  # leverage is not modelled by the paper engine
            "pending_orders": None,
        }
        risk: dict = {"open_risk_amount": 0.0, "open_risk_pct": None}
        if runtime is not None:
            engine_object, paper, pipeline, _controls = runtime
            marks = (engine or {}).get("last_prices") or {}
            execution.update({
                "current_equity": round(paper.equity(marks), 2),
                "available_capital": round(paper.available_balance(), 2),
                "realized_pnl": round(paper.realized_pnl(), 2),
                "unrealized_pnl": round(paper.unrealized_pnl(marks), 2),
                "entry_mode": (engine or {}).get("entry_mode"),
                "fill_model": type(paper.fill_model).__name__,
                "position_sizing_mode": pipeline.position_sizing_mode,
                "fixed_position_size": pipeline.fixed_position_size if pipeline.position_sizing_mode == "fixed" else None,
                "max_open_positions": pipeline.max_open_positions,
                "pending_orders": (engine or {}).get("pending_orders"),
            })
            execution["return_pct"] = round((float(execution["current_equity"]) - inst.capital_allocation) /
                                               max(inst.capital_allocation, 1.0) * 100, 3)
            if current_position:
                open_risk = abs(float(current_position.get("entry") or 0) - float(current_position.get("stop") or current_position.get("entry") or 0)) * float(current_position.get("size") or 0)
                risk = {"open_risk_amount": round(open_risk, 2),
                        "open_risk_pct": round(open_risk / max(float(execution["current_equity"] or 0), 1.0) * 100, 3)}
        else:
            # A stopped worker has no authoritative in-memory mark.  Its
            # closed-trade balance is available, but available cash and any
            # live open-position valuation are intentionally left unknown.
            execution["current_equity"] = metrics.get("balance")
        last_decision = None
        if self.decision_store is not None:
            try:
                rows = self.decision_store.list(limit=1, instance_id=instance_id)
                last_decision = rows[0] if rows else None
            except Exception:  # noqa: BLE001 -- observability must not stop a worker
                last_decision = None
        configuration = {
            "symbol": inst.symbol, "strategy": inst.strategy_label,
            "strategy_key": inst.strategy_key, "strategy_version": inst.strategy_version,
            "timeframe": inst.timeframe, "capital_allocation": inst.capital_allocation,
            "max_open_positions": inst.max_open_positions,
            "risk_per_trade_pct": inst.risk_per_trade_pct,
            "sizing_mode": inst.sizing_mode, "fixed_position_size": inst.fixed_position_size,
            "entry_mode": inst.entry_mode, "fill_model": inst.fill_model,
            "execution_mode": inst.execution_mode, "market_data_mode": inst.market_data_mode,
        }
        return {**inst.to_dict(), "state": state, "engine": engine,
                "configuration": configuration, "execution": execution,
                "risk": risk, "market_data": market, "current_position": current_position,
                "performance": {**metrics, "net_pnl": execution.get("realized_pnl"),
                                "return_pct": execution.get("return_pct")},
                "strategy_health": metrics.get("strategy_health"),
                "last_decision": last_decision, "metrics": metrics}

    def list(self) -> list[dict]:
        return [self.status(i) for i in self._instances]

    def leaderboard(self, sort: str = "realized_pnl") -> list[dict]:
        rows = [{**self.status(i), **self.metrics(i)} for i in self._instances]
        allowed = {"realized_pnl", "profit_factor", "win_rate", "max_drawdown_pct", "expectancy", "trades"}
        key = sort if sort in allowed else "realized_pnl"
        return sorted(rows, key=lambda r: float(r.get(key) or 0), reverse=key != "max_drawdown_pct")

    def best_measured_instance(self, *, minimum_sample: int = 5) -> TradingInstance:
        """Promote the best measured *trading* combination into one slot.

        This intentionally ranks only isolated closed-trade records. It never
        treats a raw win rate, a cross-strategy blend, or a backfilled legacy
        trade as evidence. The score rewards profitability and risk-adjusted
        returns while penalising drawdown and undersized samples.
        """
        candidates = []
        for inst in self._instances.values():
            if inst.mode != "trading" or inst.state == "running":
                continue
            metrics = self.metrics(inst.id)
            trades = int(metrics.get("trades") or 0)
            if trades < minimum_sample:
                continue
            pf = min(float(metrics.get("profit_factor") or 0), 5.0)
            expectancy_pct = float(metrics.get("expectancy") or 0) / max(inst.capital_allocation, 1.0) * 100
            score = (pf * 20 + float(metrics.get("sharpe_ratio") or 0) * 8
                     + expectancy_pct * 100 - float(metrics.get("max_drawdown_pct") or 0) * 1.5
                     + min(trades, 100) * 0.1)
            candidates.append((score, inst))
        if not candidates:
            raise ValueError(f"No trading instance has the required {minimum_sample} isolated closed trades")
        return max(candidates, key=lambda item: item[0])[1]

    def auto_select(self, *, minimum_sample: int = 5) -> TradingInstance:
        winner = self.best_measured_instance(minimum_sample=minimum_sample)
        self.start(winner.id)
        return winner
