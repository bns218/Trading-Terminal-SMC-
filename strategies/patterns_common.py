"""Shared types and swing-point detection used by strategies/smc.py,
strategies/ict.py, and strategies/chart_patterns.py.

Swing detection algorithm (stated explicitly, per the project's rule that
discretionary concepts being mechanised must have their rule documented, not
presented as objective truth):

  A bar at index i is a SWING HIGH if its high is strictly greater than the
  high of every bar in [i-lookback, i-1] and every bar in [i+1, i+lookback].
  A SWING LOW is the mirror (strictly lower than both neighbouring windows).

  This is the standard "fractal" definition (Williams Fractals use lookback=2).
  Default lookback here is 2. A larger lookback finds fewer, more significant
  swings; a smaller lookback finds more, noisier swings. This is one
  reasonable choice among several (e.g. ZigZag-percentage-based swing
  detection is a common alternative that reacts to price move magnitude
  rather than bar count) — not the only valid definition.

  Because a swing point needs `lookback` bars AFTER it to confirm, swing
  points are only confirmed `lookback` bars later than where they occur —
  i.e. detecting a swing high at bar i is only possible once bar i+lookback
  has closed. Callers doing walk-forward/backtest analysis must respect this
  lag themselves (this module does not hide it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

import pandas as pd

Direction = Literal["bullish", "bearish", "neutral"]
SwingType = Literal["high", "low"]


@dataclass(frozen=True)
class PatternEvent:
    pattern: str
    bar_index: int
    timestamp: datetime  # close_time of the bar the pattern is detected on/confirmed at
    direction: Direction
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SwingPoint:
    index: int
    timestamp: datetime
    price: float
    kind: SwingType


def find_swing_points(df: pd.DataFrame, lookback: int = 2) -> list[SwingPoint]:
    if lookback < 1:
        raise ValueError("lookback must be >= 1")
    swings: list[SwingPoint] = []
    n = len(df)
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    for i in range(lookback, n - lookback):
        window_highs_left = highs[i - lookback : i]
        window_highs_right = highs[i + 1 : i + 1 + lookback]
        if highs[i] > window_highs_left.max() and highs[i] > window_highs_right.max():
            swings.append(SwingPoint(i, df["close_time"].iloc[i], float(highs[i]), "high"))
            continue  # a bar is at most one swing type in this simple rule
        window_lows_left = lows[i - lookback : i]
        window_lows_right = lows[i + 1 : i + 1 + lookback]
        if lows[i] < window_lows_left.min() and lows[i] < window_lows_right.min():
            swings.append(SwingPoint(i, df["close_time"].iloc[i], float(lows[i]), "low"))
    return swings


def classify_structure(swings: list[SwingPoint]) -> list[str]:
    """Label each swing HH/HL/LH/LL relative to the previous swing of the SAME
    kind (i.e. compare this high to the prior high, this low to the prior low —
    the standard SMC convention, not consecutive swings regardless of kind).
    The first swing of each kind has no label (nothing to compare against).
    """
    labels: list[str] = []
    last_high: float | None = None
    last_low: float | None = None
    for s in swings:
        if s.kind == "high":
            if last_high is None:
                labels.append("")
            else:
                labels.append("HH" if s.price > last_high else "LH")
            last_high = s.price
        else:
            if last_low is None:
                labels.append("")
            else:
                labels.append("HL" if s.price > last_low else "LL")
            last_low = s.price
    return labels
