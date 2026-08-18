"""Validation for ticks and candles before they reach the store/strategies.
Reject and log — never silently drop.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from config.market_calendar import MarketCalendar
from data.models import Candle


@dataclass
class ValidationResult:
    ok: bool
    reason: Optional[str] = None


def validate_tick(
    *,
    ltp: Decimal,
    volume: Optional[int],
    exchange_ts: datetime,
    calendar: MarketCalendar,
    last_tick_ts: Optional[datetime] = None,
) -> ValidationResult:
    if ltp <= 0:
        return ValidationResult(False, f"non-positive ltp: {ltp}")
    if volume is not None and volume < 0:
        return ValidationResult(False, f"negative volume: {volume}")
    if exchange_ts.tzinfo is None:
        return ValidationResult(False, "naive exchange_ts")
    if not (calendar.is_regular_session(exchange_ts) or calendar.is_pre_open(exchange_ts)):
        return ValidationResult(False, f"timestamp outside market hours/holiday: {exchange_ts.isoformat()}")
    if last_tick_ts is not None and exchange_ts < last_tick_ts:
        return ValidationResult(False, f"out-of-order tick: {exchange_ts.isoformat()} < {last_tick_ts.isoformat()}")
    if last_tick_ts is not None and exchange_ts == last_tick_ts:
        return ValidationResult(False, "duplicate tick timestamp")
    return ValidationResult(True)


def validate_candle(candle: Candle, calendar: MarketCalendar) -> ValidationResult:
    if candle.open <= 0 or candle.high <= 0 or candle.low <= 0 or candle.close <= 0:
        return ValidationResult(False, "non-positive OHLC price")
    if candle.volume < 0:
        return ValidationResult(False, f"negative volume: {candle.volume}")
    if candle.high < candle.low:
        return ValidationResult(False, f"high < low ({candle.high} < {candle.low})")
    if not (candle.low <= candle.close <= candle.high):
        return ValidationResult(False, f"close {candle.close} outside [low, high] [{candle.low}, {candle.high}]")
    if not (candle.low <= candle.open <= candle.high):
        return ValidationResult(False, f"open {candle.open} outside [low, high] [{candle.low}, {candle.high}]")
    if not calendar.is_regular_session(candle.open_time) and not calendar.is_pre_open(candle.open_time):
        return ValidationResult(False, f"candle open_time outside market hours/holiday: {candle.open_time.isoformat()}")
    if candle.close_time <= candle.open_time:
        return ValidationResult(False, "close_time <= open_time")
    return ValidationResult(True)


def is_stale(
    last_tick_ts: Optional[datetime],
    now: datetime,
    calendar: MarketCalendar,
    max_staleness_seconds: int,
) -> bool:
    """Staleness is only meaningful during the regular session — this check
    must not fire on weekends/holidays/outside session hours."""
    if not calendar.is_regular_session(now):
        return False
    if last_tick_ts is None:
        return True
    return (now - last_tick_ts) > timedelta(seconds=max_staleness_seconds)
