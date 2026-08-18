"""Black-Scholes (equity/stock options) and Black-76 (index options, since
NIFTY/BANKNIFTY/etc. options are cash-settled European-style contracts more
naturally priced off a forward) pricing, Greeks, and implied volatility.

This is the documented fallback for the Phase 0 finding that Angel One's
Option Greek API doesn't cover every contract/strike (e.g. expired contracts,
or gaps for illiquid strikes). Every value this module returns should be
tagged `source="computed_bs76"` (index) or `source="computed_bs"` (stock) by
the caller, per the project's rule that every derived metric carries its
provenance — this module itself has no I/O and does not know about
DerivedMetric, it just does the math.

Model assumptions (stated explicitly, since these are simplifications):
  - European exercise (reasonable for NSE index options; NSE stock options
    are also European-style since 2001, so this applies to both).
  - No dividends modeled for stock options (a real dividend yield would
    reduce a call's theoretical price — ignored here).
  - Time to expiry T is calendar days / 365, not a trading-day count — one
    reasonable convention among several (252-trading-day-year is a common
    alternative).
  - Uses `math.erf` for the normal CDF (no scipy dependency needed).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional

OptionSide = Literal["CE", "PE"]


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float  # per 1.0 (100%) change in vol — divide by 100 for "per 1% vol point"
    rho: float


def _d1_d2(spot_or_forward: float, strike: float, time_to_expiry: float, rate: float, sigma: float) -> tuple[float, float]:
    if time_to_expiry <= 0 or sigma <= 0:
        raise ValueError("time_to_expiry and sigma must both be positive")
    d1 = (math.log(spot_or_forward / strike) + (rate + sigma**2 / 2) * time_to_expiry) / (sigma * math.sqrt(time_to_expiry))
    d2 = d1 - sigma * math.sqrt(time_to_expiry)
    return d1, d2


def bs_price(spot: float, strike: float, time_to_expiry: float, rate: float, sigma: float, side: OptionSide) -> float:
    """Black-Scholes price for a non-dividend-paying underlying (stock options)."""
    d1, d2 = _d1_d2(spot, strike, time_to_expiry, rate, sigma)
    if side == "CE":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * time_to_expiry) * _norm_cdf(d2)
    return strike * math.exp(-rate * time_to_expiry) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_greeks(spot: float, strike: float, time_to_expiry: float, rate: float, sigma: float, side: OptionSide) -> Greeks:
    d1, d2 = _d1_d2(spot, strike, time_to_expiry, rate, sigma)
    pdf_d1 = _norm_pdf(d1)
    sqrt_t = math.sqrt(time_to_expiry)
    disc = math.exp(-rate * time_to_expiry)

    gamma = pdf_d1 / (spot * sigma * sqrt_t)
    vega = spot * pdf_d1 * sqrt_t

    if side == "CE":
        delta = _norm_cdf(d1)
        theta_annual = -(spot * pdf_d1 * sigma) / (2 * sqrt_t) - rate * strike * disc * _norm_cdf(d2)
        rho = strike * time_to_expiry * disc * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1
        theta_annual = -(spot * pdf_d1 * sigma) / (2 * sqrt_t) + rate * strike * disc * _norm_cdf(-d2)
        rho = -strike * time_to_expiry * disc * _norm_cdf(-d2)

    return Greeks(delta=delta, gamma=gamma, theta=theta_annual / 365.0, vega=vega, rho=rho)


def black76_price(forward: float, strike: float, time_to_expiry: float, rate: float, sigma: float, side: OptionSide) -> float:
    """Black-76 price. `forward` is the underlying's forward price (for index
    options, the futures/forward price for the matching expiry is the
    theoretically correct input; using spot as a stand-in for the forward is
    an additional simplification some implementations make — this function
    itself is agnostic and just takes whatever `forward` value you pass)."""
    d1, d2 = _d1_d2(forward, strike, time_to_expiry, rate, sigma)
    disc = math.exp(-rate * time_to_expiry)
    if side == "CE":
        return disc * (forward * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return disc * (strike * _norm_cdf(-d2) - forward * _norm_cdf(-d1))


def black76_greeks(forward: float, strike: float, time_to_expiry: float, rate: float, sigma: float, side: OptionSide) -> Greeks:
    d1, d2 = _d1_d2(forward, strike, time_to_expiry, rate, sigma)
    pdf_d1 = _norm_pdf(d1)
    sqrt_t = math.sqrt(time_to_expiry)
    disc = math.exp(-rate * time_to_expiry)

    gamma = disc * pdf_d1 / (forward * sigma * sqrt_t)
    vega = disc * forward * pdf_d1 * sqrt_t

    if side == "CE":
        delta = disc * _norm_cdf(d1)
        theta_annual = -(forward * disc * pdf_d1 * sigma) / (2 * sqrt_t) - rate * disc * strike * _norm_cdf(d2) + rate * disc * forward * _norm_cdf(d1)
        rho = -time_to_expiry * black76_price(forward, strike, time_to_expiry, rate, sigma, side)
    else:
        delta = -disc * _norm_cdf(-d1)
        theta_annual = -(forward * disc * pdf_d1 * sigma) / (2 * sqrt_t) + rate * disc * strike * _norm_cdf(-d2) - rate * disc * forward * _norm_cdf(-d1)
        rho = -time_to_expiry * black76_price(forward, strike, time_to_expiry, rate, sigma, side)

    return Greeks(delta=delta, gamma=gamma, theta=theta_annual / 365.0, vega=vega, rho=rho)


def _implied_volatility(
    price_fn, market_price: float, underlying: float, strike: float, time_to_expiry: float, rate: float, side: OptionSide,
    initial_guess: float = 0.3, tol: float = 1e-6, max_iter: int = 100,
) -> Optional[float]:
    """Newton-Raphson with a bisection fallback if NR fails to converge or
    steps out of a sane volatility range. Returns None (not an exception,
    not a fabricated number) if no volatility in (1e-4, 5.0) reproduces the
    market price within tolerance — e.g. the quoted price is outside
    no-arbitrage bounds (stale/bad quote)."""
    sigma = initial_guess
    for _ in range(max_iter):
        try:
            price = price_fn(underlying, strike, time_to_expiry, rate, sigma, side)
        except ValueError:
            break
        d1, _ = _d1_d2(underlying, strike, time_to_expiry, rate, sigma)
        vega_val = underlying * _norm_pdf(d1) * math.sqrt(time_to_expiry)
        diff = price - market_price
        if abs(diff) < tol:
            return sigma
        if vega_val < 1e-8:
            break
        sigma -= diff / vega_val
        if sigma <= 0 or sigma > 5.0:
            break
    else:
        d1, _ = _d1_d2(underlying, strike, time_to_expiry, rate, sigma)
        if abs(price_fn(underlying, strike, time_to_expiry, rate, sigma, side) - market_price) < tol:
            return sigma

    # Bisection fallback over a wide, sane volatility range.
    lo, hi = 1e-4, 5.0
    price_lo = price_fn(underlying, strike, time_to_expiry, rate, lo, side) - market_price
    price_hi = price_fn(underlying, strike, time_to_expiry, rate, hi, side) - market_price
    if price_lo * price_hi > 0:
        return None  # market price is outside what any volatility in range can produce
    for _ in range(200):
        mid = (lo + hi) / 2
        price_mid = price_fn(underlying, strike, time_to_expiry, rate, mid, side) - market_price
        if abs(price_mid) < tol:
            return mid
        if price_lo * price_mid < 0:
            hi = mid
        else:
            lo, price_lo = mid, price_mid
    return (lo + hi) / 2


def bs_implied_volatility(market_price: float, spot: float, strike: float, time_to_expiry: float, rate: float, side: OptionSide) -> Optional[float]:
    return _implied_volatility(bs_price, market_price, spot, strike, time_to_expiry, rate, side)


def black76_implied_volatility(market_price: float, forward: float, strike: float, time_to_expiry: float, rate: float, side: OptionSide) -> Optional[float]:
    return _implied_volatility(black76_price, market_price, forward, strike, time_to_expiry, rate, side)
