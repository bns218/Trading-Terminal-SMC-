from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from config.market_calendar import MarketCalendar
from config.risk_config import RiskConfig
from config.settings import get_settings
from data.models import Exchange, Instrument, InstrumentType, SignalDirection, TradeSignal
from engine.risk_manager import RiskManager

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def calendar():
    return MarketCalendar(get_settings().holidays_file)


def within_session(day=(2026, 8, 18), hour=10, minute=0):
    return datetime(*day, hour, minute, tzinfo=IST).astimezone(timezone.utc)


INSTRUMENT = Instrument(
    token="26000", symbol="NIFTY-FUT", name="NIFTY", exchange=Exchange.NFO,
    instrument_type=InstrumentType.FUTIDX, lot_size=25, tick_size=Decimal("0.05"),
)


def make_buy_signal(entry="24500", stop="24490"):
    return TradeSignal(
        instrument_token="26000", direction=SignalDirection.BUY, timestamp=within_session(),
        confidence=70.0, sub_scores=[], entry=Decimal(entry), stop_loss=Decimal(stop),
        targets=[Decimal("24520")], risk_reward=1.0,
    )


def make_config(**overrides):
    defaults = dict(
        account_capital=Decimal("100000"), max_risk_per_trade_pct=Decimal("1.0"),
        max_daily_loss=Decimal("3000"), max_trades_per_day=5, max_open_positions=1,
        cooldown_after_consecutive_losses=2, cooldown_duration_minutes=30,
    )
    defaults.update(overrides)
    return RiskConfig(**defaults)


def test_approved_with_correct_position_sizing(calendar):
    rm = RiskManager(make_config(), calendar)
    signal = make_buy_signal(entry="24500", stop="24490")  # per-share risk = 10
    decision = rm.evaluate(signal, INSTRUMENT, within_session())
    assert decision.approved is True
    # risk_amount = 100000 * 1% = 1000; raw_qty = 1000/10 = 100; lot_size=25 -> 100 (exact multiple)
    assert decision.sized_quantity == 100
    assert decision.risk_amount == Decimal("1000")


def test_no_trade_signal_rejected(calendar):
    rm = RiskManager(make_config(), calendar)
    signal = TradeSignal(
        instrument_token="26000", direction=SignalDirection.NO_TRADE, timestamp=within_session(),
        confidence=10.0, sub_scores=[],
    )
    decision = rm.evaluate(signal, INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "no_trade_signal"


def test_kill_switch_rejects(calendar):
    rm = RiskManager(make_config(), calendar)
    rm.set_kill_switch(True)
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "kill_switch"


def test_outside_configured_session_window_rejected(calendar):
    rm = RiskManager(make_config(), calendar)
    early = within_session(hour=9, minute=0)  # before session_start (09:20 default)
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, early)
    assert decision.approved is False
    assert decision.rejected_rule == "outside_session_hours"


def test_holiday_rejected_even_within_configured_hours(calendar):
    rm = RiskManager(make_config(), calendar)
    holiday = within_session(day=(2026, 1, 26), hour=10, minute=0)  # Republic Day
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, holiday)
    assert decision.approved is False
    assert decision.rejected_rule == "outside_session_hours"


def test_max_daily_loss_blocks_further_trades(calendar):
    rm = RiskManager(make_config(max_daily_loss=Decimal("1000")), calendar)
    rm.record_trade_closed(Decimal("-1500"), within_session())
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "max_daily_loss_reached"


def test_max_trades_per_day_blocks_further_trades(calendar):
    rm = RiskManager(make_config(max_trades_per_day=2), calendar)
    rm.record_trade_opened(within_session())
    rm.record_trade_closed(Decimal("100"), within_session())
    rm.record_trade_opened(within_session())
    rm.record_trade_closed(Decimal("100"), within_session())
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "max_trades_per_day_reached"


def test_max_open_positions_blocks_new_trade(calendar):
    rm = RiskManager(make_config(max_open_positions=1), calendar)
    rm.record_trade_opened(within_session())  # now 1 open position, at the limit
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "max_open_positions_reached"


def test_cooldown_after_consecutive_losses(calendar):
    rm = RiskManager(make_config(cooldown_after_consecutive_losses=2, cooldown_duration_minutes=30), calendar)
    rm.record_trade_opened(within_session())
    rm.record_trade_closed(Decimal("-100"), within_session())
    rm.record_trade_opened(within_session())
    rm.record_trade_closed(Decimal("-100"), within_session())  # 2nd consecutive loss -> cooldown triggers
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "cooldown_active"


def test_win_resets_consecutive_loss_streak(calendar):
    rm = RiskManager(make_config(cooldown_after_consecutive_losses=2), calendar)
    rm.record_trade_opened(within_session())
    rm.record_trade_closed(Decimal("-100"), within_session())
    rm.record_trade_opened(within_session())
    rm.record_trade_closed(Decimal("200"), within_session())  # win resets streak
    rm.record_trade_opened(within_session())
    rm.record_trade_closed(Decimal("-100"), within_session())  # only 1 consecutive loss now
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, within_session())
    assert decision.approved is True


def test_position_size_below_one_lot_rejected(calendar):
    rm = RiskManager(make_config(max_risk_per_trade_pct=Decimal("0.01")), calendar)  # tiny risk budget
    decision = rm.evaluate(make_buy_signal(entry="24500", stop="24490"), INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "position_size_below_one_lot"


def test_invalid_stop_distance_rejected(calendar):
    rm = RiskManager(make_config(), calendar)
    bad_signal = make_buy_signal(entry="24500", stop="24500")  # zero distance
    decision = rm.evaluate(bad_signal, INSTRUMENT, within_session())
    assert decision.approved is False
    assert decision.rejected_rule == "invalid_stop_distance"


def test_daily_state_resets_on_new_trading_day(calendar):
    rm = RiskManager(make_config(max_daily_loss=Decimal("1000")), calendar)
    day1 = within_session(day=(2026, 8, 18))
    rm.record_trade_closed(Decimal("-1500"), day1)
    assert rm.daily_realized_pnl == Decimal("-1500")

    day2 = within_session(day=(2026, 8, 19))  # Wednesday, next trading day
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, day2)
    assert decision.approved is True  # daily loss counter reset
    assert rm.daily_realized_pnl == Decimal("0")


def test_every_rejection_names_a_rule(calendar):
    rm = RiskManager(make_config(), calendar)
    rm.set_kill_switch(True)
    decision = rm.evaluate(make_buy_signal(), INSTRUMENT, within_session())
    assert decision.rejected_rule is not None
    assert decision.rejected_reason is not None
