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
    mode: str = "trading"              # trading | research (paper only)
    state: str = "stopped"             # running | paused | stopped | error
    desired_running: bool = False
    created_at: str = field(default_factory=_now)
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
 mode TEXT NOT NULL, state TEXT NOT NULL, desired_running INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS instance_metrics (
 instance_id TEXT PRIMARY KEY, data_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instance_engine_logs (
 id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, ts TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS trading_instance_platform_settings (
 id TEXT PRIMARY KEY, max_active_slots INTEGER NOT NULL DEFAULT 1,
 max_global_risk_pct REAL NOT NULL DEFAULT 0.02,
 max_global_daily_loss_pct REAL NOT NULL DEFAULT 0.05, updated_at TEXT NOT NULL
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
                (id,symbol,strategy_key,strategy_label,strategy_version,timeframe,risk_per_trade_pct,capital_allocation,mode,state,desired_running,created_at,updated_at,last_error)
                VALUES (:id,:symbol,:strategy_key,:strategy_label,:strategy_version,:timeframe,:risk_per_trade_pct,:capital_allocation,:mode,:state,:desired_running,:created_at,:updated_at,:last_error)""", row)
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
                self.ledger._c.execute("""UPDATE trading_instances SET state=:state,desired_running=:desired_running,updated_at=:updated_at,last_error=:last_error WHERE id=:id""", row)
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
                    "max_global_daily_loss_pct": 0.05}
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
                               max_global_daily_loss_pct: float) -> None:
        row = {"id": "default", "max_active_slots": max_active_slots,
               "max_global_risk_pct": max_global_risk_pct,
               "max_global_daily_loss_pct": max_global_daily_loss_pct, "updated_at": _now()}
        if self.remote:
            self._table("trading_instance_platform_settings").upsert(row).execute()
        else:
            with self.ledger._lock:
                self.ledger._c.execute("""INSERT OR REPLACE INTO trading_instance_platform_settings
                    (id,max_active_slots,max_global_risk_pct,max_global_daily_loss_pct,updated_at)
                    VALUES (:id,:max_active_slots,:max_global_risk_pct,:max_global_daily_loss_pct,:updated_at)""", row)
                self.ledger._c.commit()


class TradingInstanceManager:
    def __init__(self, ledger: Ledger, *, strategy_factory: Callable[[str, str], object],
                 live: bool, live_poll_s: float, fetcher=None, max_slots: int = 1,
                 max_global_risk_pct: float = 0.02, max_global_daily_loss_pct: float = 0.05):
        self.ledger, self.store = ledger, InstanceStore(ledger)
        self.strategy_factory, self.live, self.live_poll_s, self.fetcher = strategy_factory, live, live_poll_s, fetcher
        configured = self.store.platform_settings() if self.store.available else {}
        self.max_slots = min(3, max(1, int(configured.get("max_active_slots", max_slots))))
        self.max_global_risk_pct = min(1.0, max(0.001, float(configured.get("max_global_risk_pct", max_global_risk_pct))))
        self.max_global_daily_loss_pct = min(1.0, max(0.001, float(configured.get("max_global_daily_loss_pct", max_global_daily_loss_pct))))
        self._instances: dict[str, TradingInstance] = {i.id: i for i in self.store.list()}
        self._runtime: dict[str, tuple[AutoStrategyEngine, PaperExecutionEngine, SignalPipeline, TradingControl]] = {}
        self._metric_fingerprints: dict[str, tuple] = {}
        self._lock = threading.RLock()

    def create(self, *, symbol: str, strategy_key: str, strategy_label: str, strategy_version: str,
               timeframe: str, risk_per_trade_pct: float, capital_allocation: float, mode: str = "trading") -> TradingInstance:
        if mode not in ("trading", "research"):
            raise ValueError("mode must be trading or research")
        inst = TradingInstance(id=_id(), symbol=symbol.upper(), strategy_key=strategy_key,
                               strategy_label=strategy_label, strategy_version=strategy_version or "builtin-1",
                               timeframe=timeframe, risk_per_trade_pct=risk_per_trade_pct,
                               capital_allocation=capital_allocation, mode=mode)
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
        capital = sum(i.capital_allocation for i in self._instances.values() if i.mode == "trading") or 1.0
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
            pipeline = SignalPipeline(scoped, paper, controls, equity=inst.capital_allocation,
                                      risk_per_trade_pct=inst.risk_per_trade_pct, exposure_limit_pct=0.05)
            pipeline.global_entry_guard = lambda **kw: self._global_guard(instance_id, **kw)
            engine = AutoStrategyEngine(pipeline, paper, scoped, symbols=[inst.symbol], timeframe=inst.timeframe,
                                        strategy_factory=lambda symbol: self.strategy_factory(inst.strategy_key, symbol),
                                        live=self.live, live_poll_s=self.live_poll_s, fetcher=self.fetcher)
            engine.strategy_label = f"{inst.strategy_label} {inst.strategy_version}"
            engine.start(); self._runtime[instance_id] = (engine, paper, pipeline, controls)
            inst.state, inst.desired_running, inst.last_error = "running", True, ""
            self.store.save(inst); return inst

    def stop(self, instance_id: str) -> TradingInstance:
        inst = self._instances[instance_id]
        runtime = self._runtime.get(instance_id)
        if runtime: runtime[0].stop("Stopped by instance operator")
        inst.state, inst.desired_running = "stopped", False; self.store.save(inst); return inst

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

    def configure(self, *, max_active_slots: int | None = None,
                  max_global_risk_pct: float | None = None,
                  max_global_daily_loss_pct: float | None = None) -> dict:
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
        self.store.save_platform_settings(max_active_slots=self.max_slots,
                                          max_global_risk_pct=self.max_global_risk_pct,
                                          max_global_daily_loss_pct=self.max_global_daily_loss_pct)
        return self.platform_status()

    def platform_status(self) -> dict:
        active = sum(1 for key, r in self._runtime.items()
                     if r[0].running and self._instances[key].mode == "trading")
        open_positions = [p for p in self.ledger.get_positions("open")
                          if p.get("instance_id") in self._instances]
        used_risk = sum(abs(float(p.get("entry", 0)) - float(p.get("stop") or p.get("entry", 0)))
                        * float(p.get("size", 0)) for p in open_positions)
        capital = sum(i.capital_allocation for i in self._instances.values() if i.mode == "trading") or 1.0
        return {"max_active_slots": self.max_slots, "active_slots": active,
                "max_global_risk_pct": self.max_global_risk_pct,
                "max_global_daily_loss_pct": self.max_global_daily_loss_pct,
                "max_global_risk_amount": round(capital * self.max_global_risk_pct, 2),
                "current_global_risk_amount": round(used_risk, 2)}

    def restore_desired_instances(self) -> list[str]:
        """Restore independently desired workers after an application restart.

        A browser session is deliberately irrelevant here: the persisted
        server-owned desired state decides whether an instance resumes.
        """
        restored: list[str] = []
        for inst in sorted(self._instances.values(), key=lambda item: item.created_at):
            if not inst.desired_running:
                continue
            try:
                self.start(inst.id)
                restored.append(inst.id)
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
        if engine is not None:
            engine = {**engine, "open_positions": len(runtime[1].positions()),
                      "paper_balance": runtime[1].balance()}
        state = inst.state if engine is None else ("paused" if inst.state == "paused" else engine.get("lifecycle_state", inst.state))
        if engine and state in ("stopped", "error") and inst.state != state:
            # Persist terminal worker state so a stale UI can never claim a
            # dead engine is live. Errors require explicit operator recovery;
            # a clean replay completion likewise must not restart on deploy.
            inst.state = state
            inst.last_error = engine.get("last_error") or engine.get("stop_reason") or ""
            inst.desired_running = False
            self.store.save(inst)
        return {**inst.to_dict(), "state": state, "engine": engine, "metrics": self.metrics(instance_id)}

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
