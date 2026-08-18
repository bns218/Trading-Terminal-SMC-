from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from strategies.chart_patterns import (
    classify_trendline_shape,
    detect_double_bottom,
    detect_double_top,
    detect_flag_or_pennant,
    detect_head_and_shoulders,
    detect_inverse_head_and_shoulders,
)

START = datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc)


def bars_to_df(bars):
    """bars: list of (open, high, low, close) tuples."""
    n = len(bars)
    return pd.DataFrame(
        {
            "open_time": [START + timedelta(minutes=i) for i in range(n)],
            "close_time": [START + timedelta(minutes=i + 1) for i in range(n)],
            "open": [b[0] for b in bars],
            "high": [b[1] for b in bars],
            "low": [b[2] for b in bars],
            "close": [b[3] for b in bars],
            "volume": [100] * n,
        }
    )


# --- Double top / bottom ---
# Hand-traced against find_swing_points(lookback=1): swing highs at idx1
# (110) and idx5 (109, within 1.5% tolerance), swing low at idx3 (80)
# between them -> neckline=80, confirmed when close drops below 80 at idx7.
DOUBLE_TOP_BARS = [
    (88, 90, 85, 88),
    (105, 110, 95, 105),
    (97, 100, 94, 97),
    (85, 99, 80, 85),
    (92, 95, 88, 92),
    (104, 109, 90, 104),
    (90, 98, 85, 90),
    (79, 97, 79, 79),
]


def test_double_top_detected_and_confirmed():
    df = bars_to_df(DOUBLE_TOP_BARS)
    events = detect_double_top(df, lookback=1)
    assert len(events) == 1
    assert events[0].direction == "bearish"
    assert events[0].bar_index == 7
    assert events[0].details["neckline"] == 80


def test_double_top_not_confirmed_without_neckline_break():
    bars = DOUBLE_TOP_BARS[:-1] + [(90, 98, 85, 90)]  # close stays above neckline
    df = bars_to_df(bars)
    assert detect_double_top(df, lookback=1) == []


