"""Candlestick pattern detection. Pure functions: DataFrame in, list[PatternEvent] out.

Every rule below is ONE reasonable, commonly-taught interpretation of these
patterns, not an objective/unique definition — thresholds like "body must be
<= 30% of range" vary between sources. Parameters are exposed so you can
retune them; defaults are documented per function.

Implementation note: every detector pulls its columns to numpy arrays once
up front and indexes those in its loop, rather than using df.iloc[i]/
df.iterrows() per row — each of those constructs a new pandas Series per
row, which dominates runtime once df is a few thousand rows (candlestick
detection on ~5000 bars went from ~9s to ~0.1s after this change). The
per-row arithmetic and thresholds are unchanged from the row-wise version.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.patterns_common import PatternEvent


def detect_doji(df: pd.DataFrame, body_to_range_max: float = 0.1) -> list[PatternEvent]:
    """Doji: body is a small fraction of the bar's total range (open ~= close)."""
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    body = np.abs(c - o)
    rng = h - l
    events = []
    for i in range(len(df)):
        if rng[i] == 0:
            continue
        ratio = body[i] / rng[i]
        if ratio <= body_to_range_max:
            events.append(PatternEvent("doji", i, close_times[i], "neutral", {"body_to_range": float(ratio)}))
    return events


def detect_hammer(df: pd.DataFrame, lower_wick_min_ratio: float = 2.0, upper_wick_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Hammer (bullish reversal at a low): small body near the top of the range,
    lower wick at least `lower_wick_min_ratio`x the body, small/no upper wick."""
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    body = np.abs(c - o)
    rng = h - l
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    events = []
    for i in range(len(df)):
        if body[i] == 0 or rng[i] == 0:
            continue
        if lower[i] >= lower_wick_min_ratio * body[i] and upper[i] <= upper_wick_max_ratio * rng[i]:
            events.append(PatternEvent("hammer", i, close_times[i], "bullish", {"lower_wick_ratio": float(lower[i] / body[i])}))
    return events


def detect_shooting_star(df: pd.DataFrame, upper_wick_min_ratio: float = 2.0, lower_wick_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Shooting star (bearish reversal at a high): mirror of hammer — long upper
    wick, small/no lower wick, small body near the bottom of the range."""
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    body = np.abs(c - o)
    rng = h - l
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    events = []
    for i in range(len(df)):
        if body[i] == 0 or rng[i] == 0:
            continue
        if upper[i] >= upper_wick_min_ratio * body[i] and lower[i] <= lower_wick_max_ratio * rng[i]:
            events.append(PatternEvent("shooting_star", i, close_times[i], "bearish", {"upper_wick_ratio": float(upper[i] / body[i])}))
    return events


def detect_engulfing(df: pd.DataFrame) -> list[PatternEvent]:
    """Bullish engulfing: a bearish candle followed by a bullish candle whose
    body fully contains the prior candle's body (open <= prior close, close >=
    prior open). Bearish engulfing is the mirror."""
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    is_bullish = c > o
    events = []
    for i in range(1, len(df)):
        prev_bull, curr_bull = is_bullish[i - 1], is_bullish[i]
        if not prev_bull and curr_bull:
            if o[i] <= c[i - 1] and c[i] >= o[i - 1]:
                events.append(PatternEvent("engulfing", i, close_times[i], "bullish", {}))
                continue
        if prev_bull and not curr_bull:
            if o[i] >= c[i - 1] and c[i] <= o[i - 1]:
                events.append(PatternEvent("engulfing", i, close_times[i], "bearish", {}))
    return events


def detect_harami(df: pd.DataFrame) -> list[PatternEvent]:
    """Harami: a large candle followed by a small candle whose body is fully
    CONTAINED within the prior candle's body (the inverse containment of
    engulfing)."""
    o, c = df["open"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    is_bullish = c > o
    body = np.abs(c - o)
    body_low = np.minimum(o, c)
    body_high = np.maximum(o, c)
    events = []
    for i in range(1, len(df)):
        if body_low[i] >= body_low[i - 1] and body_high[i] <= body_high[i - 1] and body[i] < body[i - 1]:
            if not is_bullish[i - 1] and is_bullish[i]:
                direction = "bullish"
            elif is_bullish[i - 1] and not is_bullish[i]:
                direction = "bearish"
            else:
                direction = "neutral"
            events.append(PatternEvent("harami", i, close_times[i], direction, {}))
    return events


def detect_inside_bar(df: pd.DataFrame) -> list[PatternEvent]:
    """Inside bar: current bar's high/low is fully within the prior bar's high/low
    (a range containment pattern, distinct from harami which is body containment)."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    close_times = df["close_time"].to_numpy()
    events = []
    for i in range(1, len(df)):
        if h[i] <= h[i - 1] and l[i] >= l[i - 1]:
            events.append(PatternEvent("inside_bar", i, close_times[i], "neutral", {}))
    return events


def detect_pin_bar(df: pd.DataFrame, wick_min_ratio: float = 2.0, body_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Pin bar: a bar with a small body and a single long wick (either side),
    signalling rejection. Direction is bullish if the long wick is on the
    LOWER side (rejection of lower prices) and bearish if on the UPPER side."""
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    body = np.abs(c - o)
    rng = h - l
    upper = h - np.maximum(o, c)
    lower = np.minimum(o, c) - l
    events = []
    for i in range(len(df)):
        if rng[i] == 0:
            continue
        if body[i] / rng[i] > body_max_ratio:
            continue
        body_floor = max(body[i], 1e-9)
        if lower[i] >= wick_min_ratio * body_floor and lower[i] > upper[i]:
            events.append(PatternEvent("pin_bar", i, close_times[i], "bullish", {"wick_ratio": float(lower[i] / rng[i])}))
        elif upper[i] >= wick_min_ratio * body_floor and upper[i] > lower[i]:
            events.append(PatternEvent("pin_bar", i, close_times[i], "bearish", {"wick_ratio": float(upper[i] / rng[i])}))
    return events


def detect_morning_star(df: pd.DataFrame, small_body_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Morning star (bullish reversal, 3 bars): large bearish candle, then a
    small-bodied candle that gaps down (or at least closes below the first
    candle's body), then a bullish candle closing above the midpoint of the
    first candle's body."""
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    is_bullish = c > o
    body = np.abs(c - o)
    rng = h - l
    body_low = np.minimum(o, c)
    body_high = np.maximum(o, c)
    midpoint = (o + c) / 2
    events = []
    for i in range(2, len(df)):
        i1, i2, i3 = i - 2, i - 1, i
        if is_bullish[i1]:
            continue
        c2_range = rng[i2]
        if not (c2_range == 0 or body[i2] / c2_range <= small_body_max_ratio):
            continue
        if body_high[i2] > body_low[i1]:
            continue
        if is_bullish[i3] and c[i3] > midpoint[i1]:
            events.append(PatternEvent("morning_star", i, close_times[i3], "bullish", {}))
    return events


def detect_evening_star(df: pd.DataFrame, small_body_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Evening star (bearish reversal, 3 bars): mirror of morning star."""
    o, h, l, c = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    close_times = df["close_time"].to_numpy()
    is_bullish = c > o
    body = np.abs(c - o)
    rng = h - l
    body_low = np.minimum(o, c)
    body_high = np.maximum(o, c)
    midpoint = (o + c) / 2
    events = []
    for i in range(2, len(df)):
        i1, i2, i3 = i - 2, i - 1, i
        if not is_bullish[i1]:
            continue
        c2_range = rng[i2]
        if not (c2_range == 0 or body[i2] / c2_range <= small_body_max_ratio):
            continue
        if body_low[i2] < body_high[i1]:
            continue
        if not is_bullish[i3] and c[i3] < midpoint[i1]:
            events.append(PatternEvent("evening_star", i, close_times[i3], "bearish", {}))
    return events


def detect_all_candlestick_patterns(df: pd.DataFrame) -> list[PatternEvent]:
    detectors = [
        detect_doji, detect_hammer, detect_shooting_star, detect_engulfing,
        detect_harami, detect_inside_bar, detect_pin_bar, detect_morning_star, detect_evening_star,
    ]
    events: list[PatternEvent] = []
    for detector in detectors:
        events.extend(detector(df))
    return sorted(events, key=lambda e: e.bar_index)
