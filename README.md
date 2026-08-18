# Trading Terminal — Angel One SmartAPI (Paper Trading)

Personal, paper-trading-only trading terminal for Indian equities/F&O on Angel One SmartAPI.
`TRADING_MODE` is hard-wired to `PAPER`. There is no configuration flag that turns on live
order placement — see [Known Limitations](#known-limitations) and `engine/execution/` (Phase 7)
for how that boundary is enforced in code.

This project is for **personal, non-commercial paper trading**. If you ever enable live
execution, you are responsible for complying with Angel One's API terms and the current
SEBI rules on retail algo trading via broker APIs — verify the current regulatory position
yourself before doing so; this README does not constitute legal or compliance advice.

Build is proceeding in phases; each phase is documented here as it lands. See
`docs/phase0_capability_matrix.md` for the SmartAPI capability audit and architecture
decisions this project is built on (FastAPI + SSE dashboard, PCR/OI-buildup shown as both
computed and API-sourced values, conservative placeholder rate limits pending doc verification).

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your real Angel One credentials — never commit .env
```

Verified 2026-08-18: `pip install -r requirements.txt && pip check` resolves with **no
dependency conflicts** in a clean Python 3.11 venv (28 packages, including
`smartapi-python==1.5.5`, `pyotp`, `logzero`, `websocket-client`, `fastapi`). No version
pins had to be worked around.

## Phase 1 — Skeleton, config, logging, auth, instrument master, one LTP fetch

What exists after Phase 1:

- `config/settings.py` — env-driven settings via `pydantic-settings`. Credentials are
  `SecretStr`, never rendered in `repr()`/`str()`. `trading_mode` is a `Literal["PAPER"]`
  field — passing anything else raises a validation error at startup, not at trade time.
- `config/logging_config.py` — structured JSON logging to stdout + `logs/trading_terminal.jsonl`,
  with a correlation-ID contextvar and regex + field-name-based secret redaction (defense in
  depth: both the rendered message text and any structured `extra` fields with sensitive
  names like `jwttoken`/`password`/`api_key` are redacted).
- `config/market_calendar.py` — NSE/BSE session windows (09:15–15:30 IST), holiday-aware,
  rejects naive datetimes. Holiday list lives in `config/holidays.json`, user-maintained —
  see the file's own header comment for why the 2026 seed data is marked `verified: false`
  (conflicting sources on the Diwali date is a concrete example of why this must not be
  trusted blindly).
- `config/smartapi_limits.py` — per-endpoint rate limits and historical-candle lookback
  windows. **All values are placeholder/unverified** (docs domain was network-blocked from
  the dev environment) — see file header and Known Limitations below.
- `broker/ratelimit.py` — per-endpoint token-bucket `RateLimiter`, exponential backoff with
  jitter on throttle, shared by one `AngelOneClient` instance.
- `broker/auth.py` — TOTP login (`generateSession`), explicit refresh (`generateToken`), and
  a session-expiry hook that **halts** (flips `is_valid=False` and logs) rather than
  silently retrying — mid-session auth failure must be surfaced, per the project's ground
  rules, not papered over.
- `broker/instruments.py` — instrument master download from
  `margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json`, cached to
  disk per day, falls back to the most recent cached file on download failure, raises
  `InstrumentMasterError` only if neither network nor cache works.
- `broker/market_data.py` — `get_ltp()`, rate-limited and retried through the shared client.
- `data/models.py` — the typed contracts (`Instrument`, `Quote`, `Candle`, `DerivedMetric`)
  that broker code and (future) strategy code communicate through. `Decimal` for all
  prices/strikes/tick sizes; every datetime field rejects naive values.
- `scripts/fetch_ltp.py` — the manual acceptance script for this phase (see below).

### How to run it yourself

```bash
cp .env.example .env
# edit .env with your real ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PASSWORD_OR_PIN, ANGEL_TOTP_SECRET
python -m scripts.fetch_ltp RELIANCE-EQ NSE
# or, if you already know the symbol token:
python -m scripts.fetch_ltp RELIANCE-EQ NSE 2885
```

Run the test suite:

```bash
python -m pytest tests/ -v
```

### Phase 1 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| `pip install -r requirements.txt` + `pip check` | Ran for real in a clean Python 3.11 venv | Clean install, no conflicts, versions pinned in `requirements.txt` | — fully tested |
| Settings / `TRADING_MODE` hard-wiring / SecretStr never leaking into repr | `pytest tests/test_settings.py` (4 tests) | All pass | — fully tested |
| JSON logging + secret redaction (message text and structured `extra` fields) | `pytest tests/test_logging_redaction.py` (4 tests) | All pass | — fully tested |
| Market calendar (session windows, holiday awareness, naive-datetime rejection, next-trading-day) | `pytest tests/test_market_calendar.py` (7 tests) | All pass | — fully tested |
| Rate limiter (token bucket throttling, retry-on-throttle, no-retry-on-other-errors, backoff bounds) | `pytest tests/test_ratelimit.py` (6 tests) | All pass | — fully tested |
| Instrument master parsing (strike/tick-size /100 scaling, malformed-record skipping, option-chain filter) | `pytest tests/test_instruments.py` (7 tests, sample records) | All pass | Parsing logic tested against a hand-built sample based on a real record seen during Phase 0 research, not against the full live file (see next row) |
| Instrument master live download | Ran `requests.get()` against the real scrip-master URL from this environment | **Blocked**: `margincalculator.angelbroking.com` is unreachable through this environment's egress proxy (403 on CONNECT) | No network path to Angel One's infrastructure exists in this sandbox — needs to be run from your own machine/environment |
| Instrument master fallback-to-cache and raise-when-no-cache paths | Ran for real (not mocked) against the actual blocked network call above, both with and without a pre-seeded cache file | Both paths behave correctly: falls back to cache when one exists, raises `InstrumentMasterError` when neither network nor cache is available | — fully tested, and against a genuine network failure rather than a simulated one |
| `AngelOneSession.login()` — TOTP generation, `generateSession` call | Ran `scripts.fetch_ltp` with placeholder `.env.example` values | Fails exactly where expected: invalid base32 TOTP secret raises `AuthenticationError` with a clear message, no crash, no hang | No real Angel One credentials exist for this sandbox to test an actual successful login, mid-session expiry hook firing, or `generateToken` refresh — **you must run `scripts/fetch_ltp.py` yourself with real credentials to validate this** |
| `get_ltp()` against a live quote | Not run | Unknown | Depends on a successful login above, which requires real credentials and a reachable broker endpoint — neither available here |
| `SmartConnect()` instantiation's own IP-lookup call (to `api.ipify.org`) | Observed as a side effect while testing the above | Fails silently inside the SDK (falls back to localhost IP) when `api.ipify.org` is unreachable, does not raise | Confirms the SDK itself has an external dependency beyond Angel One's own endpoints — noting this so it isn't mistaken for a bug in our code if you see the same warning outside this sandbox with normal internet access |

**Bottom line: everything that can be tested without your real Angel One credentials and
a real network path to Angel One's infrastructure has been tested for real (28/28 pytest
tests pass, plus two manual runs against genuine — not mocked — network failures). The
login → instrument-master-download → LTP-fetch happy path requires you to run
`scripts/fetch_ltp.py` yourself** with real credentials, since I have neither.

## Phase 2 — Ingestion service: WebSocket, candle store, validation, reconnect/backfill

What exists after Phase 2:

- `data/database.py` — `TickStore`: SQLite in WAL mode, one writer (the ingestion process).
  Prices stored as `TEXT` (exact `Decimal` string), never `REAL`/float, so nothing is
  silently rounded. Tables: `ticks`, `candles` (unique on instrument+timeframe+open_time,
  upserted so an in-progress bar can be updated tick-by-tick), `data_health_events`
  (reconnects, disconnects, rejected bars, backfills — timestamped, queryable by window).
- `data/validation.py` — `validate_tick()` / `validate_candle()` reject (never silently
  drop): non-positive prices, negative volume, `high < low`, `close`/`open` outside
  `[low, high]`, timestamps outside session hours or on a holiday, out-of-order and
  duplicate ticks. `is_stale()` is calendar-aware — it only fires during the regular
  session, so it cannot spuriously trigger over a weekend or holiday.
- `ingestion/candle_builder.py` — aggregates validated ticks into session-anchored 1-minute
  candles. In-progress bars are persisted with `is_closed=False` on every tick (so the
  dashboard can show a live-updating last bar); once a new minute starts the prior bar is
  finalized with `is_closed=True`. Downstream resampling/strategy code (Phase 3+) must
  filter to `is_closed=True` — nothing in this phase enforces that filter yet, it's a
  contract the data carries via the flag.
- `ingestion/websocket_client.py` — `IngestionWebSocketClient` wraps `SmartWebSocketV2`.
  Subscription-cap-aware chunking (`chunk_subscriptions`, pure function), disconnect/
  reconnect duration logging (recorded as `data_health_events`), tick validation before
  persistence. Reconnection and resubscribe-on-reconnect are largely handled by the SDK
  itself (`RESUBSCRIBE_FLAG` + its own configurable exponential backoff) — confirmed by
  reading the installed `smartapi-python==1.5.5` source, not assumed; this wrapper adds the
  logging, chunking, and validation the SDK doesn't provide.
- `ingestion/backfill.py` — detects gaps in stored candles (`TickStore.find_gaps`) and
  fills them from the historical-candle endpoint, marking filled bars `is_backfilled=True`.
  The historical response shape used here (`[iso_ts, open, high, low, close, volume]` rows)
  is based on forum examples, not the official docs — flagged as unverified, same as the
  rest of the docs-domain-blocked items from Phase 0/1.
- `ingestion/health.py` — `get_data_health()`: last-tick age, stale flag, and 24h counts of
  reconnects/disconnects/rejected bars/backfills per instrument, for the dashboard's data
  health widget (Phase 8).
- `ingestion/run_ingestion.py` — the long-lived process entrypoint. Owns the only SmartAPI
  WebSocket connection in the whole system, per the architecture rule that the dashboard
  is a pure reader.

### How to run it yourself

```bash
cp .env.example .env   # fill in real credentials
python -m ingestion.run_ingestion RELIANCE-EQ:NSE NIFTY:NSE
```

Run the test suite (now 74 tests total):

```bash
python -m pytest tests/ -v
```

### Phase 2 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| Tick/candle validation rules (all rejection cases: non-positive price, negative volume, high<low, close/open outside range, outside session hours, holiday, out-of-order, duplicate) | `pytest tests/test_validation.py` (18 tests) | All pass | — fully tested |
| Calendar-aware staleness check (fires only during session, not on weekends/holidays) | `pytest tests/test_validation.py::test_is_stale_*` (4 tests) | All pass | — fully tested |
| `TickStore` (WAL mode confirmed via `PRAGMA journal_mode`, Decimal round-trips exactly as text not float, candle upsert/overwrite, gap detection, health event counting with time-window filter) | `pytest tests/test_database.py` (9 tests) against a real SQLite file on disk | All pass | — fully tested |
| `CandleBuilder` (bar open/update/close on minute boundary, multi-instrument isolation, volume-from-cumulative-delta, `flush_all` on shutdown) | `pytest tests/test_candle_builder.py` (6 tests) | All pass | — fully tested |
| Subscription-cap chunking and token-list grouping (pure functions, no live connection) | `pytest tests/test_websocket_client.py::test_chunk_*` and `::test_to_token_list_*` (4 tests) | All pass | — fully tested |
| `IngestionWebSocketClient._on_data` tick handling — valid tick persisted, invalid tick rejected+logged, malformed payload handled without crashing | `pytest tests/test_websocket_client.py` (3 tests), constructing a real `SmartWebSocketV2` instance (not connected) and calling the callback methods directly | All pass | — fully tested at the callback level; never tested against real binary WebSocket frames from Angel One's server (see below) |
| Disconnect/reconnect duration logging | `pytest tests/test_websocket_client.py::test_on_close_then_on_open_logs_disconnect_duration` | Passes | — fully tested at the callback level |
| SDK's own reconnect/resubscribe behavior (`RESUBSCRIBE_FLAG`, `retry_strategy`, exponential backoff) | Read the installed `smartapi-python==1.5.5` source directly (`SmartWebSocketV2._on_error`, `.resubscribe()`, `._on_open()`) | Confirmed present and matches what this wrapper assumes — not a guess | Never observed firing against a real dropped connection, since no live connection exists in this sandbox |
| Historical-candle parsing and gap-fill logic | `pytest tests/test_backfill.py` (3 tests) against a fake `getCandleData` response shaped per forum examples | All pass | The response *shape* itself is unverified against official docs (see Known Limitations); real API was never called |
| Data health aggregation (`get_data_health`) | `pytest tests/test_health.py` (3 tests) | All pass | — fully tested |
| Live WebSocket connection to `wss://smartapisocket.angelone.in/smart-stream`, real binary tick frames, real reconnect/resubscribe under an actual dropped connection, real historical backfill call | Not run | Unknown | No credentials and no network path to Angel One's infrastructure exist in this sandbox — **you must run `python -m ingestion.run_ingestion SYMBOL:EXCHANGE` yourself** to validate the live path |

**Bottom line: 74/74 pytest tests pass, covering every validation rule, the full candle-
aggregation state machine, the SQLite store (including a real WAL-mode check and a real
Decimal-precision round-trip check), subscription chunking, and every WebSocket callback
in isolation. What's still unverified is the live path itself — real binary frames, a real
dropped connection, a real historical-API call — none of which are reachable from this
environment. Run `ingestion/run_ingestion.py` yourself against a real session to close
that gap.**

## Phase 3 — Indicators, multi-timeframe resampling, backtest harness with structural look-ahead prevention

What exists after Phase 3:

- `data/resample.py` — the single source of truth for turning stored 1-minute candles
  into analysis-ready DataFrames. `candles_to_dataframe()` filters to `is_closed=True`
  rows only (a partial bar simply cannot appear downstream). `resample_candles()` builds
  session-anchored 3/5/15/30/60-min bars from those 1-minute bars and marks each resampled
  bucket `is_closed` only if the underlying 1-minute data actually covers it — a bucket
  that's still forming (e.g. a 15:16 partial 15-min bar) is never reported as closed, while
  a genuinely short final bucket caused by the 375-minute session not dividing evenly into
  30-min blocks (the last block of the day is only 15 minutes) correctly IS marked closed,
  since the session really did end there.
- `strategies/indicators.py` — VWAP (session-anchored), EMA (9/20/50/200 via `ema(df, period)`),
  RSI (Wilder), MACD, ATR (Wilder), Bollinger Bands, ADX (Wilder, with +DI/-DI), volume
  statistics (rolling average + ratio). Every function is pure: DataFrame in, a typed
  `IndicatorSeries`/`MACDResult`/`BollingerBandsResult`/`ADXResult`/`VolumeStatsResult` out.
  No I/O, no network calls, no global state, per the project's strategy-layer rule.
- `backtesting/harness.py` — `run_backtest()`: a walk-forward engine where the structural
  guarantee is that at decision index `i`, the strategy function is handed
  `candles.iloc[:i+1]` — a real pandas slice that does not contain row `i+1` or beyond, not
  a convention the strategy is trusted to respect. A signal fires at bar `i`'s close and
  executes at bar `i+1`'s open (never an optimistic same-bar fill). This is a minimal
  harness scoped to prove the no-lookahead property and produce basic trade metrics (win
  rate, avg R, max drawdown, profit factor) — it does not yet model brokerage/slippage/lot
  sizing; that arrives in Phase 7 when `PaperExecutor` plugs into this same walk-forward
  core.

### How to run it yourself

```bash
python -m pytest tests/test_indicators.py tests/test_resample.py tests/test_backtest_harness.py -v
```

No credentials or network access are needed for any of Phase 3 — it operates purely on
DataFrames, so unlike Phases 1-2 everything here really was run end-to-end in this
environment, not just exercised against simulated failures.

### Phase 3 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| EMA | Cross-checked against an independent pure-Python recursive EMA implementation (not pandas) on a 10-bar series | Exact match (rel tol 1e-9) | — fully tested |
| ATR (Wilder) | Cross-checked against an independent pure-Python Wilder ATR implementation on a 10-bar series | Exact match (rel tol 1e-9) | — fully tested |
| RSI | Property tests (bounded 0-100, all-gains-series is exactly 100) + degenerate flat-price case (must not be NaN/crash) | All pass | Not cross-checked against an independent reference implementation the way EMA/ATR were — bounded/property tests give weaker assurance than an exact-match test |
| MACD | Internal-consistency check: `histogram == macd_line - signal_line` exactly, on a 30-bar series | Passes | Not cross-checked against an independent reference implementation |
| Bollinger Bands | Ordering property (`upper >= middle >= lower`) + zero-variance collapse case | Passes | Not cross-checked against an independent reference implementation |
| ADX / +DI / -DI | Bounded 0-100 property test on a 60-bar random-walk series (seeded, reproducible) | Passes | Not cross-checked against an independent reference implementation — Wilder's ADX has several documented variants in practice; this implementation is one standard formulation, not verified against a second source |
| Volume stats | Exact match against manually computed rolling mean; ratio > 1 on an injected volume spike | Passes | — fully tested |
| VWAP | Session reset verified across a synthetic 2-day dataset (day 2's VWAP is unaffected by day 1's much higher prices); bounded-by-day's-high/low property check | Passes | — fully tested |
| Multi-timeframe resampling (5-min full coverage, partial-final-bucket exclusion, session-end short-bucket correctly still closed, 1-min passthrough, unsupported timeframe rejection, empty input) | `pytest tests/test_resample.py` (8 tests) | All pass | — fully tested |
| Backtest harness structural look-ahead prevention | `pytest tests/test_backtest_harness.py::test_lookahead_structurally_impossible` — a strategy that tries to index one row past what it was given, asserting `IndexError` is raised (not "the value happens to be right") | Passes | — fully tested, and the strongest form of this test available: it proves the future row is structurally absent, not merely unread |
| Backtest harness never exposes more rows than the decision index, and full-dataset max timestamp never visible early | `pytest tests/test_backtest_harness.py` (2 tests) | Both pass | — fully tested |
| Trade simulation (target hit, stop-loss hit, entry at next-bar open not signal-bar close, no overlapping trades, end-of-data with no future bar produces no trade, multi-trade summary metrics) | `pytest tests/test_backtest_harness.py` (6 tests) | All pass | — fully tested against synthetic OHLC data; never run against real historical data since that requires the historical endpoint, which needs credentials/network unavailable here |

**Bottom line: 107/107 pytest tests pass. Phase 3 is the first phase with no
credential/network dependency at all, so every piece of it — including the specific
mechanism that prevents look-ahead bias, which is the single most important correctness
property of a backtester — was actually run and verified in this environment, not just
implemented and asserted to work.**

## Known limitations

- **Rate limits and historical-candle lookback windows are unverified placeholders**
  (`config/smartapi_limits.py`). The official SmartAPI docs domains were blocked by this
  environment's network egress proxy during development. Values are deliberately
  conservative but not confirmed — verify against the live docs before relying on them
  under load.
- **Holiday calendar (`config/holidays.json`) is unverified seed data.** Two web sources
  consulted during Phase 0 disagreed on the 2026 Diwali Muhurat date; that date was
  deliberately omitted rather than guessed. Cross-check every date against the official
  NSE circular before trusting this for live staleness/session logic.
- **Tick-size and strike-price /100 scaling in the instrument master parser is based on a
  single sample record**, not official documentation. Re-verify before this feeds any
  order-price rounding logic (Phase 7).
- **Freeze quantity is not available from Angel One's instrument master at all.** It's
  `None` on every `Instrument` until a separate NSE F&O contract-file source is wired in —
  not yet built as of Phase 1.
- **No live-credential testing has been possible in this development environment.** Auth,
  instrument-master download, LTP fetch, and the WebSocket ingestion path are implemented
  and exercised against real failure modes (network blocked, invalid TOTP secret, callback
  logic tested directly) but never against a real successful broker session or a real
  dropped WebSocket connection. You must validate the happy path yourself.
- **Historical-candle response shape (`ingestion/backfill.py`) is based on forum examples,
  not official docs.** If the real response nests differently, `fetch_historical_candles`
  will raise on the first real call rather than silently misparsing (it unpacks a fixed
  6-tuple per row) — but verify against docs before relying on backfill in anger.
- **Base candle timeframe is 1 minute**; 3/5/15/30/60-min resampling is built (Phase 3) on
  top of it, session-anchored and partial-bar-safe.
- **RSI, MACD, Bollinger Bands, and ADX were verified with property/consistency tests, not
  against an independent reference implementation** the way EMA and ATR were. They follow
  standard textbook formulations (Wilder smoothing where applicable), but if you need
  bit-for-bit parity with a specific charting platform, cross-check independently — ADX in
  particular has multiple documented variants in practice.
- **The Phase 3 backtest harness is intentionally minimal**: one open trade at a time, next-
  bar-open fills, no brokerage/slippage/lot-size modeling, and a conservative same-bar
  stop-and-target-both-hit tiebreak (assumes the worse outcome, since OHLC data alone can't
  tell you the intrabar sequence). Phase 7's `PaperExecutor` will plug a more realistic fill
  model into this same walk-forward core rather than replacing it.
- This is a personal paper-trading tool, not a production system. Security hardening,
  secrets management, and regulatory compliance are your responsibility if you extend this
  toward live use.
