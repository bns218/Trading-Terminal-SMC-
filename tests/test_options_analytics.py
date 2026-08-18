from datetime import datetime, timezone
from decimal import Decimal

import pytest

from data.models import Exchange, OptionChainSnapshot, OptionContract, OptionType, Quote
from strategies.options_analytics import (
    call_oi_resistance,
    classify_moneyness,
    classify_oi_buildup,
    compute_max_pain,
    compute_pcr,
    find_atm_strike,
    put_oi_support,
)

NOW = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
EXPIRY = datetime(2026, 8, 27, tzinfo=timezone.utc)


def quote(oi, ltp="100"):
    return Quote(instrument_token="x", exchange=Exchange.NFO, ltp=Decimal(ltp), open_interest=oi, timestamp=NOW)


def make_chain(strike_oi_pairs):
    """strike_oi_pairs: list of (strike, call_oi, put_oi)."""
    contracts = [
        OptionContract(
            strike=Decimal(str(strike)),
            call_quote=quote(call_oi) if call_oi is not None else None,
            put_quote=quote(put_oi) if put_oi is not None else None,
        )
        for strike, call_oi, put_oi in strike_oi_pairs
    ]
    return OptionChainSnapshot(
        underlying_name="NIFTY", expiry=EXPIRY, spot_price=Decimal("105"), contracts=contracts, computed_at=NOW
    )


# --- ATM / moneyness ---

def test_find_atm_strike_nearest():
    chain = make_chain([(100, 10, 10), (105, 10, 10), (110, 10, 10)])
    assert find_atm_strike(chain) == Decimal("105")


def test_find_atm_strike_ties_broken_toward_lower():
    chain = make_chain([(100, 1, 1), (110, 1, 1)])  # spot=105, equidistant from both
    assert find_atm_strike(chain) == Decimal("100")


def test_find_atm_strike_empty_chain():
    chain = make_chain([])
    assert find_atm_strike(chain) is None


def test_classify_moneyness_call():
    atm = Decimal("105")
    spot = Decimal("105")
    assert classify_moneyness(Decimal("100"), spot, atm, OptionType.CE) == "ITM"
    assert classify_moneyness(Decimal("110"), spot, atm, OptionType.CE) == "OTM"
    assert classify_moneyness(Decimal("105"), spot, atm, OptionType.CE) == "ATM"


def test_classify_moneyness_put():
    atm = Decimal("105")
    spot = Decimal("105")
    assert classify_moneyness(Decimal("110"), spot, atm, OptionType.PE) == "ITM"
    assert classify_moneyness(Decimal("100"), spot, atm, OptionType.PE) == "OTM"
    assert classify_moneyness(Decimal("105"), spot, atm, OptionType.PE) == "ATM"


# --- PCR ---

def test_compute_pcr_basic_ratio():
    chain = make_chain([(100, 200, 400), (105, 100, 100)])
    pcr = compute_pcr(chain, NOW)
    assert pcr.value == Decimal("500") / Decimal("300")
    assert pcr.source == "computed_chain"
    assert pcr.computed_at == NOW


def test_compute_pcr_zero_call_oi():
    chain = make_chain([(100, 0, 50)])
    pcr = compute_pcr(chain, NOW)
    assert pcr.value == Decimal(0)


# --- Max pain ---
# Hand-computed: call OI concentrated at 120 (heavy resistance), put OI
# concentrated at 100 (heavy support). Total payout at each candidate
# settlement: 100->500, 110->0, 120->500. Minimum is at 110.
def test_max_pain_hand_computed_example():
    chain = make_chain([(100, 0, 100), (110, 50, 50), (120, 100, 0)])
    result = compute_max_pain(chain, NOW)
    assert result.value == Decimal("110")
    assert result.source == "computed_chain"


def test_max_pain_empty_chain_returns_none():
    chain = make_chain([])
    assert compute_max_pain(chain, NOW) is None


def test_max_pain_zero_oi_everywhere_picks_lowest_strike_on_tie():
    chain = make_chain([(100, 0, 0), (105, 0, 0), (110, 0, 0)])
    result = compute_max_pain(chain, NOW)
    assert result.value == Decimal("100")


# --- OI buildup classification ---

def test_oi_buildup_long_buildup():
    assert classify_oi_buildup(Decimal("2"), 500) == "long_buildup"


def test_oi_buildup_short_buildup():
    assert classify_oi_buildup(Decimal("-2"), 500) == "short_buildup"


def test_oi_buildup_short_covering():
    assert classify_oi_buildup(Decimal("2"), -500) == "short_covering"


def test_oi_buildup_long_unwinding():
    assert classify_oi_buildup(Decimal("-2"), -500) == "long_unwinding"


def test_oi_buildup_neutral_on_no_price_change():
    assert classify_oi_buildup(Decimal("0"), 500) == "neutral"


def test_oi_buildup_neutral_on_no_oi_change():
    assert classify_oi_buildup(Decimal("2"), 0) == "neutral"


# --- OI resistance / support ---

def test_call_oi_resistance_picks_max_call_oi_strike():
    chain = make_chain([(100, 50, 10), (105, 300, 10), (110, 100, 10)])
    result = call_oi_resistance(chain, NOW)
    assert result.value == Decimal("105")


def test_put_oi_support_picks_max_put_oi_strike():
    chain = make_chain([(100, 10, 400), (105, 10, 50), (110, 10, 20)])
    result = put_oi_support(chain, NOW)
    assert result.value == Decimal("100")


def test_oi_resistance_none_when_no_call_quotes():
    chain = make_chain([(100, None, 50)])
    assert call_oi_resistance(chain, NOW) is None
