# Changelog

All notable changes to this project are documented here, most recent first.

## 2026-08-19

### Added
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
