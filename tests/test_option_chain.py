from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data.models import Exchange, Instrument, InstrumentType, OptionType, Quote
from strategies.option_chain import build_option_chain

EXPIRY = datetime(2026, 8, 27, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)


def make_option(token, strike, option_type):
    return Instrument(
        token=token,
        symbol=f"NIFTY27AUG26{strike}{option_type.value}",
        name="NIFTY",
        exchange=Exchange.NFO,
        instrument_type=InstrumentType.OPTIDX,
        lot_size=50,
        tick_size=Decimal("0.05"),
        expiry=EXPIRY,
        strike=Decimal(str(strike)),
        option_type=option_type,
    )


def make_quote(token, ltp, oi):
    return Quote(instrument_token=token, exchange=Exchange.NFO, ltp=Decimal(str(ltp)), open_interest=oi, timestamp=NOW)


def test_pairs_ce_and_pe_by_strike():
    instruments = [
        make_option("1", 24500, OptionType.CE),
        make_option("2", 24500, OptionType.PE),
        make_option("3", 24600, OptionType.CE),
        make_option("4", 24600, OptionType.PE),
    ]
    quotes = {
        "1": make_quote("1", 150, 10000),
        "2": make_quote("2", 100, 12000),
        "3": make_quote("3", 100, 8000),
        "4": make_quote("4", 150, 9000),
    }
    chain = build_option_chain(instruments, quotes, "NIFTY", EXPIRY, Decimal("24550"), NOW)
    assert len(chain.contracts) == 2
    assert chain.contracts[0].strike == Decimal("24500")
    assert chain.contracts[0].call_quote.ltp == Decimal("150")
    assert chain.contracts[0].put_quote.ltp == Decimal("100")
    assert chain.contracts[1].strike == Decimal("24600")


def test_contracts_sorted_by_strike_ascending():
    instruments = [
        make_option("1", 25000, OptionType.CE),
        make_option("2", 24000, OptionType.CE),
        make_option("3", 24500, OptionType.CE),
    ]
    chain = build_option_chain(instruments, {}, "NIFTY", EXPIRY, Decimal("24500"), NOW)
    strikes = [c.strike for c in chain.contracts]
    assert strikes == sorted(strikes)


def test_missing_leg_is_none_not_dropped():
    instruments = [make_option("1", 24500, OptionType.CE)]  # no matching PE
    chain = build_option_chain(instruments, {}, "NIFTY", EXPIRY, Decimal("24500"), NOW)
    assert len(chain.contracts) == 1
    assert chain.contracts[0].call_instrument is not None
    assert chain.contracts[0].put_instrument is None
    assert chain.contracts[0].put_quote is None


def test_missing_quote_is_none_not_error():
    instruments = [make_option("1", 24500, OptionType.CE)]
    chain = build_option_chain(instruments, {}, "NIFTY", EXPIRY, Decimal("24500"), NOW)
    assert chain.contracts[0].call_quote is None


def test_non_option_instruments_ignored():
    equity = Instrument(
        token="99", symbol="RELIANCE-EQ", name="RELIANCE", exchange=Exchange.NSE,
        instrument_type=InstrumentType.EQ, lot_size=1, tick_size=Decimal("0.05"),
    )
    chain = build_option_chain([equity], {}, "NIFTY", EXPIRY, Decimal("24500"), NOW)
    assert chain.contracts == []


def test_snapshot_metadata():
    chain = build_option_chain([], {}, "NIFTY", EXPIRY, Decimal("24500"), NOW)
    assert chain.underlying_name == "NIFTY"
    assert chain.spot_price == Decimal("24500")
    assert chain.computed_at == NOW
