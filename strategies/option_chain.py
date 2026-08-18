"""Option chain construction: pure combination of instrument-master records
(already filtered to a name+expiry by InstrumentMaster.find_option_chain)
and a caller-supplied quotes map. No I/O here — fetching the quotes is the
caller's job (broker/market_data.py or a batch-quote equivalent), same
separation as every other strategies/ module.

There is no "option chain" endpoint on SmartAPI (confirmed in Phase 0): the
chain must be built by filtering the instrument master by name+expiry, then
batch-quoting the resulting tokens. This module is the second half of that —
pairing CE/PE legs by strike into OptionContract rows.
"""
from __future__ import annotations

from datetime import datetime

from data.models import Instrument, OptionChainSnapshot, OptionContract, OptionType, Quote


def build_option_chain(
    instruments: list[Instrument],
    quotes: dict[str, Quote],
    underlying_name: str,
    expiry: datetime,
    spot_price,
    computed_at: datetime,
) -> OptionChainSnapshot:
    """`instruments` should already be filtered to this name+expiry (e.g. via
    InstrumentMaster.find_option_chain) — this function does not re-filter,
    it only pairs CE/PE legs by strike and attaches quotes by token.
    Instruments with no matching quote get call_quote/put_quote=None rather
    than being dropped, so a missing quote is visible, not silently absent.
    """
    by_strike: dict = {}
    for inst in instruments:
        if inst.strike is None or inst.option_type is None:
            continue  # not an option leg we can pair (shouldn't normally happen post-filter)
        entry = by_strike.setdefault(inst.strike, {"call": None, "put": None})
        if inst.option_type == OptionType.CE:
            entry["call"] = inst
        else:
            entry["put"] = inst

    contracts = []
    for strike in sorted(by_strike.keys()):
        legs = by_strike[strike]
        call_inst, put_inst = legs["call"], legs["put"]
        contracts.append(
            OptionContract(
                strike=strike,
                call_instrument=call_inst,
                put_instrument=put_inst,
                call_quote=quotes.get(call_inst.token) if call_inst else None,
                put_quote=quotes.get(put_inst.token) if put_inst else None,
            )
        )

    return OptionChainSnapshot(
        underlying_name=underlying_name,
        expiry=expiry,
        spot_price=spot_price,
        contracts=contracts,
        computed_at=computed_at,
    )
