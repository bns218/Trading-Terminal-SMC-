"""Long-lived ingestion process entrypoint. Run this as a separate process
from the dashboard — it is the only thing that owns the SmartAPI WebSocket.

Usage (cannot be executed in the development sandbox — no credentials/network
path to Angel One exist there; run this yourself):

    cp .env.example .env   # fill in real credentials
    python -m ingestion.run_ingestion RELIANCE-EQ:NSE NIFTY:NSE

Each argument is SYMBOL:EXCHANGE, resolved to a token via the instrument master.
"""
from __future__ import annotations

import logging
import sys

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from broker.angelone_client import AngelOneClient
from config.logging_config import configure_logging
from config.market_calendar import MarketCalendar
from config.settings import get_settings
from data.database import TickStore
from data.models import Exchange
from ingestion.candle_builder import CandleBuilder
from ingestion.websocket_client import IngestionWebSocketClient, Subscription

logger = logging.getLogger(__name__)

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
