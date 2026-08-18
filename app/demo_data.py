"""Synthetic demo dataset for `--demo` mode. Every instrument symbol, price
series, and journal entry generated here is clearly labelled DEMO — this is
for UI demonstration only, per the project's ground rule to never present
synthetic output as live market data. See README's Known Limitations for the
same statement in user-facing form.

Uses a seeded RNG so the demo is reproducible (same look every run), not
because reproducibility is asked for by the brief but because it makes any
demo-mode bug report reproducible too.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from config.market_calendar import MarketCalendar
from data.database import TickStore
from data.models import Exchange, Instrument, InstrumentType, SignalDirection, TradeJournalEntry
from app.state import AppState

DEMO_TOKEN = "DEMO26000"
DEMO_SYMBOL = "DEMO-NIFTYFUT"

DEMO_INSTRUMENT = Instrument(
    token=DEMO_TOKEN,
    symbol=DEMO_SYMBOL,
    name="DEMO-NIFTY",
    exchange=Exchange.NFO,
    instrument_type=InstrumentType.FUTIDX,
    lot_size=25,
    tick_size=Decimal("0.05"),
)


def _demo_session_start(calendar: MarketCalendar) -> datetime:
    today = datetime.now(timezone.utc).date()
    day = today if calendar.is_trading_day(today) else calendar.next_trading_day(today)
    bounds = calendar.session_bounds(day)
    if bounds is None:
        day = calendar.next_trading_day(day)
        bounds = calendar.session_bounds(day)
    return bounds[0]


def seed_demo_candles(store: TickStore, calendar: MarketCalendar, num_minutes: int = 90, seed: int = 42) -> None:
    rng = random.Random(seed)
    session_start = _demo_session_start(calendar)
    price = Decimal("24500.00")

    for i in range(num_minutes):
        open_time = session_start + timedelta(minutes=i)
        close_time = open_time + timedelta(minutes=1)

        drift = Decimal(str(rng.gauss(0, 8))).quantize(Decimal("0.01"))
        open_price = price
        close_price = (open_price + drift).quantize(Decimal("0.01"))
        high_price = max(open_price, close_price) + Decimal(str(abs(rng.gauss(3, 2)))).quantize(Decimal("0.01"))
        low_price = min(open_price, close_price) - Decimal(str(abs(rng.gauss(3, 2)))).quantize(Decimal("0.01"))
        volume = rng.randint(200, 5000)

        store.upsert_candle(
            DEMO_TOKEN, "1min", open_time, close_time,
            open_price, high_price, low_price, close_price, volume,
            is_backfilled=False, is_closed=True,
        )
        price = close_price


def seed_demo_journal(store: TickStore, calendar: MarketCalendar, seed: int = 42) -> None:
    rng = random.Random(seed + 1)
    session_start = _demo_session_start(calendar)

    outcomes = [
        (SignalDirection.BUY, Decimal("24500"), Decimal("24575"), "target", 68.5),
        (SignalDirection.SELL, Decimal("24560"), Decimal("24610"), "stop_loss", 61.0),
        (SignalDirection.BUY, Decimal("24480"), Decimal("24420"), "stop_loss", 64.0),
        (SignalDirection.BUY, Decimal("24510"), Decimal("24590"), "target", 72.0),
    ]
    for i, (direction, entry, exit_, reason, confidence) in enumerate(outcomes):
        entry_time = session_start + timedelta(minutes=10 * i)
        exit_time = entry_time + timedelta(minutes=rng.randint(5, 25))
        sign = 1 if direction == SignalDirection.BUY else -1
        pnl_gross = sign * (exit_ - entry) * 25
        charges = Decimal("45.30")
        store.insert_journal_entry(
            TradeJournalEntry(
                instrument_token=DEMO_TOKEN, instrument_symbol=DEMO_SYMBOL, direction=direction,
                quantity=25, entry_price=entry, exit_price=exit_, stop_loss=entry - Decimal("75") if direction == SignalDirection.BUY else entry + Decimal("75"),
                target=exit_, pnl_gross=pnl_gross, pnl_net=pnl_gross - charges, charges_total=charges,
                confidence=confidence, strategy="demo_signal_engine",
                entry_reason="[DEMO DATA] synthetic confluence example, not a real signal",
                exit_reason=reason, entry_time=entry_time, exit_time=exit_time,
            )
        )


def seed_demo_health_events(store: TickStore) -> None:
    store.record_health_event("reconnect", "[DEMO DATA] duration_seconds=4.2")
    store.record_health_event("rejected_bar", "[DEMO DATA] reason=non-positive ltp")
    store.record_health_event("backfill", "[DEMO DATA] bars=3")


def build_demo_positions() -> list[dict]:
    return [
        {
            "position_id": "demo-pos-1",
            "instrument_symbol": DEMO_SYMBOL,
            "direction": "BUY",
            "quantity": 25,
            "entry_price": "24520.00",
            "current_price": "24555.00",
            "unrealized_pnl": "875.00",
            "stop_loss": "24445.00",
            "target": "24595.00",
            "is_demo": True,
        }
    ]


def seed_demo_state(app_state: AppState, seed: int = 42) -> None:
    """Populate the store and AppState with the full demo dataset. Idempotent
    enough for repeated dev-server reloads (candle upsert is keyed by
    open_time, so re-seeding just overwrites the same rows)."""
    seed_demo_candles(app_state.store, app_state.calendar, seed=seed)
    seed_demo_journal(app_state.store, app_state.calendar, seed=seed)
    seed_demo_health_events(app_state.store)
    app_state.demo_positions = build_demo_positions()
    app_state.demo_mode = True
