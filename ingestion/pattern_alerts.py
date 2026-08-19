"""Live pattern/signal monitor: watches the tick_store for newly closed
candles across the F&O universe and, whenever one appears for a tracked
(token, timeframe), re-runs chart-pattern, candlestick-pattern, and SMC Pro
signal detection over a small recent window — then sends a Telegram alert
for anything CONFIRMED on that newest bar (not stale re-detections of
already-alerted events; see `check_one`'s bar_index filter).

This process only reads the store; it does not open a broker session. Run it
alongside ingestion.run_ingestion (which is what actually writes new candles)
and app.main (the dashboard) — three independent processes sharing the same
SQLite file, per this project's existing process model.

Usage:

    python -m ingestion.pattern_alerts

    # one-shot: replay detection over the latest stored bars and send real
    # (clearly DEMO-labelled) Telegram alerts, to verify the alert path
    # without waiting for a live session
    python -m ingestion.pattern_alerts --demo
"""
from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from config.logging_config import configure_logging
from config.market_calendar import MarketCalendar
from config.settings import REPO_ROOT, get_settings
from data.database import TickStore
from data.resample import candles_to_dataframe, resample_candles
from ingestion.telegram_client import TelegramClient
from strategies import candlestick, smc_pro
from strategies.chart_patterns import (
    detect_double_bottom,
    detect_double_top,
    detect_head_and_shoulders,
    detect_inverse_head_and_shoulders,
)

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_TIMEFRAME_MINUTES = {"1min": 1, "3min": 3, "5min": 5, "15min": 15, "30min": 30, "60min": 60}

# Bars of the TARGET timeframe kept for detection. Small on purpose — this is
# a live "did something just confirm" check, not the Patterns tab's ~90-day
# lookback; 200 bars is still comfortably above every detector's warm-up
# requirement (EMA50, ADX14, volume MA20, smc_pro's swing lookback=7).
DETECTION_WINDOW = 200

POLL_SECONDS = 20

# Skip patterns with no directional bias. In practice this drops every Doji and
# Inside Bar (always "neutral") plus the neutral variant of Harami — the bulk of
# raw detections, and the least actionable. Directional Harami, Hammer,
# Shooting Star, Engulfing, Pin Bar, Morning/Evening Star still alert normally.
# Set to False to get every detection back.
SKIP_NEUTRAL_PATTERNS = True

# Which categories actually get sent to Telegram. Default is SMC Pro signals
# only: those already require confluence (swing BOS + EMA + VWAP + ADX +
# volume + order block) and must clear a minimum score, so they're far rarer
# and more actionable than raw pattern hits. Raw chart/candlestick patterns
# fire on nearly every bar across 200+ symbols — they stay available in the
# dashboard's Patterns tab, they're just not pushed as notifications.
# Flip either flag to True to add that category back.
ALERT_CHART_PATTERNS = False
ALERT_CANDLESTICK_PATTERNS = False
ALERT_SMC_PRO_SIGNALS = True


