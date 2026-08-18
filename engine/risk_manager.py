"""Risk manager: sits between the signal engine and the executor. No order
reaches the executor without passing evaluate(). Every rejection is logged
with the specific rule that fired.

Stateful by necessity (daily loss/trade counters, cooldown, open-position
count) — this is the one place in engine/ that isn't a pure function, and
that's intentional: risk state must persist across signals within a trading
day. State resets automatically when the calendar day changes (checked on
every evaluate() call), so a long-running process doesn't need an external
midnight cron to stay correct.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from config.market_calendar import IST, MarketCalendar
from config.risk_config import RiskConfig
from data.models import Instrument, SignalDirection, TradeSignal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    rejected_rule: Optional[str] = None
    rejected_reason: Optional[str] = None
    sized_quantity: Optional[int] = None
    risk_amount: Optional[Decimal] = None


@dataclass
class _DailyState:
    trading_day: date
    realized_pnl: Decimal = Decimal("0")
    trades_opened: int = 0
    consecutive_losses: int = 0
    cooldown_until: Optional[datetime] = None


class RiskManager:
    def __init__(self, config: RiskConfig, calendar: MarketCalendar):
        self._config = config
        self._calendar = calendar
        self._state = _DailyState(trading_day=date.min)
        self._open_positions = 0
        self._kill_switch = False

    def set_kill_switch(self, enabled: bool) -> None:
        self._kill_switch = enabled
        logger.warning("Risk manager kill switch set to %s", enabled)

    def _maybe_roll_day(self, now: datetime) -> None:
        today = now.astimezone(timezone.utc).date()
        if self._state.trading_day != today:
            logger.info("Risk manager: new trading day %s, resetting daily counters.", today)
            self._state = _DailyState(trading_day=today)

    def _reject(self, rule: str, reason: str) -> RiskDecision:
        logger.warning("Risk manager REJECTED signal: rule=%s reason=%s", rule, reason)
        return RiskDecision(approved=False, rejected_rule=rule, rejected_reason=reason)

    def evaluate(self, signal: TradeSignal, instrument: Instrument, now: datetime) -> RiskDecision:
        self._maybe_roll_day(now)

        if signal.direction == SignalDirection.NO_TRADE:
            return self._reject("no_trade_signal", "Signal engine returned NO_TRADE.")

        if self._kill_switch:
            return self._reject("kill_switch", "Emergency kill switch is active.")

        # Session-hour check uses the CONFIGURED window (which may be tighter
        # than the full 09:15-15:30 regular session, e.g. to avoid opening/
        # closing volatility), plus the calendar's own holiday/weekend check.
        ist_now = now.astimezone(IST).time()
        if not (self._config.session_start <= ist_now <= self._config.session_end):
            return self._reject(
                "outside_session_hours",
                f"{ist_now.isoformat()} IST outside configured window {self._config.session_start}-{self._config.session_end}",
            )
        if not self._calendar.is_regular_session(now):
            return self._reject("outside_session_hours", "Not inside a regular trading session (holiday/weekend/off-hours).")

        if self._state.realized_pnl <= -self._config.max_daily_loss:
            return self._reject(
                "max_daily_loss_reached",
                f"Realized P&L {self._state.realized_pnl} breaches -{self._config.max_daily_loss}",
            )

        if self._state.trades_opened >= self._config.max_trades_per_day:
            return self._reject(
                "max_trades_per_day_reached",
                f"{self._state.trades_opened} trades already opened today (limit {self._config.max_trades_per_day})",
            )

        if self._open_positions >= self._config.max_open_positions:
            return self._reject(
                "max_open_positions_reached",
                f"{self._open_positions} open positions (limit {self._config.max_open_positions})",
            )

        if self._state.cooldown_until is not None and now < self._state.cooldown_until:
            return self._reject(
                "cooldown_active",
                f"Cooldown active until {self._state.cooldown_until.isoformat()} "
                f"({self._config.cooldown_after_consecutive_losses} consecutive losses)",
            )

        if signal.entry is None or signal.stop_loss is None:
            return self._reject("invalid_signal", "Signal missing entry/stop_loss despite non-NO_TRADE direction.")

        per_share_risk = abs(signal.entry - signal.stop_loss)
        if per_share_risk <= 0:
            return self._reject("invalid_stop_distance", "Entry and stop-loss are equal or invalid.")

        risk_amount = self._config.account_capital * self._config.max_risk_per_trade_pct / Decimal("100")
        raw_qty = risk_amount / per_share_risk
        lot_size = instrument.lot_size
        sized_qty = int(raw_qty // lot_size) * lot_size

        if sized_qty < lot_size:
            return self._reject(
                "position_size_below_one_lot",
                f"Risk budget {risk_amount} / per-share-risk {per_share_risk} yields {raw_qty:.2f} units, "
                f"below one lot ({lot_size})",
            )

        actual_risk = sized_qty * per_share_risk
        return RiskDecision(approved=True, sized_quantity=sized_qty, risk_amount=actual_risk)

    def record_trade_opened(self, now: datetime) -> None:
        self._maybe_roll_day(now)
        self._state.trades_opened += 1
        self._open_positions += 1

    def record_trade_closed(self, pnl: Decimal, now: datetime) -> None:
        self._maybe_roll_day(now)
        self._state.realized_pnl += pnl
        self._open_positions = max(0, self._open_positions - 1)

        if pnl < 0:
            self._state.consecutive_losses += 1
        else:
            self._state.consecutive_losses = 0

        if self._state.consecutive_losses >= self._config.cooldown_after_consecutive_losses:
            self._state.cooldown_until = now + timedelta(minutes=self._config.cooldown_duration_minutes)
            logger.warning(
                "Risk manager: %d consecutive losses, cooldown active until %s",
                self._state.consecutive_losses, self._state.cooldown_until.isoformat(),
            )

    @property
    def daily_realized_pnl(self) -> Decimal:
        return self._state.realized_pnl

    @property
    def trades_opened_today(self) -> int:
        return self._state.trades_opened

    @property
    def open_positions(self) -> int:
        return self._open_positions
