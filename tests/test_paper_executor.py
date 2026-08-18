from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from config.charges_config import ChargesConfig
from data.database import TickStore
from data.models import Exchange, Instrument, InstrumentType, SignalDirection, TradeSignal
from execution.paper_executor import PaperExecutor

NOW = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)

INSTRUMENT = Instrument(
    token="26000", symbol="NIFTY-FUT", name="NIFTY", exchange=Exchange.NFO,
    instrument_type=InstrumentType.FUTIDX, lot_size=25, tick_size=Decimal("0.05"),
)


def make_signal(direction=SignalDirection.BUY, entry="24500", stop="24425", target="24575"):
    return TradeSignal(
        instrument_token="26000", direction=direction, timestamp=NOW, confidence=70.0, sub_scores=[],
        entry=Decimal(entry), stop_loss=Decimal(stop), targets=[Decimal(target)], risk_reward=1.0,
        reasons=["bullish confluence"],
    )


def make_executor(tmp_path, slippage_bps="0"):
    store = TickStore(tmp_path / "t.db")
    return PaperExecutor(store, ChargesConfig(), slippage_bps=Decimal(slippage_bps)), store


def test_submit_no_trade_signal_raises(tmp_path: Path):
    executor, _ = make_executor(tmp_path)
    no_trade = TradeSignal(instrument_token="26000", direction=SignalDirection.NO_TRADE, timestamp=NOW, confidence=10.0, sub_scores=[])
    with pytest.raises(ValueError):
        executor.submit_order(no_trade, INSTRUMENT, 50, NOW)


def test_submit_does_not_immediately_fill(tmp_path: Path):
    """No optimistic same-bar fill: submitting an order must not open a position."""
    executor, _ = make_executor(tmp_path)
    position_id = executor.submit_order(make_signal(), INSTRUMENT, 50, NOW)
    assert position_id not in executor.open_position_ids()
    assert position_id in executor.pending_order_ids()


def test_fill_happens_on_next_bar_open(tmp_path: Path):
    executor, _ = make_executor(tmp_path)
    position_id = executor.submit_order(make_signal(), INSTRUMENT, 50, NOW)
    bar_time = NOW + timedelta(minutes=1)
    executor.on_bar_open("26000", Decimal("24505"), bar_time)
    assert position_id in executor.open_position_ids()
    assert position_id not in executor.pending_order_ids()
    position = executor.get_position(position_id)
    assert position.entry_price == Decimal("24505")  # filled at actual bar open, not signal's intended entry


def test_buy_fill_slippage_is_worse_than_quoted_open(tmp_path: Path):
    executor, _ = make_executor(tmp_path, slippage_bps="10")  # 0.1%
    position_id = executor.submit_order(make_signal(SignalDirection.BUY), INSTRUMENT, 50, NOW)
    executor.on_bar_open("26000", Decimal("24500"), NOW + timedelta(minutes=1))
    position = executor.get_position(position_id)
    expected = Decimal("24500") + Decimal("24500") * Decimal("10") / Decimal("10000")
    assert position.entry_price == expected
    assert position.entry_price > Decimal("24500")  # buy fills WORSE (higher), never optimistic


def test_sell_fill_slippage_is_worse_than_quoted_open(tmp_path: Path):
    executor, _ = make_executor(tmp_path, slippage_bps="10")
    position_id = executor.submit_order(
        make_signal(SignalDirection.SELL, entry="24500", stop="24575", target="24425"), INSTRUMENT, 50, NOW
    )
    executor.on_bar_open("26000", Decimal("24500"), NOW + timedelta(minutes=1))
    position = executor.get_position(position_id)
    assert position.entry_price < Decimal("24500")  # sell fills WORSE (lower), never optimistic


def test_price_update_triggers_stop_loss_close_and_writes_journal(tmp_path: Path):
    executor, store = make_executor(tmp_path)
    position_id = executor.submit_order(make_signal(entry="24500", stop="24425", target="24575"), INSTRUMENT, 50, NOW)
    fill_time = NOW + timedelta(minutes=1)
    executor.on_bar_open("26000", Decimal("24500"), fill_time)
    exit_time = fill_time + timedelta(minutes=5)
    executor.on_price_update("26000", Decimal("24420"), exit_time)  # below stop loss

    assert position_id not in executor.open_position_ids()
    entries = store.get_journal_entries()
    assert len(entries) == 1
    assert entries[0].exit_reason == "stop_loss"
    assert entries[0].pnl_gross < 0


