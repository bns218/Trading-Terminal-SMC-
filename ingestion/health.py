"""Data-health metrics exposed to the dashboard: last tick age, reconnect
count, rejected-bar count, backfill count."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from config.market_calendar import MarketCalendar
from data.database import TickStore
from data.validation import is_stale


@dataclass
class DataHealth:
    instrument_token: str
    last_tick_age_seconds: Optional[float]
    is_stale: bool
    reconnect_count_24h: int
    disconnect_count_24h: int
    rejected_bar_count_24h: int
    backfill_count_24h: int


def get_data_health(
    store: TickStore,
    calendar: MarketCalendar,
    instrument_token: str,
    max_staleness_seconds: int = 30,
    now: Optional[datetime] = None,
) -> DataHealth:
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=24)

    last_tick_ts = store.latest_tick_time(instrument_token)
    age = (now - last_tick_ts).total_seconds() if last_tick_ts is not None else None
    stale = is_stale(last_tick_ts, now, calendar, max_staleness_seconds)

    return DataHealth(
        instrument_token=instrument_token,
        last_tick_age_seconds=age,
        is_stale=stale,
        reconnect_count_24h=store.count_health_events("reconnect", since),
        disconnect_count_24h=store.count_health_events("disconnect", since),
        rejected_bar_count_24h=store.count_health_events("rejected_bar", since),
        backfill_count_24h=store.count_health_events("backfill", since),
    )
