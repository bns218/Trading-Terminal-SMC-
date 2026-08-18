"""Shared, in-process application state for the FastAPI dashboard.

The dashboard is a READER: it never opens the SmartAPI WebSocket itself
(ingestion/run_ingestion.py, a separate process, is the only thing that
does) — see docs/phase0_capability_matrix.md's architecture decision. This
module just holds references to the store/calendar every route needs, plus
demo-mode state for the `--demo` flag.

Open positions are runtime state owned by execution/paper_executor.py's
PositionManager, which lives in whatever process is actually running the
signal engine + risk manager + executor loop — NOT this dashboard process in
this phase's wiring (no such loop is started here; that's a further
integration step left for you to wire up once you're running against a real
session). In `--demo` mode, `demo_positions` fakes a couple of open
positions purely for UI demonstration, clearly labelled as such.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config.market_calendar import MarketCalendar
from data.database import TickStore


@dataclass
class AppState:
    store: TickStore
    calendar: MarketCalendar
    demo_mode: bool = False
    demo_instrument_token: str = "DEMO26000"
    demo_instrument_symbol: str = "DEMO-NIFTYFUT"
    demo_positions: list[dict] = field(default_factory=list)
    kill_switch_enabled: bool = False
