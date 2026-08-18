import asyncio
from pathlib import Path

import pytest

from app.demo_data import DEMO_TOKEN, seed_demo_state
from app.sse.streams import health_stream, tick_stream
from app.state import AppState
from config.market_calendar import MarketCalendar
from config.settings import get_settings
from data.database import TickStore


def make_app_state(tmp_path: Path) -> AppState:
    store = TickStore(tmp_path / "sse.db")
    calendar = MarketCalendar(get_settings().holidays_file)
    return AppState(store=store, calendar=calendar)


async def _collect(agen, n):
    out = []
    async for item in agen:
        out.append(item)
        if len(out) >= n:
            break
    return out


def test_tick_stream_yields_finite_events(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=1)
    events = asyncio.run(_collect(tick_stream(app_state, DEMO_TOKEN, poll_interval=0.01, max_iterations=3), 10))
    assert len(events) == 3
    assert all(e.startswith("data: ") and e.endswith("\n\n") for e in events)


def test_tick_stream_reports_no_data_for_unknown_token(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    events = asyncio.run(_collect(tick_stream(app_state, "NOPE", poll_interval=0.01, max_iterations=1), 5))
    assert len(events) == 1
    assert "heartbeat" in events[0]


def test_health_stream_yields_finite_events(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=1)
    events = asyncio.run(_collect(health_stream(app_state, DEMO_TOKEN, poll_interval=0.01, max_iterations=2), 10))
    assert len(events) == 2
    for e in events:
        assert "reconnect_count_24h" in e
