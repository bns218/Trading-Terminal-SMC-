from decimal import Decimal

import pytest

from config.charges_config import ChargesConfig, Segment, compute_leg_charges, compute_round_trip_charges


def test_brokerage_uses_percentage_when_below_flat_cap():
    config = ChargesConfig()
    turnover = Decimal("1000")  # 0.03% of 1000 = 0.30, well below flat cap of 20
    result = compute_leg_charges(turnover, Segment.EQUITY_INTRADAY, config, is_buy=True)
    assert result.brokerage == turnover * config.brokerage_pct


def test_brokerage_capped_at_flat_amount_for_large_turnover():
    config = ChargesConfig()
    turnover = Decimal("1000000")  # 0.03% of 1M = 300, above the flat cap of 20
    result = compute_leg_charges(turnover, Segment.EQUITY_INTRADAY, config, is_buy=True)
    assert result.brokerage == config.brokerage_flat_per_order


def test_stt_only_charged_on_sell_leg():
    config = ChargesConfig()
    buy_leg = compute_leg_charges(Decimal("10000"), Segment.OPTIONS, config, is_buy=True)
    sell_leg = compute_leg_charges(Decimal("10000"), Segment.OPTIONS, config, is_buy=False)
    assert buy_leg.stt == Decimal("0")
    assert sell_leg.stt == Decimal("10000") * config.stt_pct_sell[Segment.OPTIONS]


def test_stamp_duty_only_charged_on_buy_leg():
    config = ChargesConfig()
    buy_leg = compute_leg_charges(Decimal("10000"), Segment.FUTURES, config, is_buy=True)
    sell_leg = compute_leg_charges(Decimal("10000"), Segment.FUTURES, config, is_buy=False)
    assert buy_leg.stamp_duty == Decimal("10000") * config.stamp_duty_pct_buy[Segment.FUTURES]
    assert sell_leg.stamp_duty == Decimal("0")


def test_gst_applies_only_to_brokerage_exchange_and_sebi_not_stt_or_stamp_duty():
    config = ChargesConfig()
    result = compute_leg_charges(Decimal("10000"), Segment.OPTIONS, config, is_buy=False)
    expected_gst = config.gst_pct * (result.brokerage + result.exchange_txn + result.sebi_charges)
    assert result.gst == expected_gst


def test_total_sums_all_components():
    config = ChargesConfig()
    result = compute_leg_charges(Decimal("5000"), Segment.EQUITY_INTRADAY, config, is_buy=True)
    assert result.total == result.brokerage + result.stt + result.exchange_txn + result.sebi_charges + result.stamp_duty + result.gst


def test_negative_turnover_rejected():
    config = ChargesConfig()
    with pytest.raises(ValueError):
        compute_leg_charges(Decimal("-100"), Segment.EQUITY_INTRADAY, config, is_buy=True)


def test_round_trip_long_trade_entry_is_buy_exit_is_sell():
    config = ChargesConfig()
    round_trip = compute_round_trip_charges(
        entry_price=Decimal("100"), exit_price=Decimal("110"), quantity=50,
        segment=Segment.FUTURES, config=config, is_long=True,
    )
    entry_leg = compute_leg_charges(Decimal("5000"), Segment.FUTURES, config, is_buy=True)
    exit_leg = compute_leg_charges(Decimal("5500"), Segment.FUTURES, config, is_buy=False)
    assert round_trip.total == entry_leg.total + exit_leg.total
    assert round_trip.stamp_duty == entry_leg.stamp_duty  # only entry (buy) leg has stamp duty
    assert round_trip.stt == exit_leg.stt  # only exit (sell) leg has STT


def test_round_trip_short_trade_entry_is_sell_exit_is_buy():
    config = ChargesConfig()
    round_trip = compute_round_trip_charges(
        entry_price=Decimal("100"), exit_price=Decimal("90"), quantity=50,
        segment=Segment.FUTURES, config=config, is_long=False,
    )
    entry_leg = compute_leg_charges(Decimal("5000"), Segment.FUTURES, config, is_buy=False)  # sell to open
    exit_leg = compute_leg_charges(Decimal("4500"), Segment.FUTURES, config, is_buy=True)  # buy to close
    assert round_trip.total == entry_leg.total + exit_leg.total
    assert round_trip.stt == entry_leg.stt  # STT on the sell (entry) leg this time
    assert round_trip.stamp_duty == exit_leg.stamp_duty  # stamp duty on the buy (exit) leg this time
