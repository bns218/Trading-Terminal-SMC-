from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from broker.auth import SessionState
from config.market_calendar import MarketCalendar
from config.settings import Settings, get_settings
from data.database import TickStore
from ingestion.candle_builder import CandleBuilder
from ingestion.websocket_client import (
    IngestionWebSocketClient,
    Subscription,
    chunk_subscriptions,
    to_token_list,
)


def test_chunk_subscriptions_respects_cap():
    subs = [Subscription(exchange_type=1, token=str(i)) for i in range(125)]
    batches = chunk_subscriptions(subs, cap=50)
    assert len(batches) == 3
    assert [len(b) for b in batches] == [50, 50, 25]


def test_chunk_subscriptions_empty_list():
    assert chunk_subscriptions([], cap=50) == []


def test_chunk_subscriptions_rejects_nonpositive_cap():
    with pytest.raises(ValueError):
        chunk_subscriptions([Subscription(1, "x")], cap=0)


def test_to_token_list_groups_by_exchange():
    subs = [
        Subscription(exchange_type=1, token="111"),
        Subscription(exchange_type=1, token="222"),
        Subscription(exchange_type=2, token="333"),
    ]
    token_list = to_token_list(subs)
    by_exchange = {entry["exchangeType"]: entry["tokens"] for entry in token_list}
    assert by_exchange[1] == ["111", "222"]
    assert by_exchange[2] == ["333"]


@pytest.fixture
def fake_client(tmp_path):
    """A minimal fake AngelOneClient carrying just enough for
    IngestionWebSocketClient.__init__ to construct a real SmartWebSocketV2
    (no live connection is ever made in these tests)."""
    base_settings = get_settings()
    settings = Settings(angel_api_key="fake-api-key", holidays_file=base_settings.holidays_file)
    session_state = SessionState(
        jwt_token="fake-jwt",
        refresh_token="fake-refresh",
        feed_token="fake-feed",
        client_code="A1234",
        logged_in_at=datetime.now(timezone.utc),
    )
    session = SimpleNamespace(state=session_state)
    return SimpleNamespace(session=session, settings=settings)


@pytest.fixture
def ws_harness(tmp_path, fake_client):
    store = TickStore(tmp_path / "t.db")
    calendar = MarketCalendar(get_settings().holidays_file)
    builder = CandleBuilder(store)
    client_wrapper = IngestionWebSocketClient(
        fake_client, store, calendar, builder, subscriptions=[Subscription(exchange_type=1, token="2885")]
    )
    return client_wrapper, store


def test_on_data_persists_valid_tick(ws_harness):
    client_wrapper, store = ws_harness
    mid_session_utc = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)  # 11:30 IST
    message = {
        "token": "2885",
        "last_traded_price": 250050,  # scaled by 100 -> 2500.50
        "exchange_timestamp": int(mid_session_utc.timestamp() * 1000),
        "volume_trade_for_the_day": 1000,
    }
    client_wrapper._on_data(None, message)  # noqa: SLF001
    latest = store.latest_tick_time("2885")
    assert latest == mid_session_utc


def test_on_data_rejects_and_logs_invalid_tick(ws_harness):
    client_wrapper, store = ws_harness
    outside_session_utc = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
    message = {
        "token": "2885",
        "last_traded_price": 250050,
        "exchange_timestamp": int(outside_session_utc.timestamp() * 1000),
        "volume_trade_for_the_day": 1000,
    }
    client_wrapper._on_data(None, message)  # noqa: SLF001
    assert store.latest_tick_time("2885") is None
    assert store.count_health_events("rejected_bar") == 1


def test_on_data_rejects_malformed_payload(ws_harness):
    client_wrapper, store = ws_harness
    client_wrapper._on_data(None, {"token": "2885"})  # missing required fields
    assert store.count_health_events("rejected_bar") == 1


def test_on_close_then_on_open_logs_disconnect_duration(ws_harness, monkeypatch):
    client_wrapper, store = ws_harness

    fixed_close_time = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)
    fixed_open_time = fixed_close_time + timedelta(seconds=7)

    times = iter([fixed_close_time, fixed_open_time])
    monkeypatch.setattr(
        "ingestion.websocket_client.datetime",
        SimpleNamespace(now=lambda tz=None: next(times)),
    )

    # Prevent _subscribe_all from touching the real (unconnected) wsapp.
    client_wrapper._subscribe_all = lambda: None  # noqa: SLF001

    client_wrapper._on_close(None)  # noqa: SLF001
    assert store.count_health_events("disconnect") == 1

    client_wrapper._on_open(None)  # noqa: SLF001
    assert store.count_health_events("reconnect") == 1
