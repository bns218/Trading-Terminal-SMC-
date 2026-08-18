"""The single source of truth for turning stored 1-minute candles into
DataFrames for analysis, and for resampling into higher timeframes
(3/5/15/30/60 min).

Two rules enforced structurally here, not just by convention:
  1. Only `is_closed=True` bars are ever used as input to resampling or
     handed to a strategy. A partial (still-forming) bar simply isn't in the
     DataFrame.
  2. A resampled higher-timeframe bucket is marked closed only if its clipped
     end time is actually covered by the 1-minute data available — i.e. the
     bucket isn't still forming. This is what stops a "3:16pm 15-min bar"
     from being reported as closed at 3:20pm when only 4 of its 15 minutes
     have arrived.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd

from config.market_calendar import MarketCalendar
from data.database import TickStore

IST = ZoneInfo("Asia/Kolkata")

SUPPORTED_TIMEFRAMES_MINUTES = [1, 3, 5, 15, 30, 60]


def candles_to_dataframe(raw_candles: list[dict]) -> pd.DataFrame:
    """Convert TickStore.get_candles() rows (all closed, per that method's
    contract) into a float DataFrame indexed by close_time (UTC), sorted
    ascending. This is the I/O-adjacent boundary conversion — everything
    downstream (indicators, resampling) is pure.
    """
    closed_only = [c for c in raw_candles if c["is_closed"] == 1]
    if not closed_only:
        return pd.DataFrame(columns=["open_time", "close_time", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(closed_only)
    df["open_time"] = pd.to_datetime(df["open_time"], utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], utc=True)
    for col in ("open", "high", "low", "close"):
        df[col] = df[col].apply(lambda v: float(Decimal(v)))
    df["volume"] = df["volume"].astype(int)
    df = df.sort_values("close_time").reset_index(drop=True)
    return df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]


def _session_buckets(day: date, calendar: MarketCalendar, target_minutes: int) -> list[tuple[datetime, datetime]]:
    bounds = calendar.session_bounds(day)
    if bounds is None:
        return []
    session_open, session_close = bounds
    buckets = []
    cursor = session_open
    step = timedelta(minutes=target_minutes)
    while cursor < session_close:
        bucket_end = min(cursor + step, session_close)
        buckets.append((cursor, bucket_end))
        cursor = bucket_end
    return buckets


def resample_candles(one_min_df: pd.DataFrame, target_minutes: int, calendar: MarketCalendar) -> pd.DataFrame:
    """Resample closed 1-minute candles into `target_minutes` bars, session-anchored
    at 09:15 IST. Returns a DataFrame with an `is_closed` column — callers (and
    Phase 4+ strategy code) must filter to is_closed=True before use.
    """
    if target_minutes not in SUPPORTED_TIMEFRAMES_MINUTES:
        raise ValueError(f"Unsupported timeframe: {target_minutes} minutes")
    if target_minutes == 1:
        result = one_min_df.copy()
        result["is_closed"] = True
        return result

    if one_min_df.empty:
        return pd.DataFrame(columns=["open_time", "close_time", "open", "high", "low", "close", "volume", "is_closed"])

    last_available_close = one_min_df["close_time"].max()
    rows = []
    days = sorted({ts.astimezone(IST).date() for ts in one_min_df["open_time"]})
    for day in days:
        day_df = one_min_df[one_min_df["open_time"].dt.tz_convert(IST).dt.date == day]
        for bucket_start, bucket_end in _session_buckets(day, calendar, target_minutes):
            in_bucket = day_df[(day_df["open_time"] >= bucket_start) & (day_df["open_time"] < bucket_end)]
            if in_bucket.empty:
                continue
            bucket_covered = in_bucket["close_time"].max() >= bucket_end
            is_closed = bool(bucket_covered and bucket_end <= last_available_close)
            rows.append(
                {
                    "open_time": bucket_start,
                    "close_time": bucket_end,
                    "open": in_bucket.iloc[0]["open"],
                    "high": in_bucket["high"].max(),
                    "low": in_bucket["low"].min(),
                    "close": in_bucket.iloc[-1]["close"],
                    "volume": int(in_bucket["volume"].sum()),
                    "is_closed": is_closed,
                }
            )
    return pd.DataFrame(rows)


def load_closed_candles(store: TickStore, instrument_token: str, timeframe: str = "1min", limit: int = 5000) -> pd.DataFrame:
    raw = store.get_candles(instrument_token, timeframe, limit=limit)
    return candles_to_dataframe(raw)
