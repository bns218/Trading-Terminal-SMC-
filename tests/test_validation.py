from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from config.market_calendar import MarketCalendar
from config.settings import get_settings
from data.models import Candle
from data.validation import is_stale, validate_candle, validate_tick

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def calendar():
    return MarketCalendar(get_settings().holidays_file)


def mid_session(day=(2026, 8, 18)):
    return datetime(*day, 11, 0, tzinfo=IST)


def test_valid_tick_passes(calendar):
    result = validate_tick(ltp=Decimal("100.5"), volume=10, exchange_ts=mid_session(), calendar=calendar)
    assert result.ok is True


def test_non_positive_ltp_rejected(calendar):
    result = validate_tick(ltp=Decimal("0"), volume=10, exchange_ts=mid_session(), calendar=calendar)
    assert result.ok is False
    assert "non-positive" in result.reason


def test_negative_volume_rejected(calendar):
    result = validate_tick(ltp=Decimal("100"), volume=-5, exchange_ts=mid_session(), calendar=calendar)
    assert result.ok is False
    assert "negative volume" in result.reason


def test_tick_outside_market_hours_rejected(calendar):
    outside = datetime(2026, 8, 18, 20, 0, tzinfo=IST)
    result = validate_tick(ltp=Decimal("100"), volume=10, exchange_ts=outside, calendar=calendar)
    assert result.ok is False
    assert "outside market hours" in result.reason


def test_tick_on_holiday_rejected(calendar):
    holiday = datetime(2026, 1, 26, 11, 0, tzinfo=IST)  # Republic Day
    result = validate_tick(ltp=Decimal("100"), volume=10, exchange_ts=holiday, calendar=calendar)
    assert result.ok is False


def test_out_of_order_tick_rejected(calendar):
    t0 = mid_session()
    t_earlier = t0 - timedelta(seconds=5)
    result = validate_tick(ltp=Decimal("100"), volume=10, exchange_ts=t_earlier, calendar=calendar, last_tick_ts=t0)
    assert result.ok is False
    assert "out-of-order" in result.reason


def test_duplicate_tick_timestamp_rejected(calendar):
    t0 = mid_session()
    result = validate_tick(ltp=Decimal("100"), volume=10, exchange_ts=t0, calendar=calendar, last_tick_ts=t0)
    assert result.ok is False
    assert "duplicate" in result.reason


def _make_candle(**overrides):
    defaults = dict(
        instrument_token="2885",
        timeframe="1min",
        open=Decimal("100"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=500,
        open_time=mid_session(),
        close_time=mid_session() + timedelta(minutes=1),
    )
    defaults.update(overrides)
    return Candle(**defaults)


def test_valid_candle_passes(calendar):
    result = validate_candle(_make_candle(), calendar)
    assert result.ok is True


def test_high_less_than_low_rejected(calendar):
    result = validate_candle(_make_candle(high=Decimal("90"), low=Decimal("99")), calendar)
    assert result.ok is False
    assert "high < low" in result.reason


def test_close_outside_range_rejected(calendar):
    result = validate_candle(_make_candle(close=Decimal("200")), calendar)
    assert result.ok is False
    assert "outside [low, high]" in result.reason


def test_negative_candle_volume_rejected(calendar):
    result = validate_candle(_make_candle(volume=-1), calendar)
    assert result.ok is False


def test_candle_outside_session_rejected(calendar):
    outside = datetime(2026, 8, 18, 20, 0, tzinfo=IST)
    result = validate_candle(_make_candle(open_time=outside, close_time=outside + timedelta(minutes=1)), calendar)
    assert result.ok is False


def test_close_time_not_after_open_time_rejected(calendar):
    t0 = mid_session()
    result = validate_candle(_make_candle(open_time=t0, close_time=t0), calendar)
    assert result.ok is False


def test_is_stale_true_during_session_with_no_ticks(calendar):
    assert is_stale(None, mid_session(), calendar, max_staleness_seconds=30) is True


def test_is_stale_false_outside_session(calendar):
    outside = datetime(2026, 8, 18, 20, 0, tzinfo=IST)
    assert is_stale(None, outside, calendar, max_staleness_seconds=30) is False


def test_is_stale_true_after_gap(calendar):
    last_tick = mid_session()
    now = last_tick + timedelta(seconds=60)
    assert is_stale(last_tick, now, calendar, max_staleness_seconds=30) is True


def test_is_stale_false_within_window(calendar):
    last_tick = mid_session()
    now = last_tick + timedelta(seconds=5)
    assert is_stale(last_tick, now, calendar, max_staleness_seconds=30) is False
