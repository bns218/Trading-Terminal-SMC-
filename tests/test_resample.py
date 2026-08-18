from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from config.market_calendar import MarketCalendar
from config.settings import get_settings
from data.resample import candles_to_dataframe, resample_candles

SESSION_OPEN_UTC = datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc)  # 09:15 IST, Tuesday


@pytest.fixture
def calendar():
    return MarketCalendar(get_settings().holidays_file)


def make_one_min_candles(n_minutes: int, start=SESSION_OPEN_UTC):
    rows = []
    for i in range(n_minutes):
        open_time = start + timedelta(minutes=i)
        close_time = open_time + timedelta(minutes=1)
        rows.append(
            {
                "open_time": open_time,
                "close_time": close_time,
                "open": 100.0 + i,
                "high": 100.5 + i,
                "low": 99.5 + i,
                "close": 100.2 + i,
                "volume": 10,
            }
        )
    return pd.DataFrame(rows)


def test_candles_to_dataframe_filters_unclosed_bars():
    raw = [
        {"open_time": SESSION_OPEN_UTC.isoformat(), "close_time": (SESSION_OPEN_UTC + timedelta(minutes=1)).isoformat(),
         "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": 10, "is_backfilled": 0, "is_closed": 1},
        {"open_time": (SESSION_OPEN_UTC + timedelta(minutes=1)).isoformat(),
         "close_time": (SESSION_OPEN_UTC + timedelta(minutes=2)).isoformat(),
         "open": "100.5", "high": "101", "low": "100", "close": "100.7", "volume": 5, "is_backfilled": 0, "is_closed": 0},
    ]
    df = candles_to_dataframe(raw)
    assert len(df) == 1  # the is_closed=0 row must never appear


def test_candles_to_dataframe_empty_when_nothing_closed():
    raw = [
        {"open_time": SESSION_OPEN_UTC.isoformat(), "close_time": (SESSION_OPEN_UTC + timedelta(minutes=1)).isoformat(),
         "open": "100", "high": "101", "low": "99", "close": "100.5", "volume": 10, "is_backfilled": 0, "is_closed": 0},
    ]
    df = candles_to_dataframe(raw)
    assert df.empty


def test_resample_5min_full_session_worth_of_data(calendar):
    df = make_one_min_candles(15)  # exactly three 5-min buckets, all fully covered
    resampled = resample_candles(df, 5, calendar)
    assert len(resampled) == 3
    assert resampled.iloc[0]["open"] == 100.0
    assert resampled.iloc[0]["close"] == pytest.approx(104.2)
    assert resampled.iloc[0]["high"] == pytest.approx(104.5)
    assert resampled.iloc[0]["low"] == pytest.approx(99.5)
    assert resampled.iloc[0]["volume"] == 50
    assert resampled["is_closed"].all()


def test_resample_partial_final_bucket_marked_not_closed(calendar):
    # 12 minutes of data -> two full 5-min buckets, then a 3rd bucket with only 2 of 5 minutes.
    df = make_one_min_candles(12)
    resampled = resample_candles(df, 5, calendar)
    assert len(resampled) == 3
    assert resampled.iloc[0]["is_closed"] == True
    assert resampled.iloc[1]["is_closed"] == True
    assert resampled.iloc[2]["is_closed"] == False  # still forming — must not be usable


def test_resample_session_end_shorter_bucket_is_still_closed(calendar):
    """375-minute session / 30-min buckets doesn't divide evenly (375/30=12.5):
    the last 30-min bucket of the day is only 15 minutes wide, but since the
    session genuinely ended there, it IS a legitimately closed bar — this
    must not be confused with a still-forming partial bucket."""
    df = make_one_min_candles(375)  # the full trading session, minute by minute
    resampled = resample_candles(df, 30, calendar)
    assert len(resampled) == 13  # 12 full 30-min buckets + 1 short 15-min closing bucket
    assert resampled["is_closed"].all()
    last_bucket_minutes = (resampled.iloc[-1]["close_time"] - resampled.iloc[-1]["open_time"]).total_seconds() / 60
    assert last_bucket_minutes == 15


def test_resample_1min_passthrough(calendar):
    df = make_one_min_candles(5)
    resampled = resample_candles(df, 1, calendar)
    assert len(resampled) == 5
    assert resampled["is_closed"].all()


def test_resample_rejects_unsupported_timeframe(calendar):
    df = make_one_min_candles(5)
    with pytest.raises(ValueError):
        resample_candles(df, 7, calendar)


def test_resample_empty_input(calendar):
    df = make_one_min_candles(0)
    resampled = resample_candles(df, 5, calendar)
    assert resampled.empty
