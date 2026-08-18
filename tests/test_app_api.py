from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.demo_data import DEMO_TOKEN
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(demo=True, db_path=tmp_path / "api_test.db")
    return TestClient(app)


def test_meta_reports_demo_mode(client):
    resp = client.get("/api/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert body["demo_mode"] is True
    assert body["trading_mode"] == "PAPER"


def test_candles_1min_returns_all_demo_bars(client):
    resp = client.get(f"/api/candles/{DEMO_TOKEN}?timeframe=1min&limit=200")
    assert resp.status_code == 200
    candles = resp.json()
    assert len(candles) == 90
    for c in candles:
        assert c["low"] <= c["open"] <= c["high"]
        assert c["low"] <= c["close"] <= c["high"]


def test_candles_resampled_5min_fewer_bars_than_1min(client):
    resp_1m = client.get(f"/api/candles/{DEMO_TOKEN}?timeframe=1min")
    resp_5m = client.get(f"/api/candles/{DEMO_TOKEN}?timeframe=5min")
    assert len(resp_5m.json()) < len(resp_1m.json())


def test_candles_unknown_timeframe_rejected(client):
    resp = client.get(f"/api/candles/{DEMO_TOKEN}?timeframe=7min")
    assert resp.status_code == 400


def test_candles_unknown_token_returns_empty_list(client):
    resp = client.get("/api/candles/NOPE")
    assert resp.status_code == 200
    assert resp.json() == []


def test_indicators_return_expected_keys_and_aligned_length(client):
    candles = client.get(f"/api/candles/{DEMO_TOKEN}").json()
    resp = client.get(f"/api/indicators/{DEMO_TOKEN}")
    assert resp.status_code == 200
    data = resp.json()
    for key in ["ema_9", "ema_20", "ema_50", "vwap", "rsi_14", "atr_14", "bb_upper", "bb_middle", "bb_lower", "macd_line", "macd_signal"]:
        assert key in data
        assert len(data[key]) == len(candles)


def test_indicators_json_serializable_no_nan_leaks_as_string(client):
    resp = client.get(f"/api/indicators/{DEMO_TOKEN}")
    data = resp.json()
    # Early bars are warm-up NaN — must serialize as JSON null, not the string "NaN".
    assert data["ema_50"][0][1] is None


def test_overlays_returns_all_expected_keys(client):
    resp = client.get(f"/api/candles/{DEMO_TOKEN}/overlays")
    assert resp.status_code == 200
    data = resp.json()
    for key in ["candlestick", "structure", "fair_value_gaps", "chart_patterns"]:
        assert key in data
        assert isinstance(data[key], list)


def test_overlay_events_have_expected_shape(client):
    resp = client.get(f"/api/candles/{DEMO_TOKEN}/overlays")
    data = resp.json()
    all_events = data["candlestick"] + data["structure"] + data["fair_value_gaps"] + data["chart_patterns"]
    for e in all_events:
        assert set(e.keys()) == {"pattern", "bar_index", "timestamp", "direction", "details"}
        assert e["direction"] in ("bullish", "bearish", "neutral", "BOS", "CHOCH")


def test_positions_returns_demo_position(client):
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    positions = resp.json()
    assert len(positions) == 1
    assert positions[0]["is_demo"] is True


def test_journal_returns_entries_json_serializable(client):
    resp = client.get("/api/journal")
    assert resp.status_code == 200
    entries = resp.json()
    assert len(entries) == 4
    assert all("[DEMO DATA]" in e["entry_reason"] for e in entries)
    assert isinstance(entries[0]["pnl_net"], str)  # Decimal serialized as exact string, not lossy float


def test_data_health_returns_valid_structure(client):
    resp = client.get(f"/api/data-health/{DEMO_TOKEN}")
    assert resp.status_code == 200
    data = resp.json()
    assert "last_tick_age_seconds" in data
    assert "reconnect_count_24h" in data
    assert data["reconnect_count_24h"] == 1


def test_kill_switch_toggle_round_trip(client):
    resp = client.post("/api/risk/kill-switch", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["kill_switch_enabled"] is True

    meta = client.get("/api/meta").json()
    assert meta["kill_switch_enabled"] is True

    resp2 = client.post("/api/risk/kill-switch", json={"enabled": False})
    assert resp2.json()["kill_switch_enabled"] is False


def test_instrument_search_returns_demo_instrument(client):
    resp = client.get("/api/instruments/search")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["token"] == DEMO_TOKEN


def test_static_index_served_at_root(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Trading Terminal" in resp.text


def test_live_mode_positions_empty(tmp_path: Path):
    app = create_app(demo=False, db_path=tmp_path / "live.db")
    client = TestClient(app)
    resp = client.get("/api/positions")
    assert resp.json() == []


def test_live_mode_no_demo_banner_flag(tmp_path: Path):
    app = create_app(demo=False, db_path=tmp_path / "live2.db")
    client = TestClient(app)
    meta = client.get("/api/meta").json()
    assert meta["demo_mode"] is False
