"""Bulk historical backfill sourced from Dhan's /charts/intraday endpoint,
used instead of Angel One's historical endpoint for large multi-year pulls
(Dhan allows 90-day windows per request at 5 req/s vs. Angel's much more
restrictive placeholder limits).

Candles are stored under the *Angel One* instrument token for each symbol,
not a Dhan-specific id, so this data lands in the same TickStore rows that
live Angel One ingestion (ingestion/run_ingestion.py) writes to. That's what
makes historical + live show up as one continuous series in the dashboard.

Auth: DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN from Settings (never logged).
"""
from __future__ import annotations

import csv
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from data.database import TickStore
from ingestion.candle_builder import BASE_TIMEFRAME_LABEL, BASE_TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_BASE_URL = "https://api.dhan.co/v2/charts/intraday"
_MAX_WINDOW_DAYS = 90  # Dhan's documented per-request cap for intraday intervals
_MIN_REQUEST_GAP_SECONDS = 0.25  # ~4 req/s, under Dhan's 5 req/s data-API limit

# The four NSE F&O indices covered by live Angel One ingestion (run_ingestion.py
# subscribes on NSE only). SENSEX/BANKEX are BSE-listed and out of scope here.
NSE_FO_INDICES = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]


@dataclass(frozen=True)
class SymbolMapping:
    symbol: str
    angel_token: str
    dhan_security_id: str
    dhan_exchange_segment: str
    dhan_instrument: str


