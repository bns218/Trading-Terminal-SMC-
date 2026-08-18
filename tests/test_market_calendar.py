from datetime import datetime, date
from zoneinfo import ZoneInfo

import pytest

from config.market_calendar import MarketCalendar, NaiveDatetimeError
from config.settings import get_settings

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def calendar():
    settings = get_settings()
    return MarketCalendar(settings.holidays_file)


def test_republic_day_is_holiday(calendar):
    assert calendar.is_holiday(date(2026, 1, 26)) is True


def test_regular_trading_day_not_holiday(calendar):
    # 2026-08-18 is a Tuesday, not in the seeded holiday list.
    assert calendar.is_holiday(date(2026, 8, 18)) is False


def test_saturday_is_not_a_trading_day(calendar):
    saturday = date(2026, 8, 22)
    assert saturday.weekday() == 5
    assert calendar.is_trading_day(saturday) is False


def test_regular_session_window(calendar):
    open_dt = datetime(2026, 8, 18, 9, 15, tzinfo=IST)
    close_dt = datetime(2026, 8, 18, 15, 30, tzinfo=IST)
    before_open = datetime(2026, 8, 18, 9, 0, tzinfo=IST)
    after_close = datetime(2026, 8, 18, 15, 31, tzinfo=IST)

    assert calendar.is_regular_session(open_dt) is True
    assert calendar.is_regular_session(close_dt) is True
    assert calendar.is_regular_session(before_open) is False
    assert calendar.is_regular_session(after_close) is False


def test_regular_session_false_on_holiday(calendar):
    holiday_at_session_time = datetime(2026, 1, 26, 10, 0, tzinfo=IST)
    assert calendar.is_regular_session(holiday_at_session_time) is False


def test_naive_datetime_rejected(calendar):
    naive = datetime(2026, 8, 18, 10, 0)
    with pytest.raises(NaiveDatetimeError):
        calendar.is_regular_session(naive)


def test_next_trading_day_skips_weekend_and_holiday():
    # 2026-01-23 (Friday) -> next trading day should skip Sat/Sun to 2026-01-27
    # (2026-01-26 Republic Day is a Monday holiday in the seed data).
    settings = get_settings()
    calendar = MarketCalendar(settings.holidays_file)
    friday = date(2026, 1, 23)
    assert calendar.next_trading_day(friday) == date(2026, 1, 27)
