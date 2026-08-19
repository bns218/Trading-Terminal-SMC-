"""Gap detection + historical-endpoint backfill for candles missed during a
WebSocket disconnect. Backfilled bars are marked is_backfilled=True so nothing
downstream mistakes them for live ticks.

Historical response shape (`data: [[iso_timestamp, open, high, low, close, volume], ...]`)
is based on SmartAPI forum examples, not the official docs (unreachable during
development — see docs/phase0_capability_matrix.md). Re-verify before depending
on this for anything beyond gap-filling display data.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from broker.angelone_client import AngelOneClient
from broker.exceptions import RateLimitedError
from broker.ratelimit import call_with_retry
from config.smartapi_limits import HISTORICAL_LOOKBACK_DAYS
from data.database import TickStore
from data.models import Exchange, Instrument
from ingestion.candle_builder import BASE_TIMEFRAME_LABEL, BASE_TIMEFRAME_SECONDS

logger = logging.getLogger(__name__)

_THROTTLE_ERROR_CODES = {"AB1004", "AB2000"}
_IST = ZoneInfo("Asia/Kolkata")


def fetch_historical_candles(
    client: AngelOneClient, instrument: Instrument, interval: str, from_dt: datetime, to_dt: datetime
) -> list[dict]:
    def _call():
        response = client.session.client.getCandleData(
            {
                "exchange": instrument.exchange.value,
                "symboltoken": instrument.token,
                "interval": interval,
                # SmartAPI's historical endpoint expects fromdate/todate as exchange-local
                # (IST) wall-clock strings, not UTC — confirmed against a live response
                # (bars come back with "+05:30" offsets) after UTC-formatted requests were
                # silently returning zero rows despite status=SUCCESS.
                "fromdate": from_dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M"),
                "todate": to_dt.astimezone(_IST).strftime("%Y-%m-%d %H:%M"),
            }
        )
        if not response or not response.get("status"):
            errorcode = (response or {}).get("errorcode", "")
            if errorcode in _THROTTLE_ERROR_CODES:
                raise RateLimitedError(f"Throttled fetching historical candles: {errorcode}")
            message = (response or {}).get("message", "unknown error")
            raise RuntimeError(f"getCandleData failed: {message} (errorcode={errorcode})")
        return response["data"]

    raw_rows = call_with_retry(_call, "historical", client.rate_limiter)
    candles = []
    for row in raw_rows:
        ts, o, h, l, c, v = row
        candles.append(
            {
                "open_time": datetime.fromisoformat(ts).astimezone(timezone.utc),
                "open": Decimal(str(o)),
                "high": Decimal(str(h)),
                "low": Decimal(str(l)),
                "close": Decimal(str(c)),
                "volume": int(v),
            }
        )
    return candles


def backfill_gaps(
    client: AngelOneClient,
    store: TickStore,
    instrument: Instrument,
    interval_api: str = "ONE_MINUTE",
    timeframe_label: str = BASE_TIMEFRAME_LABEL,
    expected_step_seconds: int = BASE_TIMEFRAME_SECONDS,
) -> int:
    """Find gaps in stored candles for this instrument and fill them from the
    historical endpoint. Returns the number of bars backfilled."""
    gaps = store.find_gaps(instrument.token, timeframe_label, expected_step_seconds)
    total_filled = 0
    for gap_start, gap_end in gaps:
        try:
            candles = fetch_historical_candles(client, instrument, interval_api, gap_start, gap_end)
        except Exception as exc:
            logger.error(
                "Backfill failed for %s gap %s..%s: %s", instrument.symbol, gap_start.isoformat(), gap_end.isoformat(), exc
            )
            continue
        for c in candles:
            close_time = c["open_time"] + timedelta(seconds=expected_step_seconds)
            store.upsert_candle(
                instrument.token,
                timeframe_label,
                c["open_time"],
                close_time,
                c["open"],
                c["high"],
                c["low"],
                c["close"],
                c["volume"],
                is_backfilled=True,
                is_closed=True,
            )
        total_filled += len(candles)
        store.record_health_event(
            "backfill",
            f"token={instrument.token} gap={gap_start.isoformat()}..{gap_end.isoformat()} bars={len(candles)}",
        )
        logger.info("Backfilled %d bars for %s in gap %s..%s", len(candles), instrument.symbol, gap_start.isoformat(), gap_end.isoformat())
    return total_filled


def backfill_range(
    client: AngelOneClient,
    store: TickStore,
    instrument: Instrument,
    from_dt: datetime,
    to_dt: datetime,
    interval_api: str = "ONE_MINUTE",
    timeframe_label: str = BASE_TIMEFRAME_LABEL,
    expected_step_seconds: int = BASE_TIMEFRAME_SECONDS,
) -> int:
    """Download an explicit historical window (not gap-only) in
    per-endpoint-limit-sized chunks, oldest chunk first. Used for the initial
    load of history rather than filling a disconnect gap.
    """
    chunk_days = HISTORICAL_LOOKBACK_DAYS.get(interval_api, 30)
    chunk = timedelta(days=chunk_days)
    total_filled = 0
    cursor = from_dt
    while cursor < to_dt:
        chunk_end = min(cursor + chunk, to_dt)
        # SmartAPI's historical endpoint returns zero rows (despite status=SUCCESS)
        # for windows whose from/to clock time falls outside the NSE session, even
        # though the window otherwise spans valid trading days. Snap each chunk's
        # boundary times to the session open/close so every request looks like the
        # known-good shape (confirmed against a live call).
        request_from = datetime.combine(cursor.astimezone(_IST).date(), datetime.min.time(), tzinfo=_IST).replace(hour=9, minute=15)
        request_to = datetime.combine(chunk_end.astimezone(_IST).date(), datetime.min.time(), tzinfo=_IST).replace(hour=15, minute=30)
        try:
            candles = fetch_historical_candles(client, instrument, interval_api, request_from, request_to)
        except Exception as exc:
            logger.error(
                "Historical fetch failed for %s window %s..%s: %s",
                instrument.symbol, cursor.isoformat(), chunk_end.isoformat(), exc,
            )
            cursor = chunk_end
            continue
        for c in candles:
            close_time = c["open_time"] + timedelta(seconds=expected_step_seconds)
            store.upsert_candle(
                instrument.token,
                timeframe_label,
                c["open_time"],
                close_time,
                c["open"],
                c["high"],
                c["low"],
                c["close"],
                c["volume"],
                is_backfilled=True,
                is_closed=True,
            )
        total_filled += len(candles)
        logger.info(
            "Downloaded %d bars for %s window %s..%s (running total %d)",
            len(candles), instrument.symbol, cursor.isoformat(), chunk_end.isoformat(), total_filled,
        )
        cursor = chunk_end
    store.record_health_event(
        "historical_download",
        f"token={instrument.token} range={from_dt.isoformat()}..{to_dt.isoformat()} bars={total_filled}",
    )
    return total_filled
