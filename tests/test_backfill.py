from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from broker.ratelimit import RateLimiter
from data.database import TickStore
from data.models import Exchange, Instrument, InstrumentType
from ingestion.backfill import backfill_gaps, fetch_historical_candles

T0 = datetime(2026, 8, 18, 6, 0, tzinfo=timezone.utc)

INSTRUMENT = Instrument(
    token="2885",
    symbol="RELIANCE-EQ",
    name="RELIANCE",
    exchange=Exchange.NSE,
    instrument_type=InstrumentType.EQ,
    lot_size=1,
    tick_size=Decimal("0.05"),
)


def make_fake_client(candle_rows):
    fake_smartconnect = SimpleNamespace(
        getCandleData=lambda params: {"status": True, "data": candle_rows}
    )
    session = SimpleNamespace(client=fake_smartconnect)
    return SimpleNamespace(session=session, rate_limiter=RateLimiter())


def test_fetch_historical_candles_parses_response():
    rows = [
        ["2026-08-18T11:30:00+05:30", "100", "105", "99", "103", "500"],
        ["2026-08-18T11:31:00+05:30", "103", "106", "102", "104", "300"],
    ]
    client = make_fake_client(rows)
    candles = fetch_historical_candles(client, INSTRUMENT, "ONE_MINUTE", T0, T0 + timedelta(minutes=2))
    assert len(candles) == 2
    assert candles[0]["close"] == Decimal("103")
    assert candles[0]["volume"] == 500


def test_backfill_gaps_fills_missing_bars_and_marks_backfilled(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    t0 = T0
    t1 = t0 + timedelta(minutes=1)
    t3 = t0 + timedelta(minutes=3)
    store.upsert_candle(INSTRUMENT.token, "1min", t0, t1, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1, is_closed=True)
    store.upsert_candle(INSTRUMENT.token, "1min", t3, t3 + timedelta(minutes=1), Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1, is_closed=True)

    rows = [
        [t1.isoformat(), "1", "1", "1", "1", "1"],
        [(t1 + timedelta(minutes=1)).isoformat(), "1", "1", "1", "1", "1"],
    ]
    client = make_fake_client(rows)

    filled = backfill_gaps(client, store, INSTRUMENT)
    assert filled == 2

    candles = store.get_candles(INSTRUMENT.token, "1min")
    backfilled = [c for c in candles if c["is_backfilled"] == 1]
    assert len(backfilled) == 2
    assert store.count_health_events("backfill") == 1


def test_backfill_gaps_no_gaps_does_nothing(tmp_path: Path):
    store = TickStore(tmp_path / "t.db")
    t0 = T0
    t1 = t0 + timedelta(minutes=1)
    store.upsert_candle(INSTRUMENT.token, "1min", t0, t1, Decimal("1"), Decimal("1"), Decimal("1"), Decimal("1"), 1, is_closed=True)
    client = make_fake_client([])
    filled = backfill_gaps(client, store, INSTRUMENT)
    assert filled == 0
