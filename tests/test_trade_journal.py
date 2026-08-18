from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from data.database import TickStore
from data.models import SignalDirection, TradeJournalEntry

ENTRY_TIME = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)
EXIT_TIME = ENTRY_TIME + timedelta(minutes=15)


def make_entry(**overrides):
    defaults = dict(
        instrument_token="26000", instrument_symbol="NIFTY24AUGFUT", direction=SignalDirection.BUY,
        quantity=50, entry_price=Decimal("24500"), exit_price=Decimal("24575"),
        stop_loss=Decimal("24425"), target=Decimal("24575"), pnl_gross=Decimal("3750"),
        pnl_net=Decimal("3700.25"), charges_total=Decimal("49.75"), confidence=72.5,
        strategy="smc_confluence", entry_reason="Strong bullish confluence", exit_reason="target",
        entry_time=ENTRY_TIME, exit_time=EXIT_TIME,
    )
    defaults.update(overrides)
    return TradeJournalEntry(**defaults)


def test_insert_and_retrieve_journal_entry(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    store.insert_journal_entry(make_entry())
    entries = store.get_journal_entries()
    assert len(entries) == 1
    e = entries[0]
    assert e.instrument_symbol == "NIFTY24AUGFUT"
    assert e.pnl_net == Decimal("3700.25")
    assert e.direction == SignalDirection.BUY
    assert e.exit_reason == "target"


def test_decimal_precision_preserved_through_round_trip(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    store.insert_journal_entry(make_entry(pnl_net=Decimal("123.456789")))
    entries = store.get_journal_entries()
    assert entries[0].pnl_net == Decimal("123.456789")


def test_entries_ordered_most_recent_first(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    store.insert_journal_entry(make_entry(entry_time=ENTRY_TIME, exit_time=EXIT_TIME, instrument_symbol="FIRST"))
    later = ENTRY_TIME + timedelta(hours=1)
    store.insert_journal_entry(make_entry(entry_time=later, exit_time=later + timedelta(minutes=5), instrument_symbol="SECOND"))
    entries = store.get_journal_entries()
    assert entries[0].instrument_symbol == "SECOND"
    assert entries[1].instrument_symbol == "FIRST"


def test_get_journal_entries_respects_limit(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    for i in range(5):
        t = ENTRY_TIME + timedelta(hours=i)
        store.insert_journal_entry(make_entry(entry_time=t, exit_time=t + timedelta(minutes=5)))
    assert len(store.get_journal_entries(limit=3)) == 3


def test_empty_journal_returns_empty_list(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    assert store.get_journal_entries() == []