def load_universe(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def timeframes_for(kind: str) -> list[str]:
    """Indices re-check on 5-minute candle close; F&O stocks on both 5m and
    15m — per the user's explicit scope for this monitor."""
    return ["5min"] if kind == "index" else ["5min", "15min"]


def _load_dataframe(store: TickStore, calendar: MarketCalendar, token: str, timeframe: str, limit: int):
    minutes = _TIMEFRAME_MINUTES[timeframe]
    raw_limit = max(limit * minutes, 500)
    raw = store.get_candles(token, "1min", limit=raw_limit)
    df = candles_to_dataframe(raw)
    if timeframe != "1min":
        resampled = resample_candles(df, minutes, calendar)
        df = resampled[resampled["is_closed"] == True].reset_index(drop=True)  # noqa: E712
    return df.tail(limit).reset_index(drop=True)


def _fmt_time(ts) -> str:
    return ts.astimezone(IST).strftime("%Y-%m-%d %H:%M")


def _pattern_message(symbol: str, timeframe: str, category: str, e) -> str:
    icon = "🟢" if e.direction == "bullish" else "🔴" if e.direction == "bearish" else "⚪"
    return (
        f"{icon} <b>{category}</b> — {symbol} ({timeframe})\n"
        f"Pattern: {e.pattern.replace('_', ' ').title()} ({e.direction})\n"
        f"Time: {_fmt_time(e.timestamp)} IST"
    )


def _signal_message(symbol: str, timeframe: str, s) -> str:
    icon = "🟢🚀" if s.direction == "long" else "🔴🔻"
    label = "BUY" if s.direction == "long" else "SELL"
    return (
        f"{icon} <b>SMC Pro {label}</b> — {symbol} ({timeframe})\n"
        f"Score: {s.score}/100\n"
        f"Entry: {s.entry:.2f} | SL: {s.stop_loss:.2f}\n"
        f"TP1: {s.tp1:.2f} | TP2: {s.tp2:.2f} | TP3: {s.tp3:.2f}\n"
        f"Time: {_fmt_time(s.timestamp)} IST"
    )


@dataclass
class MonitorState:
    """Tracks the last-seen closed-bar timestamp per (token, timeframe), so
    a symbol only gets (re-)checked when a genuinely new bar has closed —
    and so the very first sighting of each key just records a baseline
    instead of alerting on whatever's already sitting on the latest bar
    (avoids a startup burst of "alerts" for pre-existing conditions)."""
    last_seen_close: dict[tuple[str, str], str] = field(default_factory=dict)


def check_one(store: TickStore, calendar: MarketCalendar, telegram: TelegramClient, entry: dict, timeframe: str, state: MonitorState) -> int:
    df = _load_dataframe(store, calendar, entry["token"], timeframe, DETECTION_WINDOW)
    if df.empty or len(df) < 20:
        return 0

    key = (entry["token"], timeframe)
    latest_close = df["close_time"].iloc[-1].isoformat()
    is_first_sighting = key not in state.last_seen_close
    if state.last_seen_close.get(key) == latest_close:
        return 0  # no new closed bar since last check
    state.last_seen_close[key] = latest_close
    if is_first_sighting:
        return 0  # baseline only

    last_idx = len(df) - 1
    sent = 0

    def is_alertable(e) -> bool:
        if e.bar_index != last_idx:
            return False
        return not (SKIP_NEUTRAL_PATTERNS and e.direction == "neutral")

    # Detection is skipped entirely for disabled categories — no point paying
    # for detectors whose output would be discarded on every single bar.
    if ALERT_CANDLESTICK_PATTERNS:
        for e in (e for e in candlestick.detect_all_candlestick_patterns(df) if is_alertable(e)):
            if telegram.send_message(_pattern_message(entry["symbol"], timeframe, "Candlestick Pattern", e)):
                sent += 1

    if ALERT_CHART_PATTERNS:
        chart_events = (
            detect_double_top(df) + detect_double_bottom(df)
            + detect_head_and_shoulders(df) + detect_inverse_head_and_shoulders(df)
        )
        for e in (e for e in chart_events if is_alertable(e)):
            if telegram.send_message(_pattern_message(entry["symbol"], timeframe, "Chart Pattern", e)):
                sent += 1

    if ALERT_SMC_PRO_SIGNALS:
        zones = smc_pro.detect_order_blocks_and_breakers(df)
        for s in (s for s in smc_pro.compute_trade_signals(df, zones) if s.bar_index == last_idx):
            if telegram.send_message(_signal_message(entry["symbol"], timeframe, s)):
                sent += 1

    return sent


def run_once(store: TickStore, calendar: MarketCalendar, telegram: TelegramClient, universe: list[dict], state: MonitorState) -> int:
    total = 0
    for entry in universe:
        for tf in timeframes_for(entry["kind"]):
            try:
                total += check_one(store, calendar, telegram, entry, tf, state)
            except Exception:
                logger.exception("Pattern check failed for %s %s", entry["symbol"], tf)
    return total


def run_demo(store: TickStore, calendar: MarketCalendar, telegram: TelegramClient, universe: list[dict], symbol_limit: int, max_alerts: int) -> int:
    """Send real Telegram alerts for whatever the detectors find on the most
    recent stored bar, so the end-to-end alert path can be verified without
    waiting for a live session.

    These are genuine detections from real stored candles — not fabricated
    messages — but they are re-detections of ALREADY-CLOSED historical bars,
    not live signals. Each is labelled DEMO in the message so a demo alert
    can never be mistaken for a live trade signal arriving during market
    hours. Capped at `max_alerts` so a demo can't flood the chat.
    """
    subset = universe[:symbol_limit]
    state = MonitorState()
    # Pre-seed with a sentinel so check_one treats every key as "seen before"
    # (not a first sighting) and therefore actually runs detection instead of
    # just recording a baseline.
    for entry in subset:
        for tf in timeframes_for(entry["kind"]):
            state.last_seen_close[(entry["token"], tf)] = "__demo_force_stale__"

    collected: list[str] = []

    class _Collector:
        def send_message(self, text: str, timeout: float = 10.0) -> bool:
            collected.append(text)
            return True

    run_once(store, calendar, _Collector(), subset, state)

    telegram.send_message(
        f"🧪 <b>DEMO RUN</b>\nReplaying detection over the latest stored bars for "
        f"{len(subset)} symbols.\nFound {len(collected)} signal(s); sending up to {max_alerts}.\n"
        f"<i>These are historical re-detections, not live signals.</i>"
    )

    sent = 0
    for text in collected[:max_alerts]:
        if telegram.send_message("🧪 <b>[DEMO]</b>\n" + text):
            sent += 1

    telegram.send_message(
        f"🧪 <b>DEMO COMPLETE</b>\nSent {sent} demo alert(s).\n"
        f"Live alerts resume automatically at the next session (09:15 IST)."
    )
    return sent


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_dir, settings.log_level)

    bot_token = settings.telegram_bot_token.get_secret_value()
    if not bot_token or not settings.telegram_chat_id:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing from .env — cannot send alerts.")
        return 1

    universe = load_universe(REPO_ROOT / "config" / "fo_universe.json")
    store = TickStore(settings.tick_store_path)
    calendar = MarketCalendar(settings.holidays_file)
    telegram = TelegramClient(bot_token, settings.telegram_chat_id)
    state = MonitorState()

    if "--demo" in sys.argv:
        logger.info("Running one-shot demo (no live loop).")
        sent = run_demo(store, calendar, telegram, universe, symbol_limit=40, max_alerts=8)
        logger.info("Demo complete: %d alert(s) sent.", sent)
        return 0

    logger.info("Pattern alert monitor started: %d symbols, poll every %ds", len(universe), POLL_SECONDS)
    try:
        while True:
            cycle_start = time.monotonic()
            sent = run_once(store, calendar, telegram, universe, state)
            if sent:
                logger.info("Sent %d alert(s) this cycle", sent)
            elapsed = time.monotonic() - cycle_start
            if elapsed < POLL_SECONDS:
                time.sleep(POLL_SECONDS - elapsed)
    except KeyboardInterrupt:
        logger.info("Pattern alert monitor stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
