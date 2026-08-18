from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from data.database import TickStore
from ingestion.candle_builder import CandleBuilder

T0 = datetime(2026, 8, 18, 10, 0, 5, tzinfo=timezone.utc)  # 5s into a minute bucket


def test_first_tick_opens_candle(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    builder = CandleBuilder(store)
    builder.on_tick("2885", Decimal("100"), 1000, T0)
    candles = store.get_candles("2885", "1min")
    assert len(candles) == 1
    assert candles[0]["open"] == "100"
    assert candles[0]["close"] == "100"
    assert candles[0]["is_closed"] == 0


def test_ticks_within_same_minute_update_high_low_close(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    builder = CandleBuilder(store)
    builder.on_tick("2885", Decimal("100"), 1000, T0)
    builder.on_tick("2885", Decimal("105"), 1010, T0 + timedelta(seconds=10))
    builder.on_tick("2885", Decimal("98"), 1020, T0 + timedelta(seconds=20))
    builder.on_tick("2885", Decimal("102"), 1030, T0 + timedelta(seconds=30))
    candles = store.get_candles("2885", "1min")
    assert len(candles) == 1
    c = candles[0]
    assert c["open"] == "100"
    assert c["high"] == "105"
    assert c["low"] == "98"
    assert c["close"] == "102"
    assert c["volume"] == 30  # 1030 - 1000


def test_tick_in_new_minute_closes_previous_candle(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    builder = CandleBuilder(store)
    builder.on_tick("2885", Decimal("100"), 1000, T0)
    next_minute = T0 + timedelta(minutes=1)
    builder.on_tick("2885", Decimal("110"), 1050, next_minute)

    candles = store.get_candles("2885", "1min")
    assert len(candles) == 2
    assert candles[0]["is_closed"] == 1
    assert candles[0]["close"] == "100"
    assert candles[1]["is_closed"] == 0
    assert candles[1]["open"] == "110"


def test_flush_all_closes_in_progress_candles(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    builder = CandleBuilder(store)
    builder.on_tick("2885", Decimal("100"), 1000, T0)
    builder.flush_all()
    candles = store.get_candles("2885", "1min")
    assert candles[-1]["is_closed"] == 1


def test_multiple_instruments_tracked_independently(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    builder = CandleBuilder(store)
    builder.on_tick("2885", Decimal("100"), 1000, T0)
    builder.on_tick("99926017", Decimal("15.5"), None, T0)
    assert len(store.get_candles("2885", "1min")) == 1
    assert len(store.get_candles("99926017", "1min")) == 1


def test_volume_none_when_cumulative_volume_missing(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    builder = CandleBuilder(store)
    builder.on_tick("99926017", Decimal("15.5"), None, T0)  # e.g. index has no volume
    candles = store.get_candles("99926017", "1min")
    assert candles[0]["volume"] == 0
