"""Options analytics computed client-side from a constructed OptionChainSnapshot:
PCR, max pain, OI buildup classification, ATM/ITM/OTM, Call OI resistance /
Put OI support.

Per the confirmed Phase 0 decision, PCR and OI-buildup here are always
`source="computed_chain"` — Angel One's own PCR/OI-Buildup API values (when
wired in via broker/options_api.py) are shown ALONGSIDE these, tagged
`source="angelone_api"`, never substituted for them.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from data.models import DerivedMetric, OptionChainSnapshot, OptionType

Moneyness = Literal["ITM", "ATM", "OTM"]
OIBuildup = Literal["long_buildup", "short_buildup", "short_covering", "long_unwinding", "neutral"]


def find_atm_strike(chain: OptionChainSnapshot) -> Optional[Decimal]:
    """The single strike closest to spot. Ties broken toward the lower strike."""
    if not chain.contracts:
        return None
    return min((c.strike for c in chain.contracts), key=lambda k: (abs(k - chain.spot_price), k))


def classify_moneyness(strike: Decimal, spot: Decimal, atm_strike: Decimal, option_type: OptionType) -> Moneyness:
    if strike == atm_strike:
        return "ATM"
    if option_type == OptionType.CE:
        return "ITM" if strike < spot else "OTM"
    return "ITM" if strike > spot else "OTM"


def compute_pcr(chain: OptionChainSnapshot, computed_at: datetime) -> DerivedMetric:
    """Put/Call ratio by open interest, summed across the whole chain."""
    call_oi = sum((c.call_quote.open_interest or 0) for c in chain.contracts if c.call_quote)
    put_oi = sum((c.put_quote.open_interest or 0) for c in chain.contracts if c.put_quote)
    ratio = Decimal(put_oi) / Decimal(call_oi) if call_oi > 0 else Decimal(0)
    return DerivedMetric(value=ratio, source="computed_chain", computed_at=computed_at)


def compute_max_pain(chain: OptionChainSnapshot, computed_at: datetime) -> Optional[DerivedMetric]:
    """Max pain: the strike at which option WRITERS collectively lose the
    least (equivalently, buyers gain the least) if the underlying settled
    there — computed by summing, for each candidate settlement strike, the
    intrinsic-value payout owed to ITM call and put holders across all
    strikes, weighted by OI, and picking the settlement strike that
    minimizes total payout.
    """
    strikes = [c.strike for c in chain.contracts]
    if not strikes:
        return None

    best_strike = None
    best_payout = None
    for candidate in strikes:
        total_payout = Decimal(0)
        for c in chain.contracts:
            if candidate > c.strike:  # calls at this strike are ITM if settlement > strike
                call_oi = c.call_quote.open_interest if c.call_quote and c.call_quote.open_interest else 0
                total_payout += (candidate - c.strike) * call_oi
            if candidate < c.strike:  # puts at this strike are ITM if settlement < strike
                put_oi = c.put_quote.open_interest if c.put_quote and c.put_quote.open_interest else 0
                total_payout += (c.strike - candidate) * put_oi
        if best_payout is None or total_payout < best_payout:
            best_payout = total_payout
            best_strike = candidate

    return DerivedMetric(value=best_strike, source="computed_chain", computed_at=computed_at)


def classify_oi_buildup(price_change: Decimal, oi_change: int) -> OIBuildup:
    """Standard price-vs-OI-change matrix:
        price up   + OI up   -> long_buildup    (new longs being added)
        price down + OI up   -> short_buildup   (new shorts being added)
        price up   + OI down -> short_covering  (shorts closing out)
        price down + OI down -> long_unwinding  (longs closing out)
        no price change, or no OI change -> neutral (insufficient signal)
    """
    if price_change == 0 or oi_change == 0:
        return "neutral"
    if price_change > 0 and oi_change > 0:
        return "long_buildup"
    if price_change < 0 and oi_change > 0:
        return "short_buildup"
    if price_change > 0 and oi_change < 0:
        return "short_covering"
    return "long_unwinding"


def call_oi_resistance(chain: OptionChainSnapshot, computed_at: datetime) -> Optional[DerivedMetric]:
    """The strike with the highest call OI — interpreted as the level option
    writers are most heavily positioned to defend on the upside (resistance)."""
    candidates = [(c.strike, c.call_quote.open_interest) for c in chain.contracts if c.call_quote and c.call_quote.open_interest]
    if not candidates:
        return None
    strike, _ = max(candidates, key=lambda pair: pair[1])
    return DerivedMetric(value=strike, source="computed_chain", computed_at=computed_at)


def put_oi_support(chain: OptionChainSnapshot, computed_at: datetime) -> Optional[DerivedMetric]:
    """Mirror of call_oi_resistance: the strike with the highest put OI,
    interpreted as downside support."""
    candidates = [(c.strike, c.put_quote.open_interest) for c in chain.contracts if c.put_quote and c.put_quote.open_interest]
    if not candidates:
        return None
    strike, _ = max(candidates, key=lambda pair: pair[1])
    return DerivedMetric(value=strike, source="computed_chain", computed_at=computed_at)
