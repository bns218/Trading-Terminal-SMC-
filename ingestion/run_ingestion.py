"""Long-lived ingestion process entrypoint. Run this as a separate process
from the dashboard — it is the only thing that owns the SmartAPI WebSocket.

Usage (cannot be executed in the development sandbox — no credentials/network
path to Angel One exist there; run this yourself):

    cp .env.example .env   # fill in real credentials

    # explicit symbols — each argument is SYMBOL:EXCHANGE, resolved to a token
    # via the instrument master. NOTE: SYMBOL must be the instrument master's
    # `symbol` field, which for NSE equities carries a series suffix
    # (RELIANCE-EQ, not RELIANCE); indices have no suffix (NIFTY).
    python -m ingestion.run_ingestion RELIANCE-EQ:NSE NIFTY:NSE

    # or subscribe to the whole cached F&O universe by token (no symbol
    # spelling to get wrong — config/fo_universe.json already stores the
    # resolved instrument-master tokens):
    python -m ingestion.run_ingestion --universe
"""
from __future__ import annotations

import json
import logging
import sys

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from broker.angelone_client import AngelOneClient
from config.logging_config import configure_logging
from config.market_calendar import MarketCalendar
from config.settings import REPO_ROOT, get_settings
from data.database import TickStore
from data.models import Exchange
from ingestion.candle_builder import CandleBuilder
from ingestion.websocket_client import IngestionWebSocketClient, Subscription

logger = logging.getLogger(__name__)

_FO_UNIVERSE_PATH = REPO_ROOT / "config" / "fo_universe.json"

_EXCHANGE_TYPE_MAP = {
    Exchange.NSE: SmartWebSocketV2.NSE_CM,
    Exchange.NFO: SmartWebSocketV2.NSE_FO,
    Exchange.BSE: SmartWebSocketV2.BSE_CM,
    Exchange.BFO: SmartWebSocketV2.BSE_FO,
    Exchange.MCX: SmartWebSocketV2.MCX_FO,
    Exchange.CDS: SmartWebSocketV2.CDE_FO,
}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)
    calendar = MarketCalendar(settings.holidays_file)
    store = TickStore(settings.tick_store_path)

    client = AngelOneClient(settings)
    client.start()  # login + instrument master

    subscriptions: list[Subscription] = []
    if sys.argv[1] == "--universe":
        if not _FO_UNIVERSE_PATH.exists():
            logger.error("Universe file not found at %s", _FO_UNIVERSE_PATH)
            return 1
        universe = json.loads(_FO_UNIVERSE_PATH.read_text(encoding="utf-8"))
        for entry in universe:
            exchange = Exchange(entry["exchange"].upper())
            subscriptions.append(Subscription(exchange_type=_EXCHANGE_TYPE_MAP[exchange], token=entry["token"]))
        logger.info("Loaded %d instruments from the cached F&O universe.", len(subscriptions))
    else:
        for arg in sys.argv[1:]:
            symbol, exchange_name = arg.split(":")
            exchange = Exchange(exchange_name.upper())
            matches = [
                inst
                for inst in client.instrument_master._instruments  # noqa: SLF001
                if inst.symbol == symbol and inst.exchange == exchange
            ]
            if not matches:
                logger.error("No instrument found for %s:%s, skipping", symbol, exchange_name)
                continue
            subscriptions.append(Subscription(exchange_type=_EXCHANGE_TYPE_MAP[exchange], token=matches[0].token))

    if not subscriptions:
        logger.error("No valid subscriptions resolved; exiting.")
        return 1

    candle_builder = CandleBuilder(store)
    ws_client = IngestionWebSocketClient(client, store, calendar, candle_builder, subscriptions)

    try:
        ws_client.connect()  # blocks forever
    except KeyboardInterrupt:
        logger.info("Shutdown requested, flushing in-progress candles.")
        ws_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
