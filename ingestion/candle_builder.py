"""Aggregates validated ticks into session-anchored candles for a single
timeframe (base timeframe is 1 minute; higher timeframes are resampled from
this in Phase 3, from closed 1-minute bars only).

In-progress candles are persisted with is_closed=False so the data-health
dashboard can show "last candle in progress," but strategy code (Phase 3+)
must filter to is_closed=True to avoid leaking a partial bar into analysis.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from data.database import TickStore

BASE_TIMEFRAME_LABEL = "1min"
BASE_TIMEFRAME_SECONDS = 60


class CandleBuilder:
    def __init__(self, store: TickStore, timeframe_seconds: int = BASE_TIMEFRAME_SECONDS, timeframe_label: str = BASE_TIMEFRAME_LABEL):
        self._store = store
        self._step = timeframe_seconds
        self._label = timeframe_label
        self._current: dict[str, dict] = {}

    def _bucket_start(self, ts: datetime) -> datetime:
        ts_utc = ts.astimezone(timezone.utc)
        epoch = ts_utc.timestamp()
        bucket_epoch = int(epoch // self._step) * self._step
        return datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)

    def on_tick(self, instrument_token: str, ltp: Decimal, cumulative_volume: Optional[int], exchange_ts: datetime) -> None:
        bucket_start = self._bucket_start(exchange_ts)
        bucket_end = bucket_start + timedelta(seconds=self._step)
        state = self._current.get(instrument_token)

        if state is None or state["open_time"] != bucket_start:
            if state is not None:
                self._persist(instrument_token, state, is_closed=True)
            state = {
                "open_time": bucket_start,
                "close_time": bucket_end,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume_open": cumulative_volume,
                "volume_last": cumulative_volume,
            }
            self._current[instrument_token] = state
        else:
            state["high"] = max(state["high"], ltp)
            state["low"] = min(state["low"], ltp)
            state["close"] = ltp
            state["volume_last"] = cumulative_volume

        self._persist(instrument_token, state, is_closed=False)

    def _persist(self, instrument_token: str, state: dict, is_closed: bool) -> None:
        volume = 0
        if state["volume_open"] is not None and state["volume_last"] is not None:
            volume = max(0, state["volume_last"] - state["volume_open"])
        self._store.upsert_candle(
            instrument_token,
            self._label,
            state["open_time"],
            state["close_time"],
            state["open"],
            state["high"],
            state["low"],
            state["close"],
            volume,
            is_backfilled=False,
            is_closed=is_closed,
        )

    def flush_all(self) -> None:
        """Force-close every in-progress candle. Call on graceful shutdown or
        at session close so the last bar of the day isn't stuck open forever."""
        for token, state in list(self._current.items()):
            self._persist(token, state, is_closed=True)
        self._current.clear()
