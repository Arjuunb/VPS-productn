from datetime import datetime, timezone

import pytest

from bot.risk import RiskConfig, RiskManager
from bot.types import AccountSnapshot, Signal, SignalType
from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine
from services.controls import TradingControl
from services.fill_model import RealisticFill
from services.signal_pipeline import SignalPipeline
from tradexa.risk.position_sizing import (
    DYNAMIC_CURRENT_EQUITY_PERCENT, FIXED_QUANTITY,
    FIXED_STARTING_EQUITY_PERCENT, InstrumentMetadata,
    PositionSizingRequest, PositionSizingService, normalize_sizing_mode,
)


def sized(mode, *, equity=500, starting=500, risk=0.01, fixed=0,
          reinvest=True, stop=99, cap=None, floor=None, metadata=None):
    return PositionSizingService.calculate(PositionSizingRequest(
        mode=mode, entry_price=100, stop_price=stop,
        starting_equity=starting, current_realized_equity=equity,
        risk_per_trade_pct=risk, fixed_quantity=fixed,
        profit_reinvestment=reinvest, maximum_risk_amount=cap,
        minimum_equity=floor, instrument=metadata or InstrumentMetadata(),
    ))


def test_legacy_auto_is_fixed_starting_not_silent_dynamic():
    assert normalize_sizing_mode("auto") == FIXED_STARTING_EQUITY_PERCENT
    result = sized("auto", equity=900, starting=500)
    assert result.risk_basis == 500
    assert result.risk_amount == 5


def test_all_three_modes_are_deterministic():
    fixed = sized(FIXED_QUANTITY, equity=550, fixed=0.3)
    starting = sized(FIXED_STARTING_EQUITY_PERCENT, equity=550)
    dynamic = sized(DYNAMIC_CURRENT_EQUITY_PERCENT, equity=550)
    assert (fixed.quantity, fixed.risk_amount) == pytest.approx((0.3, 0.3))
    assert (starting.quantity, starting.risk_basis) == pytest.approx((5, 500))
    assert (dynamic.quantity, dynamic.risk_basis) == pytest.approx((5.5, 550))


def test_compounding_sequence_plus_2r_plus_2r_minus_1r():
    fixed_equity = dynamic_equity = 500.0
    fixed_risks, dynamic_risks = [], []
    for outcome_r in (2, 2, -1):
        fixed = sized(FIXED_STARTING_EQUITY_PERCENT, equity=fixed_equity)
        dynamic = sized(DYNAMIC_CURRENT_EQUITY_PERCENT, equity=dynamic_equity)
        fixed_risks.append(fixed.risk_amount)
        dynamic_risks.append(dynamic.risk_amount)
        fixed_equity += fixed.risk_amount * outcome_r
        dynamic_equity += dynamic.risk_amount * outcome_r
    assert fixed_risks == pytest.approx([5, 5, 5])
    assert dynamic_risks == pytest.approx([5, 5.1, 5.202])
    assert fixed_equity == pytest.approx(515)
    assert dynamic_equity == pytest.approx(514.998)


def test_reinvestment_off_freezes_profit_but_scales_losses():
    profit = sized(DYNAMIC_CURRENT_EQUITY_PERCENT, equity=550, reinvest=False)
    loss = sized(DYNAMIC_CURRENT_EQUITY_PERCENT, equity=450, reinvest=False)
    assert profit.risk_basis == 500
    assert loss.risk_basis == 450