class DhanHistoricalClient:
    def __init__(self, client_id: str, access_token: str):
        self._headers = {
            "access-token": access_token,
            "client-id": client_id,
            "Content-Type": "application/json",
        }
        self._last_request_at = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        # Shared across worker threads so concurrent symbol downloads still
        # collectively respect Dhan's ~5 req/s data-API limit.
        with self._lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < _MIN_REQUEST_GAP_SECONDS:
                time.sleep(_MIN_REQUEST_GAP_SECONDS - elapsed)
            self._last_request_at = time.monotonic()

    def fetch_intraday(
        self,
        security_id: str,
        exchange_segment: str,
        instrument: str,
        from_dt: datetime,
        to_dt: datetime,
        interval: str = "1",
        max_attempts: int = 4,
    ) -> list[dict]:
        body = {
            "securityId": security_id,
            "exchangeSegment": exchange_segment,
            "instrument": instrument,
            "interval": interval,
            "oi": False,
            "fromDate": from_dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M:%S"),
            "toDate": to_dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for attempt in range(max_attempts):
            self._throttle()
            resp = requests.post(_BASE_URL, json=body, headers=self._headers, timeout=30)
            if resp.status_code == 429:
                delay = 2 ** attempt
                logger.warning("Dhan rate-limited, retrying in %ds", delay)
                time.sleep(delay)
                continue
            if resp.status_code != 200:
                raise RuntimeError(f"Dhan intraday request failed: {resp.status_code} {resp.text[:300]}")
            data = resp.json()
            opens = data.get("open") or []
            highs = data.get("high") or []
            lows = data.get("low") or []
            closes = data.get("close") or []
            volumes = data.get("volume") or []
            timestamps = data.get("timestamp") or []
            candles = []
            for i in range(len(timestamps)):
                candles.append(
                    {
                        "open_time": datetime.fromtimestamp(timestamps[i], tz=timezone.utc),
                        "open": opens[i],
                        "high": highs[i],
                        "low": lows[i],
                        "close": closes[i],
                        "volume": int(volumes[i]) if i < len(volumes) else 0,
                    }
                )
            return candles
        raise RuntimeError("Dhan intraday request exhausted retries")


def build_universe(angel_cache_dir: Path, dhan_csv_path: Path) -> list[SymbolMapping]:
    """Resolve the F&O-stock + NSE-F&O-index universe to (Angel token, Dhan
    security id) pairs, joining on the Angel scrip master's `name` field vs.
    Dhan's FUTSTK `UNDERLYING_SYMBOL` (numeric UNDERLYING_SECURITY_ID is the
    Dhan-side join key back to the EQUITY row, but we only need the id itself
    for historical requests).
    """
    angel_files = sorted(angel_cache_dir.glob("scrip_master_*.json"))
    if not angel_files:
        raise FileNotFoundError(f"No Angel scrip master cached in {angel_cache_dir}")
    angel_records = json.loads(angel_files[-1].read_text(encoding="utf-8"))
    angel_name_to_token: dict[str, str] = {}
    for r in angel_records:
        if r.get("exch_seg") == "NSE" and r.get("instrumenttype", "") == "":
            name = r.get("name")
            if name and name not in angel_name_to_token:
                angel_name_to_token[name] = r["token"]

    dhan_rows = list(csv.DictReader(dhan_csv_path.open(encoding="utf-8")))

    fo_stock_security_id: dict[str, str] = {}
    for r in dhan_rows:
        if r["EXCH_ID"] == "NSE" and r["SEGMENT"] == "D" and r["INSTRUMENT"] == "FUTSTK":
            symbol = r["UNDERLYING_SYMBOL"]
            if "NSETEST" in symbol:
                continue  # exchange dummy/test scrips, not real tradable underlyings
            fo_stock_security_id.setdefault(symbol, r["UNDERLYING_SECURITY_ID"])

    index_security_id: dict[str, str] = {}
    for r in dhan_rows:
        if r["SEGMENT"] == "I" and r["INSTRUMENT"] == "INDEX" and r["EXCH_ID"] == "NSE":
            index_security_id.setdefault(r["SYMBOL_NAME"], r["SECURITY_ID"])

    universe: list[SymbolMapping] = []
    unmatched: list[str] = []

    for symbol, dhan_sid in fo_stock_security_id.items():
        angel_token = angel_name_to_token.get(symbol)
        if angel_token is None:
            unmatched.append(symbol)
            continue
        universe.append(SymbolMapping(symbol, angel_token, dhan_sid, "NSE_EQ", "EQUITY"))

    for symbol in NSE_FO_INDICES:
        angel_token = angel_name_to_token.get(symbol)
        dhan_sid = index_security_id.get(symbol)
        if angel_token is None or dhan_sid is None:
            unmatched.append(symbol)
            continue
        universe.append(SymbolMapping(symbol, angel_token, dhan_sid, "IDX_I", "INDEX"))

    if unmatched:
        logger.warning("Could not resolve %d symbol(s) to both Angel and Dhan ids: %s", len(unmatched), unmatched)
    logger.info("Resolved %d symbols for Dhan historical download.", len(universe))
    return universe


def backfill_symbol(
    client: DhanHistoricalClient,
    store: TickStore,
    mapping: SymbolMapping,
    from_dt: datetime,
    to_dt: datetime,
    timeframe_label: str = BASE_TIMEFRAME_LABEL,
    expected_step_seconds: int = BASE_TIMEFRAME_SECONDS,
) -> int:
    chunk = timedelta(days=_MAX_WINDOW_DAYS)
    cursor = from_dt
    total = 0
    while cursor < to_dt:
        chunk_end = min(cursor + chunk, to_dt)
        try:
            candles = client.fetch_intraday(
                mapping.dhan_security_id, mapping.dhan_exchange_segment, mapping.dhan_instrument, cursor, chunk_end
            )
        except Exception as exc:
            logger.error("Dhan fetch failed for %s window %s..%s: %s", mapping.symbol, cursor.date(), chunk_end.date(), exc)
            cursor = chunk_end
            continue
        rows = [
            {
                "open_time": c["open_time"],
                "close_time": c["open_time"] + timedelta(seconds=expected_step_seconds),
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
                "is_backfilled": True,
                "is_closed": True,
            }
            for c in candles
        ]
        store.bulk_upsert_candles(mapping.angel_token, timeframe_label, rows)
        total += len(candles)
        logger.info(
            "%s: %d bars for window %s..%s (running total %d)",
            mapping.symbol, len(candles), cursor.date(), chunk_end.date(), total,
        )
        cursor = chunk_end
    store.record_health_event("dhan_historical_download", f"symbol={mapping.symbol} token={mapping.angel_token} bars={total}")
    return total
