"""Risk manager parameters. STATUS: UNVALIDATED DEFAULTS — sensible-looking
starting points, not tuned to any real account size or risk appetite. Set
`account_capital` to your actual paper-trading capital before trusting
position sizing output.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from decimal import Decimal


@dataclass(frozen=True)
class RiskConfig:
    account_capital: Decimal = Decimal("100000")  # rupees; used for position sizing and max-risk-per-trade
    max_risk_per_trade_pct: Decimal = Decimal("1.0")  # % of account_capital risked on a single trade
    max_daily_loss: Decimal = Decimal("3000")  # rupees; trading halts for the day once realized P&L breaches this
    max_trades_per_day: int = 5
    max_open_positions: int = 1
    cooldown_after_consecutive_losses: int = 2  # consecutive losing trades that trigger a cooldown
    cooldown_duration_minutes: int = 30
    session_start: time = time(9, 20)  # a few minutes after open, avoiding the most volatile opening minutes
    session_end: time = time(15, 15)  # a few minutes before close, avoiding closing-auction volatility
