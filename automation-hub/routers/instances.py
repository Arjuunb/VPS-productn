"""Trading Instance API — isolated paper engines and instance analytics."""
from __future__ import annotations

import webhook_api as _wa
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

router = APIRouter()


class InstanceCreate(BaseModel):
    symbol: str = Field(..., min_length=3, max_length=30)
    strategy: str = Field("brain", min_length=1)
    # Omitted means use the immutable catalogue version.  Explicit historical
    # labels remain accepted for existing clients and imported records.
    strategy_version: Optional[str] = Field(default=None, min_length=1, max_length=80)
    timeframe: str = Field("5m", min_length=2, max_length=8)
    risk_per_trade_pct: float = Field(0.005, gt=0, le=0.05)
    capital_allocation: float = Field(..., gt=0)
    mode: str = Field("trading")
    sizing_mode: str = Field("auto")
    fixed_position_size: float = Field(0.0, ge=0)
    entry_mode: str = Field("limit")
    fill_model: str = Field("PerfectFill")
    max_open_positions: int = Field(3, ge=1, le=50)


class InstanceUpdate(BaseModel):
    capital_allocation: Optional[float] = Field(default=None, gt=0)
    risk_per_trade_pct: Optional[float] = Field(default=None, gt=0, le=0.05)
    sizing_mode: Optional[str] = None
    fixed_position_size: Optional[float] = Field(default=None, ge=0)
    entry_mode: Optional[str] = None
    max_open_positions: Optional[int] = Field(default=None, ge=1, le=50)
    strategy: Optional[str] = Field(default=None, min_length=1)
    strategy_version: Optional[str] = Field(default=None, min_length=1, max_length=80)
    timeframe: Optional[str] = Field(default=None, min_length=2, max_length=8)


class PlatformConfig(BaseModel):
    max_active_slots: Optional[int] = Field(default=None, ge=1, le=3)
    max_global_risk_pct: Optional[float] = Field(default=None, ge=0.001, le=1)
    max_global_daily_loss_pct: Optional[float] = Field(default=None, ge=0.001, le=1)
    paper_account_capital: Optional[float] = Field(default=None, gt=0)


def _catalog(key: str) -> dict:
    row = next((s for s in _wa._STRATEGY_CATALOG if s["key"] == key), None)
    if not row:
        raise HTTPException(400, f"Unknown strategy '{key}'")
    return row


def _manager():
    manager = _wa.instance_manager
    if not manager.store.available:
        raise HTTPException(503, manager.store.error)
    return manager


@router.get("/instances/options")
def instance_options():
    """Authoritative options for the instance-creation screen.

    The dashboard deliberately obtains these from the service rather than
    carrying its own list, so an installed strategy/version or supported market
    cannot silently diverge from what the worker can create.
    """
    from data.historical import SYMBOLS, TIMEFRAMES
    versions_by_strategy: dict[str, list[str]] = {}
    version_store = getattr(_wa, "version_store", None)
    if version_store is not None:
        for row in version_store.list():
            key = str(row.get("strategy") or "")
            label = str(row.get("label") or row.get("version") or "")
            if key and label:
                versions_by_strategy.setdefault(key, []).append(label)
    strategies = []
    for row in _wa._STRATEGY_CATALOG:
        key = row["key"]
        builtin = str(row.get("version") or "unversioned")
        strategies.append({"key": key, "label": row["label"],
                           "versions": list(dict.fromkeys([builtin, *versions_by_strategy.get(key, [])]))})
    return {
        "symbols": list(SYMBOLS), "timeframes": list(TIMEFRAMES),
        "strategies": strategies,
        # These are the presently implemented per-instance execution defaults,
        # not editable UI choices.  The API exposes them so they remain honest
        # and can become persisted options in a later additive migration.
        "execution_defaults": {"position_sizing_mode": "auto", "entry_mode": "limit",
                               "fill_model": "PerfectFill", "leverage": None,
                               "max_open_positions": 3, "max_quick_risk_pct": 0.01},
        "sizing_modes": [
            {"key": "auto", "label": "Dynamic Current Equity %", "implemented": True},
            {"key": "fixed", "label": "Fixed Quantity", "implemented": True},
            {"key": "fixed_starting_equity_pct", "label": "Fixed Starting Equity %", "implemented": False},
        ],
        "market_data_mode": "paper_forward_live_only",
    }


def _start_instance(manager, instance_id: str, *, restart: bool = False):
    """Enter instance-first execution without mixing legacy account trades."""
    if _wa.engine.running:
        _wa.engine.stop("Trading Instance started — legacy multi-pair engine disabled to preserve attribution")
        _wa.ledger.log(level="info", stage="instance",
                       message="Legacy multi-pair engine stopped before Trading Instance execution")
    return manager.restart(instance_id) if restart else manager.start(instance_id)


@router.get("/instances")
def list_instances():
    manager = _manager()
    return {"instances": manager.list(), **manager.platform_status()}


