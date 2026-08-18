"""LTP/OHLC/FULL quote fetches, rate-limited and retried through the shared client."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal

from broker.angelone_client import AngelOneClient
from broker.exceptions import RateLimitedError
from broker.ratelimit import call_with_retry
from data.models import Exchange, Quote

logger = logging.getLogger(__name__)

# SmartAPI error codes observed in forum reports to indicate throttling.
# UNVERIFIED against official docs — treat conservatively (see config/smartapi_limits.py).
_THROTTLE_ERROR_CODES = {"AB1004", "AB2000"}


def get_ltp(client: AngelOneClient, exchange: Exchange, tradingsymbol: str, symboltoken: str) -> Quote:
    def _call():
        response = client.session.client.ltpData(exchange.value, tradingsymbol, symboltoken)
        if not response or not response.get("status"):
            errorcode = (response or {}).get("errorcode", "")
            if errorcode in _THROTTLE_ERROR_CODES:
                raise RateLimitedError(f"Throttled fetching LTP for {tradingsymbol}: {errorcode}")
            message = (response or {}).get("message", "unknown error")
            raise RuntimeError(f"ltpData failed for {tradingsymbol}: {message} (errorcode={errorcode})")
        return response["data"]

    data = call_with_retry(_call, "quote", client.rate_limiter)
    return Quote(
        instrument_token=symboltoken,
        exchange=exchange,
        ltp=Decimal(str(data["ltp"])),
        timestamp=datetime.now(timezone.utc),
    )
