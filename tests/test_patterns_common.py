from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from strategies.patterns_common import classify_structure, find_swing_points

START = datetime(2026, 8, 18, 3, 45, tzinfo=timezone.utc)


def make_df(highs, lows, closes=None):
    n = len(highs)
    closes = closes or [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame(
        {
            "open_time": [START + timedelta(minutes=i) for i in range(n)],
            "close_time": [START + timedelta(minutes=i + 1) for i in range(n)],
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [100] * n,
        }
    )


def test_finds_single_swing_high():
    highs = [10, 11, 15, 11, 10]
    lows = [9, 10, 14, 10, 9]
    df = make_df(highs, lows)
    swings = find_swing_points(df, lookback=2)
    assert len(swings) == 1
    assert swings[0].kind == "high"
    assert swings[0].index == 2
    assert swings[0].price == 15


def test_finds_single_swing_low():
    highs = [20, 19, 15, 19, 20]
    lows = [19, 18, 10, 18, 19]
    df = make_df(highs, lows)
    swings = find_swing_points(df, lookback=2)
    assert len(swings) == 1
    assert swings[0].kind == "low"
    assert swings[0].price == 10


def test_no_swing_when_flat():
    highs = [10] * 7
    lows = [9] * 7
    df = make_df(highs, lows)
    assert find_swing_points(df, lookback=2) == []


def test_swing_requires_strict_dominance_over_full_window():
    # Peak at index 2 ties with index 4 within the right-side lookback window of index 2
    # (window is [3,4]) -> not a strict swing high because tie doesn't count as "greater".
    highs = [10, 11, 15, 11, 15, 11, 10]
    lows = [9, 10, 14, 10, 14, 10, 9]
    df = make_df(highs, lows)
    swings = find_swing_points(df, lookback=2)
    highs_found = [s for s in swings if s.kind == "high"]
    assert all(s.index != 2 for s in highs_found)


def test_swing_needs_lookback_bars_on_both_sides():
    highs = [15, 11, 10]  # peak at index 0 has no left-side window
    lows = [14, 10, 9]
    df = make_df(highs, lows)
    assert find_swing_points(df, lookback=2) == []


def test_lookback_must_be_positive():
    df = make_df([10, 11, 10], [9, 10, 9])
    with pytest.raises(ValueError):
        find_swing_points(df, lookback=0)


def test_classify_structure_hh_hl_lh_ll():
    from strategies.patterns_common import SwingPoint

    swings = [
        SwingPoint(0, START, 100.0, "low"),
        SwingPoint(1, START, 110.0, "high"),
        SwingPoint(2, START, 105.0, "low"),  # HL (105 > 100)
        SwingPoint(3, START, 115.0, "high"),  # HH (115 > 110)
        SwingPoint(4, START, 95.0, "low"),  # LL (95 < 105)
        SwingPoint(5, START, 108.0, "high"),  # LH (108 < 115)
    ]
    labels = classify_structure(swings)
    assert labels == ["", "", "HL", "HH", "LL", "LH"]