@router.post("/instances/platform")
def configure_platform(body: PlatformConfig, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _manager().configure(max_active_slots=body.max_active_slots,
                                    max_global_risk_pct=body.max_global_risk_pct,
                                    max_global_daily_loss_pct=body.max_global_daily_loss_pct,
                                    paper_account_capital=body.paper_account_capital)
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/instances/auto-select")
def auto_select_instance(x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        manager = _manager()
        # Rank before stopping legacy work, so a no-candidate response is
        # non-disruptive. Starting the selected instance remains exclusive.
        candidate = manager.best_measured_instance()
        slots = manager.platform_status()
        if slots["active_slots"] >= slots["max_active_slots"]:
            raise ValueError("Maximum active trading slots reached")
        if _wa.engine.running:
            _wa.engine.stop("Trading Instance auto-selection started — legacy multi-pair engine disabled")
        instance = manager.start(candidate.id)
        return {"selected": _manager().status(instance.id), "selection": "measured isolated performance"}
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@router.post("/instances")
def create_instance(body: InstanceCreate, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    strategy = _catalog(body.strategy)
    from data.historical import SYMBOLS, TIMEFRAMES
    if body.symbol.upper() not in SYMBOLS:
        raise HTTPException(400, f"Unsupported pair '{body.symbol.upper()}'")
    if body.timeframe not in TIMEFRAMES:
        raise HTTPException(400, f"Unsupported timeframe '{body.timeframe}'")
    inst = _manager().create(symbol=body.symbol, strategy_key=strategy["key"], strategy_label=strategy["label"],
                             strategy_version=body.strategy_version or strategy.get("version", "unversioned"), timeframe=body.timeframe,
                             risk_per_trade_pct=body.risk_per_trade_pct,
                             capital_allocation=body.capital_allocation, mode=body.mode,
                             sizing_mode=body.sizing_mode, fixed_position_size=body.fixed_position_size,
                             entry_mode=body.entry_mode, fill_model=body.fill_model,
                             max_open_positions=body.max_open_positions)
    _wa.ledger.log(level="info", stage="instance", message=f"Instance created: {inst.symbol} {inst.strategy_label} {inst.strategy_version}", symbol=inst.symbol)
    return {"instance": _manager().status(inst.id)}


@router.get("/instances/leaderboard")
def instance_leaderboard(sort: str = "realized_pnl"):
    return {"rows": _manager().leaderboard(sort), "sort": sort}


@router.get("/instances/{instance_id}")
def instance_detail(instance_id: str):
    try: return _manager().status(instance_id)
    except KeyError: raise HTTPException(404, "Trading instance not found")


@router.patch("/instances/{instance_id}")
def update_instance(instance_id: str, body: InstanceUpdate,
                    x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        strategy = _catalog(body.strategy) if body.strategy is not None else None
        if body.timeframe is not None:
            from data.historical import TIMEFRAMES
            if body.timeframe not in TIMEFRAMES:
                raise HTTPException(400, f"Unsupported timeframe '{body.timeframe}'")
        if strategy is not None and body.strategy_version is not None:
            valid_versions = next((row["versions"] for row in instance_options()["strategies"]
                                   if row["key"] == strategy["key"]), [])
            if body.strategy_version not in valid_versions:
                raise HTTPException(400, f"Unknown version '{body.strategy_version}' for {strategy['label']}")
        inst = _manager().update_configuration(
            instance_id, capital_allocation=body.capital_allocation,
            risk_per_trade_pct=body.risk_per_trade_pct, sizing_mode=body.sizing_mode,
            fixed_position_size=body.fixed_position_size, entry_mode=body.entry_mode,
            max_open_positions=body.max_open_positions,
            strategy_key=strategy["key"] if strategy else None,
            strategy_label=strategy["label"] if strategy else None,
            strategy_version=(body.strategy_version or strategy.get("version")) if strategy else None,
            timeframe=body.timeframe)
        return {"instance": _manager().status(inst.id)}
    except KeyError: raise HTTPException(404, "Trading instance not found")
    except ValueError as exc: raise HTTPException(409, str(exc))


@router.post("/instances/{instance_id}/{action}")
def instance_action(instance_id: str, action: str, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    manager = _manager()
    try:
        if action == "start": inst = _start_instance(manager, instance_id)
        elif action == "stop": inst = manager.stop(instance_id)
        elif action == "pause": inst = manager.pause(instance_id)
        elif action == "resume": inst = manager.resume(instance_id)
        elif action == "restart": inst = _start_instance(manager, instance_id, restart=True)
        else: raise HTTPException(404, "Unknown instance action")
    except KeyError: raise HTTPException(404, "Trading instance not found")
    except ValueError as exc: raise HTTPException(409, str(exc))
    return {"instance": manager.status(inst.id)}


@router.get("/instances/{instance_id}/trades")
def instance_trades(instance_id: str):
    manager = _manager()
    try: manager.status(instance_id)
    except KeyError: raise HTTPException(404, "Trading instance not found")
    return {"trades": [t for t in _wa.ledger.get_paper_trades() if t.get("instance_id") == instance_id]}


@router.get("/instances/{instance_id}/logs")
def instance_logs(instance_id: str, limit: int = 100):
    manager = _manager()
    try: manager.status(instance_id)
    except KeyError: raise HTTPException(404, "Trading instance not found")
    return {"logs": [r for r in _wa.ledger.get_logs(max(1, min(limit * 5, 500))) if r.get("instance_id") == instance_id][:limit]}
