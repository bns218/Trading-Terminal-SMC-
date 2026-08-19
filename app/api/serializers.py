"""Plain-dict serializers for the strategies/ dataclass outputs, since these
are internal engine types, not the pydantic contracts in data/models.py.
Kept separate from the strategy modules themselves (which stay pure/no-I/O)
and from the route handlers (which stay thin)."""
from __future__ import annotations

from decimal import Decimal

from strategies.patterns_common import PatternEvent
from strategies.smc_pro import OpeningRange, PremiumDiscountZone, TradeSignal, Zone


def zone_to_dict(zone: Zone, close_times: list) -> dict:
    return {
        "kind": zone.kind,
        "bias": zone.bias,
        "top": zone.top,
        "bottom": zone.bottom,
        "start_index": zone.start_index,
        "start_time": zone.start_time.isoformat(),
        "end_time": close_times[zone.end_index].isoformat() if zone.end_index is not None else None,
        "is_inverse": zone.is_inverse,
    }


def premium_discount_to_dict(zone: PremiumDiscountZone | None) -> dict | None:
    if zone is None:
        return None
    return {
        "range_high": zone.range_high,
        "range_low": zone.range_low,
        "premium_level": zone.premium_level,
        "equilibrium_level": zone.equilibrium_level,
        "discount_level": zone.discount_level,
    }


def opening_range_to_dict(orb: OpeningRange) -> dict:
    return {"session_date": orb.session_date, "high": orb.high, "low": orb.low, "locked": orb.locked}


def trade_signal_to_dict(signal: TradeSignal) -> dict:
    return {
        "bar_index": signal.bar_index,
        "timestamp": signal.timestamp.isoformat(),
        "direction": signal.direction,
        "score": signal.score,
        "entry": signal.entry,
        "stop_loss": signal.stop_loss,
        "tp1": signal.tp1,
        "tp2": signal.tp2,
        "tp3": signal.tp3,
        "reasons": {k: bool(v) for k, v in signal.reasons.items()},
    }


def pattern_event_to_dict(event: PatternEvent) -> dict:
    return {
        "pattern": event.pattern,
        "bar_index": event.bar_index,
        "timestamp": event.timestamp.isoformat(),
        "direction": event.direction,
        "details": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in event.details.items()},
    }


def candle_row_to_dict(row: dict) -> dict:
    return {
        "open_time": row["open_time"],
        "close_time": row["close_time"],
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "volume": row["volume"],
        "is_backfilled": bool(row["is_backfilled"]),
        "is_closed": bool(row["is_closed"]),
    }
