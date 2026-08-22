from data.journal_store import JournalStore
from data.ledger import SqliteLedger
from services.decision_journal import DecisionJournal
from services.trading_instances import InstanceLedger


def _entry(store, trade_id, *, instance_id="i-1", execution="paper",
           data_mode="live", symbol="BTCUSDT", strategy_id="smc",
           timeframe="5m"):
    journal = DecisionJournal(store)
    journal.record_entry(
        trade_id=trade_id, position_id=f"p-{trade_id}", mode=data_mode,
        symbol=symbol, side="long", strategy="Smart Money Concepts",
        timeframe=timeframe, entry=100, stop=95, target=110, size=1,
        equity=1000, confidence=0.8, brain_score=70, regime="trend",
        steps=[], payload={"journal_execution": {
            "instance_id": instance_id,
            "instance_name": f"{symbol} SMC {timeframe} #01",
            "strategy_id": strategy_id,
            "strategy_name": "Smart Money Concepts",
            "strategy_version": "1.2.3",
            "execution_mode": execution,
            "market_data_mode": data_mode,
            "market_data_source": "live:binance-usdm",
            "exchange": "binance",
        }},
    )
    return store.get(trade_id)


def test_paper_instance_with_live_market_data_stays_paper():
    row = _entry(JournalStore(":memory:"), "paper-live")
    assert row["execution_mode"] == "paper"
    assert row["market_data_mode"] == "live"
    assert row["market_data_source"] == "live:binance-usdm"


def test_live_instance_with_live_market_data_stays_live():
    row = _entry(JournalStore(":memory:"), "live-live", execution="live")
    assert row["execution_mode"] == "live"
    assert row["market_data_mode"] == "live"


def test_replay_market_data_is_preserved_separately():
    row = _entry(JournalStore(":memory:"), "replay", data_mode="replay")
    assert row["execution_mode"] == "paper"
    assert row["market_data_mode"] == "replay"


def test_legacy_ambiguous_record_is_unverified_without_fake_instance():
    store = JournalStore(":memory:")
    store.record_entry({"trade_id": "legacy", "mode": "live", "symbol": "BTCUSDT",
                        "strategy": "Legacy", "sections": {}})
    row = store.get("legacy")
    assert row["execution_mode"] == "LEGACY / UNVERIFIED"
    assert row["instance_id"] is None


def test_two_btc_instances_and_trade_ids_remain_isolated():
    store = JournalStore(":memory:")
    first = _entry(store, "trade-a", instance_id="instance-a")
    second = _entry(store, "trade-b", instance_id="instance-b")
    assert first["trade_id"] != second["trade_id"]
    assert [r["trade_id"] for r in store.list(instance_id="instance-a")] == ["trade-a"]
    assert [r["trade_id"] for r in store.list(instance_id="instance-b")] == ["trade-b"]


def test_journal_filters_preserve_instance_execution_strategy_symbol_timeframe():
    store = JournalStore(":memory:")
    _entry(store, "match", instance_id="wanted")
    _entry(store, "other", instance_id="other", symbol="ETHUSDT", timeframe="15m")
    rows = store.list(instance_id="wanted", mode="paper", strategy="smc",
                      symbol="BTCUSDT", timeframe="5m")
    assert [row["trade_id"] for row in rows] == ["match"]


def test_one_instance_cannot_close_or_update_another_instances_records():
    ledger = SqliteLedger(":memory:")
    owner = InstanceLedger(ledger, "owner")
    attacker = InstanceLedger(ledger, "attacker")
    position_id = owner.open_position(symbol="BTCUSDT", side="long", size=1,
                                      entry=100, stop=95)
    trade_id = owner.record_paper_trade({"symbol": "BTCUSDT", "side": "long",
                                         "size": 1, "entry": 100})
    assert attacker.update_position_management(symbol="BTCUSDT", stop=99) == 0
    assert attacker.close_position(position_id, exit_price=110, pnl=10) == 0
    assert attacker.close_paper_trade(trade_id, exit_price=110, pnl=10, rr=2) == 0
    assert owner.get_positions("open")[0]["stop"] == 95
    assert owner.get_paper_trades()[0]["status"] == "open"


def test_journal_api_shape_preserves_canonical_provenance_fields():
    row = _entry(JournalStore(":memory:"), "api-shape")
    fields = {"instance_id", "instance_name", "strategy_id", "strategy_name",
              "strategy_version", "symbol", "timeframe", "execution_mode",
              "market_data_mode", "market_data_source", "exchange", "trade_id",
              "position_id", "created_at", "closed_at"}
    assert fields <= row.keys()
