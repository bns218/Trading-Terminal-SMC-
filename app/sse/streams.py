"""SSE generator functions. Each is a plain async generator that polls the
store — the dashboard never owns a broker WebSocket, so "live" here means
"picks up whatever the ingestion process most recently wrote," not a direct
feed.

`max_iterations` exists purely for testability (drives the generator to a
finite, deterministic end without needing a real client disconnect). Leave
it as None (the default) in production use; the route wrapper in
app/main.py stops the loop on client disconnect instead.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

from app.state import AppState
from ingestion.health import get_data_health


def _sse_event(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


async def tick_stream(app_state: AppState, token: str, poll_interval: float = 2.0, max_iterations: Optional[int] = None) -> AsyncIterator[str]:
    last_seen: Optional[str] = None
    i = 0
    while max_iterations is None or i < max_iterations:
        latest = app_state.store.latest_tick_time(token)
        if latest is not None and latest.isoformat() != last_seen:
            last_seen = latest.isoformat()
            yield _sse_event({"instrument_token": token, "latest_tick_time": last_seen})
        else:
            yield _sse_event({"instrument_token": token, "heartbeat": datetime.now(timezone.utc).isoformat()})
        i += 1
        if max_iterations is None or i < max_iterations:
            await asyncio.sleep(poll_interval)


async def health_stream(app_state: AppState, token: str, poll_interval: float = 5.0, max_iterations: Optional[int] = None) -> AsyncIterator[str]:
    i = 0
    while max_iterations is None or i < max_iterations:
        health = get_data_health(app_state.store, app_state.calendar, token)
        yield _sse_event(
            {
                "instrument_token": health.instrument_token,
                "last_tick_age_seconds": health.last_tick_age_seconds,
                "is_stale": health.is_stale,
                "reconnect_count_24h": health.reconnect_count_24h,
                "rejected_bar_count_24h": health.rejected_bar_count_24h,
                "backfill_count_24h": health.backfill_count_24h,
            }
        )
        i += 1
        if max_iterations is None or i < max_iterations:
            await asyncio.sleep(poll_interval)
