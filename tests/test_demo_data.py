from pathlib import Path

from config.market_calendar import MarketCalendar
from config.settings import get_settings
from data.database import TickStore
from app.demo_data import DEMO_SYMBOL, DEMO_TOKEN, seed_demo_state
from app.state import AppState


def make_app_state(tmp_path: Path) -> AppState:
    store = TickStore(tmp_path / "demo.db")
    calendar = MarketCalendar(get_settings().holidays_file)
    return AppState(store=store, calendar=calendar)


def test_seed_demo_state_populates_candles(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=1)
    candles = app_state.store.get_candles(DEMO_TOKEN, "1min", limit=200)
    assert len(candles) == 90
    assert all(c["is_closed"] == 1 for c in candles)


def test_seed_demo_state_populates_journal(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=1)
    entries = app_state.store.get_journal_entries()
    assert len(entries) == 4
    assert all(e.instrument_symbol == DEMO_SYMBOL for e in entries)
    assert all("[DEMO DATA]" in e.entry_reason for e in entries)


def test_seed_demo_state_populates_health_events(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=1)
    assert app_state.store.count_health_events("reconnect") == 1
    assert app_state.store.count_health_events("rejected_bar") == 1
    assert app_state.store.count_health_events("backfill") == 1


def test_seed_demo_state_sets_demo_mode_and_positions(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=1)
    assert app_state.demo_mode is True
    assert len(app_state.demo_positions) == 1
    assert app_state.demo_positions[0]["is_demo"] is True
    assert app_state.demo_positions[0]["instrument_symbol"] == DEMO_SYMBOL


def test_demo_candles_are_reproducible_with_same_seed(tmp_path: Path):
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=7)
    first_run = app_state.store.get_candles(DEMO_TOKEN, "1min", limit=200)

    app_state2 = make_app_state(tmp_path.parent / (tmp_path.name + "_2"))
    seed_demo_state(app_state2, seed=7)
    second_run = app_state2.store.get_candles(DEMO_TOKEN, "1min", limit=200)

    assert [c["close"] for c in first_run] == [c["close"] for c in second_run]


def test_demo_candles_form_a_valid_ohlc_series(tmp_path: Path):
    from decimal import Decimal
    app_state = make_app_state(tmp_path)
    seed_demo_state(app_state, seed=3)
    candles = app_state.store.get_candles(DEMO_TOKEN, "1min", limit=200)
    for c in candles:
        o, h, l, close = Decimal(c["open"]), Decimal(c["high"]), Decimal(c["low"]), Decimal(c["close"])
        assert l <= o <= h
        assert l <= close <= h
