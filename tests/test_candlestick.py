from datetime import datetime, timedelta, timezone

import pandas as pd

from strategies.candlestick import (
    detect_doji,
    detect_engulfing,
    detect_evening_star,
    detect_hammer,
    detect_harami,
    detect_inside_bar,
    detect_morning_star,
    detect_pin_bar,
    detect_shooting_star,
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


def test_doji_detected():
    df = bars_to_df([(100, 102, 98, 100.05)])  # body ~0.05, range 4 -> ratio 0.0125
    events = detect_doji(df)
    assert len(events) == 1
    assert events[0].direction == "neutral"


def test_doji_not_detected_for_large_body():
    df = bars_to_df([(100, 105, 95, 104)])
    assert detect_doji(df) == []


def test_hammer_detected():
    # body [99.5,100], range [95,100.2]: body=0.5, lower wick=99.5-95=4.5 (9x body), upper wick=0.2 (0.4% of range... check ratio)
    df = bars_to_df([(99.5, 100.2, 95.0, 100.0)])
    events = detect_hammer(df)
    assert len(events) == 1
    assert events[0].direction == "bullish"


def test_hammer_not_detected_with_large_upper_wick():
    df = bars_to_df([(99.5, 110.0, 95.0, 100.0)])  # huge upper wick disqualifies it
    assert detect_hammer(df) == []


def test_shooting_star_detected():
    df = bars_to_df([(100.0, 105.0, 99.8, 100.2)])  # long upper wick, tiny lower wick
    events = detect_shooting_star(df)
    assert len(events) == 1
    assert events[0].direction == "bearish"


def test_bullish_engulfing_detected():
    bars = [
        (100, 101, 95, 96),   # bearish candle, body 96-100
        (95, 105, 94, 102),   # bullish candle engulfing prior body
    ]
    df = bars_to_df(bars)
    events = detect_engulfing(df)
    assert len(events) == 1
    assert events[0].direction == "bullish"
    assert events[0].bar_index == 1


def test_bearish_engulfing_detected():
    bars = [
        (95, 101, 94, 100),   # bullish candle
        (101, 102, 90, 93),   # bearish candle engulfing prior body
    ]
    df = bars_to_df(bars)
    events = detect_engulfing(df)
    assert len(events) == 1
    assert events[0].direction == "bearish"


def test_engulfing_not_detected_when_body_smaller():
    bars = [(100, 101, 95, 96), (97, 99, 96.5, 98)]  # second body doesn't fully engulf first
    df = bars_to_df(bars)
    assert detect_engulfing(df) == []


def test_harami_detected():
    bars = [
        (95, 105, 94, 103),   # large bullish candle, body [95,103]
        (100, 100.5, 97, 98),  # small bearish candle fully inside prior body
    ]
    df = bars_to_df(bars)
    events = detect_harami(df)
    assert len(events) == 1
    assert events[0].direction == "bearish"  # bullish then smaller bearish body


def test_inside_bar_detected():
    bars = [(95, 110, 90, 100), (98, 105, 95, 102)]  # bar2 high/low fully within bar1 high/low
    df = bars_to_df(bars)
    events = detect_inside_bar(df)
    assert len(events) == 1


def test_inside_bar_not_detected_when_range_extends_beyond():
    bars = [(95, 110, 90, 100), (98, 111, 95, 102)]  # bar2 high exceeds bar1 high
    df = bars_to_df(bars)
    assert detect_inside_bar(df) == []


def test_pin_bar_bullish_long_lower_wick():
    df = bars_to_df([(99.5, 100.2, 90.0, 100.0)])  # tiny body near top, huge lower wick
    events = detect_pin_bar(df)
    assert len(events) == 1
    assert events[0].direction == "bullish"


def test_pin_bar_bearish_long_upper_wick():
    df = bars_to_df([(100.0, 110.0, 99.8, 100.2)])  # tiny body near bottom, huge upper wick
    events = detect_pin_bar(df)
    assert len(events) == 1
    assert events[0].direction == "bearish"


def test_morning_star_detected():
    bars = [
        (110, 111, 100, 101),  # large bearish, body [101,110]
        (99, 100, 98, 99.5),   # small body below c1's body low (101)
        (100, 108, 99, 107),   # bullish closing above c1's midpoint (105.5)
    ]
    df = bars_to_df(bars)
    events = detect_morning_star(df)
    assert len(events) == 1
    assert events[0].direction == "bullish"
    assert events[0].bar_index == 2


def test_evening_star_detected():
    bars = [
        (100, 110, 99, 109),   # large bullish, body [100,109]
        (110, 111, 109.5, 110.2),  # small body above c1's body high (109)
        (109, 110, 101, 102),  # bearish closing below c1's midpoint (104.5)
    ]
    df = bars_to_df(bars)
    events = detect_evening_star(df)
    assert len(events) == 1
    assert events[0].direction == "bearish"


def test_no_false_positive_morning_star_on_trending_bars():
    bars = [(100, 105, 99, 104), (104, 109, 103, 108), (108, 113, 107, 112)]
    df = bars_to_df(bars)
    assert detect_morning_star(df) == []