def test_price_update_triggers_target_close_and_writes_journal(tmp_path: Path):
    executor, store = make_executor(tmp_path)
    position_id = executor.submit_order(make_signal(entry="24500", stop="24425", target="24575"), INSTRUMENT, 50, NOW)
    fill_time = NOW + timedelta(minutes=1)
    executor.on_bar_open("26000", Decimal("24500"), fill_time)
    exit_time = fill_time + timedelta(minutes=5)
    executor.on_price_update("26000", Decimal("24580"), exit_time)  # above target

    entries = store.get_journal_entries()
    assert entries[0].exit_reason == "target"
    assert entries[0].pnl_gross > 0


def test_charges_reduce_net_pnl_below_gross_on_a_win(tmp_path: Path):
    executor, store = make_executor(tmp_path)
    position_id = executor.submit_order(make_signal(entry="24500", stop="24425", target="24575"), INSTRUMENT, 50, NOW)
    executor.on_bar_open("26000", Decimal("24500"), NOW + timedelta(minutes=1))
    executor.on_price_update("26000", Decimal("24580"), NOW + timedelta(minutes=5))

    entry = store.get_journal_entries()[0]
    assert entry.pnl_net < entry.pnl_gross
    assert entry.charges_total > 0
    assert entry.pnl_net == entry.pnl_gross - entry.charges_total


def test_charges_make_net_pnl_more_negative_on_a_loss(tmp_path: Path):
    executor, store = make_executor(tmp_path)
    position_id = executor.submit_order(make_signal(entry="24500", stop="24425", target="24575"), INSTRUMENT, 50, NOW)
    executor.on_bar_open("26000", Decimal("24500"), NOW + timedelta(minutes=1))
    executor.on_price_update("26000", Decimal("24420"), NOW + timedelta(minutes=5))

    entry = store.get_journal_entries()[0]
    assert entry.pnl_net < entry.pnl_gross  # net loss is WORSE than gross loss (charges added)


def test_manual_close_position(tmp_path: Path):
    executor, store = make_executor(tmp_path)
    position_id = executor.submit_order(make_signal(entry="24500", stop="24425", target="24575"), INSTRUMENT, 50, NOW)
    executor.on_bar_open("26000", Decimal("24500"), NOW + timedelta(minutes=1))
    executor.close_position(position_id, Decimal("24510"), "session_close", NOW + timedelta(hours=6))

    assert position_id not in executor.open_position_ids()
    entries = store.get_journal_entries()
    assert entries[0].exit_reason == "session_close"


def test_short_trade_full_round_trip(tmp_path: Path):
    executor, store = make_executor(tmp_path)
    signal = make_signal(SignalDirection.SELL, entry="24500", stop="24575", target="24425")
    position_id = executor.submit_order(signal, INSTRUMENT, 50, NOW)
    executor.on_bar_open("26000", Decimal("24500"), NOW + timedelta(minutes=1))
    executor.on_price_update("26000", Decimal("24420"), NOW + timedelta(minutes=5))  # price fell -> short profits

    entry = store.get_journal_entries()[0]
    assert entry.direction == SignalDirection.SELL
    assert entry.pnl_gross > 0


def test_journal_entry_carries_confidence_and_reason(tmp_path: Path):
    executor, store = make_executor(tmp_path)
    signal = make_signal()
    position_id = executor.submit_order(signal, INSTRUMENT, 50, NOW)
    executor.on_bar_open("26000", Decimal("24500"), NOW + timedelta(minutes=1))
    executor.on_price_update("26000", Decimal("24580"), NOW + timedelta(minutes=5))

    entry = store.get_journal_entries()[0]
    assert entry.confidence == 70.0
    assert "bullish confluence" in entry.entry_reason


def test_submit_without_stop_or_targets_raises(tmp_path: Path):
    executor, _ = make_executor(tmp_path)
    bad_signal = TradeSignal(
        instrument_token="26000", direction=SignalDirection.BUY, timestamp=NOW, confidence=70.0, sub_scores=[],
        entry=Decimal("24500"),  # no stop_loss, no targets
    )
    with pytest.raises(ValueError):
        executor.submit_order(bad_signal, INSTRUMENT, 50, NOW)
