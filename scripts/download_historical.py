"""One-time historical backfill: pulls 1-minute candles for the given symbols
into the same tick_store the dashboard and live ingestion already read/write,
so downloaded history and live ticks show up in one continuous series.

Usage:

    python -m scripts.download_historical NIFTY:NSE BANKNIFTY:NSE --days 730

Chunks each symbol's range into windows sized per
config.smartapi_limits.HISTORICAL_LOOKBACK_DAYS and honors the shared
rate limiter, so this is safe to run even for long ranges.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone

from broker.angelone_client import AngelOneClient
from config.logging_config import configure_logging
from config.settings import get_settings
from data.database import TickStore
from data.models import Exchange
from ingestion.backfill import backfill_range

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbols", nargs="+", help="SYMBOL:EXCHANGE, e.g. NIFTY:NSE")
    parser.add_argument("--days", type=int, default=730, help="How many days of history to pull (default 730).")
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)
    store = TickStore(settings.tick_store_path)

    client = AngelOneClient(settings)
    client.start()  # login + instrument master

    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=args.days)

    grand_total = 0
    for arg in args.symbols:
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
        instrument = matches[0]
        logger.info("Downloading %d days of 1-min history for %s:%s (token=%s)", args.days, symbol, exchange_name, instrument.token)
        filled = backfill_range(client, store, instrument, from_dt, to_dt)
        logger.info("Done: %d bars stored for %s:%s", filled, symbol, exchange_name)
        grand_total += filled

    logger.info("Historical download complete: %d total bars across %d symbol(s)", grand_total, len(args.symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
