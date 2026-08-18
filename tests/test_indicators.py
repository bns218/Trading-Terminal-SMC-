from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from strategies.indicators import (
    adx,
    atr,
    bollinger_bands,
    ema,
    macd,
    rsi,
    volume_stats,
    vwap_session,
)

IST_SESSION_START = datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc)  # 09:15 IST


def make_df(closes, highs=None, lows=None, volumes=None, start=IST_SESSION_START):
    n = len(closes)
    highs = highs or [c + 0.5 for c in closes]
    lows = lows or [c - 0.5 for c in closes]
    volumes = volumes or [100] * n
    close_times = [start + timedelta(minutes=i + 1) for i in range(n)]
    open_times = [start + timedelta(minutes=i) for i in range(n)]
    return pd.DataFrame(
        {
            "open_time": open_times,
            "close_time": close_times,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


# --- EMA: cross-checked against an independent pure-python recursive implementation ---

def reference_ema(values, period):
    alpha = 2 / (period + 1)
    out = []
    prev = None
    for v in values:
        prev = v if prev is None else alpha * v + (1 - alpha) * prev
        out.append(prev)
    return out


def test_ema_matches_independent_reference_implementation():
    closes = [10, 11, 12, 11, 13, 15, 14, 16, 18, 17]
    df = make_df(closes)
    result = ema(df, period=3)
    expected = reference_ema(closes, 3)
    # First `period - 1` values are NaN (min_periods enforced); from there on must match exactly.
    assert result.values.iloc[: 2].isna().all()
    for i in range(2, len(closes)):
        assert result.values.iloc[i] == pytest.approx(expected[i], rel=1e-9)


def test_ema_name_and_params():
    df = make_df([1, 2, 3, 4, 5])
    result = ema(df, period=3)
    assert result.name == "EMA_3"
    assert result.params == {"period": 3}


# --- ATR: cross-checked against an independent pure-python Wilder implementation ---

def reference_atr(highs, lows, closes, period):
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[i] - lows[i])
        else:
            trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    alpha = 1 / period
    out = []
    prev = None
    for tr in trs:
        prev = tr if prev is None else alpha * tr + (1 - alpha) * prev
        out.append(prev)
    return out


def test_atr_matches_independent_reference_implementation():
    highs = [10.5, 11.2, 12.8, 11.9, 13.5, 15.1, 14.6, 16.3, 18.2, 17.4]
    lows = [9.8, 10.6, 11.9, 11.0, 12.7, 14.2, 13.9, 15.5, 17.1, 16.6]
    closes = [10.1, 11.0, 12.3, 11.4, 13.1, 14.8, 14.2, 16.0, 17.8, 17.0]
    df = make_df(closes, highs=highs, lows=lows)
    result = atr(df, period=3)
    expected = reference_atr(highs, lows, closes, 3)
    for i in range(2, len(closes)):
        assert result.values.iloc[i] == pytest.approx(expected[i], rel=1e-9)
    assert (result.values >= 0).all() or result.values.isna().any()


# --- RSI: property + degenerate-case checks ---

def test_rsi_bounded_0_100():
    closes = [10, 11, 9, 12, 8, 14, 7, 15, 6, 16, 5, 17]
    df = make_df(closes)
    result = rsi(df, period=5)
    valid = result.values.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_rsi_all_gains_is_100():
    closes = list(range(1, 12))  # strictly increasing -> no losses at all
    df = make_df(closes)
    result = rsi(df, period=5)
    assert result.values.dropna().iloc[-1] == pytest.approx(100.0)


def test_rsi_flat_price_defined_not_nan():
    closes = [10.0] * 10
    df = make_df(closes)
    result = rsi(df, period=5)
    assert not result.values.dropna().empty


# --- MACD: internal consistency ---

def test_macd_histogram_equals_macd_minus_signal():
    closes = [10, 11, 12, 11, 13, 15, 14, 16, 18, 17, 19, 20, 18, 21, 22, 23, 24, 22, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36]
    df = make_df(closes)
    result = macd(df, fast=5, slow=10, signal=3)
    diff = (result.macd_line.values - result.signal_line.values - result.histogram.values).dropna()
    assert (diff.abs() < 1e-9).all()


# --- Bollinger Bands: ordering property ---

def test_bollinger_upper_gte_middle_gte_lower():
    closes = [10, 11, 12, 11, 13, 15, 14, 16, 18, 17, 19, 20, 18, 21, 22, 23]
    df = make_df(closes)
    result = bollinger_bands(df, period=5, num_std=2)
    m, u, l = result.middle.values.dropna(), result.upper.values.dropna(), result.lower.values.dropna()
    assert (u >= m).all()
    assert (m >= l).all()


def test_bollinger_zero_std_collapses_bands():
    closes = [10.0] * 10
    df = make_df(closes)
    result = bollinger_bands(df, period=5, num_std=2)
    assert result.upper.values.dropna().iloc[-1] == pytest.approx(result.lower.values.dropna().iloc[-1])


# --- ADX: bounded, non-negative ---

def test_adx_bounded_0_100():
    np.random.seed(42)
    closes = list(np.cumsum(np.random.randn(60)) + 100)
    highs = [c + abs(np.random.randn()) for c in closes]
    lows = [c - abs(np.random.randn()) for c in closes]
    df = make_df(closes, highs=highs, lows=lows)
    result = adx(df, period=14)
    for series in (result.adx.values, result.plus_di.values, result.minus_di.values):
        valid = series.dropna()
        assert (valid >= -1e-9).all()
        assert (valid <= 100 + 1e-6).all()


# --- Volume stats ---

def test_volume_ratio_above_one_when_volume_spikes():
    volumes = [100] * 10 + [500]
    closes = list(range(11))
    df = make_df(closes, volumes=volumes)
    result = volume_stats(df, period=10)
    assert result.volume_ratio.values.dropna().iloc[-1] > 1.0


def test_avg_volume_matches_manual_mean():
    volumes = [100, 200, 300, 400, 500]
    closes = [1, 2, 3, 4, 5]
    df = make_df(closes, volumes=volumes)
    result = volume_stats(df, period=5)
    assert result.avg_volume.values.dropna().iloc[-1] == pytest.approx(300.0)


# --- VWAP: session anchoring ---

def test_vwap_resets_across_session_days():
    day1_start = datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc)
    day2_start = datetime(2026, 8, 19, 3, 45, tzinfo=timezone.utc)
    df1 = make_df([100, 200], highs=[100, 200], lows=[100, 200], start=day1_start)
    df2 = make_df([10, 20], highs=[10, 20], lows=[10, 20], start=day2_start)
    df = pd.concat([df1, df2], ignore_index=True)

    result = vwap_session(df)
    day2_first_vwap = result.values.iloc[2]
    assert day2_first_vwap == pytest.approx(10.0)  # anchored fresh on day 2, unaffected by day 1's much higher prices


def test_vwap_within_high_low_range_for_constant_volume():
    closes = [10, 11, 12, 11, 10]
    df = make_df(closes)
    result = vwap_session(df)
    valid = result.values.dropna()
    assert (valid >= df["low"].min()).all()
    assert (valid <= df["high"].max()).all()


def test_indicator_missing_columns_raises():
    df = pd.DataFrame({"close_time": [IST_SESSION_START], "close": [10.0]})
    with pytest.raises(ValueError):
        atr(df, period=3)
