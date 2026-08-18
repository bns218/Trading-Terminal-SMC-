from datetime import datetime, timezone
from decimal import Decimal

from data.models import Exchange, Instrument, InstrumentType, SignalDirection
from engine.position_manager import PositionManager

NOW = datetime(2026, 8, 18, 4, 0, tzinfo=timezone.utc)

INSTRUMENT = Instrument(
    token="26000", symbol="NIFTY-FUT", name="NIFTY", exchange=Exchange.NFO,
    instrument_type=InstrumentType.FUTIDX, lot_size=25, tick_size=Decimal("0.05"),
)


def test_submit_creates_pending_order():
    pm = PositionManager()
    position_id = pm.submit(
        INSTRUMENT, SignalDirection.BUY, 50, Decimal("24425"), Decimal("24575"),
        "smc_confluence", "bullish BOS", 70.0, NOW,
    )
    assert position_id in pm.pending_order_ids()
    assert pm.open_position_ids() == []


def test_fill_moves_pending_to_open():
    pm = PositionManager()
    position_id = pm.submit(INSTRUMENT, SignalDirection.BUY, 50, Decimal("24425"), Decimal("24575"), "s", "r", 70.0, NOW)
    position = pm.fill(position_id, Decimal("24510"), NOW)
    assert position.entry_price == Decimal("24510")  # filled at actual fill price, not the signal's intended entry
    assert position_id not in pm.pending_order_ids()
    assert position_id in pm.open_position_ids()


def test_pending_for_instrument_filters_correctly():
    pm = PositionManager()
    other = Instrument(token="99", symbol="X", name="X", exchange=Exchange.NFO, instrument_type=InstrumentType.FUTIDX, lot_size=1, tick_size=Decimal("0.05"))
    pm.submit(INSTRUMENT, SignalDirection.BUY, 50, Decimal("24425"), Decimal("24575"), "s", "r", 70.0, NOW)
    pm.submit(other, SignalDirection.BUY, 1, Decimal("1"), Decimal("2"), "s", "r", 70.0, NOW)
    assert len(pm.pending_for_instrument("26000")) == 1
    assert len(pm.pending_for_instrument("99")) == 1
    assert len(pm.pending_for_instrument("nonexistent")) == 0


def test_update_price_computes_unrealized_pnl_long():
    pm = PositionManager()
    position_id = pm.submit(INSTRUMENT, SignalDirection.BUY, 50, Decimal("24425"), Decimal("24575"), "s", "r", 70.0, NOW)
    position = pm.fill(position_id, Decimal("24500"), NOW)
    position.update_price(Decimal("24550"))
    assert position.unrealized_pnl == Decimal("50") * 50  # (24550-24500)*50


def test_update_price_computes_unrealized_pnl_short():
    pm = PositionManager()
    position_id = pm.submit(INSTRUMENT, SignalDirection.SELL, 50, Decimal("24575"), Decimal("24425"), "s", "r", 70.0, NOW)
    position = pm.fill(position_id, Decimal("24500"), NOW)
    position.update_price(Decimal("24450"))
    assert position.unrealized_pnl == Decimal("50") * 50  # short profits as price falls


def test_hit_stop_loss_and_target_long():
    pm = PositionManager()
    position_id = pm.submit(INSTRUMENT, SignalDirection.BUY, 50, Decimal("24425"), Decimal("24575"), "s", "r", 70.0, NOW)
    position = pm.fill(position_id, Decimal("24500"), NOW)
    assert position.hit_stop_loss(Decimal("24420")) is True
    assert position.hit_stop_loss(Decimal("24450")) is False
    assert position.hit_target(Decimal("24580")) is True
    assert position.hit_target(Decimal("24560")) is False


def test_hit_stop_loss_and_target_short():
    pm = PositionManager()
    position_id = pm.submit(INSTRUMENT, SignalDirection.SELL, 50, Decimal("24575"), Decimal("24425"), "s", "r", 70.0, NOW)
    position = pm.fill(position_id, Decimal("24500"), NOW)
    assert position.hit_stop_loss(Decimal("24580")) is True
    assert position.hit_target(Decimal("24420")) is True
    assert position.hit_target(Decimal("24450")) is False


def test_close_removes_from_open():
    pm = PositionManager()
    position_id = pm.submit(INSTRUMENT, SignalDirection.BUY, 50, Decimal("24425"), Decimal("24575"), "s", "r", 70.0, NOW)
    pm.fill(position_id, Decimal("24500"), NOW)
    closed = pm.close(position_id)
    assert closed.position_id == position_id
    assert position_id not in pm.open_position_ids()
