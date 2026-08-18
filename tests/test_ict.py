from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from config.ict_settings import active_kill_zones
from strategies.ict import detect_displacement, displacement_in_kill_zone, tag_kill_zones


def bars_to_df(bars, start):
    n = len(bars)
    return pd.DataFrame(
        {
            "open_time": [start + timedelta(minutes=i) for i in range(n)],
            "close_time": [start + timedelta(minutes=i + 1) for i in range(n)],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [100] * n,
        }
    )


# --- Kill zone DST correctness ---
# Same UTC wall-clock instant (11:30) on an EDT date (August, UTC-4) vs an EST
# date (January, UTC-5) must land in different NY local times (07:30 vs
# 06:30) — proving the conversion goes through zoneinfo per-timestamp rather
# than a single fixed IST/UTC offset baked in once.

def test_kill_zone_dst_summer_active():
    ts = datetime(2026, 8, 18, 11, 30, tzinfo=timezone.utc)  # 07:30 EDT -> inside 07:00-10:00
    assert "new_york_am" in active_kill_zones(ts)


def test_kill_zone_dst_winter_not_yet_active():
    ts = datetime(2026, 1, 18, 11, 30, tzinfo=timezone.utc)  # 06:30 EST -> before 07:00
    assert "new_york_am" not in active_kill_zones(ts)


def test_kill_zone_midnight_crossing_asian_session():
    ts = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)  # 21:00 EDT (Aug 18 local) -> inside 20:00-00:00
    assert "asian" in active_kill_zones(ts)


def test_kill_zone_naive_datetime_rejected():
    with pytest.raises(ValueError):
        active_kill_zones(datetime(2026, 8, 18, 11, 30))


def test_kill_zone_outside_all_windows():
    ts = datetime(2026, 8, 18, 18, 0, tzinfo=timezone.utc)  # 14:00 EDT -> between london_close end and asian start
    assert active_kill_zones(ts) == []


# --- Displacement ---

def test_displacement_detected_for_strong_directional_move():
    bars = [
        (100, 101, 99, 100.3),
        (100.3, 101.3, 99.3, 100.6),
        (100.6, 101.6, 99.6, 100.9),
        (100.9, 115.2, 100.8, 115.0),  # huge, mostly-body bullish candle
    ]
    df = bars_to_df(bars, datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc))
    events = detect_displacement(df, atr_period=3, displacement_atr_multiple=2.0)
    assert len(events) == 1
    assert events[0].bar_index == 3
    assert events[0].direction == "bullish"


def test_no_displacement_for_wide_indecision_candle():
    bars = [
        (100, 101, 99, 100.3),
        (100.3, 101.3, 99.3, 100.6),
        (100.6, 101.6, 99.6, 100.9),
        (108, 115, 101, 108.5),  # huge range but tiny body (long wicks both sides)
    ]
    df = bars_to_df(bars, datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc))
    assert detect_displacement(df, atr_period=3, displacement_atr_multiple=2.0) == []


def test_no_displacement_when_too_few_bars():
    bars = [(100, 101, 99, 100.3)] * 3
    df = bars_to_df(bars, datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc))
    assert detect_displacement(df, atr_period=14) == []


# --- Kill-zone tagging + displacement intersection ---

def test_tag_kill_zones_marks_correct_bar():
    start = datetime(2026, 8, 18, 11, 29, tzinfo=timezone.utc)  # close_time of bar0 = 11:30 UTC -> in NY AM
    bars = [(100, 101, 99, 100)] * 2
    df = bars_to_df(bars, start)
    events = tag_kill_zones(df)
    zone_names = {e.details["zone"] for e in events if e.bar_index == 0}
    assert "new_york_am" in zone_names


def test_displacement_in_kill_zone_intersection():
    start = datetime(2026, 8, 18, 11, 26, tzinfo=timezone.utc)  # bar3's close_time = 11:30 UTC -> in NY AM
    bars = [
        (100, 101, 99, 100.3),
        (100.3, 101.3, 99.3, 100.6),
        (100.6, 101.6, 99.6, 100.9),
        (100.9, 115.2, 100.8, 115.0),
    ]
    df = bars_to_df(bars, start)
    events = displacement_in_kill_zone(df, atr_period=3, displacement_atr_multiple=2.0)
    assert len(events) == 1
    assert events[0].bar_index == 3


def test_displacement_outside_kill_zone_excluded():
    start = datetime(2026, 8, 18, 17, 26, tzinfo=timezone.utc)  # bar3's close_time = 17:30 UTC -> no kill zone active
    bars = [
        (100, 101, 99, 100.3),
        (100.3, 101.3, 99.3, 100.6),
        (100.6, 101.6, 99.6, 100.9),
        (100.9, 115.2, 100.8, 115.0),
    ]
    df = bars_to_df(bars, start)
    assert displacement_in_kill_zone(df, atr_period=3, displacement_atr_multiple=2.0) == []
