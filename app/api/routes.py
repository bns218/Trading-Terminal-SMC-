"""REST endpoints. The dashboard is a pure reader — every route here reads
from the SQLite store (written by ingestion/execution processes) or runs
pure strategies/ computation on data already in the store. Nothing here
opens a broker connection.
"""
from __future__ import annotations

import json
import math
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Request

from app.api.serializers import candle_row_to_dict, pattern_event_to_dict
from app.state import AppState
from data.resample import candles_to_dataframe, resample_candles
from ingestion.health import get_data_health
from strategies import candlestick, indicators as ind, smc
from strategies.chart_patterns import detect_double_bottom, detect_double_top, detect_head_and_shoulders, detect_inverse_head_and_shoulders

router = APIRouter(prefix="/api")


def _get_state(request: Request) -> AppState:
    return request.app.state.app_state


def _load_dataframe(app_state: AppState, token: str, timeframe: str, limit: int):
    raw = app_state.store.get_candles(token, "1min", limit=max(limit, 500))
    df = candles_to_dataframe(raw)
    if timeframe != "1min":
        minutes = {"3min": 3, "5min": 5, "15min": 15, "30min": 30, "60min": 60}.get(timeframe)
        if minutes is None:
            raise HTTPException(400, f"Unsupported timeframe: {timeframe}")
        resampled = resample_candles(df, minutes, app_state.calendar)
        df = resampled[resampled["is_closed"] == True].reset_index(drop=True)  # noqa: E712
    return df.tail(limit).reset_index(drop=True)


@router.get("/meta")
def get_meta(request: Request):
    app_state = _get_state(request)
    return {
        "demo_mode": app_state.demo_mode,
        "trading_mode": "PAPER",
        "kill_switch_enabled": app_state.kill_switch_enabled,
    }


@router.get("/candles/{token}")
def get_candles(token: str, request: Request, timeframe: str = "1min", limit: int = 200):
    app_state = _get_state(request)
    df = _load_dataframe(app_state, token, timeframe, limit)
    if df.empty:
        return []
    return [
        {
            "open_time": row["open_time"].isoformat(),
            "close_time": row["close_time"].isoformat(),
            "open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"],
            "volume": int(row["volume"]),
        }
        for _, row in df.iterrows()
    ]


def _series_to_points(series, close_times) -> list[list]:
    points = []
    for ts, value in zip(close_times, series):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            points.append([ts.isoformat(), None])
        else:
            points.append([ts.isoformat(), round(float(value), 4)])
    return points


@router.get("/indicators/{token}")
def get_indicators(token: str, request: Request, timeframe: str = "1min", limit: int = 200):
    app_state = _get_state(request)
    df = _load_dataframe(app_state, token, timeframe, limit)
    if df.empty:
        return {}

    close_times = df["close_time"].tolist()
    result = {
        "ema_9": _series_to_points(ind.ema(df, 9).values, close_times),
        "ema_20": _series_to_points(ind.ema(df, 20).values, close_times),
        "ema_50": _series_to_points(ind.ema(df, 50).values, close_times),
        "vwap": _series_to_points(ind.vwap_session(df).values, close_times),
        "rsi_14": _series_to_points(ind.rsi(df, 14).values, close_times),
        "atr_14": _series_to_points(ind.atr(df, 14).values, close_times),
    }
    bb = ind.bollinger_bands(df, 20, 2.0)
    result["bb_upper"] = _series_to_points(bb.upper.values, close_times)
    result["bb_middle"] = _series_to_points(bb.middle.values, close_times)
    result["bb_lower"] = _series_to_points(bb.lower.values, close_times)
    macd = ind.macd(df)
    result["macd_line"] = _series_to_points(macd.macd_line.values, close_times)
    result["macd_signal"] = _series_to_points(macd.signal_line.values, close_times)
    return result


@router.get("/candles/{token}/overlays")
def get_overlays(token: str, request: Request, timeframe: str = "1min", limit: int = 200):
    app_state = _get_state(request)
    df = _load_dataframe(app_state, token, timeframe, limit)
    if df.empty or len(df) < 5:
        return {"candlestick": [], "structure": [], "fair_value_gaps": [], "chart_patterns": []}

    candlestick_events = candlestick.detect_all_candlestick_patterns(df)
    structure_events = smc.detect_bos_choch(df, lookback=2)
    fvg_events = smc.detect_fair_value_gaps(df)
    chart_events = (
        detect_double_top(df) + detect_double_bottom(df)
        + detect_head_and_shoulders(df) + detect_inverse_head_and_shoulders(df)
    )

    return {
        "candlestick": [pattern_event_to_dict(e) for e in candlestick_events],
        "structure": [pattern_event_to_dict(e) for e in structure_events],
        "fair_value_gaps": [pattern_event_to_dict(e) for e in fvg_events],
        "chart_patterns": [pattern_event_to_dict(e) for e in chart_events],
    }


@router.get("/positions")
def get_positions(request: Request):
    app_state = _get_state(request)
    if app_state.demo_mode:
        return app_state.demo_positions
    return []  # no live executor process is wired into this dashboard in this phase — see README


@router.get("/journal")
def get_journal(request: Request, limit: int = 100):
    app_state = _get_state(request)
    entries = app_state.store.get_journal_entries(limit=limit)
    return [json.loads(e.model_dump_json()) for e in entries]


@router.get("/data-health/{token}")
def get_health(token: str, request: Request, max_staleness_seconds: int = 30):
    app_state = _get_state(request)
    health = get_data_health(app_state.store, app_state.calendar, token, max_staleness_seconds=max_staleness_seconds)
    return {
        "instrument_token": health.instrument_token,
        "last_tick_age_seconds": health.last_tick_age_seconds,
        "is_stale": health.is_stale,
        "reconnect_count_24h": health.reconnect_count_24h,
        "disconnect_count_24h": health.disconnect_count_24h,
        "rejected_bar_count_24h": health.rejected_bar_count_24h,
        "backfill_count_24h": health.backfill_count_24h,
    }


@router.post("/risk/kill-switch")
def set_kill_switch(request: Request, body: dict):
    """Illustrative only in this phase: toggles AppState.kill_switch_enabled,
    which is NOT wired to a live engine.risk_manager.RiskManager instance
    here (no live signal/risk/executor loop runs inside this dashboard
    process). Wiring this to a real running RiskManager is a further
    integration step for whoever runs this against a live session."""
    app_state = _get_state(request)
    enabled = bool(body.get("enabled", False))
    app_state.kill_switch_enabled = enabled
    return {"kill_switch_enabled": enabled}


@router.get("/instruments/search")
def search_instruments(request: Request, q: str = ""):
    app_state = _get_state(request)
    if app_state.demo_mode:
        candidates = [{"token": app_state.demo_instrument_token, "symbol": app_state.demo_instrument_symbol, "name": "DEMO-NIFTY"}]
        return [c for c in candidates if q.upper() in c["symbol"]] if q else candidates
    return []  # live instrument search requires a broker session's cached instrument master — not wired into this read-only dashboard process
