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


def test_open_rolls_back_both_rows_when_execution_journal_fails():
    ledger = SqliteLedger(":memory:")
    ledger._c.execute(
        "CREATE TRIGGER fail_open_execution BEFORE INSERT ON paper_executions "
        "WHEN NEW.action='OPEN' BEGIN SELECT RAISE(ABORT, 'injected execution failure'); END"
    )
    paper = PaperExecutionEngine(ledger, 10_000)
    with pytest.raises(Exception, match="injected execution failure"):
        paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
                   stop=90, target=120, alert_id="open-execution-boundary")
    assert ledger.get_positions() == []
    assert ledger.get_paper_trades() == []


def test_close_rolls_back_both_rows_when_execution_journal_fails():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
               stop=90, target=120, alert_id="open-before-close-journal")
    ledger._c.execute(
        "CREATE TRIGGER fail_close_execution BEFORE INSERT ON paper_executions "
        "WHEN NEW.action='CLOSE' BEGIN SELECT RAISE(ABORT, 'injected execution failure'); END"
    )
    with pytest.raises(Exception, match="injected execution failure"):
        paper.close(symbol="BTCUSDT", exit_price=110, execution_id="close-boundary")
    assert len(ledger.get_positions("open")) == 1
    assert ledger.get_paper_trades()[0]["status"] == "open"


def test_reduce_rolls_back_every_row_when_execution_journal_fails():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
               stop=90, target=120, alert_id="open-before-reduce-journal")
    ledger._c.execute(
        "CREATE TRIGGER fail_reduce_execution BEFORE INSERT ON paper_executions "
        "WHEN NEW.action='REDUCE' BEGIN SELECT RAISE(ABORT, 'injected execution failure'); END"
    )
    with pytest.raises(Exception, match="injected execution failure"):
        paper.reduce(symbol="BTCUSDT", exit_price=110, fraction=0.5,
                     execution_id="reduce-boundary")
    assert len(ledger.get_positions("open")) == 1
    assert ledger.get_positions("open")[0]["size"] == 1
    assert len(ledger.get_paper_trades()) == 1


def test_execution_id_is_unique_and_duplicate_open_rolls_back():
    ledger = SqliteLedger(":memory:")
    paper = PaperExecutionEngine(ledger, 10_000)
    paper.open(symbol="BTCUSDT", side="BUY", size=1, entry=100,
               stop=90, target=120, alert_id="unique-execution")
    with pytest.raises(Exception, match="UNIQUE constraint failed"):
        paper.open(symbol="ETHUSDT", side="BUY", size=1, entry=100,
                   stop=90, target=120, alert_id="unique-execution")
    assert [row["symbol"] for row in ledger.get_positions("open")] == ["BTCUSDT"]
    executions = ledger._c.execute("SELECT * FROM paper_executions").fetchall()
    assert len(executions) == 1 and executions[0]["action"] == "OPEN"


def test_orphan_position_close_fails_closed_without_mutation():
    ledger = SqliteLedger(":memory:")
    ledger.open_position(symbol="BTCUSDT", side="long", size=1, entry=100,
                         stop=90, target=120)
    paper = PaperExecutionEngine(ledger, 10_000)
    with pytest.raises(RuntimeError, match="position has no open trade"):
        paper.close(symbol="BTCUSDT", exit_price=110)
    assert len(ledger.get_positions("open")) == 1
    assert ledger.get_paper_trades() == []
