"""REST endpoints. The dashboard is a pure reader for market data — every
price/pattern route here reads from the SQLite store (written by
ingestion/execution processes) or runs pure strategies/ computation on data
already in the store, and nothing here opens a broker connection.

Exception: GET /news/{token} makes a best-effort outbound HTTP call to
Google News' public RSS search (no broker session, no credentials) — news
headlines aren't stored data, there's nowhere else to read them from. It
fails soft (returns an empty article list) so the rest of the dashboard
never depends on that network call succeeding.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import requests
from fastapi import APIRouter, HTTPException, Request

from app.api.serializers import (
    candle_row_to_dict,
    opening_range_to_dict,
    pattern_event_to_dict,
    premium_discount_to_dict,
    trade_signal_to_dict,
    zone_to_dict,
)
from app.state import AppState
from config.settings import REPO_ROOT
from data.resample import candles_to_dataframe, resample_candles
from ingestion.health import get_data_health
from strategies import candlestick, indicators as ind, smc, smc_pro
from strategies.chart_patterns import detect_double_bottom, detect_double_top, detect_head_and_shoulders, detect_inverse_head_and_shoulders

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

_FO_UNIVERSE_PATH = REPO_ROOT / "config" / "fo_universe.json"
_fo_universe_cache: list[dict] | None = None


def _load_fo_universe() -> list[dict]:
    """Static NSE F&O-stock + F&O-index list (symbol -> Angel One token),
    generated once via ingestion.dhan_historical.build_universe and cached
    to config/fo_universe.json — read-only here, no broker session needed."""
    global _fo_universe_cache
    if _fo_universe_cache is None:
        if not _FO_UNIVERSE_PATH.exists():
            _fo_universe_cache = []
        else:
            _fo_universe_cache = json.loads(_FO_UNIVERSE_PATH.read_text(encoding="utf-8"))
    return _fo_universe_cache


def _get_state(request: Request) -> AppState:
    return request.app.state.app_state


_TIMEFRAME_MINUTES = {"1min": 1, "3min": 3, "5min": 5, "15min": 15, "30min": 30, "60min": 60}


def _load_dataframe(app_state: AppState, token: str, timeframe: str, limit: int):
    """Fetch `limit` bars of `timeframe`, resampled from the 1-minute store.

    The raw 1-minute fetch is scaled by the timeframe's minute multiple —
    asking for `limit` 60-minute bars needs 60x as many raw 1-minute rows as
    asking for `limit` 1-minute bars. Without this scaling, every timeframe
    silently got the same ~`limit` *minutes* of raw history (e.g. 5000
    1-minute rows resamples to only ~83 60-minute bars, covering the same
    ~13 trading days as 5000 1-minute bars — nowhere near enough runway for
    a 60-minute Head & Shoulders to form).
    """
    minutes = _TIMEFRAME_MINUTES.get(timeframe)
    if minutes is None:
        raise HTTPException(400, f"Unsupported timeframe: {timeframe}")
    raw_limit = max(limit * minutes, 500)
    raw = app_state.store.get_candles(token, "1min", limit=raw_limit)
    df = candles_to_dataframe(raw)
    if timeframe != "1min":
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
    result["adx_14"] = _series_to_points(ind.adx(df, 14).adx.values, close_times)
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


_PATTERN_LOOKBACK_TRADING_DAYS = 90  # ~4.5 months — generous confirmation runway for classical chart patterns
_TRADING_MINUTES_PER_DAY = 375  # NSE cash session: 09:15-15:30 IST


@router.get("/patterns/{token}")
def get_patterns(token: str, request: Request, timeframe: str = "1min", days: int = 2):
    """Chart patterns (double top/bottom, H&S) and candlestick patterns
    (hammer, engulfing, etc.), restricted to the last `days` distinct trading
    dates present in the store — the most recent completed session plus
    today's live/backfilled session, whichever is newest. Detection itself
    runs over a larger window (patterns like H&S need history before the
    display window to confirm) — only the OUTPUT events get date-filtered.

    The detection window is sized to ~90 trading days' worth of `timeframe`
    bars, not a flat bar count — a flat 5000-bar window meant 60-minute
    detection was chewing through ~3 years of history (and the O(n^2)-ish
    chart-pattern detectors taking ~15s+ per request) while 5-minute
    detection only saw a few months. 90 trading days is already generous
    confirmation runway for these patterns and keeps every timeframe's
    request in the same, fast ballpark.
    """
    app_state = _get_state(request)
    minutes = _TIMEFRAME_MINUTES.get(timeframe, 1)
    detection_limit = max((_PATTERN_LOOKBACK_TRADING_DAYS * _TRADING_MINUTES_PER_DAY) // minutes, 50)
    df = _load_dataframe(app_state, token, timeframe, limit=detection_limit)
    if df.empty or len(df) < 5:
        return {"chart_patterns": [], "candlestick_patterns": [], "trading_dates": []}

    trading_dates = sorted({t.isoformat()[:10] for t in df["close_time"]})[-days:]
    trading_date_set = set(trading_dates)

    candlestick_events = candlestick.detect_all_candlestick_patterns(df)
    chart_events = (
        detect_double_top(df) + detect_double_bottom(df)
        + detect_head_and_shoulders(df) + detect_inverse_head_and_shoulders(df)
    )

    def in_range(e) -> bool:
        return e.timestamp.isoformat()[:10] in trading_date_set

    # These detectors run on every bar's fractal window, so a busy 2-day
    # stretch can surface hundreds of overlapping matches (e.g. nested
    # double-tops at slightly different swing pairs). Cap to the most
    # recent ones so the sidebar list stays usable — the underlying
    # data isn't lost, /candles/{token}/overlays still returns everything.
    max_events = 40
    chart_filtered = sorted((e for e in chart_events if in_range(e)), key=lambda e: e.timestamp, reverse=True)[:max_events]
    candlestick_filtered = sorted((e for e in candlestick_events if in_range(e)), key=lambda e: e.timestamp, reverse=True)[:max_events]

    return {
        "chart_patterns": [pattern_event_to_dict(e) for e in chart_filtered],
        "candlestick_patterns": [pattern_event_to_dict(e) for e in candlestick_filtered],
        "trading_dates": trading_dates,
    }


@router.get("/news/{token}")
def get_news(token: str, request: Request, days: int = 3, limit: int = 15):
    """Headlines from the last `days` days for this instrument's underlying
    symbol, via Google News' public RSS search (no API key). Best-effort:
    network/parse failures return an empty list rather than a 500, since news
    is supplementary and the rest of the dashboard must keep working without
    it. Google's search ranks by relevance, not recency, so it readily
    returns multi-year-old articles — every item's <pubDate> is parsed and
    anything older than the cutoff is dropped before the limit is applied.
    """
    universe = _load_fo_universe()
    entry = next((e for e in universe if e["token"] == token), None)
    symbol = entry["symbol"] if entry else token
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    try:
        resp = requests.get(
            "https://news.google.com/rss/search",
            params={"q": f"{symbol} NSE stock", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"},
            timeout=6,
        )
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.content)
        items = []
        for item in root.findall("./channel/item"):
            pub_date_raw = (item.findtext("pubDate") or "").strip()
            try:
                pub_date = parsedate_to_datetime(pub_date_raw)
                if pub_date.tzinfo is None:
                    pub_date = pub_date.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue  # unparseable date -> can't verify recency, skip rather than risk showing something stale
            if pub_date < cutoff:
                continue
            source_el = item.find("source")
            items.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published": pub_date_raw,
                "source": source_el.text.strip() if source_el is not None and source_el.text else None,
                "_sort": pub_date,
            })
        items.sort(key=lambda i: i["_sort"], reverse=True)
        for i in items:
            del i["_sort"]
        return {"symbol": symbol, "articles": items[:limit]}
    except Exception as exc:
        logger.warning("News fetch failed for %s: %s", symbol, exc)
        return {"symbol": symbol, "articles": []}


@router.get("/candles/{token}/smc_pro")
def get_smc_pro(token: str, request: Request, timeframe: str = "1min", limit: int = 200):
    """Port of the user-supplied "SMC Pro [Nifty/Sensex/BankNifty]" Pine Script
    indicator: swing-anchored order blocks + breaker-block flips, FVGs with
    inverse-FVG flips, premium/discount/equilibrium zone, opening range, and
    weighted-score BUY/SELL signals with SL/TP1-3. See strategies/smc_pro.py.
    """
    app_state = _get_state(request)
    df = _load_dataframe(app_state, token, timeframe, limit)
    if df.empty or len(df) < 20:
        return {"order_blocks": [], "fair_value_gaps": [], "premium_discount": None, "opening_range": [], "signals": []}

    close_times = df["close_time"].tolist()
    zones = smc_pro.detect_order_blocks_and_breakers(df)
    fvgs = smc_pro.detect_fvg_with_inverse(df)
    pdz = smc_pro.premium_discount_zone(df)
    orb = smc_pro.opening_range(df)
    signals = smc_pro.compute_trade_signals(df, zones)

    return {
        "order_blocks": [zone_to_dict(z, close_times) for z in zones],
        "fair_value_gaps": [zone_to_dict(z, close_times) for z in fvgs],
        "premium_discount": premium_discount_to_dict(pdz),
        "opening_range": [opening_range_to_dict(o) for o in orb],
        "signals": [trade_signal_to_dict(s) for s in signals],
    }


@router.get("/watchlist")
def get_watchlist(request: Request, timeframe: str = "1min"):
    """Card list for every NSE F&O index + F&O-eligible stock: LTP, change,
    and change % vs. the previous trading day's close, computed from
    whatever candle history is already in the store (no broker call)."""
    app_state = _get_state(request)
    universe = _load_fo_universe()
    cards = []
    for entry in universe:
        quote = app_state.store.latest_and_prev_close(entry["token"], timeframe)
        if quote is None:
            continue
        change = None
        change_pct = None
        if quote["prev_close"] is not None and quote["prev_close"] != 0:
            change = quote["ltp"] - quote["prev_close"]
            change_pct = (change / quote["prev_close"]) * 100
        cards.append({
            "symbol": entry["symbol"],
            "token": entry["token"],
            "kind": entry["kind"],
            "ltp": quote["ltp"],
            "change": change,
            "change_pct": change_pct,
            "day_high": quote["day_high"],
            "day_low": quote["day_low"],
            "day_range_pct": quote["day_range_pct"],
            "as_of": quote["latest_time"],
        })
    return cards


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
