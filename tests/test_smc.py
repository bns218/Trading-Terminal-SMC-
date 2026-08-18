from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from strategies.smc import (
    detect_bos_choch,
    detect_fair_value_gaps,
    detect_liquidity_sweeps,
    detect_order_blocks,
    premium_discount_zone,
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


# --- BOS / CHOCH ---
# Carefully constructed 8-bar sequence (traced by hand against the
# implementation): a swing high confirms at idx1 (105), price closes above it
# at idx3 (108) -> first-ever break = BOS bullish. A swing low confirms at
# idx4 (90); price later closes below it at idx7 (85) while trend is bullish
# -> CHOCH bearish. An incidental extra swing high at idx5 (99) is never
# broken by the remaining closes, so it produces no event.
BOS_CHOCH_BARS = [
    (95, 98, 94, 96),
    (96, 105, 95, 100),
    (100, 101, 97, 99),
    (99, 100, 96, 108),
    (108, 97, 90, 95),
    (95, 99, 93, 96),
    (96, 98, 92, 97),
    (97, 98, 80, 85),
]


def test_bos_then_choch_sequence():
    df = bars_to_df(BOS_CHOCH_BARS)
    events = detect_bos_choch(df, lookback=1)
    assert [(e.pattern, e.bar_index, e.direction) for e in events] == [
        ("BOS", 3, "bullish"),
        ("CHOCH", 7, "bearish"),
    ]
    assert events[0].details["broken_level"] == 105
    assert events[1].details["broken_level"] == 90


def test_no_bos_choch_when_no_swing_broken():
    bars = [(100, 101, 99, 100)] * 10
    df = bars_to_df(bars)
    assert detect_bos_choch(df, lookback=2) == []


def test_first_break_is_always_bos_not_choch():
    # Regardless of direction, the very first structure break has no prior
    # trend to contradict, so it must be labelled BOS.
    df = bars_to_df(BOS_CHOCH_BARS)
    events = detect_bos_choch(df, lookback=1)
    assert events[0].pattern == "BOS"


# --- Fair Value Gaps ---

def test_bullish_fvg_detected():
    bars = [
        (100, 101, 99, 100.5),   # c1: high=101
        (101, 103, 100, 102),    # c2: doesn't fill the gap
        (102, 106, 104, 105),    # c3: low=104 > c1.high=101 -> bullish FVG
    ]
    df = bars_to_df(bars)
    events = detect_fair_value_gaps(df)
    assert len(events) == 1
    assert events[0].direction == "bullish"
    assert events[0].bar_index == 2
    assert events[0].details == {"gap_low": 101.0, "gap_high": 104.0}


def test_bearish_fvg_detected():
    bars = [
        (100, 101, 99, 99.5),    # c1: low=99
        (99, 100, 96, 97),       # c2
        (97, 95, 90, 92),        # c3: high=95 < c1.low=99 -> bearish FVG
    ]
    df = bars_to_df(bars)
    events = detect_fair_value_gaps(df)
    assert len(events) == 1
    assert events[0].direction == "bearish"


def test_no_fvg_when_candles_overlap():
    bars = [(100, 105, 95, 102), (102, 106, 98, 103), (103, 107, 99, 104)]
    df = bars_to_df(bars)
    assert detect_fair_value_gaps(df) == []


# --- Order Blocks ---

def test_bullish_order_block_before_displacement():
    bars = [
        (100, 101, 99, 100.5),
        (100.5, 101.5, 99.5, 100),
        (100, 101, 98.5, 99),
        (99, 100.5, 98, 98.5),   # bearish candle immediately before displacement
        (98.5, 110, 98, 109),    # huge bullish displacement candle
    ]
    df = bars_to_df(bars)
    events = detect_order_blocks(df, displacement_atr_multiple=1.5, atr_period=3)
    assert len(events) == 1
    assert events[0].direction == "bullish"
    assert events[0].bar_index == 3  # the order block is the PRIOR candle, not the displacement candle
    assert events[0].details["displacement_index"] == 4


def test_no_order_block_without_displacement():
    bars = [(100, 101, 99, 100.5)] * 6
    df = bars_to_df(bars)
    assert detect_order_blocks(df, atr_period=3) == []


def test_order_blocks_returns_empty_for_too_few_bars():
    bars = [(100, 101, 99, 100.5)] * 3
    df = bars_to_df(bars)
    assert detect_order_blocks(df, atr_period=14) == []


# --- Liquidity sweeps ---

def test_bearish_liquidity_sweep_of_swing_high():
    highs = [10, 11, 15, 11, 10, 16]
    lows = [9, 10, 14, 10, 9, 14]
    closes = [9.5, 10.5, 14.5, 10.5, 9.5, 14]  # last bar: high 16 > 15, close 14 < 15
    opens = closes
    bars = list(zip(opens, highs, lows, closes))
    df = bars_to_df(bars)
    events = detect_liquidity_sweeps(df, lookback=2)
    assert len(events) == 1
    assert events[0].direction == "bearish"
    assert events[0].bar_index == 5
    assert events[0].details["swept_level"] == 15


def test_no_sweep_when_level_cleanly_broken():
    highs = [10, 11, 15, 11, 10, 20]
    lows = [9, 10, 14, 10, 9, 16]
    closes = [9.5, 10.5, 14.5, 10.5, 9.5, 19]  # closes ABOVE 15 -> clean break, not a sweep
    bars = list(zip(closes, highs, lows, closes))
    df = bars_to_df(bars)
    assert detect_liquidity_sweeps(df, lookback=2) == []


# --- Premium / discount ---

def test_premium_zone():
    assert premium_discount_zone(90, 50, 100) == "premium"


def test_discount_zone():
    assert premium_discount_zone(60, 50, 100) == "discount"


def test_equilibrium_zone():
    assert premium_discount_zone(75, 50, 100) == "equilibrium"


def test_premium_discount_rejects_invalid_range():
    with pytest.raises(ValueError):
        premium_discount_zone(60, 100, 50)
