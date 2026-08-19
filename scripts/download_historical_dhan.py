"""Bulk 1-minute historical backfill for the full NSE F&O universe (226
F&O-eligible stocks + NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY), sourced from
Dhan's historical API, stored under Angel One instrument tokens so it lines
up with live Angel One ingestion in the same tick_store.

Usage:

    python -m scripts.download_historical_dhan --years 7
"""
from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.logging_config import configure_logging
from config.settings import get_settings
from data.database import TickStore
from ingestion.dhan_historical import DhanHistoricalClient, backfill_symbol, build_universe

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=float, default=7.0)
    parser.add_argument(
        "--workers", type=int, default=5,
        help="Concurrent symbol downloads. Requests across all workers still share one "
             "rate limiter respecting Dhan's ~5 req/s data-API cap, so this parallelizes "
             "network latency (each request's own round-trip) rather than exceeding the limit.",
    )
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)

    client_id = settings.dhan_client_id.get_secret_value()
    access_token = settings.dhan_access_token.get_secret_value()
    if not client_id or not access_token:
        logger.error("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing from .env")
        return 1

    dhan_csv = Path("cache/dhan_scrip_master.csv")
    if not dhan_csv.exists():
        logger.error("Dhan scrip master not found at %s — download it first.", dhan_csv)
        return 1

    universe = build_universe(settings.instrument_cache_dir, dhan_csv)
    if not universe:
        logger.error("No symbols resolved; aborting.")
        return 1

    store = TickStore(settings.tick_store_path)
    client = DhanHistoricalClient(client_id, access_token)

    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=int(args.years * 365))

    grand_total = 0
    completed = 0

    def _run_one(mapping):
        return mapping, backfill_symbol(client, store, mapping, from_dt, to_dt)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_one, m): m for m in universe}
        for future in as_completed(futures):
            mapping = futures[future]
            try:
                _, filled = future.result()
            except Exception:
                logger.exception("Symbol %s failed", mapping.symbol)
                filled = 0
            completed += 1
            grand_total += filled
            logger.info("[%d/%d done] %s: %d bars (running grand total %d)", completed, len(universe), mapping.symbol, filled, grand_total)

    logger.info("Dhan historical download complete: %d total bars across %d symbols", grand_total, len(universe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
