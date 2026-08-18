"""Phase 1 acceptance script: log in, load the instrument master, fetch one LTP.

Run yourself (this cannot be executed in the development sandbox — no real
Angel One credentials or network path to the broker exist there):

    cp .env.example .env   # then fill in real credentials
    python -m scripts.fetch_ltp RELIANCE-EQ NSE 2885

The third argument is the symbol token. If you don't know it, omit it and the
script will look it up via the instrument master by trading symbol instead:

    python -m scripts.fetch_ltp RELIANCE-EQ NSE
"""
from __future__ import annotations

import sys

from broker.angelone_client import AngelOneClient
from broker.market_data import get_ltp
from config.logging_config import configure_logging
from config.settings import Settings, get_settings
from data.models import Exchange


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    tradingsymbol = sys.argv[1]
    exchange = Exchange(sys.argv[2].upper())
    symboltoken = sys.argv[3] if len(sys.argv) > 3 else None

    settings: Settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)

    client = AngelOneClient(settings)
    client.start()  # login + instrument master refresh

    if symboltoken is None:
        matches = [
            inst
            for inst in client.instrument_master._instruments  # noqa: SLF001 (script-local, read-only)
            if inst.symbol == tradingsymbol and inst.exchange == exchange
        ]
        if not matches:
            print(f"No instrument found for {tradingsymbol} on {exchange.value}")
            return 1
        symboltoken = matches[0].token

    quote = get_ltp(client, exchange, tradingsymbol, symboltoken)
    print(f"{tradingsymbol} ({exchange.value}, token={symboltoken}): LTP={quote.ltp} at {quote.timestamp.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
