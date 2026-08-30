"""Fault-injection coverage for position/trade transaction invariants."""
import pytest

from data.ledger import SqliteLedger
from execution.paper_engine import PaperExecutionEngine


def test_open_rolls_back_position_when_trade_insert_fails():
    ledger = SqliteLedger(":memory:")
    ledger._c.execute(
        "CREATE TRIGGER fail_trade_insert BEFORE INSERT ON paper_trades "
        "BEGIN SELECT RAISE(ABORT, 'injected trade failure'); END"
    )
    paper = PaperExecutionEngine(ledger, 10_000)
    with pytest.raises(Exception, match="injected trade failure"):
        paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
                   stop=90, target=120, alert_id="atomic-open")
    assert ledger.get_positions() == []
    assert ledger.get_paper_trades() == []


def test_close_rolls_back_position_when_trade_update_fails():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
               stop=90, target=120, alert_id="atomic-close")
    ledger._c.execute(
        "CREATE TRIGGER fail_trade_close BEFORE UPDATE ON paper_trades "
        "WHEN NEW.status='closed' BEGIN SELECT RAISE(ABORT, 'injected close failure'); END"
    )
    with pytest.raises(Exception, match="injected close failure"):
        paper.close(symbol="BTCUSDT", exit_price=110)
    assert ledger.get_positions("open")[0]["symbol"] == "BTCUSDT"
    assert ledger.get_paper_trades()[0]["status"] == "open"


def test_reduce_rolls_back_close_and_remainder_when_remainder_trade_fails():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
               stop=90, target=120, alert_id="atomic-reduce")
    ledger._c.execute(
        "CREATE TRIGGER fail_remainder_trade BEFORE INSERT ON paper_trades "
        "BEGIN SELECT RAISE(ABORT, 'injected remainder failure'); END"
    )
    with pytest.raises(Exception, match="injected remainder failure"):
        paper.reduce(symbol="BTCUSDT", exit_price=110, fraction=0.5)
    positions = ledger.get_positions("open")
    trades = ledger.get_paper_trades()
    assert len(positions) == 1 and positions[0]["size"] == 1
    assert len(trades) == 1 and trades[0]["status"] == "open" and trades[0]["size"] == 1
