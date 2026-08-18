"""Candlestick pattern detection. Pure functions: DataFrame in, list[PatternEvent] out.

Every rule below is ONE reasonable, commonly-taught interpretation of these
patterns, not an objective/unique definition — thresholds like "body must be
<= 30% of range" vary between sources. Parameters are exposed so you can
retune them; defaults are documented per function.
"""
from __future__ import annotations

import pandas as pd

from strategies.patterns_common import PatternEvent


def _body(row) -> float:
    return abs(row["close"] - row["open"])


def _range(row) -> float:
    return row["high"] - row["low"]


def _upper_wick(row) -> float:
    return row["high"] - max(row["open"], row["close"])


def _lower_wick(row) -> float:
    return min(row["open"], row["close"]) - row["low"]


def _is_bullish(row) -> bool:
    return row["close"] > row["open"]


def detect_doji(df: pd.DataFrame, body_to_range_max: float = 0.1) -> list[PatternEvent]:
    """Doji: body is a small fraction of the bar's total range (open ~= close)."""
    events = []
    for i, row in df.iterrows():
        rng = _range(row)
        if rng == 0:
            continue
        if _body(row) / rng <= body_to_range_max:
            events.append(PatternEvent("doji", i, row["close_time"], "neutral", {"body_to_range": _body(row) / rng}))
    return events


