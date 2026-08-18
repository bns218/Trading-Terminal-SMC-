from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from config.market_calendar import MarketCalendar
from config.settings import get_settings
from data.database import TickStore
from ingestion.health import get_data_health

MID_SESSION_UTC = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)  # 11:30 IST


def test_health_no_ticks_yet(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    calendar = MarketCalendar(get_settings().holidays_file)
    health = get_data_health(store, calendar, "2885", max_staleness_seconds=30, now=MID_SESSION_UTC)
    assert health.last_tick_age_seconds is None
    assert health.is_stale is True  # no ticks during session = stale


def test_health_fresh_tick_not_stale(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    calendar = MarketCalendar(get_settings().holidays_file)
    store.insert_tick("2885", Decimal("100"), 10, MID_SESSION_UTC)
    now = MID_SESSION_UTC + timedelta(seconds=5)
    health = get_data_health(store, calendar, "2885", max_staleness_seconds=30, now=now)
    assert health.last_tick_age_seconds == 5
    assert health.is_stale is False


def test_health_counts_events(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    calendar = MarketCalendar(get_settings().holidays_file)
    store.record_health_event("reconnect")
    store.record_health_event("rejected_bar")
    store.record_health_event("rejected_bar")
    store.record_health_event("backfill")
    health = get_data_health(store, calendar, "2885", now=MID_SESSION_UTC)
    assert health.reconnect_count_24h == 1
    assert health.rejected_bar_count_24h == 2
    assert health.backfill_count_24h == 1
