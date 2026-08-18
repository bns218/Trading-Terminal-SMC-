"""PaperExecutor: full paper-trading simulation. Honest fill model, honest
charges, real trade journal persistence.

Fill model (see execution/executor_protocol.py's module docstring for the
full statement): orders fill at the NEXT bar's open, never the signal bar's
own close. A configurable slippage (in basis points, ALWAYS against the
trader — a buy fills slightly above the bar open, a sell fills slightly
below it) is applied on both entry and exit fills. This is deliberately
pessimistic: it never gives an optimistic mid-price fill.

Stop-loss and target checks use the price fed via on_price_update, which is
expected to be a single tick/LTP value, not an OHLC bar — a single price can
only be on one side of an entry (above or below), so there is no same-update
stop-vs-target ambiguity here the way there is when checking a full bar's
high/low (as the Phase 3 backtest harness does). If you instead feed this
from bar data, feed the bar's LOW before its HIGH for a long position (and
vice-versa for a short) to preserve the same conservative "stop wins ties"
assumption the backtest harness uses.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from config.charges_config import ChargesConfig, Segment, compute_round_trip_charges
from data.database import TickStore
from data.models import Instrument, InstrumentType, SignalDirection, TradeJournalEntry, TradeSignal
from engine.position_manager import PositionManager

logger = logging.getLogger(__name__)

_INSTRUMENT_TYPE_TO_SEGMENT = {
    InstrumentType.EQ: Segment.EQUITY_INTRADAY,
    InstrumentType.FUTSTK: Segment.FUTURES,
    InstrumentType.FUTIDX: Segment.FUTURES,
    InstrumentType.OPTSTK: Segment.OPTIONS,
    InstrumentType.OPTIDX: Segment.OPTIONS,
}


class PaperExecutor:
    def __init__(self, store: TickStore, charges_config: ChargesConfig = ChargesConfig(), slippage_bps: Decimal = Decimal("2")):
        self._store = store
        self._charges_config = charges_config
        self._slippage_bps = slippage_bps
        self._positions = PositionManager()

    def _apply_slippage(self, price: Decimal, is_buy: bool) -> Decimal:
        """Slippage always moves the fill price against the trader: worse
        (higher) for a buy, worse (lower) for a sell."""
        adjustment = price * self._slippage_bps / Decimal("10000")
        return price + adjustment if is_buy else price - adjustment

    def submit_order(self, signal: TradeSignal, instrument: Instrument, quantity: int, now: datetime) -> str:
        if signal.direction == SignalDirection.NO_TRADE:
            raise ValueError("Cannot submit an order for a NO_TRADE signal.")
        if signal.stop_loss is None or not signal.targets:
            raise ValueError("Signal missing stop_loss/targets — cannot submit order.")
        position_id = self._positions.submit(
            instrument, signal.direction, quantity, signal.stop_loss, signal.targets[0],
            strategy="signal_engine", entry_reason="; ".join(signal.reasons) or "no reason given",
            confidence=signal.confidence, now=now,
        )
        logger.info("Order submitted: position_id=%s instrument=%s direction=%s qty=%d", position_id, instrument.symbol, signal.direction.value, quantity)
        return position_id

    def on_bar_open(self, instrument_token: str, open_price: Decimal, bar_time: datetime) -> None:
        for pending in list(self._positions.pending_for_instrument(instrument_token)):
            is_buy = pending.direction == SignalDirection.BUY
            fill_price = self._apply_slippage(open_price, is_buy)
            position = self._positions.fill(pending.position_id, fill_price, bar_time)
            logger.info(
                "Order filled: position_id=%s instrument=%s fill_price=%s (bar_open=%s, slippage_bps=%s)",
                position.position_id, instrument_token, fill_price, open_price, self._slippage_bps,
            )

    def on_price_update(self, instrument_token: str, price: Decimal, now: datetime) -> None:
        for position in list(self._positions.open_positions_for_instrument(instrument_token)):
            position.update_price(price)
            if position.hit_stop_loss(price):
                self._settle(position.position_id, position.stop_loss, "stop_loss", now)
            elif position.hit_target(price):
                self._settle(position.position_id, position.target, "target", now)

    def close_position(self, position_id: str, exit_price: Decimal, exit_reason: str, now: datetime) -> None:
        self._settle(position_id, exit_price, exit_reason, now)

    def _settle(self, position_id: str, exit_price: Decimal, exit_reason: str, now: datetime) -> None:
        position = self._positions.close(position_id)
        is_long = position.direction == SignalDirection.BUY

        exit_is_buy = not is_long  # closing a long = selling; closing a short = buying
        filled_exit_price = self._apply_slippage(exit_price, exit_is_buy)

        segment = _INSTRUMENT_TYPE_TO_SEGMENT.get(position.instrument.instrument_type, Segment.EQUITY_INTRADAY)
        charges = compute_round_trip_charges(
            position.entry_price, filled_exit_price, position.quantity, segment, self._charges_config, is_long
        )

        sign = 1 if is_long else -1
        pnl_gross = sign * (filled_exit_price - position.entry_price) * position.quantity
        pnl_net = pnl_gross - charges.total

        entry = TradeJournalEntry(
            instrument_token=position.instrument.token,
            instrument_symbol=position.instrument.symbol,
            direction=position.direction,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=filled_exit_price,
            stop_loss=position.stop_loss,
            target=position.target,
            pnl_gross=pnl_gross,
            pnl_net=pnl_net,
            charges_total=charges.total,
            confidence=position.confidence,
            strategy=position.strategy,
            entry_reason=position.entry_reason,
            exit_reason=exit_reason,
            entry_time=position.entry_time,
            exit_time=now,
        )
        self._store.insert_journal_entry(entry)
        logger.info(
            "Position closed: position_id=%s instrument=%s exit_reason=%s pnl_gross=%s pnl_net=%s",
            position_id, position.instrument.symbol, exit_reason, pnl_gross, pnl_net,
        )

    def open_position_ids(self) -> list[str]:
        return self._positions.open_position_ids()

    def pending_order_ids(self) -> list[str]:
        return self._positions.pending_order_ids()

    def get_position(self, position_id: str):
        return self._positions.get_open(position_id)
