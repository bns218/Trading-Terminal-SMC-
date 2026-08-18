"""FastAPI dashboard entrypoint.

Run for real (once you have a live ingestion process writing to the same
tick_store_path):

    python -m app.main

Run in demo mode — synthetic, clearly-labelled data, no credentials or
network needed, safe to run in any environment:

    python -m app.main --demo

Then open http://127.0.0.1:8000 in a browser.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.requests import Request

from app.api.routes import router as api_router
from app.demo_data import seed_demo_state
from app.sse.streams import health_stream, tick_stream
from app.state import AppState
from config.logging_config import configure_logging
from config.market_calendar import MarketCalendar
from config.settings import get_settings
from data.database import TickStore

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(demo: bool = False, db_path: Path | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)

    store = TickStore(db_path or settings.tick_store_path)
    calendar = MarketCalendar(settings.holidays_file)
    app_state = AppState(store=store, calendar=calendar, demo_mode=demo)

    if demo:
        seed_demo_state(app_state)

    app = FastAPI(title="Trading Terminal Dashboard (PAPER mode)")
    app.state.app_state = app_state
    app.include_router(api_router)

    @app.get("/api/stream/ticks/{token}")
    async def stream_ticks(token: str, request: Request):
        async def event_source():
            async for event in tick_stream(app_state, token):
                if await request.is_disconnected():
                    break
                yield event
        return StreamingResponse(event_source(), media_type="text/event-stream")

    @app.get("/api/stream/health/{token}")
    async def stream_health(token: str, request: Request):
        async def event_source():
            async for event in health_stream(app_state, token):
                if await request.is_disconnected():
                    break
                yield event
        return StreamingResponse(event_source(), media_type="text/event-stream")

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="Seed and serve synthetic demo data instead of reading live data.")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    settings = get_settings()
    host = args.host or settings.api_host
    port = args.port or settings.api_port

    import uvicorn

    app = create_app(demo=args.demo)
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
