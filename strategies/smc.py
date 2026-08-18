"""Smart Money Concepts (SMC): market structure, BOS/CHOCH, order blocks, fair
value gaps, liquidity zones/sweeps, premium/discount zones.

These are discretionary trading concepts being mechanised into fixed rules.
Every function below documents ONE reasonable interpretation of its concept —
different SMC educators define these differently in ways that would change
detection results. Treat this as a starting point to calibrate, not ground
truth.
"""
from __future__ import annotations

import pandas as pd

from strategies.patterns_common import PatternEvent, SwingPoint, classify_structure, find_swing_points


def detect_bos_choch(df: pd.DataFrame, lookback: int = 2) -> list[PatternEvent]:
    """Break of Structure (BOS) / Change of Character (CHOCH).

    Rule: track the most recent confirmed swing high and swing low. Maintain
    a "trend" state (bullish/bearish/unknown) based on the HH/HL vs LH/LL
    sequence. When a bar's CLOSE (not wick) breaks beyond the most recent
    relevant swing point:
      - if the break is in the direction of the current trend -> BOS
        (continuation: trend keeps making new highs in an uptrend, or new
        lows in a downtrend)
      - if the break is AGAINST the current trend -> CHOCH (the first sign of
        a potential reversal)
    Trend starts "unknown" until the first BOS/CHOCH establishes a direction.

    This only uses swing points confirmed by find_swing_points (see that
    function's docstring for the fractal lag caveat: a swing at index i is
    only knowable once bar i+lookback has closed).
    """
    swings = find_swing_points(df, lookback=lookback)
    events: list[PatternEvent] = []

    last_swing_high: SwingPoint | None = None
    last_swing_low: SwingPoint | None = None
    trend: str = "unknown"

    swing_iter = iter(swings)
    next_swing = next(swing_iter, None)

    for i in range(len(df)):
        # Update known swing points as we walk forward past their confirmation bar.
        while next_swing is not None and next_swing.index <= i:
            if next_swing.kind == "high":
                last_swing_high = next_swing
            else:
                last_swing_low = next_swing
            next_swing = next(swing_iter, None)

        close = df["close"].iloc[i]

        if last_swing_high is not None and i > last_swing_high.index and close > last_swing_high.price:
            is_continuation = trend in ("bullish", "unknown")
            label = "BOS" if is_continuation else "CHOCH"
            events.append(
                PatternEvent(label, i, df["close_time"].iloc[i], "bullish",
                             {"broken_swing_index": last_swing_high.index, "broken_level": last_swing_high.price})
            )
            trend = "bullish"
            last_swing_high = None  # consumed — avoid re-firing on the same level every subsequent bar

        if last_swing_low is not None and i > last_swing_low.index and close < last_swing_low.price:
            is_continuation = trend in ("bearish", "unknown")
            label = "BOS" if is_continuation else "CHOCH"
            events.append(
                PatternEvent(label, i, df["close_time"].iloc[i], "bearish",
                             {"broken_swing_index": last_swing_low.index, "broken_level": last_swing_low.price})
            )
            trend = "bearish"
            last_swing_low = None

    return events


def detect_fair_value_gaps(df: pd.DataFrame) -> list[PatternEvent]:
    """Fair Value Gap (FVG) / imbalance: a 3-candle pattern where candle 1 and
    candle 3 leave a price gap that candle 2 does not fill.
      Bullish FVG: candle1.high < candle3.low  (gap is [c1.high, c3.low])
      Bearish FVG: candle1.low  > candle3.high (gap is [c3.high, c1.low])
    Detected/reported at candle 3's index (the bar that completes the gap).
    """
    events = []
    for i in range(2, len(df)):
        c1, c3 = df.iloc[i - 2], df.iloc[i]
        if c1["high"] < c3["low"]:
            events.append(
                PatternEvent("fair_value_gap", i, df["close_time"].iloc[i], "bullish",
                             {"gap_low": float(c1["high"]), "gap_high": float(c3["low"])})
            )
        elif c1["low"] > c3["high"]:
            events.append(
                PatternEvent("fair_value_gap", i, df["close_time"].iloc[i], "bearish",
                             {"gap_low": float(c3["high"]), "gap_high": float(c1["low"])})
            )
    return events


