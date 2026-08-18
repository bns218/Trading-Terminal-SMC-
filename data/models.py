"""Shared data contracts. Broker code and strategy code meet only through these
types — neither imports the other directly.

Rules enforced here:
- All prices/P&L use Decimal, never float.
- All datetimes must be timezone-aware (validators reject naive datetimes).
- Every derived/computed metric (Greeks, PCR, max pain, OI buildup, etc.)
  carries `source` and `computed_at` so the UI can show provenance on hover.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, field_validator


def _reject_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"Naive datetime {value!r} is not allowed — attach a timezone.")
    return value


class Exchange(str, Enum):
    NSE = "NSE"
    BSE = "BSE"
    NFO = "NFO"
    BFO = "BFO"
    MCX = "MCX"
    CDS = "CDS"


class InstrumentType(str, Enum):
    EQ = "EQ"
    FUTSTK = "FUTSTK"
    FUTIDX = "FUTIDX"
    OPTSTK = "OPTSTK"
    OPTIDX = "OPTIDX"
    INDEX = "INDEX"


class OptionType(str, Enum):
    CE = "CE"
    PE = "PE"


class Instrument(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: str
    symbol: str
    name: str
    exchange: Exchange
    instrument_type: InstrumentType
    lot_size: int
    tick_size: Decimal
    expiry: Optional[datetime] = None
    strike: Optional[Decimal] = None
    freeze_quantity: Optional[int] = None  # None until NSE contract-file source is wired in
    option_type: Optional[OptionType] = None  # CE/PE, derived from symbol suffix; None for non-options

    @field_validator("expiry")
    @classmethod
    def _expiry_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        return _reject_naive(v) if v is not None else v


class Quote(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_token: str
    exchange: Exchange
    ltp: Decimal
    open: Optional[Decimal] = None
    high: Optional[Decimal] = None
    low: Optional[Decimal] = None
    close: Optional[Decimal] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    timestamp: datetime

    @field_validator("timestamp")
    @classmethod
    def _timestamp_aware(cls, v: datetime) -> datetime:
        return _reject_naive(v)


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True)

    instrument_token: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    open_time: datetime
    close_time: datetime
    is_backfilled: bool = False

    @field_validator("open_time", "close_time")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _reject_naive(v)


class DerivedMetric(BaseModel):
    """Base for any client-computed or API-fetched analytical value.

    `source` distinguishes provenance so the UI never presents a computed
    number as if it were a raw broker value, or vice versa. `value` is a
    union because this one type carries everything from a strike-price-like
    figure (max pain: Decimal, tick-sized like money) to a dimensionless
    ratio or IV/Greek (float) to a category label (OI buildup classification:
    str) — the provenance contract (source + computed_at) is what's uniform,
    not the value's shape.
    """

    model_config = ConfigDict(frozen=True)

    value: Union[Decimal, float, str]
    source: str  # e.g. "computed_chain", "angelone_api", "computed_bs76"
    computed_at: datetime

    @field_validator("computed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _reject_naive(v)


class OptionContract(BaseModel):
    """One strike's paired call+put legs for a given underlying/expiry. Either
    leg may be absent (e.g. deep OTM strikes sometimes missing from the
    instrument master, or a quote fetch failure for just one leg)."""

    model_config = ConfigDict(frozen=True)

    strike: Decimal
    call_instrument: Optional[Instrument] = None
    put_instrument: Optional[Instrument] = None
    call_quote: Optional[Quote] = None
    put_quote: Optional[Quote] = None


class OptionChainSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    underlying_name: str
    expiry: datetime
    spot_price: Decimal
    contracts: list[OptionContract]
    computed_at: datetime

    @field_validator("expiry", "computed_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        return _reject_naive(v)