def test_caps_floor_and_instrument_constraints_fail_closed():
    assert sized(DYNAMIC_CURRENT_EQUITY_PERCENT, equity=400, floor=450).reason == "instance equity floor reached"
    assert sized(FIXED_QUANTITY, fixed=10, cap=5).reason == "fixed quantity exceeds maximum risk amount"
    assert not sized(DYNAMIC_CURRENT_EQUITY_PERCENT, stop=100).approved
    rounded = sized(DYNAMIC_CURRENT_EQUITY_PERCENT,
                    metadata=InstrumentMetadata(quantity_step=0.3))
    assert rounded.quantity == pytest.approx(4.8)
    assert not sized(DYNAMIC_CURRENT_EQUITY_PERCENT,
                     metadata=InstrumentMetadata(maximum_quantity=4)).approved
    assert not sized(DYNAMIC_CURRENT_EQUITY_PERCENT,
                     metadata=InstrumentMetadata(minimum_notional=1_000)).approved


def test_unrealized_profit_never_changes_paper_realized_basis_and_fees_reduce_it():
    ledger = SqliteLedger(":memory:")
    fills = RealisticFill(spread_pct=0, slippage_pct=0, latency_pct=0,
                          taker_fee_pct=0.001)
    paper = PaperExecutionEngine(ledger, 500, fill_model=fills)
    paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100, stop=99,
               sizing_context={"sizing_mode": DYNAMIC_CURRENT_EQUITY_PERCENT,
                               "sizing_engine_version": "v2",
                               "risk_basis_at_entry": 500,
                               "risk_pct_at_entry": 0.01,
                               "risk_amount_at_entry": 1,
                               "equity_before_trade": 500})
    assert paper.equity({"BTCUSDT": 120}) > paper.current_realized_equity()
    assert paper.current_realized_equity() == 500
    paper.close(symbol="BTCUSDT", exit_price=110)
    trade = paper.history()[0]
    assert trade["fees"] == pytest.approx(0.21)
    assert paper.current_realized_equity() == pytest.approx(509.79)
    assert trade["equity_after_close"] == pytest.approx(509.79)
    assert trade["sizing_mode"] == DYNAMIC_CURRENT_EQUITY_PERCENT


def test_backtest_risk_manager_uses_the_shared_sizing_service():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    manager = RiskManager(RiskConfig(
        risk_per_trade_pct=0.01, sizing_mode=FIXED_STARTING_EQUITY_PERCENT,
        starting_equity=500, max_position_pct=1,
    ))
    manager.on_bar(500, now)
    signal = Signal(now, "BTCUSDT", SignalType.LONG, 100, 99, 102)
    allowed, quantity, reason = manager.evaluate(
        signal, AccountSnapshot(cash=500, equity=500), now)
    direct = sized(FIXED_STARTING_EQUITY_PERCENT)
    assert allowed, reason
    assert quantity == pytest.approx(direct.quantity)


def test_live_paper_pipeline_refreshes_realized_equity_before_every_entry():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 500)
    pipeline = SignalPipeline(
        ledger, paper, TradingControl(), equity=500,
        risk_per_trade_pct=0.01, exposure_limit_pct=1,
        max_total_exposure_pct=1, adaptive_risk=False,
        equity_throttle=False,
        position_sizing_mode=DYNAMIC_CURRENT_EQUITY_PERCENT,
        profit_reinvestment=True, equity_provider=paper.current_realized_equity,
    )
    first = pipeline.process({"alert_id": "one", "symbol": "BTCUSDT",
                              "side": "BUY", "entry": 100, "stop": 99,
                              "confidence": 1})
    assert first.accepted
    assert first.fill["size"] == pytest.approx(5)
    closed = pipeline.process({"alert_id": "close-one", "symbol": "BTCUSDT",
                               "side": "CLOSE", "entry": 102, "stop": 99})
    assert closed.accepted
    second = pipeline.process({"alert_id": "two", "symbol": "BTCUSDT",
                               "side": "BUY", "entry": 100, "stop": 99,
                               "confidence": 1})
    assert second.accepted
    assert second.fill["size"] == pytest.approx(5.1)
    open_trade = next(t for t in ledger.get_paper_trades() if t["status"] == "open")
    assert open_trade["risk_basis_at_entry"] == pytest.approx(510)
    assert open_trade["equity_before_trade"] == pytest.approx(510)
