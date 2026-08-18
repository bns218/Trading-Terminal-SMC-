"""LiveExecutor: interface only. Every method raises NotImplementedError.

This exists so the Executor protocol has a second, real implementation slot
— proving PaperExecutor isn't hard-wired as "the only possible executor" —
without writing a single line of code that could place a real order against
a real account. Per the project's non-negotiable ground rules,
TRADING_MODE is hard-wired to PAPER (config/settings.py) and there is no
runtime path that constructs a LiveExecutor from that config. Implementing
this for real is a deliberate, separate decision for a human to make later,
not something that happens by editing a config value.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from data.models import Instrument, TradeSignal


class LiveExecutor:
    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "LiveExecutor is an interface stub. Live order placement against a real "
            "Angel One account is intentionally not implemented in this build — "
            "TRADING_MODE is hard-wired to PAPER. Implementing this requires a deliberate, "
            "separate decision and review, not a config change."
        )

    def submit_order(self, signal: TradeSignal, instrument: Instrument, quantity: int, now: datetime) -> str:
        raise NotImplementedError("LiveExecutor.submit_order is not implemented — see class docstring.")

    def on_bar_open(self, instrument_token: str, open_price: Decimal, bar_time: datetime) -> None:
        raise NotImplementedError("LiveExecutor.on_bar_open is not implemented — see class docstring.")

    def on_price_update(self, instrument_token: str, price: Decimal, now: datetime) -> None:
        raise NotImplementedError("LiveExecutor.on_price_update is not implemented — see class docstring.")

    def close_position(self, position_id: str, exit_price: Decimal, exit_reason: str, now: datetime) -> None:
        raise NotImplementedError("LiveExecutor.close_position is not implemented — see class docstring.")

    def open_position_ids(self) -> list[str]:
        raise NotImplementedError("LiveExecutor.open_position_ids is not implemented — see class docstring.")
