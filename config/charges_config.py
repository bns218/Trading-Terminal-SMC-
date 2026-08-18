"""Brokerage + statutory charges schedule for paper P&L calculation.

STATUS: PLACEHOLDER PERCENTAGES — VERIFY AGAINST ANGEL ONE'S CURRENT TARIFF SHEET.

Only `gst_pct` (18%, a fixed statutory GST rate on brokerage + exchange
transaction charges + SEBI charges) is reasonably stable. Every other
percentage below (brokerage, STT, exchange transaction charges, SEBI
turnover fees, stamp duty) is a representative order-of-magnitude figure for
Indian equity/F&O trading, NOT a verified current Angel One rate — broker
tariffs and regulatory charge rates both change over time, and this
environment has no network path to Angel One's published tariff sheet to
confirm current numbers (same docs-domain-blocked constraint as the rest of
this project). Net P&L computed by PaperExecutor using these defaults is
useful for exercising the calculation MECHANISM, not as a real cost estimate,
until you update these values from the current official tariff sheet.

Charge structure modeled (standard Indian discount-broker structure):
  brokerage      = min(brokerage_flat_per_order, brokerage_pct * turnover), applied per leg (entry + exit)
  stt            = stt_pct * turnover, SELL side only, rate depends on segment
  exchange_txn   = exchange_txn_pct * turnover, both legs
  sebi_charges   = sebi_charges_pct * turnover, both legs
  stamp_duty     = stamp_duty_pct * turnover, BUY side only
  gst            = gst_pct * (brokerage + exchange_txn + sebi_charges)   [GST does not apply to STT or stamp duty]
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Segment(str, Enum):
    EQUITY_INTRADAY = "EQUITY_INTRADAY"
    FUTURES = "FUTURES"
    OPTIONS = "OPTIONS"


@dataclass(frozen=True)
class ChargesConfig:
    brokerage_flat_per_order: Decimal = Decimal("20")  # PLACEHOLDER
    brokerage_pct: Decimal = Decimal("0.0003")  # 0.03% PLACEHOLDER

    stt_pct_sell: dict = None  # set in __post_init__ per segment (PLACEHOLDER values)
    exchange_txn_pct: dict = None  # PLACEHOLDER values, differ by segment
    sebi_charges_pct: Decimal = Decimal("0.0000010")  # PLACEHOLDER, applied to both legs
    stamp_duty_pct_buy: dict = None  # PLACEHOLDER values, differ by segment

    gst_pct: Decimal = Decimal("0.18")  # statutory, comparatively stable

    def __post_init__(self):
        object.__setattr__(
            self, "stt_pct_sell",
            {
                Segment.EQUITY_INTRADAY: Decimal("0.00025"),
                Segment.FUTURES: Decimal("0.0001"),
                Segment.OPTIONS: Decimal("0.001"),  # applied on premium turnover, not notional
            },
        )
        object.__setattr__(
            self, "exchange_txn_pct",
            {
                Segment.EQUITY_INTRADAY: Decimal("0.0000345"),
                Segment.FUTURES: Decimal("0.0000173"),
                Segment.OPTIONS: Decimal("0.0003503"),  # on premium turnover
            },
        )
        object.__setattr__(
            self, "stamp_duty_pct_buy",
            {
                Segment.EQUITY_INTRADAY: Decimal("0.00003"),
                Segment.FUTURES: Decimal("0.00002"),
                Segment.OPTIONS: Decimal("0.00003"),
            },
        )


@dataclass(frozen=True)
class ChargeBreakdown:
    brokerage: Decimal
    stt: Decimal
    exchange_txn: Decimal
    sebi_charges: Decimal
    stamp_duty: Decimal
    gst: Decimal

    @property
    def total(self) -> Decimal:
        return self.brokerage + self.stt + self.exchange_txn + self.sebi_charges + self.stamp_duty + self.gst


def compute_leg_charges(turnover: Decimal, segment: Segment, config: ChargesConfig, is_buy: bool) -> ChargeBreakdown:
    """Charges for ONE leg (either the entry or the exit) of a trade.
    `turnover` = price * quantity for that leg."""
    if turnover < 0:
        raise ValueError("turnover must be non-negative")

    brokerage = min(config.brokerage_flat_per_order, config.brokerage_pct * turnover)
    stt = Decimal("0") if is_buy else config.stt_pct_sell[segment] * turnover
    exchange_txn = config.exchange_txn_pct[segment] * turnover
    sebi_charges = config.sebi_charges_pct * turnover
    stamp_duty = config.stamp_duty_pct_buy[segment] * turnover if is_buy else Decimal("0")
    gst = config.gst_pct * (brokerage + exchange_txn + sebi_charges)

    return ChargeBreakdown(
        brokerage=brokerage, stt=stt, exchange_txn=exchange_txn,
        sebi_charges=sebi_charges, stamp_duty=stamp_duty, gst=gst,
    )


def compute_round_trip_charges(
    entry_price: Decimal, exit_price: Decimal, quantity: int, segment: Segment, config: ChargesConfig, is_long: bool
) -> ChargeBreakdown:
    """Full round-trip (entry leg + exit leg) charges for one trade."""
    entry_turnover = entry_price * quantity
    exit_turnover = exit_price * quantity

    entry_is_buy = is_long
    exit_is_buy = not is_long

    entry_charges = compute_leg_charges(entry_turnover, segment, config, is_buy=entry_is_buy)
    exit_charges = compute_leg_charges(exit_turnover, segment, config, is_buy=exit_is_buy)

    return ChargeBreakdown(
        brokerage=entry_charges.brokerage + exit_charges.brokerage,
        stt=entry_charges.stt + exit_charges.stt,
        exchange_txn=entry_charges.exchange_txn + exit_charges.exchange_txn,
        sebi_charges=entry_charges.sebi_charges + exit_charges.sebi_charges,
        stamp_duty=entry_charges.stamp_duty + exit_charges.stamp_duty,
        gst=entry_charges.gst + exit_charges.gst,
    )
