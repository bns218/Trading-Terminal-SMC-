"""ICT (Inner Circle Trader) concepts: displacement and kill-zone tagging,
built on top of the SMC layer (strategies/smc.py) rather than duplicating it.
"""
from __future__ import annotations

import pandas as pd

from config.ict_settings import active_kill_zones
from strategies.patterns_common import PatternEvent


def detect_displacement(df: pd.DataFrame, atr_period: int = 14, displacement_atr_multiple: float = 2.0) -> list[PatternEvent]:
    """Displacement: a candle whose range is at least `displacement_atr_multiple`
    times the ATR at that point AND whose body is at least 70% of its own
    range (i.e. a strong, mostly-one-directional move, not a big-range
    indecision candle with long wicks both ways). This 70% body threshold is
    an explicit, tunable choice — not a standard fixed by ICT literature.
    """
    if len(df) < atr_period + 1:
        return []

    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_series = tr.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()

    events = []
    for i in range(len(df)):
        atr_val = atr_series.iloc[i]
        if pd.isna(atr_val) or atr_val == 0:
            continue
        row = df.iloc[i]
        rng = row["high"] - row["low"]
        if rng == 0 or rng < displacement_atr_multiple * atr_val:
            continue
        body = abs(row["close"] - row["open"])
        if body / rng < 0.7:
            continue
        direction = "bullish" if row["close"] > row["open"] else "bearish"
        events.append(
            PatternEvent("displacement", i, df["close_time"].iloc[i], direction,
                         {"range": float(rng), "atr": float(atr_val), "atr_multiple": float(rng / atr_val)})
        )
    return events


def tag_kill_zones(df: pd.DataFrame) -> list[PatternEvent]:
    """Tag every bar whose close_time falls inside one or more ICT kill zones.
    One PatternEvent per bar per active zone (a bar can be in more than one,
    e.g. overlapping window definitions)."""
    events = []
    for i in range(len(df)):
        ts = df["close_time"].iloc[i]
        for zone_name in active_kill_zones(ts):
            events.append(PatternEvent("kill_zone", i, ts, "neutral", {"zone": zone_name}))
    return events


def displacement_in_kill_zone(df: pd.DataFrame, atr_period: int = 14, displacement_atr_multiple: float = 2.0) -> list[PatternEvent]:
    """Displacement candles that occur DURING an active kill zone — the ICT
    setup of highest interest (a strong directional move during a session's
    highest-liquidity window). Built by intersecting detect_displacement()
    and tag_kill_zones() rather than re-implementing either rule."""
    displacements = {e.bar_index: e for e in detect_displacement(df, atr_period, displacement_atr_multiple)}
    kill_zone_bars = {e.bar_index for e in tag_kill_zones(df)}
    return [displacements[i] for i in sorted(displacements) if i in kill_zone_bars]
