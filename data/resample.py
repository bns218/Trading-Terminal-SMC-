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


def resample_candles(one_min_df: pd.DataFrame, target_minutes: int, calendar: MarketCalendar) -> pd.DataFrame:
    """Resample closed 1-minute candles into `target_minutes` bars, session-anchored
    at 09:15 IST. Returns a DataFrame with an `is_closed` column — callers (and
    Phase 4+ strategy code) must filter to is_closed=True before use.

    Vectorized: bucket boundaries are computed column-wise and grouped once,
    rather than re-scanning the full 1-minute DataFrame once per calendar day
    (that O(days x n) pattern was fine at the few-hundred-row scale this was
    first tested at, but multi-year 1-minute history makes it minutes-slow).
    """
    if target_minutes not in SUPPORTED_TIMEFRAMES_MINUTES:
        raise ValueError(f"Unsupported timeframe: {target_minutes} minutes")
    if target_minutes == 1:
        result = one_min_df.copy()
        result["is_closed"] = True
        return result

    if one_min_df.empty:
        return pd.DataFrame(columns=["open_time", "close_time", "open", "high", "low", "close", "volume", "is_closed"])

    df = one_min_df.copy()
    ist_time = df["open_time"].dt.tz_convert(IST)
    ist_date = ist_time.dt.date

    unique_days = ist_date.unique()
    session_open = {}
    session_close = {}
    for day in unique_days:
        bounds = calendar.session_bounds(day)
        if bounds is not None:
            session_open[day], session_close[day] = bounds

    in_session = ist_date.isin(session_open.keys())
    df = df.loc[in_session].copy()
    ist_time = ist_time.loc[in_session]
    ist_date = ist_date.loc[in_session]
    if df.empty:
        return pd.DataFrame(columns=["open_time", "close_time", "open", "high", "low", "close", "volume", "is_closed"])

    day_open = ist_date.map(session_open)
    day_close = ist_date.map(session_close)
    minutes_since_open = (ist_time - day_open).dt.total_seconds() / 60.0
    bucket_index = (minutes_since_open // target_minutes).astype(int)
    bucket_start = day_open + pd.to_timedelta(bucket_index * target_minutes, unit="m")
    bucket_end = (bucket_start + pd.Timedelta(minutes=target_minutes)).clip(upper=day_close)

    # Match open_time/close_time's UTC tz (they come straight from the store) so
    # later comparisons against them never hit a tz-naive-vs-aware mismatch.
    # Assign the Series directly (index-aligned, not .values) — .values on a
    # tz-aware Series silently strips tz-awareness on this pandas version.
    df["_bucket_start"] = bucket_start.dt.tz_convert("UTC")
    df["_bucket_end"] = bucket_end.dt.tz_convert("UTC")

    last_available_close = df["close_time"].max()

    # A single named .agg() call mixing tz-aware datetime columns with plain
    # numeric ones has been observed (this pandas version) to silently drop
    # tz-awareness on the datetime outputs. Aggregating the datetime columns
    # via their own standalone groupby().max() Series sidesteps that —
    # within a bucket, _bucket_start/_bucket_end/close_time-max are each
    # constant-or-well-defined so this changes nothing about the result.
    df_sorted = df.sort_values("open_time")
    grouped = df_sorted.groupby("_bucket_start", sort=True)

    ohlcv = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    ohlcv["volume"] = ohlcv["volume"].astype(int)
    ohlcv["open_time"] = ohlcv.index
    ohlcv["close_time"] = grouped["_bucket_end"].max()
    max_close_time = grouped["close_time"].max()

    ohlcv = ohlcv.reset_index(drop=True)
    bucket_covered = max_close_time.reset_index(drop=True) >= ohlcv["close_time"]
    ohlcv["is_closed"] = bucket_covered & (ohlcv["close_time"] <= last_available_close)
    return ohlcv[["open_time", "close_time", "open", "high", "low", "close", "volume", "is_closed"]]


def load_closed_candles(store: TickStore, instrument_token: str, timeframe: str = "1min", limit: int = 5000) -> pd.DataFrame:
    raw = store.get_candles(instrument_token, timeframe, limit=limit)
    return candles_to_dataframe(raw)
