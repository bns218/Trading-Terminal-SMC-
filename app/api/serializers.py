"""Plain-dict serializers for the strategies/ dataclass outputs, since these
are internal engine types, not the pydantic contracts in data/models.py.
Kept separate from the strategy modules themselves (which stay pure/no-I/O)
and from the route handlers (which stay thin)."""
from __future__ import annotations

from decimal import Decimal

from strategies.patterns_common import PatternEvent


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
