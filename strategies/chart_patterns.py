"""Classical chart pattern detection: double top/bottom, head & shoulders
(and inverse), triangles/wedges/channels, flags/pennants.

These are the most subjective patterns in the whole strategies/ package.
Every detector here is ONE heuristic implementation with explicit, tunable
thresholds (price-similarity tolerance, minimum bars, slope-classification
cutoffs) — not a unique or "correct" definition. Two experienced chartists
looking at the same chart will often disagree on whether a pattern is really
there; treat this module as a starting point for calibration, never as
ground truth, and never present its output as objective fact to a user.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategies.patterns_common import PatternEvent, SwingPoint, find_swing_points


def _pct_diff(a: float, b: float) -> float:
    return abs(a - b) / max(abs(a), abs(b), 1e-9)


def detect_double_top(df: pd.DataFrame, lookback: int = 2, price_tolerance_pct: float = 0.015) -> list[PatternEvent]:
    """Double top: two swing highs of similar price (within `price_tolerance_pct`)
    with a swing low between them, CONFIRMED when price closes below that
    intervening swing low after the second top (the "neckline break").
    Reported at the confirmation bar, not the second top itself — an
    unconfirmed double top is just two similar-height peaks, not yet a
    completed pattern.
    """
    swings = find_swing_points(df, lookback=lookback)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    events = []
    for i in range(len(highs) - 1):
        top1, top2 = highs[i], highs[i + 1]
        if _pct_diff(top1.price, top2.price) > price_tolerance_pct:
            continue
        between_lows = [l for l in lows if top1.index < l.index < top2.index]
        if not between_lows:
            continue
        neckline = min(l.price for l in between_lows)
        for j in range(top2.index + 1, len(df)):
            if df["close"].iloc[j] < neckline:
                events.append(
                    PatternEvent("double_top", j, df["close_time"].iloc[j], "bearish",
                                 {"top1_index": top1.index, "top2_index": top2.index, "neckline": neckline})
                )
                break
    return events


def detect_double_bottom(df: pd.DataFrame, lookback: int = 2, price_tolerance_pct: float = 0.015) -> list[PatternEvent]:
    """Mirror of detect_double_top."""
    swings = find_swing_points(df, lookback=lookback)
    lows = [s for s in swings if s.kind == "low"]
    highs = [s for s in swings if s.kind == "high"]
    events = []
    for i in range(len(lows) - 1):
        bot1, bot2 = lows[i], lows[i + 1]
        if _pct_diff(bot1.price, bot2.price) > price_tolerance_pct:
            continue
        between_highs = [h for h in highs if bot1.index < h.index < bot2.index]
        if not between_highs:
            continue
        neckline = max(h.price for h in between_highs)
        for j in range(bot2.index + 1, len(df)):
            if df["close"].iloc[j] > neckline:
                events.append(
                    PatternEvent("double_bottom", j, df["close_time"].iloc[j], "bullish",
                                 {"bottom1_index": bot1.index, "bottom2_index": bot2.index, "neckline": neckline})
                )
                break
    return events


def detect_head_and_shoulders(df: pd.DataFrame, lookback: int = 2, shoulder_tolerance_pct: float = 0.03) -> list[PatternEvent]:
    """Head & shoulders: three consecutive swing highs where the middle one
    (the head) is strictly higher than both outer ones (the shoulders), and
    the two shoulders are within `shoulder_tolerance_pct` of each other.
    Confirmed when price closes below the neckline (the lower of the two
    intervening swing lows) after the right shoulder.
    """
    swings = find_swing_points(df, lookback=lookback)
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    events = []
    for i in range(len(highs) - 2):
        left, head, right = highs[i], highs[i + 1], highs[i + 2]
        if not (head.price > left.price and head.price > right.price):
            continue
        if _pct_diff(left.price, right.price) > shoulder_tolerance_pct:
            continue
        between = [l for l in lows if left.index < l.index < right.index]
        if len(between) < 2:
            continue
        neckline = max(l.price for l in between)  # neckline is the line through the two intervening troughs
        for j in range(right.index + 1, len(df)):
            if df["close"].iloc[j] < neckline:
                events.append(
                    PatternEvent("head_and_shoulders", j, df["close_time"].iloc[j], "bearish",
                                 {"left_shoulder": left.index, "head": head.index, "right_shoulder": right.index, "neckline": neckline})
                )
                break
    return events


def detect_inverse_head_and_shoulders(df: pd.DataFrame, lookback: int = 2, shoulder_tolerance_pct: float = 0.03) -> list[PatternEvent]:
    """Mirror of detect_head_and_shoulders, built on swing lows."""
    swings = find_swing_points(df, lookback=lookback)
    lows = [s for s in swings if s.kind == "low"]
    highs = [s for s in swings if s.kind == "high"]
    events = []
    for i in range(len(lows) - 2):
        left, head, right = lows[i], lows[i + 1], lows[i + 2]
        if not (head.price < left.price and head.price < right.price):
            continue
        if _pct_diff(left.price, right.price) > shoulder_tolerance_pct:
            continue
        between = [h for h in highs if left.index < h.index < right.index]
        if len(between) < 2:
            continue
        neckline = min(h.price for h in between)
        for j in range(right.index + 1, len(df)):
            if df["close"].iloc[j] > neckline:
                events.append(
                    PatternEvent("inverse_head_and_shoulders", j, df["close_time"].iloc[j], "bullish",
                                 {"left_shoulder": left.index, "head": head.index, "right_shoulder": right.index, "neckline": neckline})
                )
                break
    return events


@dataclass(frozen=True)
class TrendlineShape:
    label: str  # "ascending_triangle" | "descending_triangle" | "symmetrical_triangle" | "rising_wedge" | "falling_wedge" | "channel_up" | "channel_down" | "channel_flat" | "none"
    upper_slope: float
    lower_slope: float
    start_index: int
    end_index: int


def classify_trendline_shape(
    df: pd.DataFrame,
    window: int = 20,
    flat_slope_threshold: float = 0.001,
    converging_ratio_threshold: float = 0.3,
) -> TrendlineShape:
    """Fit a linear regression trendline through the highs and another
    through the lows over the last `window` bars, then classify the shape
    from the two slopes (normalized by average price, so the thresholds are
    scale-independent). This is a simplification: real triangles/wedges/
    channels are drawn through SWING points by chartists, not every bar's
    high/low by regression — this heuristic trades some fidelity for being
    mechanically well-defined and testable. Treat the label as a rough
    classification, not a precise pattern confirmation.

    Classification rules (all slopes normalized as slope / mean_price):
      - both slopes' absolute value < flat_slope_threshold -> "channel_flat"
      - both slopes point the same direction (up or down) and are roughly
        parallel (their difference is small relative to their magnitude) -> "channel_up"/"channel_down"
      - both slopes point the same direction but CONVERGE (upper falling
        faster than lower rises, or vice versa, narrowing the range) -> "rising_wedge" (both up, converging) / "falling_wedge" (both down, converging)
      - upper slope ~flat, lower slope rising -> "ascending_triangle"
      - upper slope falling, lower slope ~flat -> "descending_triangle"
      - upper slope falling, lower slope rising (converging from both sides) -> "symmetrical_triangle"
      - anything else -> "none"

    `flat_slope_threshold` default (0.001, i.e. 0.1% of mean price per bar) is
    tuned for typical intraday index/stock price series — retune per
    instrument if you see too many/few "flat" classifications.
    """
    if len(df) < window:
        return TrendlineShape("none", 0.0, 0.0, 0, len(df) - 1 if len(df) else 0)

    recent = df.iloc[-window:]
    x = np.arange(window)
    mean_price = recent["close"].mean()
    if mean_price == 0:
        return TrendlineShape("none", 0.0, 0.0, recent.index[0], recent.index[-1])

    upper_slope = np.polyfit(x, recent["high"].to_numpy(), 1)[0] / mean_price
    lower_slope = np.polyfit(x, recent["low"].to_numpy(), 1)[0] / mean_price

    start_index = int(recent.index[0])
    end_index = int(recent.index[-1])

    upper_flat = abs(upper_slope) < flat_slope_threshold
    lower_flat = abs(lower_slope) < flat_slope_threshold

    if upper_flat and lower_flat:
        label = "channel_flat"
    elif upper_flat and lower_slope > 0:
        label = "ascending_triangle"
    elif lower_flat and upper_slope < 0:
        label = "descending_triangle"
    elif upper_slope < 0 and lower_slope > 0:
        label = "symmetrical_triangle"
    elif upper_slope > 0 and lower_slope > 0:
        label = "rising_wedge" if (upper_slope - lower_slope) < -converging_ratio_threshold * abs(upper_slope) else "channel_up"
    elif upper_slope < 0 and lower_slope < 0:
        label = "falling_wedge" if (lower_slope - upper_slope) < -converging_ratio_threshold * abs(lower_slope) else "channel_down"
    else:
        label = "none"

    return TrendlineShape(label, float(upper_slope), float(lower_slope), start_index, end_index)


def detect_flag_or_pennant(
    df: pd.DataFrame,
    pole_lookback: int = 10,
    pole_atr_multiple: float = 3.0,
    consolidation_window: int = 8,
    atr_period: int = 14,
) -> list[PatternEvent]:
    """Flag/pennant: a strong directional "pole" move (net range over
    `pole_lookback` bars >= `pole_atr_multiple` x ATR) immediately followed
    by a `consolidation_window`-bar consolidation whose own range is much
    smaller than the pole. Classified as a pennant if the consolidation's
    trendline shape converges (a triangle/wedge shape), or a flag if it's a
    parallel channel. Reported at the last bar of the consolidation window.
    """
    if len(df) < pole_lookback + consolidation_window + atr_period:
        return []

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_series = tr.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()

    events = []
    for end in range(pole_lookback + consolidation_window, len(df) + 1):
        consolidation_start = end - consolidation_window
        pole_start = consolidation_start - pole_lookback
        atr_val = atr_series.iloc[consolidation_start - 1]
        if pd.isna(atr_val) or atr_val == 0:
            continue

        pole = df.iloc[pole_start:consolidation_start]
        pole_move = pole["close"].iloc[-1] - pole["close"].iloc[0]
        if abs(pole_move) < pole_atr_multiple * atr_val:
            continue

        consolidation = df.iloc[consolidation_start:end]
        consolidation_range = consolidation["high"].max() - consolidation["low"].min()
        if consolidation_range >= abs(pole_move) * 0.5:
            continue  # consolidation isn't tight enough relative to the pole

        shape = classify_trendline_shape(consolidation, window=len(consolidation))
        direction = "bullish" if pole_move > 0 else "bearish"
        is_pennant = shape.label in ("symmetrical_triangle", "ascending_triangle", "descending_triangle", "rising_wedge", "falling_wedge")
        pattern_name = "pennant" if is_pennant else "flag"
        events.append(
            PatternEvent(pattern_name, end - 1, df["close_time"].iloc[end - 1], direction,
                         {"pole_move": float(pole_move), "atr": float(atr_val), "shape": shape.label})
        )
    return events
