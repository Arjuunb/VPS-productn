"""Price Action Visual Lab API — public data, replay, and isolated paper only."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import webhook_api as _wa
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from data.market_data_v2 import TIMEFRAMES
from services.native_price_action import RESEARCH_ID, STRATEGIES, PriceActionConfig
from services.price_action_lab import PaperExecutionConfig, replay_state
from services.price_action_research import controlled_pa_smc_report
from services.price_action_reference_study import run_reference_study
from services.research_funding import HistoricalFundingSeries
from services.smc_research_adapter import FrozenSMCNormalizationAdapter

router = APIRouter(prefix="/research/price-action", tags=["research-price-action"])


def _bad(exc: Exception, status: int = 400):
    raise HTTPException(status, str(exc)) from exc


def _utc_ms(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    except ValueError as exc:
        raise HTTPException(422, "timestamp must be ISO-8601 UTC") from exc


def _cached_funding(datasets: dict[tuple[str, str], list], *, disabled: bool = False):
    by_symbol = {}
    for (symbol, _timeframe), rows in datasets.items():
        if rows:
            by_symbol.setdefault(symbol, []).extend((rows[0].timestamp, rows[-1].timestamp))
    result = {}
    for symbol, stamps in by_symbol.items():
        start, end = min(stamps), max(stamps)
        records = _wa.v2_market_data.funding_history(
            symbol, start_ms=int(start.timestamp() * 1000), end_ms=int(end.timestamp() * 1000))
        result[symbol] = HistoricalFundingSeries.build(
            symbol, records, requested_start=start, requested_end=end,
            intentionally_disabled=disabled)
    return result


@router.get("/manifest")
def manifest():
    engine_source = Path(__file__).resolve().parents[1] / "services" / "native_price_action.py"
    return {
        "research_id": RESEARCH_ID,
        "status": "RESEARCH_ONLY",
        "version": "1.0.0",
        "native_engine_sha256": hashlib.sha256(engine_source.read_bytes()).hexdigest(),
        "strategies": list(STRATEGIES),
        "venue": "Binance USDⓈ-M Futures",
        "market_data": "public OHLCV",
        "signal_inputs": ["open", "high", "low", "close"],
        "volume_preserved_for_display": True,
        "volume_used_for_signals": False,
        "historical_and_live_share_engine": True,
        "automatic_strategy_mode": "SIGNALS_ONLY",
        "paper_account_isolated": True,
        "real_order_path": False,
        "live_execution_allowed": False,
        "baseline": {
            "swing_sensitivity": "3 completed candles per side",
            "entry": "stop beyond dominance/rejection extreme",
            "pending_expiry_bars": 3,
            "stop": "beyond full rejection extreme",
            "target_r": 2.5,
            "intrabar_ambiguity": "adverse stop first",
            "costs": "commission and adverse slippage",
        },
    }


@router.get("/contracts")
def contracts(q: str = "", limit: int = Query(250, ge=1, le=1000)):
    try:
        rows = _wa.v2_market_data.crypto_perpetuals()
    except Exception as exc:
        _bad(exc, 503)
    query = q.upper().strip()
    filtered = [row for row in rows if not query or query in row]
    return {"exchange": "Binance USDⓈ-M Futures", "contracts": filtered[:limit],
            "timeframes": list(TIMEFRAMES), "real_execution_allowed": False}


@router.get("/contracts/{symbol}")
def contract_rules(symbol: str):
    try:
        return {**_wa.v2_market_data.usdm_contract_rules(symbol),
                "exchange": "Binance USDⓈ-M Futures", "real_execution_allowed": False}
    except (ValueError, RuntimeError) as exc:
        _bad(exc, 503)


@router.get("/live-chart")
def live_chart(symbol: str = "BTCUSDT", timeframe: str = "5m",
               window: int = Query(800, ge=50, le=1500),
               visible: int = Query(400, ge=50, le=1500),
               request_id: Optional[str] = Query(default=None, max_length=100)):
    try:
        return _wa.price_action_runtime.live_state(
            symbol, timeframe, visible=visible, request_id=request_id)
    except (ValueError, RuntimeError) as exc:
        _bad(exc, 503)


@router.get("/replay")
def replay(symbol: str = "BTCUSDT", timeframe: str = "5m",
           cursor: int = Query(200, ge=1), limit: int = Query(1000, ge=50, le=3000)):
    try:
        return replay_state(_wa.v2_market_data, symbol, timeframe, cursor=cursor, limit=limit)
    except (ValueError, RuntimeError) as exc:
        _bad(exc, 409)


@router.post("/sessions/current/replay/step")
def step_replay(symbol: str = "BTCUSDT", timeframe: str = "5m",
                cursor: int = Query(200, ge=1), limit: int = Query(3000, ge=50, le=10_000),
                x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_runtime.replay_step(symbol, timeframe, cursor, limit=limit)
    except (ValueError, RuntimeError) as exc:
        _bad(exc, 409)


def _paper_state() -> dict:
    marks = {}
    for position in _wa.price_action_paper.broker.positions():
        try:
            latest = _wa.v2_market_data.public_usdm_window(position["symbol"], "1m", limit=50)[-1]
            marks[position["symbol"]] = latest.close
        except Exception:
            marks[position["symbol"]] = position["entry_price"]
    return _wa.price_action_paper.state(marks)


@router.get("/paper")
def paper_account():
    return _paper_state()


@router.get("/paper/export")
def export_paper_account():
    return {"exported_at": datetime.now(timezone.utc).isoformat(),
            "research_id": RESEARCH_ID, "format_version": "1.0",
            "data": _wa.price_action_paper.export_session(), "real_execution_allowed": False}


@router.get("/sessions")
def list_sessions():
    return {"sessions": _wa.price_action_paper.sessions(), "real_execution_allowed": False}


class SessionStartBody(BaseModel):
    mode: str = "LIVE_PAPER"
    symbol: str = "BTCUSDT"
    timeframe: str = "5m"
    starting_balance: float = Field(default=10_000, gt=0)
    operating_mode: str = "signals_only"
    strategy_id: str = "PA1_SR_REJECTION"
    risk_pct: float = Field(default=.5, gt=0, le=5)
    swing_sensitivity: int = Field(default=3, ge=2, le=5)
    entry_model: str = "confirmation"
    stop_model: str = "rejection_extreme"
    first_touch_only: bool = False
    zone_timeframe_scope: str = "same_timeframe"


def _execution(body) -> PaperExecutionConfig:
    return PaperExecutionConfig(operating_mode=body.operating_mode,
                                strategy_id=body.strategy_id, risk_pct=body.risk_pct).validated()


def _strategy_config(body, *, symbol: str, timeframe: str) -> dict:
    config = {
        "swing_left": body.swing_sensitivity,
        "swing_right": body.swing_sensitivity,
        "entry_model": body.entry_model,
        "stop_model": body.stop_model,
        "first_touch_only": body.first_touch_only,
        "zone_timeframe_scope": body.zone_timeframe_scope,
    }
    # Constructing the native engine is the canonical configuration validator.
    # Do it before persistence so a malformed session can never be saved and
    # fail only when the stream or replay later starts.
    from services.native_price_action import NativePriceActionEngine
    NativePriceActionEngine(PriceActionConfig(symbol=symbol, timeframe=timeframe, **config))
    return config


@router.post("/sessions")
def start_session(body: SessionStartBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        strategy_config = _strategy_config(body, symbol=body.symbol, timeframe=body.timeframe)
        return _wa.price_action_paper.start(mode=body.mode, symbol=body.symbol,
                                            timeframe=body.timeframe,
                                            starting_balance=body.starting_balance,
                                            execution_config=_execution(body),
                                            strategy_config=strategy_config)
    except ValueError as exc:
        _bad(exc)


class SessionConfigBody(BaseModel):
    mode: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    replay_cursor: Optional[int] = Field(default=None, ge=0)
    operating_mode: str = "signals_only"
    strategy_id: str = "PA1_SR_REJECTION"
    risk_pct: float = Field(default=.5, gt=0, le=5)
    swing_sensitivity: int = Field(default=3, ge=2, le=5)
    entry_model: str = "confirmation"
    stop_model: str = "rejection_extreme"
    first_touch_only: bool = False
    zone_timeframe_scope: str = "same_timeframe"


@router.post("/sessions/current/configuration")
def configure_session(body: SessionConfigBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        current = _wa.price_action_paper.session()
        symbol = body.symbol or current["symbol"]
        timeframe = body.timeframe or current["timeframe"]
        strategy_config = _strategy_config(body, symbol=symbol, timeframe=timeframe)
        return _wa.price_action_paper.configure(mode=body.mode, symbol=body.symbol,
                                                timeframe=body.timeframe,
                                                replay_cursor=body.replay_cursor,
                                                execution_config=_execution(body),
                                                strategy_config=strategy_config)
    except ValueError as exc:
        _bad(exc)


@router.post("/sessions/{session_id}/resume")
def resume_session(session_id: str, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_paper.resume(session_id)
    except (KeyError, ValueError) as exc:
        _bad(exc, 409)


@router.post("/sessions/{session_id}/duplicate")
def duplicate_session(session_id: str, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_paper.duplicate(session_id)
    except (KeyError, ValueError) as exc:
        _bad(exc, 409)


@router.post("/sessions/current/end")
def end_session(x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_paper.end()
    except ValueError as exc:
        _bad(exc, 409)


@router.get("/sessions/{session_id}/export")
def export_session(session_id: str):
    try:
        return _wa.price_action_paper.export_session(session_id)
    except KeyError as exc:
        _bad(exc, 404)


@router.post("/paper/candidates/{proposal_id}/approve")
def approve_candidate(proposal_id: str, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_paper.approve_candidate(proposal_id)
    except (KeyError, ValueError) as exc:
        _bad(exc, 409)


class PaperOrderBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    symbol: str
    side: str
    order_type: str = Field(alias="type")
    quantity: float = Field(gt=0)
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trailing_offset: Optional[float] = None
    reduce_only: bool = False


def _multiple(value: float, step: float) -> bool:
    if step <= 0:
        return True
    return abs(value / step - round(value / step)) <= 1e-7


@router.post("/paper/orders")
def create_paper_order(body: PaperOrderBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        rules = _wa.v2_market_data.usdm_contract_rules(body.symbol)
        if body.quantity < rules["min_quantity"] or (rules["max_quantity"] and body.quantity > rules["max_quantity"]):
            raise ValueError(f"quantity must be between {rules['min_quantity']} and {rules['max_quantity']}")
        if not _multiple(body.quantity, rules["quantity_step"]):
            raise ValueError(f"quantity must follow Binance step size {rules['quantity_step']}")
        price = body.limit_price or body.stop_price
        if price is not None and not _multiple(price, rules["tick_size"]):
            raise ValueError(f"price must follow Binance tick size {rules['tick_size']}")
        reference = price or _wa.v2_market_data.public_usdm_quote(body.symbol)["mark"]
        if body.quantity * reference < rules["min_notional"]:
            raise ValueError(f"order notional must be at least {rules['min_notional']} USDT")
        return _wa.price_action_paper.broker.submit(
            symbol=body.symbol, side=body.side, order_type=body.order_type,
            quantity=body.quantity, limit_price=body.limit_price,
            stop_price=body.stop_price, trailing_offset=body.trailing_offset,
            reduce_only=body.reduce_only, market_open=True,
        )
    except ValueError as exc:
        _bad(exc)


class LeverageBody(BaseModel):
    leverage: float = Field(ge=1, le=20)


@router.post("/paper/leverage")
def set_paper_leverage(body: LeverageBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_paper.set_leverage(body.leverage)
    except ValueError as exc:
        _bad(exc)


@router.post("/paper/orders/{order_id}/cancel")
def cancel_paper_order(order_id: str, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_paper.broker.cancel(order_id)
    except (KeyError, ValueError) as exc:
        _bad(exc)


@router.post("/paper/orders/reconcile")
def reconcile_paper_orders(x_webhook_secret: Optional[str] = Header(default=None)):
    """Reconcile strategy-generated paper orders without touching manual orders."""
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_runtime.reconcile_paper_orders()
    except (KeyError, ValueError, RuntimeError) as exc:
        _bad(exc, 409)


@router.post("/paper/process/{symbol}")
def process_paper(symbol: str, timeframe: str = "5m",
                  x_webhook_secret: Optional[str] = Header(default=None)):
    """Advance the isolated ledger using a provider candle, never a client price."""
    _wa._check_secret(x_webhook_secret)
    try:
        raw = _wa.v2_market_data.public_usdm_window(symbol, timeframe, limit=50)
        if not raw:
            raise RuntimeError("Binance returned no candle")
        result = _wa.price_action_paper.broker.process_candle(symbol, raw[-1])
        quote = _wa.v2_market_data.public_usdm_quote(symbol)
        funding = _wa.price_action_paper.apply_funding_once(
            symbol=symbol, funding_time=quote["last_funding_time"],
            rate=quote["funding_rate"], mark_price=quote["mark"],
        )
        return {**result, "funding": funding, "execution_mode": "PAPER",
                "real_execution_allowed": False}
    except (ValueError, RuntimeError) as exc:
        _bad(exc, 503)


class ProtectionBody(BaseModel):
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    trailing_offset: Optional[float] = None


@router.post("/paper/positions/{symbol}/protection")
def protect_paper_position(symbol: str, body: ProtectionBody,
                           x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.price_action_paper.broker.set_protection(
            symbol, stop_loss=body.stop_loss, take_profit=body.take_profit,
            trailing_offset=body.trailing_offset,
        )
    except ValueError as exc:
        _bad(exc)


class ResetBody(BaseModel):
    confirmation: str


@router.post("/paper/reset")
def reset_paper_account(body: ResetBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    if body.confirmation != "RESET PRICE ACTION PAPER":
        raise HTTPException(422, "confirmation must exactly match RESET PRICE ACTION PAPER")
    return _wa.price_action_paper.reset()


@router.get("/experiments")
def list_experiments():
    return {"experiments": _wa.price_action_experiments.list(),
            "research_only": True, "real_execution_allowed": False}


@router.get("/experiments/{experiment_id}")
def get_experiment(experiment_id: str):
    try:
        return _wa.price_action_experiments.get(experiment_id)
    except KeyError as exc:
        _bad(exc, 404)


class ExperimentBody(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT"])
    timeframes: list[str] = Field(default_factory=lambda: ["5m"])
    bars: int = Field(default=1500, ge=100, le=10_000)
    swing_sensitivity: int = Field(default=3, ge=2, le=5)
    entry_model: str = "confirmation"
    stop_model: str = "rejection_extreme"
    commission_bps: float = Field(default=4, ge=0, le=100)
    spread_bps: float = Field(default=2, ge=0, le=100)
    slippage_bps: float = Field(default=3, ge=0, le=100)
    funding_intentionally_disabled: bool = False
    walk_forward_folds: int = Field(default=4, ge=1, le=20)
    cost_multipliers: list[float] = Field(default_factory=lambda: [1, 1.5, 2])


@router.post("/experiments/run")
def run_experiment(body: ExperimentBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    if body.entry_model not in {"confirmation", "close", "retracement_50"}:
        raise HTTPException(422, "entry_model must be confirmation, close or retracement_50")
    if body.stop_model not in {"rejection_extreme", "pattern", "structural_zone"}:
        raise HTTPException(422, "stop_model must be rejection_extreme, pattern or structural_zone")
    datasets = {}
    try:
        for symbol in body.symbols:
            for timeframe in body.timeframes:
                rows = _wa.v2_market_data.bars(symbol, timeframe, limit=body.bars)
                if not rows:
                    raise ValueError(f"verified cache is unavailable for {symbol} {timeframe}")
                datasets[(symbol.upper(), timeframe)] = rows
        config = PriceActionConfig(
            symbol=body.symbols[0].upper(), timeframe=body.timeframes[0],
            swing_left=body.swing_sensitivity, swing_right=body.swing_sensitivity,
            entry_model=body.entry_model, stop_model=body.stop_model,
            commission_bps=body.commission_bps, spread_bps=body.spread_bps,
            slippage_bps=body.slippage_bps,
        )
        return _wa.price_action_research.run(
            datasets, config, walk_forward_folds=body.walk_forward_folds,
            cost_multipliers=body.cost_multipliers,
            funding_series=_cached_funding(
                datasets, disabled=body.funding_intentionally_disabled),
            funding_intentionally_disabled=body.funding_intentionally_disabled)
    except ValueError as exc:
        _bad(exc, 409)


class ComparisonBody(BaseModel):
    price_action: dict
    smc: dict


@router.post("/experiments/compare-smc")
def compare_smc(body: ComparisonBody, x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return controlled_pa_smc_report(body.price_action, body.smc)
    except ValueError as exc:
        _bad(exc, 409)


class FundingDownloadBody(BaseModel):
    symbol: str
    start: str
    end: str


@router.post("/funding/download")
def download_funding(body: FundingDownloadBody,
                     x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    try:
        return _wa.v2_market_data.download_usdm_funding_history(
            body.symbol, start_ms=_utc_ms(body.start), end_ms=_utc_ms(body.end))
    except (ValueError, RuntimeError) as exc:
        _bad(exc, 503)


@router.get("/funding/{symbol}")
def funding_coverage(symbol: str, start: str, end: str):
    return _wa.v2_market_data.funding_status(
        symbol, start_ms=_utc_ms(start), end_ms=_utc_ms(end))


class SMCNormalizeBody(BaseModel):
    source: dict
    assumptions: dict
    experiment_configuration: dict = Field(default_factory=dict)


@router.post("/smc-normalization")
def normalize_smc(body: SMCNormalizeBody,
                  x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    return FrozenSMCNormalizationAdapter().normalize(
        body.source, assumptions=body.assumptions,
        experiment_configuration=body.experiment_configuration)


class ReferenceStudyBody(BaseModel):
    bars: int = Field(default=3000, ge=500, le=10_000)


@router.post("/reference-study")
def reference_study(body: ReferenceStudyBody,
                    x_webhook_secret: Optional[str] = Header(default=None)):
    _wa._check_secret(x_webhook_secret)
    from services.price_action_reference_study import REFERENCE_TIMEFRAMES, REFERENCE_UNIVERSE
    datasets = {}
    try:
        for symbol in REFERENCE_UNIVERSE:
            for timeframe in REFERENCE_TIMEFRAMES:
                rows = _wa.v2_market_data.bars(symbol, timeframe, limit=body.bars)
                if len(rows) < body.bars:
                    raise ValueError(
                        f"verified cache has {len(rows)}/{body.bars} candles for {symbol} {timeframe}")
                datasets[(symbol, timeframe)] = rows
        return run_reference_study(
            _wa.price_action_research, datasets, _cached_funding(datasets), save=True)
    except ValueError as exc:
        _bad(exc, 409)


@router.get("/research-artifacts")
def list_research_artifacts():
    return {"artifacts": _wa.price_action_experiments.list_artifacts(),
            "research_only": True, "real_execution_allowed": False}


@router.get("/research-artifacts/{artifact_id}")
def get_research_artifact(artifact_id: str):
    try:
        return _wa.price_action_experiments.get_artifact(artifact_id)
    except KeyError as exc:
        _bad(exc, 404)
