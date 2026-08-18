"""Owns the single SmartAPI WebSocket connection for the ingestion process.

Per the ground rules: this is the ONLY thing in the whole system that opens
the broker WebSocket. The dashboard (FastAPI/SSE) never touches it — it only
reads what this process writes to the SQLite store.

Reconnect and resubscribe-on-reconnect are largely handled by SmartWebSocketV2
itself (RESUBSCRIBE_FLAG + its own retry_strategy/retry_delay backoff — see
docs/phase0_capability_matrix.md and the Phase 2 tested/untested table for how
this was verified against the SDK source). This wrapper adds what the SDK
does not: disconnect-duration logging, subscription-cap-aware chunking, tick
validation before persistence, and data-health event recording.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from broker.angelone_client import AngelOneClient
from broker.exceptions import SessionExpiredError
from config.market_calendar import MarketCalendar
from config.smartapi_limits import WEBSOCKET_MAX_SUBSCRIPTIONS_PER_CONNECTION
from data.database import TickStore
from data.validation import validate_tick
from ingestion.candle_builder import CandleBuilder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Subscription:
    exchange_type: int  # SmartWebSocketV2.NSE_CM / NSE_FO / etc.
    token: str


def chunk_subscriptions(subscriptions: list[Subscription], cap: int = WEBSOCKET_MAX_SUBSCRIPTIONS_PER_CONNECTION) -> list[list[Subscription]]:
    """Split subscriptions into batches respecting the per-request subscription cap.
    Pure function, independently testable without a live connection."""
    if cap <= 0:
        raise ValueError("cap must be positive")
    return [subscriptions[i : i + cap] for i in range(0, len(subscriptions), cap)]


def to_token_list(subscriptions: list[Subscription]) -> list[dict]:
    """Group a batch of Subscriptions into SmartWebSocketV2's token_list shape:
    [{"exchangeType": int, "tokens": [str, ...]}, ...]"""
    by_exchange: dict[int, list[str]] = {}
    for sub in subscriptions:
        by_exchange.setdefault(sub.exchange_type, []).append(sub.token)
    return [{"exchangeType": ex, "tokens": tokens} for ex, tokens in by_exchange.items()]


class IngestionWebSocketClient:
    def __init__(
        self,
        client: AngelOneClient,
        store: TickStore,
        calendar: MarketCalendar,
        candle_builder: CandleBuilder,
        subscriptions: list[Subscription],
        mode: int = SmartWebSocketV2.QUOTE,
        on_tick_hook: Optional[Callable[[str, Decimal, datetime], None]] = None,
    ):
        state = client.session.state
        if state is None:
            raise SessionExpiredError("Cannot start WebSocket ingestion without an active session; call client.start() first.")

        self._store = store
        self._calendar = calendar
        self._candle_builder = candle_builder
        self._subscriptions = subscriptions
        self._mode = mode
        self._on_tick_hook = on_tick_hook
        self._last_tick_ts: dict[str, datetime] = {}
        self._disconnected_at: Optional[datetime] = None
        self._connected_since: Optional[datetime] = None

        self._ws = SmartWebSocketV2(
            auth_token=state.jwt_token,
            api_key=client.settings.angel_api_key.get_secret_value(),
            client_code=state.client_code,
            feed_token=state.feed_token,
            max_retry_attempt=5,
            retry_strategy=1,  # exponential
            retry_delay=2,
            retry_multiplier=2,
            retry_duration=60,
        )
        self._ws.on_open = self._on_open
        self._ws.on_data = self._on_data
        self._ws.on_close = self._on_close
        self._ws.on_error = self._on_error

    def _subscribe_all(self) -> None:
        batches = chunk_subscriptions(self._subscriptions)
        for i, batch in enumerate(batches):
            token_list = to_token_list(batch)
            correlation_id = f"ingest{i:02d}"
            self._ws.subscribe(correlation_id, self._mode, token_list)
        logger.info("Subscribed to %d instruments in %d batch(es)", len(self._subscriptions), len(batches))

    def _on_open(self, wsapp) -> None:
        now = datetime.now(timezone.utc)
        if self._disconnected_at is not None:
            duration = (now - self._disconnected_at).total_seconds()
            logger.warning("WebSocket reconnected after %.1fs disconnect", duration)
            self._store.record_health_event("reconnect", f"duration_seconds={duration:.1f}")
            self._disconnected_at = None
        self._connected_since = now
        self._subscribe_all()

    def _on_close(self, wsapp) -> None:
        self._disconnected_at = datetime.now(timezone.utc)
        logger.error("WebSocket disconnected at %s", self._disconnected_at.isoformat())
        self._store.record_health_event("disconnect", f"at={self._disconnected_at.isoformat()}")

    def _on_error(self, wsapp, error_type=None, error_msg=None) -> None:
        logger.error("WebSocket error: type=%s msg=%s", error_type, error_msg)
        self._store.record_health_event("error", f"type={error_type} msg={error_msg}")

    def _on_data(self, wsapp, parsed_message: dict) -> None:
        try:
            token = str(parsed_message["token"])
            raw_price = parsed_message["last_traded_price"]
            ltp = Decimal(raw_price) / Decimal(100)  # SDK reports price scaled by 100
            exchange_ts = datetime.fromtimestamp(parsed_message["exchange_timestamp"] / 1000, tz=timezone.utc)
            volume = parsed_message.get("volume_trade_for_the_day")
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Malformed tick payload, rejecting: %s (%s)", parsed_message, exc)
            self._store.record_health_event("rejected_bar", f"malformed_payload: {exc}")
            return

        last_ts = self._last_tick_ts.get(token)
        result = validate_tick(ltp=ltp, volume=volume, exchange_ts=exchange_ts, calendar=self._calendar, last_tick_ts=last_ts)
        if not result.ok:
            logger.info("Rejected tick token=%s reason=%s", token, result.reason)
            self._store.record_health_event("rejected_bar", f"token={token} reason={result.reason}")
            return

        self._last_tick_ts[token] = exchange_ts
        self._store.insert_tick(token, ltp, volume, exchange_ts)
        self._candle_builder.on_tick(token, ltp, volume, exchange_ts)
        if self._on_tick_hook is not None:
            self._on_tick_hook(token, ltp, exchange_ts)

    def connect(self) -> None:
        """Blocking call — runs the WebSocket event loop forever (SDK's run_forever)."""
        self._ws.connect()

    def close(self) -> None:
        self._candle_builder.flush_all()
        self._ws.close_connection()
