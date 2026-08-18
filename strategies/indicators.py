"""Technical indicators. Every function is pure: DataFrame in, typed result
out. No I/O, no network calls, no global state — per the project's rule that
broker code and strategy code only ever meet through plain typed values.

Input contract: a DataFrame with columns [open_time, close_time, open, high,
low, close, volume], float-valued, sorted ascending by close_time, containing
ONLY closed bars (data/resample.py's job to guarantee that before this layer
ever sees it — these functions do not re-check `is_closed` themselves).

Values are plain floats here, not Decimal — these are analytical/derived
figures (like the rest of the DerivedMetric family), not money amounts, so
the ground rule about Decimal for prices/P&L/charges does not apply to them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class IndicatorSeries:
    name: str
    params: dict
    values: pd.Series  # index = close_time (UTC, tz-aware), float values (NaN during warm-up)


@dataclass(frozen=True)
class MACDResult:
    macd_line: IndicatorSeries
    signal_line: IndicatorSeries
    histogram: IndicatorSeries


@dataclass(frozen=True)
class BollingerBandsResult:
    middle: IndicatorSeries
    upper: IndicatorSeries
    lower: IndicatorSeries


@dataclass(frozen=True)
class ADXResult:
    adx: IndicatorSeries
    plus_di: IndicatorSeries
    minus_di: IndicatorSeries


@dataclass(frozen=True)
class VolumeStatsResult:
    avg_volume: IndicatorSeries
    volume_ratio: IndicatorSeries  # current volume / rolling average volume


def _require_columns(df: pd.DataFrame, columns: list[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")


def vwap_session(df: pd.DataFrame) -> IndicatorSeries:
    """Session-anchored VWAP: resets at the start of each IST trading day
    found in the data (no cross-day carry-over)."""
    _require_columns(df, ["close_time", "high", "low", "close", "volume"])
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    tp_vol = typical_price * df["volume"]
    session_date = df["close_time"].dt.tz_convert(IST).dt.date
    cum_tp_vol = tp_vol.groupby(session_date).cumsum()
    cum_vol = df["volume"].groupby(session_date).cumsum()
    vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
    vwap.index = df["close_time"]
    return IndicatorSeries("VWAP", {}, vwap)


def ema(df: pd.DataFrame, period: int) -> IndicatorSeries:
    _require_columns(df, ["close_time", "close"])
    values = df["close"].ewm(span=period, adjust=False, min_periods=period).mean()
    values.index = df["close_time"]
    return IndicatorSeries(f"EMA_{period}", {"period": period}, values)


def rsi(df: pd.DataFrame, period: int = 14) -> IndicatorSeries:
    """Wilder's RSI."""
    _require_columns(df, ["close_time", "close"])
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    values = 100 - (100 / (1 + rs))
    values = values.where(avg_loss != 0, 100.0)  # no losses in window -> RSI 100, not NaN from div-by-zero
    values.index = df["close_time"]
    return IndicatorSeries("RSI", {"period": period}, values)


def macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> MACDResult:
    _require_columns(df, ["close_time", "close"])
    ema_fast = df["close"].ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    histogram = macd_line - signal_line

    macd_line.index = signal_line.index = histogram.index = df["close_time"]
    return MACDResult(
        macd_line=IndicatorSeries("MACD_line", {"fast": fast, "slow": slow}, macd_line),
        signal_line=IndicatorSeries("MACD_signal", {"signal": signal}, signal_line),
        histogram=IndicatorSeries("MACD_histogram", {}, histogram),
    )


def atr(df: pd.DataFrame, period: int = 14) -> IndicatorSeries:
    """Wilder's ATR."""
    _require_columns(df, ["close_time", "high", "low", "close"])
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    values = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    values.index = df["close_time"]
    return IndicatorSeries("ATR", {"period": period}, values)


def bollinger_bands(df: pd.DataFrame, period: int = 20, num_std: float = 2.0) -> BollingerBandsResult:
    _require_columns(df, ["close_time", "close"])
    sma = df["close"].rolling(period, min_periods=period).mean()
    std = df["close"].rolling(period, min_periods=period).std(ddof=0)
    upper = sma + num_std * std
    lower = sma - num_std * std
    sma.index = upper.index = lower.index = df["close_time"]
    return BollingerBandsResult(
        middle=IndicatorSeries("BB_middle", {"period": period}, sma),
        upper=IndicatorSeries("BB_upper", {"period": period, "num_std": num_std}, upper),
        lower=IndicatorSeries("BB_lower", {"period": period, "num_std": num_std}, lower),
    )


def adx(df: pd.DataFrame, period: int = 14) -> ADXResult:
    """Wilder's ADX with +DI/-DI."""
    _require_columns(df, ["close_time", "high", "low", "close"])
    up_move = df["high"].diff()
    down_move = -df["low"].diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_smoothed = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_dm_smoothed = pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    minus_dm_smoothed = pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_di = 100 * plus_dm_smoothed / atr_smoothed.replace(0, np.nan)
    minus_di = 100 * minus_dm_smoothed / atr_smoothed.replace(0, np.nan)

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx_values = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    adx_values.index = plus_di.index = minus_di.index = df["close_time"]
    return ADXResult(
        adx=IndicatorSeries("ADX", {"period": period}, adx_values),
        plus_di=IndicatorSeries("PLUS_DI", {"period": period}, plus_di),
        minus_di=IndicatorSeries("MINUS_DI", {"period": period}, minus_di),
    )


def volume_stats(df: pd.DataFrame, period: int = 20) -> VolumeStatsResult:
    _require_columns(df, ["close_time", "volume"])
    avg_volume = df["volume"].rolling(period, min_periods=period).mean()
    ratio = df["volume"] / avg_volume.replace(0, np.nan)
    avg_volume.index = ratio.index = df["close_time"]
    return VolumeStatsResult(
        avg_volume=IndicatorSeries("AVG_VOLUME", {"period": period}, avg_volume),
        volume_ratio=IndicatorSeries("VOLUME_RATIO", {"period": period}, ratio),
    )
