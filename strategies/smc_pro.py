"""Python port of the "SMC Pro [Nifty/Sensex/BankNifty]" Pine Script (TradingView)
indicator supplied by the user, adapted to this project's pandas/typed-dataclass
strategy conventions.

This is a fresh, self-contained interpretation — it does NOT reuse
strategies/smc.py's BOS/order-block/FVG functions, because the Pine source
defines those events slightly differently from what's already implemented
there (order blocks are anchored to the BOS bar itself by walking backward
for the last opposite-coloured candle, not to a generic ATR-displacement
candle; FVGs track a fill/inverse lifecycle rather than being reported once).
Mixing both definitions under the same function names would be confusing, so
this stays a parallel module. Both are non-repainting for the same reason:
every level is only known once find_swing_points's fractal lag has passed.

Kept from the Pine source: swing-anchored order blocks with mitigation ->
breaker-block flip, FVG with inverse-FVG flip on full fill, premium/discount/
equilibrium zone from the latest known swing range, opening-range breakout
(ORB) levels, and a weighted 0-100 trade score gating BUY/SELL signals with
ATR+order-block based SL and R-multiple TP1/TP2/TP3.

Left out (documented, not silently dropped): equal-highs/equal-lows labels,
liquidity-sweep labels (strategies/smc.py already has a general version of
this — detect_liquidity_sweeps), and the on-chart text dashboard table (the
computed values it would show — session state, ADX regime, scores — are all
in this module's output; only the literal table rendering was skipped).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from strategies.indicators import adx as calc_adx, atr as calc_atr, ema as calc_ema, vwap_session, volume_stats
from strategies.patterns_common import find_swing_points

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Zone:
    kind: str  # "order_block" | "breaker_block" | "fair_value_gap"
    bias: str  # "bullish" | "bearish"
    top: float
    bottom: float
    start_index: int
    start_time: datetime
    end_index: int | None  # None = still active, extend to the latest bar
    is_inverse: bool = False


@dataclass(frozen=True)
class PremiumDiscountZone:
    range_high: float
    range_low: float
    premium_level: float
    equilibrium_level: float
    discount_level: float
    as_of_index: int


@dataclass(frozen=True)
class OpeningRange:
    session_date: str
    high: float
    low: float
    locked: bool
    start_index: int


@dataclass(frozen=True)
class TradeSignal:
    bar_index: int
    timestamp: datetime
    direction: str  # "long" | "short"
    score: int
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    reasons: dict = field(default_factory=dict)


def detect_order_blocks_and_breakers(
    df: pd.DataFrame,
    swing_len: int = 7,
    search_window: int = 30,
    max_active: int = 3,
    breaker_on_mitigation: bool = True,
) -> list[Zone]:
    """Order block anchored at a swing BOS: on a swing bullish break, walk
    backward from the break bar for the last BEARISH candle -> that candle's
    high/low is the bullish order block (mirror for a bearish break). Once
    price closes back through the block, it is dropped and — if
    `breaker_on_mitigation` — replaced with an open-ended breaker block of
    the opposite bias starting at the mitigation bar (matches the Pine
    source's optional "Breaker Blocks" toggle). Only the most recent
    `max_active` order blocks per side are tracked, mirroring the Pine
    source's array-with-max-size behaviour.
    """
    swings = find_swing_points(df, lookback=swing_len)
    zones: list[Zone] = []

    swing_high_level: float | None = None
    swing_high_crossed = True
    swing_low_level: float | None = None
    swing_low_crossed = True

    active_bull_obs: list[Zone] = []
    active_bear_obs: list[Zone] = []

    swing_iter = iter(swings)
    next_swing = next(swing_iter, None)

    for i in range(len(df)):
        while next_swing is not None and next_swing.index <= i:
            if next_swing.kind == "high":
                swing_high_level = next_swing.price
                swing_high_crossed = False
            else:
                swing_low_level = next_swing.price
                swing_low_crossed = False
            next_swing = next(swing_iter, None)

        close = df["close"].iloc[i]

        # mitigation check for existing OBs (before possibly creating new ones this bar)
        still_active = []
        for ob in active_bull_obs:
            if close < ob.bottom:
                zones.append(Zone(ob.kind, ob.bias, ob.top, ob.bottom, ob.start_index, ob.start_time, i))
                if breaker_on_mitigation:
                    zones.append(Zone("breaker_block", "bearish", ob.top, ob.bottom, i, df["close_time"].iloc[i], None))
            else:
                still_active.append(ob)
        active_bull_obs = still_active

        still_active = []
        for ob in active_bear_obs:
            if close > ob.top:
                zones.append(Zone(ob.kind, ob.bias, ob.top, ob.bottom, ob.start_index, ob.start_time, i))
                if breaker_on_mitigation:
                    zones.append(Zone("breaker_block", "bullish", ob.top, ob.bottom, i, df["close_time"].iloc[i], None))
            else:
                still_active.append(ob)
        active_bear_obs = still_active

        if swing_high_level is not None and not swing_high_crossed and close > swing_high_level:
            swing_high_crossed = True
            ob_offset = None
            for k in range(1, search_window + 1):
                j = i - k
                if j < 0:
                    break
                if df["close"].iloc[j] < df["open"].iloc[j]:
                    ob_offset = j
                    break
            if ob_offset is not None:
                new_ob = Zone(
                    "order_block", "bullish",
                    float(df["high"].iloc[ob_offset]), float(df["low"].iloc[ob_offset]),
                    ob_offset, df["close_time"].iloc[ob_offset], None,
                )
                active_bull_obs.insert(0, new_ob)
                while len(active_bull_obs) > max_active:
                    active_bull_obs.pop()

        if swing_low_level is not None and not swing_low_crossed and close < swing_low_level:
            swing_low_crossed = True
            ob_offset = None
            for k in range(1, search_window + 1):
                j = i - k
                if j < 0:
                    break
                if df["close"].iloc[j] > df["open"].iloc[j]:
                    ob_offset = j
                    break
            if ob_offset is not None:
                new_ob = Zone(
                    "order_block", "bearish",
                    float(df["high"].iloc[ob_offset]), float(df["low"].iloc[ob_offset]),
                    ob_offset, df["close_time"].iloc[ob_offset], None,
                )
                active_bear_obs.insert(0, new_ob)
                while len(active_bear_obs) > max_active:
                    active_bear_obs.pop()

    # anything still active at the end of the data stays open-ended (end_index=None)
    zones.extend(active_bull_obs)
    zones.extend(active_bear_obs)
    return zones


def detect_fvg_with_inverse(df: pd.DataFrame, max_active: int = 5) -> list[Zone]:
    """Same 3-candle FVG definition as strategies.smc.detect_fair_value_gaps,
    but additionally tracks each gap's fill lifecycle: once price closes
    fully through a gap, it flips to an "inverse FVG" of the opposite bias
    (matches the Pine source's inverse-FVG toggle) rather than just
    disappearing. Only the most recent `max_active` gaps are tracked.
    """
    zones: list[Zone] = []
    active: list[Zone] = []

    for i in range(len(df)):
        close = df["close"].iloc[i]

        still_active = []
        for z in active:
            if z.bias == "bullish" and not z.is_inverse and close < z.bottom:
                zones.append(Zone(z.kind, z.bias, z.top, z.bottom, z.start_index, z.start_time, i, z.is_inverse))
                still_active.append(Zone(z.kind, "bearish", z.top, z.bottom, i, df["close_time"].iloc[i], None, True))
            elif z.bias == "bearish" and not z.is_inverse and close > z.top:
                zones.append(Zone(z.kind, z.bias, z.top, z.bottom, z.start_index, z.start_time, i, z.is_inverse))
                still_active.append(Zone(z.kind, "bullish", z.top, z.bottom, i, df["close_time"].iloc[i], None, True))
            else:
                still_active.append(z)
        active = still_active

        if i >= 2:
            c1_high, c1_low = df["high"].iloc[i - 2], df["low"].iloc[i - 2]
            c3_high, c3_low = df["high"].iloc[i], df["low"].iloc[i]
            if c1_high < c3_low:
                active.insert(0, Zone("fair_value_gap", "bullish", c3_low, c1_high, i - 2, df["close_time"].iloc[i - 2], None))
            elif c1_low > c3_high:
                active.insert(0, Zone("fair_value_gap", "bearish", c1_low, c3_high, i - 2, df["close_time"].iloc[i - 2], None))
            while len(active) > max_active * 2:
                active.pop()

    zones.extend(active)
    return zones


def premium_discount_zone(df: pd.DataFrame, swing_len: int = 7) -> PremiumDiscountZone | None:
    """Premium (upper 25%+), equilibrium (50%), discount (lower 25%-) levels
    for the most recently confirmed swing-high-to-swing-low range, evaluated
    as of the last bar — mirrors the Pine source's `barstate.islast`-only
    zone redraw rather than a per-bar zone history.
    """
    swings = find_swing_points(df, lookback=swing_len)
    if not swings:
        return None
    last_high = next((s for s in reversed(swings) if s.kind == "high"), None)
    last_low = next((s for s in reversed(swings) if s.kind == "low"), None)
    if last_high is None or last_low is None:
        return None
    range_high, range_low = last_high.price, last_low.price
    if range_high <= range_low:
        return None
    span = range_high - range_low
    return PremiumDiscountZone(
        range_high=range_high,
        range_low=range_low,
        premium_level=range_low + 0.75 * span,
        equilibrium_level=range_low + 0.50 * span,
        discount_level=range_low + 0.25 * span,
        as_of_index=len(df) - 1,
    )


def opening_range(df: pd.DataFrame, session_start: str = "09:15", session_end: str = "09:30") -> list[OpeningRange]:
    """Per-trading-day opening range high/low, locked once `session_end`
    passes (mirrors the Pine source's ORB: it only updates within the
    window, then holds/locks for the rest of that day)."""
    if df.empty:
        return []
    ist_times = df["close_time"].dt.tz_convert(IST)
    session_dates = ist_times.dt.date
    times_of_day = ist_times.dt.strftime("%H:%M")

    ranges: list[OpeningRange] = []
    for day, group_idx in session_dates.groupby(session_dates).groups.items():
        idx = list(group_idx)
        in_window = [i for i in idx if session_start <= times_of_day.loc[i] <= session_end]
        if not in_window:
            continue
        high = float(df["high"].loc[in_window].max())
        low = float(df["low"].loc[in_window].min())
        locked = times_of_day.loc[idx[-1]] > session_end
        ranges.append(OpeningRange(str(day), high, low, locked, idx[0]))
    return ranges


def compute_trade_signals(
    df: pd.DataFrame,
    order_blocks: list[Zone],
    swing_len: int = 7,
    ema_fast: int = 20,
    ema_slow: int = 50,
    adx_period: int = 14,
    adx_threshold: float = 20.0,
    volume_period: int = 20,
    volume_multiple: float = 1.0,
    min_score: int = 65,
    atr_period: int = 14,
    sl_atr_buffer: float = 0.25,
    tp_r_multiples: tuple[float, float, float] = (1.0, 2.0, 3.0),
) -> list[TradeSignal]:
    """Weighted 0-100 score on every swing BOS bar: +25 for the break itself,
    +15 EMA20/50 alignment, +15 VWAP side, +15 ADX >= threshold (trending,
    not sideways), +10 volume above its rolling average, +10 an order block
    exists on that side — matching the Pine source's scoring weights.
    A signal only fires when the score clears `min_score`. Stop-loss is the
    nearest same-side order block's far edge (or 1.5x ATR if none), minus/
    plus an extra ATR buffer; targets are R-multiples of that risk.
    """
    if len(df) < max(ema_slow, adx_period, volume_period, atr_period) + swing_len + 2:
        return []

    ema20 = calc_ema(df, ema_fast).values.reset_index(drop=True)
    ema50 = calc_ema(df, ema_slow).values.reset_index(drop=True)
    vwap = vwap_session(df).values.reset_index(drop=True)
    adx_result = calc_adx(df, adx_period)
    adx_series = adx_result.adx.values.reset_index(drop=True)
    vol_stats = volume_stats(df, volume_period)
    vol_ratio = vol_stats.volume_ratio.values.reset_index(drop=True)
    atr_series = calc_atr(df, atr_period).values.reset_index(drop=True)

    swings = find_swing_points(df, lookback=swing_len)
    swing_high_level = None
    swing_high_crossed = True
    swing_low_level = None
    swing_low_crossed = True
    swing_iter = iter(swings)
    next_swing = next(swing_iter, None)

    obs_by_end = {}
    for ob in order_blocks:
        if ob.kind == "order_block":
            obs_by_end.setdefault(ob.end_index, []).append(ob)

    def active_obs_at(i: int, bias: str) -> list[Zone]:
        return [
            ob for ob in order_blocks
            if ob.kind == "order_block" and ob.bias == bias and ob.start_index <= i and (ob.end_index is None or ob.end_index > i)
        ]

    signals: list[TradeSignal] = []

    for i in range(len(df)):
        while next_swing is not None and next_swing.index <= i:
            if next_swing.kind == "high":
                swing_high_level = next_swing.price
                swing_high_crossed = False
            else:
                swing_low_level = next_swing.price
                swing_low_crossed = False
            next_swing = next(swing_iter, None)

        close = df["close"].iloc[i]
        if pd.isna(ema20[i]) or pd.isna(ema50[i]) or pd.isna(adx_series[i]) or pd.isna(atr_series[i]):
            continue

        bull_break = swing_high_level is not None and not swing_high_crossed and close > swing_high_level
        bear_break = swing_low_level is not None and not swing_low_crossed and close < swing_low_level

        if bull_break:
            swing_high_crossed = True
        if bear_break:
            swing_low_crossed = True

        if not (bull_break or bear_break):
            continue

        vol_ok = (not pd.isna(vol_ratio[i])) and vol_ratio[i] >= volume_multiple
        adx_ok = adx_series[i] >= adx_threshold

        if bull_break:
            reasons = {
                "swing_break": True,
                "ema_aligned": close > ema20[i] > ema50[i],
                "vwap_aligned": (not pd.isna(vwap[i])) and close > vwap[i],
                "adx_trending": adx_ok,
                "volume_confirmed": vol_ok,
                "order_block_present": len(active_obs_at(i, "bullish")) > 0,
            }
            score = 25 + 15 * reasons["ema_aligned"] + 15 * reasons["vwap_aligned"] + 15 * reasons["adx_trending"] + 10 * reasons["volume_confirmed"] + 10 * reasons["order_block_present"]
            if score >= min_score:
                obs = active_obs_at(i, "bullish")
                floor = obs[0].bottom if obs else close - atr_series[i] * 1.5
                sl = floor - atr_series[i] * sl_atr_buffer
                risk = close - sl
                if risk > 0:
                    signals.append(TradeSignal(
                        i, df["close_time"].iloc[i], "long", int(score), float(close), float(sl),
                        float(close + risk * tp_r_multiples[0]), float(close + risk * tp_r_multiples[1]), float(close + risk * tp_r_multiples[2]),
                        reasons,
                    ))

        if bear_break:
            reasons = {
                "swing_break": True,
                "ema_aligned": close < ema20[i] < ema50[i],
                "vwap_aligned": (not pd.isna(vwap[i])) and close < vwap[i],
                "adx_trending": adx_ok,
                "volume_confirmed": vol_ok,
                "order_block_present": len(active_obs_at(i, "bearish")) > 0,
            }
            score = 25 + 15 * reasons["ema_aligned"] + 15 * reasons["vwap_aligned"] + 15 * reasons["adx_trending"] + 10 * reasons["volume_confirmed"] + 10 * reasons["order_block_present"]
            if score >= min_score:
                obs = active_obs_at(i, "bearish")
                ceiling = obs[0].top if obs else close + atr_series[i] * 1.5
                sl = ceiling + atr_series[i] * sl_atr_buffer
                risk = sl - close
                if risk > 0:
                    signals.append(TradeSignal(
                        i, df["close_time"].iloc[i], "short", int(score), float(close), float(sl),
                        float(close - risk * tp_r_multiples[0]), float(close - risk * tp_r_multiples[1]), float(close - risk * tp_r_multiples[2]),
                        reasons,
                    ))

    return signals
