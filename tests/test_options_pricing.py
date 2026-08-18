import math

import pytest

from strategies.options_pricing import (
    bs_greeks,
    bs_implied_volatility,
    bs_price,
    black76_greeks,
    black76_implied_volatility,
    black76_price,
)


# --- Cross-check against a known textbook example ---
# Hull, "Options, Futures, and Other Derivatives": S=42, K=40, r=10%, sigma=20%,
# T=0.5 years -> call ~= 4.76, put ~= 0.81 (widely cited reference values).

def test_bs_call_matches_hull_textbook_example():
    price = bs_price(spot=42, strike=40, time_to_expiry=0.5, rate=0.10, sigma=0.20, side="CE")
    assert price == pytest.approx(4.76, abs=0.01)


def test_bs_put_matches_hull_textbook_example():
    price = bs_price(spot=42, strike=40, time_to_expiry=0.5, rate=0.10, sigma=0.20, side="PE")
    assert price == pytest.approx(0.81, abs=0.01)


# --- Put-call parity (must hold exactly, by construction of the formulas) ---

def test_bs_put_call_parity():
    call = bs_price(100, 95, 0.25, 0.05, 0.3, "CE")
    put = bs_price(100, 95, 0.25, 0.05, 0.3, "PE")
    lhs = call - put
    rhs = 100 - 95 * math.exp(-0.05 * 0.25)
    assert lhs == pytest.approx(rhs, abs=1e-9)


def test_black76_put_call_parity():
    call = black76_price(24500, 24000, 0.1, 0.06, 0.15, "CE")
    put = black76_price(24500, 24000, 0.1, 0.06, 0.15, "PE")
    lhs = call - put
    rhs = math.exp(-0.06 * 0.1) * (24500 - 24000)
    assert lhs == pytest.approx(rhs, abs=1e-9)


# --- Implied volatility round-trips ---

def test_bs_iv_recovers_known_sigma():
    true_sigma = 0.25
    price = bs_price(100, 100, 0.5, 0.06, true_sigma, "CE")
    recovered = bs_implied_volatility(price, 100, 100, 0.5, 0.06, "CE")
    assert recovered == pytest.approx(true_sigma, abs=1e-4)


def test_bs_iv_recovers_known_sigma_otm_put():
    true_sigma = 0.35
    price = bs_price(24500, 24000, 0.05, 0.06, true_sigma, "PE")
    recovered = bs_implied_volatility(price, 24500, 24000, 0.05, 0.06, "PE")
    assert recovered == pytest.approx(true_sigma, abs=1e-4)


def test_black76_iv_recovers_known_sigma():
    true_sigma = 0.18
    price = black76_price(24500, 24500, 0.08, 0.065, true_sigma, "CE")
    recovered = black76_implied_volatility(price, 24500, 24500, 0.08, 0.065, "CE")
    assert recovered == pytest.approx(true_sigma, abs=1e-4)


def test_iv_returns_none_for_price_outside_no_arbitrage_bounds():
    # A call can never be worth more than the spot price itself.
    result = bs_implied_volatility(market_price=200, spot=100, strike=100, time_to_expiry=0.5, rate=0.05, side="CE")
    assert result is None


# --- Greeks sanity checks ---

def test_delta_bounds_for_call():
    deep_itm = bs_greeks(200, 100, 0.5, 0.05, 0.2, "CE").delta
    deep_otm = bs_greeks(50, 100, 0.5, 0.05, 0.2, "CE").delta
    assert deep_itm > 0.9
    assert deep_otm < 0.1


def test_delta_bounds_for_put():
    deep_itm = bs_greeks(50, 100, 0.5, 0.05, 0.2, "PE").delta
    deep_otm = bs_greeks(200, 100, 0.5, 0.05, 0.2, "PE").delta
    assert deep_itm < -0.9
    assert deep_otm > -0.1


def test_gamma_positive_and_peaks_near_atm():
    gamma_atm = bs_greeks(100, 100, 0.5, 0.05, 0.2, "CE").gamma
    gamma_deep_itm = bs_greeks(200, 100, 0.5, 0.05, 0.2, "CE").gamma
    assert gamma_atm > 0
    assert gamma_atm > gamma_deep_itm


def test_vega_positive():
    vega = bs_greeks(100, 100, 0.5, 0.05, 0.2, "CE").vega
    assert vega > 0


def test_call_and_put_gamma_vega_equal_at_same_strike():
    # Gamma and vega are identical for calls and puts at the same strike (a
    # standard BS identity, independent of the put-call parity relation).
    call_g = bs_greeks(100, 100, 0.5, 0.05, 0.2, "CE")
    put_g = bs_greeks(100, 100, 0.5, 0.05, 0.2, "PE")
    assert call_g.gamma == pytest.approx(put_g.gamma, rel=1e-9)
    assert call_g.vega == pytest.approx(put_g.vega, rel=1e-9)


def test_invalid_time_or_sigma_raises():
    with pytest.raises(ValueError):
        bs_price(100, 100, 0, 0.05, 0.2, "CE")
    with pytest.raises(ValueError):
        bs_price(100, 100, 0.5, 0.05, 0, "CE")


def test_black76_greeks_call_delta_positive_put_negative():
    call_delta = black76_greeks(24500, 24500, 0.08, 0.06, 0.18, "CE").delta
    put_delta = black76_greeks(24500, 24500, 0.08, 0.06, 0.18, "PE").delta
    assert call_delta > 0
    assert put_delta < 0
