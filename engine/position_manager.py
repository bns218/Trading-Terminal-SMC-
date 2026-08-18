"""Tracks pending orders and open positions with live unrealized P&L.
Composed internally by execution/paper_executor.py (and, eventually,
execution/live_executor.py) rather than duplicated — this is the single
source of truth for "what's open right now" during a run.

Deliberately mutable (unlike the frozen pydantic contracts in data/models.py)
because unrealized P&L genuinely changes as new prices arrive — this is
engine-internal runtime state, not a broker/strategy boundary contract.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from data.models import Instrument, SignalDirection

_id_counter = itertools.count(1)


def _next_id() -> str:
    return f"pos-{next(_id_counter)}"


@dataclass
class PendingOrder:
    position_id: str
    instrument: Instrument
    direction: SignalDirection
    quantity: int
    stop_loss: Decimal
    target: Decimal
    strategy: str
    entry_reason: str
    confidence: float
    submitted_at: datetime


@dataclass
class Position:
    position_id: str
    instrument: Instrument
    direction: SignalDirection
    quantity: int
    entry_price: Decimal
    stop_loss: Decimal
    target: Decimal
    strategy: str
    entry_reason: str
    confidence: float
    entry_time: datetime
    current_price: Optional[Decimal] = None
    unrealized_pnl: Optional[Decimal] = None

    def update_price(self, price: Decimal) -> None:
        self.current_price = price
        sign = 1 if self.direction == SignalDirection.BUY else -1
        self.unrealized_pnl = sign * (price - self.entry_price) * self.quantity

    def hit_stop_loss(self, price: Decimal) -> bool:
        if self.direction == SignalDirection.BUY:
            return price <= self.stop_loss
        return price >= self.stop_loss

    def hit_target(self, price: Decimal) -> bool:
        if self.direction == SignalDirection.BUY:
            return price >= self.target
        return price <= self.target


class PositionManager:
    def __init__(self) -> None:
        self._pending: dict[str, PendingOrder] = {}
        self._open: dict[str, Position] = {}

    def submit(
        self, instrument: Instrument, direction: SignalDirection, quantity: int,
        stop_loss: Decimal, target: Decimal, strategy: str, entry_reason: str, confidence: float, now: datetime,
    ) -> str:
        position_id = _next_id()
        self._pending[position_id] = PendingOrder(
            position_id=position_id, instrument=instrument, direction=direction, quantity=quantity,
            stop_loss=stop_loss, target=target, strategy=strategy, entry_reason=entry_reason,
            confidence=confidence, submitted_at=now,
        )
        return position_id

    def pending_for_instrument(self, instrument_token: str) -> list[PendingOrder]:
        return [p for p in self._pending.values() if p.instrument.token == instrument_token]

    def fill(self, position_id: str, fill_price: Decimal, fill_time: datetime) -> Position:
        pending = self._pending.pop(position_id)
        position = Position(
            position_id=position_id, instrument=pending.instrument, direction=pending.direction,
            quantity=pending.quantity, entry_price=fill_price, stop_loss=pending.stop_loss, target=pending.target,
            strategy=pending.strategy, entry_reason=pending.entry_reason, confidence=pending.confidence,
            entry_time=fill_time,
        )
        self._open[position_id] = position
        return position

    def get_open(self, position_id: str) -> Optional[Position]:
        return self._open.get(position_id)

    def open_positions_for_instrument(self, instrument_token: str) -> list[Position]:
        return [p for p in self._open.values() if p.instrument.token == instrument_token]

    def close(self, position_id: str) -> Position:
        return self._open.pop(position_id)

    def open_position_ids(self) -> list[str]:
        return list(self._open.keys())

    def pending_order_ids(self) -> list[str]:
        return list(self._pending.keys())
