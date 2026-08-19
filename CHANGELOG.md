# Changelog

All notable changes to this project are documented here, most recent first.

## 2026-08-19

### Added
- **Telegram pattern/signal alerts**: new `ingestion/pattern_alerts.py` monitor process that watches the tick store for newly closed candles across the full F&O universe and pushes an alert whenever a tracked event confirms on the newest bar. Indices are re-checked on 5-minute candle closes; F&O stocks on both 5-minute and 15-minute. Backed by `ingestion/telegram_client.py` (minimal Bot API client, fails soft so a Telegram outage can't take down the monitor loop) and `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in Settings.
  - Alerts are restricted to events confirming on the *latest* bar and each `(token, timeframe)` records a baseline on first sighting, so startup doesn't fire a burst of alerts for pre-existing conditions.
  - **Alert filtering** (tunable constants at the top of the module): raw chart/candlestick pattern detections fire on nearly every bar — across 200+ symbols on two timeframes that projected to several hundred notifications per 5-minute boundary. Defaults are now `ALERT_SMC_PRO_SIGNALS = True` with `ALERT_CHART_PATTERNS` / `ALERT_CANDLESTICK_PATTERNS` off, since SMC Pro signals already require confluence (swing BOS + EMA + VWAP + ADX + volume + order block) and must clear a minimum score. Measured on a 30-symbol dry run: 85 raw → 7 alerts. `SKIP_NEUTRAL_PATTERNS` additionally drops no-bias patterns (Doji, Inside Bar, neutral Harami) when the pattern categories are re-enabled. Disabled categories skip their detectors entirely rather than computing and discarding.
  - All pattern categories remain fully visible in the dashboard's Patterns tab regardless of these flags — the flags only gate Telegram push.
  - `--demo` flag: one-shot mode that replays detection over the latest stored bars and sends real (DEMO-labelled, capped) Telegram messages, so the alert path can be verified without waiting for a live session.
  - **Multi-destination fan-out**: `TELEGRAM_CHAT_ID` now accepts a comma-separated list, so one alert can go to several chats (e.g. a group *and* a private chat). `send_message` returns True if at least one destination accepted, so alert counters aren't inflated by the number of destinations; per-destination failures are logged individually, and supergroup migration is tracked per chat id.
  - **Supergroup migration handling** in `telegram_client.py`: Telegram silently reassigns a group's chat id when it is upgraded to a supergroup, after which every send fails with HTTP 400 + a `migrate_to_chat_id` parameter — alerts would just stop arriving with no obvious cause. The client now detects that response, adopts the new id at runtime, and retries the send once (logging a warning to update `.env` so the fix persists across restarts). Hit this for real when routing was switched from a private chat to a group.
- **`--universe` flag for `ingestion.run_ingestion`**: subscribes to every instrument in `config/fo_universe.json` by token.

### Fixed
- **Ingestion silently subscribed to only 4 of 212 instruments.** Angel One's instrument master stores NSE equities with a series suffix in its `symbol` field (`WIPRO-EQ`), while `config/fo_universe.json` and the CLI args used the bare `name` (`WIPRO`). `run_ingestion.py` matched on `symbol`, so all 208 F&O stocks failed with "No instrument found ... skipping" and only the 4 indices — whose symbol and name happen to be identical — actually subscribed. Because the failure was a per-symbol `logger.error` and the process still started normally with a non-empty subscription list, this looked like a healthy startup. Fixed by adding the `--universe` flag above, which subscribes by the already-resolved tokens and sidesteps symbol-spelling entirely; now correctly reports "Subscribed to 212 instruments in 5 batch(es)".

- **SMC Pro indicator**: ported the user-supplied "SMC Pro [Nifty/Sensex/BankNifty]" Pine Script (TradingView) indicator into Python (`strategies/smc_pro.py`), wired into a new `/api/candles/{token}/smc_pro` endpoint and rendered on the chart. Includes swing-anchored order blocks with breaker-block flips, fair value gaps with inverse-FVG flips, premium/discount/equilibrium zones, opening range breakout (ORB), and a weighted 0-100 trade score gating BUY/SELL signals with ATR/order-block-based SL and R-multiple TP1/TP2/TP3.
- **Chart zoom/pan**: mouse wheel zoom (cursor-anchored), click-drag pan, +/- buttons, and a Reset button.
- **Home page symbol grid**: landing page showing every NSE F&O index and F&O-eligible stock as a card (LTP, change, change %, day's high-low range %), grouped as Indices / F&O Stocks (sorted gainers → losers), click-through opens the chart directly. Backed by a new `/api/watchlist` endpoint and `TickStore.latest_and_prev_close()`.
- **News tab**: headlines for the loaded symbol via Google News' public RSS search (`/api/news/{token}`), date-filtered to the last 3 days (Google ranks by relevance, not recency, so old articles were leaking through unfiltered).
- **Patterns tab**: chart patterns (double top/bottom, H&S) and candlestick patterns, restricted to the last 2 trading days, computed on fixed timeframe sets depending on instrument type — stocks: 15m + 60m, indices: 5m + 15m (`/api/patterns/{token}`).
- **Data-range label** above the chart showing exactly how many bars and what date range are displayed.
- `.gitignore`: excluded `venv/` (was only excluding `.venv/`, a mismatch with this project's actual folder name) and the 35MB `cache/dhan_scrip_master.csv`.

### Changed
- Removed the "Load" button — changing the timeframe dropdown now loads immediately, and pressing Enter in the token field loads that symbol.
- Chart history depth is now sized per timeframe (up to 1500 bars on 1m, scaling down for higher timeframes) instead of a flat 200-bar window that only covered ~3 hours regardless of timeframe.
- Sidebar tabs are now Positions / Journal / News / Patterns (previously included Watchlist and Data Health — Watchlist's job is now covered by the home page grid).

### Fixed (performance)
- **`_load_dataframe` raw-fetch scaling bug**: the 1-minute raw candle fetch was sized in raw *1-minute* units regardless of the requested target timeframe, so every timeframe (5m/15m/60m/...) was silently getting the same ~13-trading-day raw window after resampling — a 60-minute chart request was effectively starved of history. Fixed to scale the raw fetch by the timeframe's minute multiple.
- **`resample_candles` O(days × n) bug**: re-scanned the *entire* 1-minute DataFrame once per calendar day in a Python loop. Harmless at the small data volumes this was first tested against, but with multi-year 1-minute history now in the store, this took **2+ minutes** for a 300k-row resample. Rewritten to be fully vectorized (bucket boundaries computed column-wise, single groupby) — same request now takes **~0.4 seconds**. All 8 existing `tests/test_resample.py` cases (including the exact edge case this touches, partial/shortened session-end buckets) still pass unchanged.
- **`strategies/candlestick.py`**: all 9 detectors used `df.iterrows()`/`df.iloc[i]` per row, which constructs a new pandas Series per call — the dominant cost at a few thousand rows (**8.7s** for 5000 bars). Rewritten to pull columns to numpy arrays once and index those; **0.15s** for the same 5000 bars, identical event output. All 16 `tests/test_candlestick.py` cases pass unchanged.
- **`strategies/chart_patterns.py`**: `detect_double_top`/`detect_double_bottom`/`detect_head_and_shoulders`/`detect_inverse_head_and_shoulders` scanned forward bar-by-bar via `df["close"].iloc[j]` for the neckline-break confirmation, which is slow per-call and degrades toward O(n²) when a neckline goes unbroken for a long stretch across many candidate pairs (**~7s each** at 6675 bars). Rewritten to use `numpy.flatnonzero` over a pre-extracted array; **~0.22s each**, identical event counts. All 16 `tests/test_chart_patterns.py` cases pass unchanged.
- **`/api/patterns/{token}` detection window**: was a flat 5000-bar limit regardless of timeframe, meaning 60-minute detection chewed through ~3 years of history (~15s+ per request even after the above fixes) while 5-minute detection only saw a few months. Resized to ~90 trading days' worth of bars per timeframe — already generous confirmation runway for these pattern types, and keeps every timeframe's request in the same fast (<2s) ballpark.
- Full test suite (311 tests) passes after every fix in this batch.

## 2026-08-18

### Added
- Cloned the repo, set up the Python 3.11 virtual environment, installed `requirements.txt` (verified clean with `pip check`).
- Populated `.env` with Angel One SmartAPI credentials; verified the connection with a live TOTP login/logout round-trip.
- Started the FastAPI dashboard (`app.main`) and live Angel One WebSocket ingestion (`ingestion.run_ingestion`) for NIFTY and BANKNIFTY.
- Diagnosed an "empty dashboard" report: not a bug — ingestion was correctly rejecting ticks received outside NSE market hours; confirmed via `ingestion.websocket_client` logs.
- **Historical data backfill**: downloaded 7 years of 1-minute candles for the full NSE F&O universe (208 F&O-eligible stocks + NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY), ~127.6 million bars total, stored under the same Angel One instrument tokens live ingestion uses (so historical + live form one continuous series).
  - `scripts/download_historical.py`: initial Angel-One-sourced backfill script.
  - Fixed a bug where Angel One's historical endpoint silently returned zero rows because timestamps were sent in UTC instead of the IST wall-clock format the API expects.
  - Pivoted to Dhan's historical API (`ingestion/dhan_historical.py`, `scripts/download_historical_dhan.py`) for the bulk multi-year pull — far more generous rate limits (5 req/s vs. Angel's placeholder limits) and no meaningful lookback cap.
  - Filtered out 17 `*NSETEST` dummy/test scrips that were mixed into Dhan's F&O stock list before they wasted API calls.
  - Fixed a major `TickStore` performance bug: `upsert_candle` opened a new SQLite connection and committed on every single row, making a 19,000-row chunk take ~4 minutes. Added `bulk_upsert_candles()` (one transaction per chunk) — same chunk now takes ~0.2 seconds (~1000x).
  - Parallelized the download across 5 worker threads (shared, thread-safe rate limiter) once the bulk-insert fix removed the write bottleneck.
  - Patched two symbols (`GMRAIRPORT`, `GLENMARK`) that hit a transient read-timeout on one window during the main run.
- Verified the dashboard renders the downloaded historical data correctly end-to-end (chart, EMAs, pattern overlays) via browser testing.

---

*Maintenance note: update this file with every change made to the project — new features, fixes, and any behavior changes — dated under the day they landed.*
