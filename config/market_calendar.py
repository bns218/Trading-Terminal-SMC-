"""NSE/BSE market calendar: session windows, holiday awareness.

All datetimes handled here are timezone-aware. Naive datetimes are rejected
loudly rather than assumed to be IST or UTC.
"""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

REGULAR_SESSION_OPEN = time(9, 15)
REGULAR_SESSION_CLOSE = time(15, 30)
PRE_OPEN_START = time(9, 0)
PRE_OPEN_END = time(9, 15)

# NSE/BSE trade Mon-Fri only (barring special Muhurat sessions handled via holidays.json
# overrides, which is out of scope for this loader — Muhurat sessions are not modeled).
TRADING_WEEKDAYS = {0, 1, 2, 3, 4}  # Monday=0 ... Sunday=6


class NaiveDatetimeError(ValueError):
    pass


def _require_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        raise NaiveDatetimeError(
            f"Naive datetime {dt!r} passed to market_calendar — every timestamp crossing "
            "a module boundary must be timezone-aware."
        )
    return dt


class MarketCalendar:
    def __init__(self, holidays_file: Path):
        self._holidays_file = holidays_file
        self._holidays: set[date] = self._load_holidays(holidays_file)

    @staticmethod
    def _load_holidays(path: Path) -> set[date]:
        if not path.exists():
            raise FileNotFoundError(
                f"Holiday config not found at {path}. This must exist and be user-maintained — "
                "the calendar refuses to silently assume 'no holidays'."
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        holidays: set[date] = set()
        for key, entries in raw.items():
            if key.startswith("_"):
                continue
            for entry in entries:
                holidays.add(date.fromisoformat(entry["date"]))
        return holidays

    def is_holiday(self, day: date) -> bool:
        return day in self._holidays

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() in TRADING_WEEKDAYS and not self.is_holiday(day)

    def is_regular_session(self, dt: datetime) -> bool:
        """True if dt falls inside the 09:15-15:30 IST regular session on a trading day."""
        dt = _require_aware(dt).astimezone(IST)
        if not self.is_trading_day(dt.date()):
            return False
        return REGULAR_SESSION_OPEN <= dt.time() <= REGULAR_SESSION_CLOSE

    def is_pre_open(self, dt: datetime) -> bool:
        dt = _require_aware(dt).astimezone(IST)
        if not self.is_trading_day(dt.date()):
            return False
        return PRE_OPEN_START <= dt.time() < PRE_OPEN_END

    def next_trading_day(self, day: date) -> date:
        candidate = day + timedelta(days=1)
        while not self.is_trading_day(candidate):
            candidate += timedelta(days=1)
        return candidate

    def session_bounds(self, day: date) -> tuple[datetime, datetime] | None:
        """Return (open, close) as IST-aware datetimes for a trading day, or None if not a trading day."""
        if not self.is_trading_day(day):
            return None
        return (
            datetime.combine(day, REGULAR_SESSION_OPEN, tzinfo=IST),
            datetime.combine(day, REGULAR_SESSION_CLOSE, tzinfo=IST),
        )
