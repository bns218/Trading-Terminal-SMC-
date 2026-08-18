from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from data.database import TickStore

IST_NOON = datetime(2026, 8, 18, 10, 0, tzinfo=timezone.utc)  # ~15:30 IST


def make_store(tmp_path: Path) -> TickStore:
    return TickStore(tmp_path / "test.db")


def test_wal_mode_enabled(tmp_path: Path):
    store = make_store(tmp_path)
    with store._connect() as conn:  # noqa: SLF001 test-only access
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_insert_and_latest_tick(tmp_path: Path):
    store = make_store(tmp_path)
    store.insert_tick("2885", Decimal("2500.50"), 1000, IST_NOON)
    latest = store.latest_tick_time("2885")
    assert latest == IST_NOON


def test_decimal_precision_round_trips(tmp_path: Path):
    store = make_store(tmp_path)
    store.insert_tick("2885", Decimal("2500.15"), 10, IST_NOON)
    with store._connect() as conn:  # noqa: SLF001
        row = conn.execute("SELECT ltp FROM ticks").fetchone()
    assert row[0] == "2500.15"  # stored as exact text, not lossy float


def test_upsert_candle_and_get_candles(tmp_path: Path):
    store = make_store(tmp_path)
    open_time = IST_NOON
    close_time = open_time + timedelta(minutes=1)
    store.upsert_candle("2885", "1min", open_time, close_time, Decimal("100"), Decimal("105"), Decimal("99"), Decimal("103"), 500, is_closed=True)
    candles = store.get_candles("2885", "1min")
    assert len(candles) == 1
    assert candles[0]["close"] == "103"
    assert candles[0]["is_closed"] == 1


def test_upsert_candle_overwrites_same_open_time(tmp_path: Path):
    store = make_store(tmp_path)
    open_time = IST_NOON
    close_time = open_time + timedelta(minutes=1)
    store.upsert_candle("2885", "1min", open_time, close_time, Decimal("100"), Decimal("101"), Decimal("100"), Decimal("100"), 10, is_closed=False)
    store.upsert_candle("2885", "1min", open_time, close_time, Decimal("100"), Decimal("110"), Decimal("99"), Decimal("108"), 200, is_closed=True)
    candles = store.get_candles("2885", "1min")
    assert len(candles) == 1
    assert candles[0]["high"] == "110"
    assert candles[0]["is_closed"] == 1


def test_find_gaps_detects_missing_bar(tmp_path: Path):
    store = make_store(tmp_path)
    t0 = IST_NOON
    t1 = t0 + timedelta(minutes=1)
    t3 = t0 + timedelta(minutes=3)  # bar at t2 (minute 2) is missing
    store.upsert_candle("2885", "1min", t0, t1, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1, is_closed=True)
    store.upsert_candle("2885", "1min", t3, t3 + timedelta(minutes=1), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1, is_closed=True)
    gaps = store.find_gaps("2885", "1min", expected_step_seconds=60)
    assert len(gaps) == 1
    assert gaps[0] == (t1, t3)


def test_find_gaps_no_gap_when_contiguous(tmp_path: Path):
    store = make_store(tmp_path)
    t0 = IST_NOON
    t1 = t0 + timedelta(minutes=1)
    t2 = t0 + timedelta(minutes=2)
    store.upsert_candle("2885", "1min", t0, t1, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1, is_closed=True)
    store.upsert_candle("2885", "1min", t1, t2, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1, is_closed=True)
    assert store.find_gaps("2885", "1min", expected_step_seconds=60) == []


def test_health_events_recorded_and_counted(tmp_path: Path):
    store = make_store(tmp_path)
    store.record_health_event("reconnect", "duration_seconds=5.0")
    store.record_health_event("reconnect", "duration_seconds=2.0")
    store.record_health_event("rejected_bar", "reason=stale")
    assert store.count_health_events("reconnect") == 2
    assert store.count_health_events("rejected_bar") == 1
    assert store.count_health_events("backfill") == 0


def test_health_events_since_filter(tmp_path: Path):
    store = make_store(tmp_path)
    store.record_health_event("reconnect", "old")
    since = datetime.now(timezone.utc) + timedelta(seconds=1)
    assert store.count_health_events("reconnect", since=since) == 0