def test_no_double_top_on_monotonic_uptrend():
    bars = [(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(10)]
    df = bars_to_df(bars)
    assert detect_double_top(df, lookback=1) == []


# Mirror image of the double-top scenario.
DOUBLE_BOTTOM_BARS = [
    (88, 90, 85, 88),
    (72, 78, 70, 72),
    (80, 85, 79, 80),
    (95, 102, 90, 95),
    (85, 88, 82, 85),
    (73, 79, 71, 73),
    (87, 90, 82, 87),
    (105, 106, 79, 105),
]


def test_double_bottom_detected_and_confirmed():
    df = bars_to_df(DOUBLE_BOTTOM_BARS)
    events = detect_double_bottom(df, lookback=1)
    assert len(events) == 1
    assert events[0].direction == "bullish"
    assert events[0].bar_index == 7


# --- Head & shoulders ---
# Hand-traced: swing highs at idx1 (100, left shoulder), idx3 (115, head),
# idx5 (99, right shoulder, within 3% tolerance); swing lows at idx2 (80) and
# idx4 (78) between them -> neckline=max(80,78)=80, confirmed at idx7.
HEAD_SHOULDERS_BARS = [
    (90, 90, 85, 90),
    (94, 100, 94, 94),
    (85, 95, 80, 85),
    (92, 115, 88, 110),
    (82, 97, 78, 82),
    (94, 99, 90, 94),
    (85, 90, 82, 85),
    (79, 88, 75, 79),
]


def test_head_and_shoulders_detected_and_confirmed():
    df = bars_to_df(HEAD_SHOULDERS_BARS)
    events = detect_head_and_shoulders(df, lookback=1)
    assert len(events) == 1
    assert events[0].direction == "bearish"
    assert events[0].bar_index == 7
    assert events[0].details["neckline"] == 80


def test_no_head_and_shoulders_when_head_not_highest():
    bars = list(HEAD_SHOULDERS_BARS)
    bars[3] = (92, 97, 88, 96)  # head no longer higher than shoulders
    df = bars_to_df(bars)
    assert detect_head_and_shoulders(df, lookback=1) == []


# Mirror for inverse H&S.
INV_HEAD_SHOULDERS_BARS = [
    (90, 95, 90, 90),
    (86, 86, 80, 86),
    (95, 100, 85, 95),
    (88, 92, 65, 70),
    (98, 102, 83, 98),
    (86, 90, 81, 86),
    (95, 98, 90, 95),
    (101, 105, 92, 101),
]


def test_inverse_head_and_shoulders_detected_and_confirmed():
    df = bars_to_df(INV_HEAD_SHOULDERS_BARS)
    events = detect_inverse_head_and_shoulders(df, lookback=1)
    assert len(events) == 1
    assert events[0].direction == "bullish"
    assert events[0].bar_index == 7


# --- Trendline shape classification ---

def test_classify_flat_channel():
    closes = [100.0] * 25
    highs = [101.0] * 25
    lows = [99.0] * 25
    df = pd.DataFrame({"close_time": [START + timedelta(minutes=i) for i in range(25)], "high": highs, "low": lows, "close": closes})
    shape = classify_trendline_shape(df, window=20)
    assert shape.label == "channel_flat"


def test_classify_ascending_triangle():
    n = 25
    highs = [110.0] * n  # flat resistance
    lows = [90.0 + 0.5 * i for i in range(n)]  # rising support
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({"close_time": [START + timedelta(minutes=i) for i in range(n)], "high": highs, "low": lows, "close": closes})
    shape = classify_trendline_shape(df, window=20)
    assert shape.label == "ascending_triangle"


def test_classify_descending_triangle():
    n = 25
    lows = [90.0] * n  # flat support
    highs = [130.0 - 0.5 * i for i in range(n)]  # falling resistance
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({"close_time": [START + timedelta(minutes=i) for i in range(n)], "high": highs, "low": lows, "close": closes})
    shape = classify_trendline_shape(df, window=20)
    assert shape.label == "descending_triangle"


def test_classify_symmetrical_triangle():
    n = 25
    highs = [130.0 - 0.6 * i for i in range(n)]
    lows = [90.0 + 0.6 * i for i in range(n)]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({"close_time": [START + timedelta(minutes=i) for i in range(n)], "high": highs, "low": lows, "close": closes})
    shape = classify_trendline_shape(df, window=20)
    assert shape.label == "symmetrical_triangle"


def test_classify_channel_up():
    n = 25
    highs = [100.0 + 1.0 * i for i in range(n)]
    lows = [90.0 + 1.0 * i for i in range(n)]  # parallel, both rising at the same rate
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    df = pd.DataFrame({"close_time": [START + timedelta(minutes=i) for i in range(n)], "high": highs, "low": lows, "close": closes})
    shape = classify_trendline_shape(df, window=20)
    assert shape.label == "channel_up"


def test_classify_too_few_bars_returns_none():
    df = pd.DataFrame({"close_time": [START], "high": [100.0], "low": [99.0], "close": [99.5]})
    shape = classify_trendline_shape(df, window=20)
    assert shape.label == "none"


# --- Flags / pennants ---

def test_bullish_flag_detected():
    # Strong bullish pole (large ATR-relative move) followed by a tight,
    # roughly flat consolidation.
    pole_closes = [100 + 3 * i for i in range(14)]  # steep pole, also seeds ATR
    consolidation_closes = [pole_closes[-1] + 0.1 * ((-1) ** i) for i in range(8)]
    closes = pole_closes + consolidation_closes
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    bars = list(zip(closes, highs, lows, closes))
    df = bars_to_df(bars)
    events = detect_flag_or_pennant(df, pole_lookback=10, pole_atr_multiple=3.0, consolidation_window=8, atr_period=4)
    assert len(events) >= 1
    assert events[-1].direction == "bullish"
    assert events[-1].pattern in ("flag", "pennant")


def test_no_flag_when_no_pole():
    closes = [100.0 + 0.05 * ((-1) ** i) for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    bars = list(zip(closes, highs, lows, closes))
    df = bars_to_df(bars)
    assert detect_flag_or_pennant(df, pole_lookback=10, consolidation_window=8, atr_period=4) == []


def test_no_flag_when_too_few_bars():
    bars = [(100, 101, 99, 100)] * 5
    df = bars_to_df(bars)
    assert detect_flag_or_pennant(df, pole_lookback=10, consolidation_window=8, atr_period=14) == []