def detect_hammer(df: pd.DataFrame, lower_wick_min_ratio: float = 2.0, upper_wick_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Hammer (bullish reversal at a low): small body near the top of the range,
    lower wick at least `lower_wick_min_ratio`x the body, small/no upper wick."""
    events = []
    for i, row in df.iterrows():
        body = _body(row)
        rng = _range(row)
        if body == 0 or rng == 0:
            continue
        lower = _lower_wick(row)
        upper = _upper_wick(row)
        if lower >= lower_wick_min_ratio * body and upper <= upper_wick_max_ratio * rng:
            events.append(PatternEvent("hammer", i, row["close_time"], "bullish", {"lower_wick_ratio": lower / body}))
    return events


def detect_shooting_star(df: pd.DataFrame, upper_wick_min_ratio: float = 2.0, lower_wick_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Shooting star (bearish reversal at a high): mirror of hammer — long upper
    wick, small/no lower wick, small body near the bottom of the range."""
    events = []
    for i, row in df.iterrows():
        body = _body(row)
        rng = _range(row)
        if body == 0 or rng == 0:
            continue
        upper = _upper_wick(row)
        lower = _lower_wick(row)
        if upper >= upper_wick_min_ratio * body and lower <= lower_wick_max_ratio * rng:
            events.append(PatternEvent("shooting_star", i, row["close_time"], "bearish", {"upper_wick_ratio": upper / body}))
    return events


def detect_engulfing(df: pd.DataFrame) -> list[PatternEvent]:
    """Bullish engulfing: a bearish candle followed by a bullish candle whose
    body fully contains the prior candle's body (open <= prior close, close >=
    prior open). Bearish engulfing is the mirror."""
    events = []
    for i in range(1, len(df)):
        prev, curr = df.iloc[i - 1], df.iloc[i]
        if not _is_bullish(prev) and _is_bullish(curr):
            if curr["open"] <= prev["close"] and curr["close"] >= prev["open"]:
                events.append(PatternEvent("engulfing", i, curr["close_time"], "bullish", {}))
                continue
        if _is_bullish(prev) and not _is_bullish(curr):
            if curr["open"] >= prev["close"] and curr["close"] <= prev["open"]:
                events.append(PatternEvent("engulfing", i, curr["close_time"], "bearish", {}))
    return events


def detect_harami(df: pd.DataFrame) -> list[PatternEvent]:
    """Harami: a large candle followed by a small candle whose body is fully
    CONTAINED within the prior candle's body (the inverse containment of
    engulfing)."""
    events = []
    for i in range(1, len(df)):
        prev, curr = df.iloc[i - 1], df.iloc[i]
        prev_body_low, prev_body_high = min(prev["open"], prev["close"]), max(prev["open"], prev["close"])
        curr_body_low, curr_body_high = min(curr["open"], curr["close"]), max(curr["open"], curr["close"])
        if curr_body_low >= prev_body_low and curr_body_high <= prev_body_high and _body(curr) < _body(prev):
            direction = "bullish" if not _is_bullish(prev) and _is_bullish(curr) else (
                "bearish" if _is_bullish(prev) and not _is_bullish(curr) else "neutral"
            )
            events.append(PatternEvent("harami", i, curr["close_time"], direction, {}))
    return events


def detect_inside_bar(df: pd.DataFrame) -> list[PatternEvent]:
    """Inside bar: current bar's high/low is fully within the prior bar's high/low
    (a range containment pattern, distinct from harami which is body containment)."""
    events = []
    for i in range(1, len(df)):
        prev, curr = df.iloc[i - 1], df.iloc[i]
        if curr["high"] <= prev["high"] and curr["low"] >= prev["low"]:
            events.append(PatternEvent("inside_bar", i, curr["close_time"], "neutral", {}))
    return events


def detect_pin_bar(df: pd.DataFrame, wick_min_ratio: float = 2.0, body_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Pin bar: a bar with a small body and a single long wick (either side),
    signalling rejection. Direction is bullish if the long wick is on the
    LOWER side (rejection of lower prices) and bearish if on the UPPER side."""
    events = []
    for i, row in df.iterrows():
        rng = _range(row)
        if rng == 0:
            continue
        body = _body(row)
        upper, lower = _upper_wick(row), _lower_wick(row)
        if body / rng > body_max_ratio:
            continue
        if lower >= wick_min_ratio * max(body, 1e-9) and lower > upper:
            events.append(PatternEvent("pin_bar", i, row["close_time"], "bullish", {"wick_ratio": lower / rng}))
        elif upper >= wick_min_ratio * max(body, 1e-9) and upper > lower:
            events.append(PatternEvent("pin_bar", i, row["close_time"], "bearish", {"wick_ratio": upper / rng}))
    return events


def detect_morning_star(df: pd.DataFrame, small_body_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Morning star (bullish reversal, 3 bars): large bearish candle, then a
    small-bodied candle that gaps down (or at least closes below the first
    candle's body), then a bullish candle closing above the midpoint of the
    first candle's body."""
    events = []
    for i in range(2, len(df)):
        c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
        c1_body_low = min(c1["open"], c1["close"])
        c1_midpoint = (c1["open"] + c1["close"]) / 2
        c2_range = _range(c2)
        if not _is_bullish(c1):
            if c2_range == 0 or _body(c2) / c2_range <= small_body_max_ratio:
                if max(c2["open"], c2["close"]) <= c1_body_low:
                    if _is_bullish(c3) and c3["close"] > c1_midpoint:
                        events.append(PatternEvent("morning_star", i, c3["close_time"], "bullish", {}))
    return events


def detect_evening_star(df: pd.DataFrame, small_body_max_ratio: float = 0.3) -> list[PatternEvent]:
    """Evening star (bearish reversal, 3 bars): mirror of morning star."""
    events = []
    for i in range(2, len(df)):
        c1, c2, c3 = df.iloc[i - 2], df.iloc[i - 1], df.iloc[i]
        c1_body_high = max(c1["open"], c1["close"])
        c1_midpoint = (c1["open"] + c1["close"]) / 2
        c2_range = _range(c2)
        if _is_bullish(c1):
            if c2_range == 0 or _body(c2) / c2_range <= small_body_max_ratio:
                if min(c2["open"], c2["close"]) >= c1_body_high:
                    if not _is_bullish(c3) and c3["close"] < c1_midpoint:
                        events.append(PatternEvent("evening_star", i, c3["close_time"], "bearish", {}))
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
