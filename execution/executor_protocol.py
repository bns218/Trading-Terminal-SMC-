"""The Executor protocol both PaperExecutor and LiveExecutor implement.

Mode selection happens exactly ONCE, at process startup, from config
(config.settings.Settings.trading_mode, hard-typed to Literal["PAPER"] — see
config/settings.py). There is no runtime code path that switches an executor
between paper and live; whichever concrete class is instantiated at startup
is used for the life of the process.

Fill model (stated explicitly, per the project's ground rules): an order
submitted via `submit_order` is PENDING until the next bar's open price
arrives via `on_bar_open` — it fills there, never at the signal-deciding
bar's own close (that would be an optimistic same-bar fill) and never at an
optimistic mid-price. `on_price_update` then monitors the filled position for
stop-loss/target hits on subsequent price data.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol

from data.models import Instrument, TradeSignal


class Executor(Protocol):
    def submit_order(self, signal: TradeSignal, instrument: Instrument, quantity: int, now: datetime) -> str:
        """Submit an order sized by the risk manager. Returns a position id.
        The order is PENDING — no fill happens here."""
        ...

    def on_bar_open(self, instrument_token: str, open_price: Decimal, bar_time: datetime) -> None:
        """Fill any pending order for this instrument at this bar's open price."""
        ...

    def on_price_update(self, instrument_token: str, price: Decimal, now: datetime) -> None:
        """Feed a new price for an open (filled) position, checking stop-loss/target hits."""
        ...

    def close_position(self, position_id: str, exit_price: Decimal, exit_reason: str, now: datetime) -> None:
        """Force-close an open position (e.g. session close, manual override)."""
        ...

    def open_position_ids(self) -> list[str]:
        ...