def detect_order_blocks(df: pd.DataFrame, displacement_atr_multiple: float = 1.5, atr_period: int = 14) -> list[PatternEvent]:
    """Order block: the last opposite-direction candle immediately before a
    displacement move (a candle whose range is at least
    `displacement_atr_multiple` times the ATR at that point).

    Bullish order block: the last BEARISH candle before a strong bullish
    displacement candle. Bearish order block: the last BULLISH candle before
    a strong bearish displacement candle. Reported at the order-block
    candle's own index (not the displacement candle's).

    This is a simplified, commonly-taught definition. Some traders require
    the displacement candle to also cause a BOS; that additional condition
    is NOT applied here to keep this function independently testable —
    combine with detect_bos_choch() results yourself if you want that filter.
    """
    if len(df) < atr_period + 2:
        return []

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_series = tr.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()

    events = []
    for i in range(1, len(df)):
        atr_val = atr_series.iloc[i]
        if pd.isna(atr_val) or atr_val == 0:
            continue
        curr = df.iloc[i]
        curr_range = curr["high"] - curr["low"]
        if curr_range < displacement_atr_multiple * atr_val:
            continue
        is_bullish_displacement = curr["close"] > curr["open"]
        prev = df.iloc[i - 1]
        prev_is_bearish = prev["close"] < prev["open"]
        prev_is_bullish = prev["close"] > prev["open"]
        if is_bullish_displacement and prev_is_bearish:
            events.append(
                PatternEvent("order_block", i - 1, df["close_time"].iloc[i - 1], "bullish",
                             {"displacement_index": i, "ob_high": float(prev["high"]), "ob_low": float(prev["low"])})
            )
        elif not is_bullish_displacement and prev_is_bullish:
            events.append(
                PatternEvent("order_block", i - 1, df["close_time"].iloc[i - 1], "bearish",
                             {"displacement_index": i, "ob_high": float(prev["high"]), "ob_low": float(prev["low"])})
            )
    return events


def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 2, equal_tolerance_pct: float = 0.05) -> list[PatternEvent]:
    """Liquidity sweep: price wicks beyond a prior swing high/low (grabbing the
    resting liquidity presumed to sit just beyond it — stops/orders), then
    CLOSES back on the other side of that level within the same bar.

    Bearish sweep (sweep of a swing high): bar's high > swing high level, but
    bar's close < swing high level. Bullish sweep (sweep of a swing low): the
    mirror. `equal_tolerance_pct` is unused for single-level sweeps but kept
    as a parameter for consistency with equal-highs/lows liquidity-pool
    identification, which callers can build on top of the returned swings.
    """
    swings = find_swing_points(df, lookback=lookback)
    events = []
    for swing in swings:
        for i in range(swing.index + 1, len(df)):
            bar = df.iloc[i]
            if swing.kind == "high":
                if bar["high"] > swing.price and bar["close"] < swing.price:
                    events.append(
                        PatternEvent("liquidity_sweep", i, df["close_time"].iloc[i], "bearish",
                                     {"swept_index": swing.index, "swept_level": swing.price})
                    )
                    break  # only the first sweep of this level counts
                if bar["close"] > swing.price:
                    break  # level was broken cleanly (BOS), not swept — stop looking
            else:
                if bar["low"] < swing.price and bar["close"] > swing.price:
                    events.append(
                        PatternEvent("liquidity_sweep", i, df["close_time"].iloc[i], "bullish",
                                     {"swept_index": swing.index, "swept_level": swing.price})
                    )
                    break
                if bar["close"] < swing.price:
                    break
    return sorted(events, key=lambda e: e.bar_index)


def premium_discount_zone(price: float, range_low: float, range_high: float) -> str:
    """Classify `price` within [range_low, range_high] as "premium" (upper
    50%), "discount" (lower 50%), or "equilibrium" (exactly the midpoint).
    The range is typically the most recent significant swing-low-to-swing-high
    leg — callers choose which swing points define that range. The 50%
    midpoint convention is the simplest common definition; some traders use a
    62%/79% OTE band instead — that variant is not implemented here.
    """
    if range_high <= range_low:
        raise ValueError("range_high must be > range_low")
    midpoint = (range_low + range_high) / 2
    if price > midpoint:
        return "premium"
    if price < midpoint:
        return "discount"
    return "equilibrium"
