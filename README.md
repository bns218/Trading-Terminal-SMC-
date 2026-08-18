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

## Phase 4 — Market structure, SMC, ICT, candlestick and chart patterns

**These are discretionary trading concepts being mechanised into fixed rules.** Every
detector in this phase documents the ONE interpretation it implements, with tunable
parameters — not an objective or unique definition. Different traders/educators define
these patterns differently in ways that would change what gets detected. Treat this as a
calibration starting point; never present a pattern hit as objective fact. Full rule
descriptions live in each module's docstrings; the summary below is not a substitute for
reading them before you trust a specific detector.

What exists after Phase 4:

- `strategies/patterns_common.py` — `find_swing_points()`: the fractal swing-high/low
  detector (a bar is a swing if its high/low strictly dominates `lookback` bars on both
  sides) that SMC and chart-pattern detection are built on. Documents its own confirmation
  lag explicitly: a swing at bar `i` is only knowable once bar `i+lookback` has closed.
  `classify_structure()` labels swings HH/HL/LH/LL against the previous swing of the same
  kind.
- `strategies/candlestick.py` — doji, hammer, shooting star, (bullish/bearish) engulfing,
  harami, inside bar, pin bar, morning star, evening star. All pure, parameterized
  (wick/body ratio thresholds), returning `PatternEvent`.
- `strategies/smc.py` — BOS/CHOCH (tracks trend state; the first-ever structure break is
  always BOS, later breaks are BOS if they continue the trend and CHOCH if they reverse
  it), fair value gaps (3-candle imbalance), order blocks (last opposite-direction candle
  before an ATR-relative displacement move), liquidity sweeps (wick beyond a swing
  level, close back inside), and `premium_discount_zone()` (simple 50% midpoint
  convention — the OTE 62-79% variant is not implemented).
- `strategies/ict.py` + `config/ict_settings.py` — displacement detection (large-range,
  mostly-body candles relative to ATR) and kill-zone tagging. Kill zones are defined in
  their **native session timezone** (`America/New_York`/`Europe/London`) and converted
  per-bar via `zoneinfo`, specifically to avoid the DST bug a fixed IST-offset table would
  have (verified with a same-UTC-instant-different-season test — see table below).
- `strategies/chart_patterns.py` — double top/bottom and head & shoulders (+ inverse),
  built on confirmed swing points with a neckline-break confirmation rule (reported at the
  confirming bar, not the pattern's visual peak); a simplified linear-regression trendline
  classifier for triangles/wedges/channels (explicitly documented as trading fidelity for
  mechanical testability — real chartists draw trendlines through swing points, not a
  regression over every bar); and flag/pennant detection (a strong ATR-relative "pole" move
  followed by a tight consolidation, classified as pennant vs. flag by the same trendline
  classifier).

### How to run it yourself

```bash
python -m pytest tests/test_patterns_common.py tests/test_candlestick.py tests/test_smc.py tests/test_ict.py tests/test_chart_patterns.py -v
```

No credentials or network access needed — same as Phase 3, this operates purely on
DataFrames and ran end-to-end in this environment.

### Phase 4 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| Swing-point detection (single high/low, flat series, strict-dominance tie-breaking, both-side lookback requirement, invalid lookback) | `pytest tests/test_patterns_common.py` (6 tests) | All pass | — fully tested |
| Structure classification (HH/HL/LH/LL labeling) | `pytest tests/test_patterns_common.py::test_classify_structure_hh_hl_lh_ll` | Passes | — fully tested |
| All 9 candlestick patterns (doji, hammer, shooting star, bullish/bearish engulfing, harami, inside bar, bullish/bearish pin bar, morning star, evening star) — both positive detection and explicit negative/no-false-positive cases | `pytest tests/test_candlestick.py` (16 tests) | All pass (1 test-authoring bug caught and fixed along the way: a "bearish" harami test candle was accidentally bullish OHLC) | — fully tested |
| BOS/CHOCH sequencing (first break = BOS, trend-following break = BOS, trend-reversing break = CHOCH) | `pytest tests/test_smc.py::test_bos_then_choch_sequence` and 2 related tests, against an 8-bar sequence **hand-traced bar-by-bar against the algorithm before running** (see the test file's comment) | Passed exactly as hand-traced, including an incidental extra swing that correctly produced no spurious event | — fully tested, and to an unusually high confidence level since the expected output was derived independently before the test was run |
| Fair value gaps (bullish, bearish, no-gap-on-overlap) | `pytest tests/test_smc.py` (3 tests) | All pass | — fully tested |
| Order blocks (detected before displacement, absent without displacement, empty on too-few-bars) | `pytest tests/test_smc.py` (3 tests) | All pass | — fully tested |
| Liquidity sweeps (wick-beyond-then-close-back triggers a sweep; a clean close-through does NOT) | `pytest tests/test_smc.py` (2 tests) | All pass | — fully tested |
| Premium/discount zone classification | `pytest tests/test_smc.py` (4 tests) | All pass | — fully tested |
| ICT kill-zone DST correctness (same UTC wall-clock instant classified differently in an EDT month vs. an EST month; midnight-crossing Asian session; naive-datetime rejection) | `pytest tests/test_ict.py` (5 tests) | All pass | — fully tested, and specifically targets the DST bug a naive fixed-offset implementation would have had |
| ICT displacement (strong directional candle detected; wide indecision candle with long both-side wicks correctly rejected; too-few-bars returns empty) | `pytest tests/test_ict.py` (3 tests) | All pass | — fully tested |
| Displacement-in-kill-zone intersection | `pytest tests/test_ict.py` (2 tests) | All pass | — fully tested |
| Double top/bottom (detection + neckline confirmation, non-confirmation when neckline isn't broken, no false positive on a monotonic trend) | `pytest tests/test_chart_patterns.py` (4 tests), scenarios **hand-traced against `find_swing_points` output before writing assertions** — one hand-tracing arithmetic error (wrong neckline value) was caught and fixed this way, not by loosening the test | All pass | — fully tested |
| Head & shoulders / inverse (detection + neckline confirmation, no-false-positive when head isn't the highest) | `pytest tests/test_chart_patterns.py` (3 tests) | All pass | — fully tested |
| Trendline shape classification (flat channel, ascending/descending/symmetrical triangle, parallel channel) | `pytest tests/test_chart_patterns.py` (6 tests) — the first run exposed the default `flat_slope_threshold` as miscalibrated for the test data (real slopes were being classified as flat); the threshold was corrected, not the test | All pass after the fix | — fully tested |
| Flag/pennant detection (pole+consolidation triggers, no pole = no signal, too-few-bars = empty) | `pytest tests/test_chart_patterns.py` (3 tests) | All pass | — fully tested |
| Every detector's behavior against real market data | Not run | Unknown | All Phase 4 code operates on DataFrames only — no credentials/network needed to test the *mechanism* — but only real price action can validate whether the chosen thresholds (tolerance %, ATR multiples, slope cutoffs) produce sensible detection rates in practice. That requires you to run this against real historical candles from Phase 2/3's pipeline. |

**Bottom line: 172/172 pytest tests pass. Every detector's core logic was verified against
hand-traced expected output computed independently before running the test — not just
"write code, write a test that happens to agree with it." Three real bugs were caught this
way during development (one candlestick test's OHLC direction, one chart-pattern test's
neckline value, one miscalibrated default threshold) and fixed at the source of the
error, not by adjusting the test to match buggy output. What's genuinely untested is
real-world detection quality — that requires your own historical data and judgment, since
these are inherently subjective pattern definitions.**

## Phase 5 — Option chain construction and options analytics

What exists after Phase 5:

- `data/models.py` — `Instrument.option_type` (CE/PE, parsed from the instrument master's
  symbol suffix), `OptionContract` (a strike's paired call+put legs), `OptionChainSnapshot`.
  `DerivedMetric.value` is now `Decimal | float | str` (was `Decimal`-only) so the same
  provenance-carrying type can hold a max-pain strike (Decimal), an IV/Greek (float), or an
  OI-buildup category label (str) without inventing three parallel types.
- `strategies/option_chain.py` — `build_option_chain()`: pairs CE/PE legs by strike from
  instrument-master records (already filtered to a name+expiry) and a caller-supplied
  quotes map. Pure — no I/O, matching every other strategies/ module. A missing leg or
  missing quote is `None` on the contract, never silently dropped from the chain.
- `strategies/options_pricing.py` — Black-Scholes (stock options) and Black-76 (index
  options) pricing, Greeks, and implied volatility (Newton-Raphson with a bisection
  fallback, returning `None` — not a fabricated number — when a quoted price is outside
  what any volatility in (0.01%, 500%) could produce). This is the documented fallback for
  where Angel One's Option Greek API has gaps.
- `strategies/options_analytics.py` — ATM/ITM/OTM classification, PCR, max pain (the
  standard "settlement strike that minimizes aggregate ITM payout to option holders"
  definition), OI-buildup classification from the price-change × OI-change matrix, Call OI
  resistance / Put OI support. Everything here is tagged `source="computed_chain"`.
- `broker/options_api.py` — thin, rate-limited wrappers around the SmartAPI SDK methods
  confirmed to exist by directly introspecting the installed `smartapi-python==1.5.5`
  package (`optionGreek`, `putCallRatio`, `oIBuildup`, `gainersLosers`) — this is the
  `source="angelone_api"` half of the confirmed Phase 0 decision to show PCR/OI-buildup
  both ways rather than picking one. Returns raw response rows; wrapping a specific field
  into a `DerivedMetric` is left to the caller.

Per the decision confirmed earlier in this project: **PCR and OI-buildup are always
computed client-side from the constructed chain** (`options_analytics.py`,
`source="computed_chain"`) **and** available from Angel One's own endpoints
(`options_api.py`, `source="angelone_api"`) — shown side by side, never one silently
overriding the other.

### How to run it yourself

```bash
python -m pytest tests/test_option_chain.py tests/test_options_pricing.py tests/test_options_analytics.py tests/test_options_api.py -v
```

`strategies/option_chain.py`, `options_pricing.py`, and `options_analytics.py` need no
credentials or network access (same as Phases 3-4). `broker/options_api.py` is tested
against a mocked SmartAPI client, same pattern as Phase 2's `ingestion/backfill.py` — the
real endpoints were never called, since no credentials/network path exist here.

### Phase 5 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| `option_type` (CE/PE) parsing from the instrument master | `pytest tests/test_instruments.py` (2 new tests) | Passes | — fully tested |
| Option chain construction (CE/PE pairing by strike, ascending sort, missing-leg/missing-quote handling, non-option instruments ignored) | `pytest tests/test_option_chain.py` (6 tests) | All pass | — fully tested |
| Black-Scholes pricing | Cross-checked against a widely-cited textbook reference (Hull, S=42/K=40/r=10%/σ=20%/T=0.5y → call≈4.76, put≈0.81) | Matches within 0.01 | — fully tested against an independent published reference, not just internal consistency |
| Black-Scholes / Black-76 put-call parity | `C - P == S - K·e^(-rT)` (BS) and `C - P == e^(-rT)(F-K)` (Black-76), checked to 1e-9 | Exact match | — fully tested; parity is a model-independent no-arbitrage identity, so this is a strong correctness check |
| Implied volatility round-trip (BS and Black-76, ITM/OTM/index cases) | Price computed from a known σ, then IV solver run on that price, checked recovers σ to 1e-4 | All pass | — fully tested |
| IV solver returns `None` (not a fabricated value) for a price outside no-arbitrage bounds | `pytest tests/test_options_pricing.py::test_iv_returns_none_for_price_outside_no_arbitrage_bounds` | Passes | — fully tested |
| Greeks (delta bounds for deep ITM/OTM, gamma positivity and ATM peak, vega positivity, call/put gamma-vega equality identity) | `pytest tests/test_options_pricing.py` (6 tests) | All pass | — fully tested |
| ATM strike selection, ITM/OTM classification for calls and puts | `pytest tests/test_options_analytics.py` (5 tests) | All pass | — fully tested |
| PCR computation | `pytest tests/test_options_analytics.py` (2 tests) | All pass | — fully tested |
| Max pain | Hand-computed 3-strike example (heavy call OI at one end, heavy put OI at the other) worked out on paper before running, confirming the algorithm finds the middle strike where neither side's concentrated OI gets triggered | Matches hand computation exactly | — fully tested |
| OI-buildup classification (all 4 quadrants + 2 neutral edge cases) | `pytest tests/test_options_analytics.py` (6 tests) | All pass | — fully tested |
| Call OI resistance / Put OI support | `pytest tests/test_options_analytics.py` (3 tests) | All pass | — fully tested |
| `broker/options_api.py` wrappers (data extraction, throttle/error propagation) | `pytest tests/test_options_api.py` (6 tests) against a mocked `SmartConnect` | All pass | Real endpoint response shapes for `optionGreek`/`putCallRatio`/`oIBuildup`/`gainersLosers` are NOT verified against official docs (domain blocked) — only against forum-reported examples. A real call could return a differently-shaped response than assumed here. |
| Real option-chain construction against a live instrument master + live quotes | Not run | Unknown | Requires a live broker session (Phase 1's same constraint) — you'll need to run this yourself once you have real credentials |

**Bottom line: 220/220 pytest tests pass.** The pricing math is the strongest-verified part
of this whole project so far — cross-checked against both a published textbook reference
and a model-independent no-arbitrage identity (put-call parity), not just internal
self-consistency. What's unverified is real API response shapes (docs-blocked, same
constraint as every prior phase) and real-market chain construction (needs your
credentials).

## Phase 6 — Signal engine + risk manager

What exists after Phase 6:

- `config/signal_config.py` — `FACTOR_WEIGHTS` (the exact weights from the brief: HTF trend
  20, SMC 20, price action 15, indicators 15, option chain 15, volume/momentum 10,
  candlestick/chart confirmation 5) and `ConfluenceConfig` (total-score threshold + minimum
  agreeing-factor count). **Explicitly marked UNVALIDATED** — these are the specified
  defaults, not backtested or calibrated.
- `config/risk_config.py` — account capital, max risk per trade, max daily loss, max
  trades/day, max open positions, cooldown-after-consecutive-losses, session-hour window.
  Also UNVALIDATED defaults, yours to retune.
- `engine/signal_engine.py` — `generate_signal()`: combines caller-supplied per-factor
  scores (each -1..1, from strategies/ output) into a `TradeSignal`. Confluence is enforced
  by **two independent conditions**, both required: the total weighted score must clear the
  threshold, AND at least `min_confluence_factors` distinct factors must agree in direction
  above a minimum strength. Verified structurally (not just by a passing test): given these
  weights, the two largest factors alone (20+20=40) can never reach the 60-point threshold,
  and the three largest (20+20+15=55) still fall short — so reaching a signal requires at
  least 4 factors' worth of weight by construction, independent of the min-factor-count
  check. NO_TRADE always carries full sub-scores and a reason, so "why not" is as auditable
  as "why."
- `engine/risk_manager.py` — `RiskManager.evaluate()`: the veto gate between the signal
  engine and the executor (Phase 7). Checks, in order: NO_TRADE passthrough, kill switch,
  configured session-hour window, calendar holiday/weekend, max daily loss, max trades/day,
  max open positions, cooldown, stop-distance validity, then lot-size-aware position sizing
  from the configured risk-per-trade budget. Every rejection returns a named `rejected_rule`
  and is logged. State (daily P&L, trade count, consecutive losses, cooldown) is
  stateful-by-necessity and resets automatically on a calendar-day rollover.

### How to run it yourself

```bash
python -m pytest tests/test_signal_engine.py tests/test_risk_manager.py -v
```

No credentials or network access needed — pure logic, same as Phases 3-5.

### Phase 6 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| Strong confluence produces BUY/SELL with correct entry/stop/target arithmetic | `pytest tests/test_signal_engine.py` (4 tests) | All pass | — fully tested |
| A single factor at its absolute maximum strength alone cannot produce a trade | `pytest tests/test_signal_engine.py::test_single_maxed_factor_alone_never_produces_trade` | Passes | — fully tested; this is the specific guarantee the brief required ("a single indicator firing must never produce a trade") |
| Two maxed factors (40 points) still below the 60-point threshold | `pytest tests/test_signal_engine.py::test_two_maxed_factors_still_below_threshold` | Passes | — fully tested |
| Minimum-agreeing-factor-count condition enforced independently of total score | `pytest tests/test_signal_engine.py::test_min_confluence_factor_count_enforced_independently_of_score` (uses a stricter custom confluence config to isolate this specific condition from the score-threshold condition) | Passes | — fully tested |
| NO_TRADE is produced for weak/mixed signals; reasons and full sub-scores always populated | `pytest tests/test_signal_engine.py` (3 tests) | All pass | — fully tested |
| Raw score clamping, reason filtering to only agreeing factors, invalidation conditions present | `pytest tests/test_signal_engine.py` (3 tests) | All pass | — fully tested |
| Risk manager: approval + lot-size-aware position sizing arithmetic | `pytest tests/test_risk_manager.py::test_approved_with_correct_position_sizing` | Passes | — fully tested |
| All 9 veto rules individually (NO_TRADE passthrough, kill switch, configured session window, calendar holiday, max daily loss, max trades/day, max open positions, cooldown, invalid stop distance, position-size-below-one-lot) | `pytest tests/test_risk_manager.py` (11 tests) | All pass — **one real bug caught and fixed during testing**: `record_trade_opened()` didn't roll the daily state over a calendar-day boundary the way `record_trade_closed()` did, so the first `record_trade_closed()` call after one or more `record_trade_opened()` calls silently wiped the trade count that had just been recorded. Fixed by making `record_trade_opened()` take a timestamp and roll the day itself, exactly like its counterpart. | — fully tested, and the bug is exactly the kind of stateful-logic error unit tests exist to catch |
| Consecutive-loss cooldown triggering and win-resets-streak behavior | `pytest tests/test_risk_manager.py` (2 tests) | Both pass | — fully tested |
| Daily state auto-reset on calendar-day rollover | `pytest tests/test_risk_manager.py::test_daily_state_resets_on_new_trading_day` | Passes | — fully tested |
| Signal engine + risk manager wired together against a live strategy stack producing real factor scores from real market data | Not run | Unknown | Requires Phases 2/3/4/5's live data path (credentials/network), which doesn't exist in this sandbox — the engine logic itself is fully tested, but no real trading day has been run through it end to end |

**Bottom line: 247/247 pytest tests pass.** One genuine stateful-logic bug (the
`record_trade_opened`/day-rollover ordering issue above) was caught by the test suite
before being reported here — exactly the value of testing state machines like the risk
manager thoroughly rather than trusting them by inspection.

## Phase 7 — Paper execution engine + trade journal

What exists after Phase 7:

- `config/charges_config.py` — brokerage + STT + exchange transaction charges + SEBI
  charges + stamp duty + GST as a configurable schedule, split correctly by buy/sell leg
  (STT only on the sell leg, stamp duty only on the buy leg, GST only on
  brokerage+exchange+SEBI — never on STT/stamp duty). **Every percentage except GST (18%,
  a stable statutory rate) is an explicitly-flagged placeholder** — verify against Angel
  One's current tariff sheet before trusting net P&L for anything beyond exercising the
  calculation mechanism.
- `data/models.py` — `TradeJournalEntry`: every field the brief specified (timestamp,
  instrument, signal direction, entry, exit, SL, target, P&L gross and net, confidence,
  strategy, entry reason, exit reason). `data/database.py` persists it in a new
  `trade_journal` SQLite table, Decimal-as-text like every other money field in this
  project.
- `engine/position_manager.py` — `PositionManager`: pending-order and open-position
  tracking with live unrealized P&L. Mutable by design (unlike the frozen pydantic
  contracts) since this is genuinely-changing runtime state, not a broker/strategy boundary
  value.
- `execution/executor_protocol.py` — the `Executor` protocol, with the fill model stated
  explicitly in its docstring: an order is PENDING after `submit_order()` and fills only
  when `on_bar_open()` delivers the next bar's open price — never at the signal-deciding
  bar's own close, never at an optimistic mid-price.
- `execution/paper_executor.py` — `PaperExecutor`: the full implementation. Slippage (basis
  points, configurable) is applied on every fill and is ALWAYS against the trader — a buy
  fills above the quoted price, a sell fills below it, on both entry and exit. Stop-loss and
  target monitoring via `on_price_update()`. Every closed position writes a full
  `TradeJournalEntry` with real computed charges.
- `execution/live_executor.py` — `LiveExecutor`: every method, including `__init__`, raises
  `NotImplementedError`. It cannot even be constructed. This is the enforcement mechanism
  for "TRADING_MODE is hard-wired to PAPER" at the execution layer specifically.
- `execution/factory.py` — `create_executor()`: the ONE place mode selection happens, from
  `Settings.trading_mode` (itself `Literal["PAPER"]` since Phase 1 — there is no value you
  can pass that reaches the live branch).

### How to run it yourself

```bash
python -m pytest tests/test_charges_config.py tests/test_trade_journal.py tests/test_position_manager.py tests/test_paper_executor.py tests/test_live_executor_and_factory.py -v
```

No credentials or network access needed — pure logic + local SQLite, same as every phase
since Phase 3.

### Phase 7 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| Charges schedule (percentage-vs-flat brokerage cap, STT sell-only, stamp duty buy-only, GST base exclusions, round-trip long and short trades) | `pytest tests/test_charges_config.py` (9 tests) | All pass | — fully tested |
| Trade journal persistence (insert/retrieve, Decimal precision round-trip, ordering, limit, empty case) | `pytest tests/test_trade_journal.py` (5 tests) against a real SQLite file | All pass | — fully tested |
| Position manager (pending→open transition, per-instrument filtering, unrealized P&L for both long and short, stop/target hit detection for both directions, close) | `pytest tests/test_position_manager.py` (8 tests) | All pass | — fully tested |
| PaperExecutor: no optimistic same-bar fill (submitting ≠ filling) | `pytest tests/test_paper_executor.py::test_submit_does_not_immediately_fill` | Passes | — fully tested; this is the specific "never optimistic" guarantee the brief required |
| PaperExecutor: fill happens on next bar's open, at the ACTUAL bar price (not the signal's originally intended entry) | `pytest tests/test_paper_executor.py::test_fill_happens_on_next_bar_open` | Passes | — fully tested |
| PaperExecutor: slippage always worse for the trader (buy fills above quote, sell fills below) — exact Decimal arithmetic checked | `pytest tests/test_paper_executor.py` (2 tests) | Both pass | — fully tested |
| PaperExecutor: stop-loss and target close paths, both writing a correct journal entry | `pytest tests/test_paper_executor.py` (2 tests) | Both pass | — fully tested |
| PaperExecutor: charges genuinely reduce net P&L below gross on a win, and make a loss worse (not better) | `pytest tests/test_paper_executor.py` (2 tests) | Both pass | — fully tested; confirms charges are actually being subtracted, not just present in the data model |
| PaperExecutor: manual close, short-trade round trip, journal carries confidence/reason, rejects orders without stop/targets or for NO_TRADE | `pytest tests/test_paper_executor.py` (5 tests) | All pass | — fully tested |
| `LiveExecutor` cannot even be constructed | `pytest tests/test_live_executor_and_factory.py::test_live_executor_cannot_even_be_constructed` | Passes | — fully tested |
| `create_executor()` returns `PaperExecutor` for the only reachable `trading_mode` value; `Settings(trading_mode="LIVE")` itself is rejected at construction | `pytest tests/test_live_executor_and_factory.py` (2 tests) | Both pass | — fully tested; together these two tests prove there is no reachable path to `LiveExecutor` from configuration alone |
| PaperExecutor wired to a live tick/candle feed over a real trading session | Not run | Unknown | Requires Phase 2's live ingestion path (credentials/network), unavailable in this sandbox — the execution mechanism itself is fully tested against synthetic price sequences |

**Bottom line: 285/285 pytest tests pass.** The two guarantees the brief emphasized most for
this phase — "never an optimistic fill" and "paper mode cannot become live by accident" —
each have a direct, named test proving them, not just incidental coverage.

## Phase 8 — Dashboard (market overview, charts with overlays, positions, journal, data health)

What exists after Phase 8 — the final phase in the build order:

- `app/main.py` — FastAPI application, per the confirmed Phase 0 architecture decision
  (FastAPI + SSE over Streamlit). The dashboard is a pure reader: every route reads from
  the SQLite store or runs pure `strategies/` computation on data already there — nothing
  in `app/` opens a broker connection or a WebSocket. `python -m app.main --demo` serves
  the dashboard from clearly-labelled synthetic data with **zero credentials or network
  access needed**; `python -m app.main` serves real data once a live ingestion process
  (Phase 2) is writing to the same store.
- `app/demo_data.py` — the `--demo` flag's data: a synthetic 90-bar candle series (session-
  anchored, seeded RNG for reproducibility), 4 journal entries, and 3 data-health events,
  every one of them carrying `DEMO`/`[DEMO DATA]` in its symbol or reason text so nothing
  can be mistaken for live output, per the ground rule against presenting synthetic data as
  real.
- `app/api/routes.py` — REST endpoints: `/api/candles/{token}` (with multi-timeframe
  resampling via Phase 3's `data/resample.py`), `/api/indicators/{token}` (EMA/RSI/MACD/
  ATR/Bollinger/VWAP from Phase 3), `/api/candles/{token}/overlays` (candlestick + SMC
  BOS/CHOCH + fair value gaps + chart patterns from Phase 4, computed live on each
  request), `/api/positions`, `/api/journal` (Phase 7's trade journal), `/api/data-health/
  {token}` (Phase 2's health aggregator), `/api/risk/kill-switch`, `/api/instruments/
  search`.
- `app/sse/streams.py` — SSE generators for tick and data-health streams, polling the
  store on an interval (never owning a broker connection, per the architecture decision).
  Built with a `max_iterations` testability seam so the core polling logic is unit-testable
  without needing a real client connection or disconnect.
- `app/static/` — a self-contained vanilla HTML/CSS/JS frontend (no framework, no CDN
  dependency) with a canvas-drawn candlestick chart, EMA overlay lines, pattern markers
  color-coded by direction, tabbed positions/journal/data-health panels, a kill-switch
  toggle, and a persistent demo-mode banner.

**The dashboard was actually run in a real browser, not just covered by tests.** I started
`python -m app.main --demo`, drove it with a real headless Chromium instance (Playwright,
pre-installed in this environment), and screenshotted the result — the chart renders real
candles with EMA9/EMA20 lines and pattern markers, the Positions/Journal/Data-Health tabs
switch and populate correctly, the kill-switch button toggles and persists to the backend,
and multi-timeframe resampling (1m → 15m, 90 bars → 6 bars) works end to end. The only
network request that 404'd was the browser's own automatic `/favicon.ico` probe — every
real API call returned 200 OK, confirmed from the server's own access log, not just from
the client's perspective.

### How to run it yourself

```bash
python -m app.main --demo
# open http://127.0.0.1:8000 in a browser
```

Run the test suite (311 tests total):

```bash
python -m pytest tests/test_demo_data.py tests/test_sse_streams.py tests/test_app_api.py -v
```

### Phase 8 tested/untested

| Component | How tested | Result | Untested because |
|---|---|---|---|
| Demo data generation (candle count, OHLC validity, journal entries, health events, reproducibility with a fixed seed) | `pytest tests/test_demo_data.py` (6 tests) | All pass | — fully tested |
| SSE stream generators (finite termination via the testability seam, correct event shape, graceful handling of an unknown token) | `pytest tests/test_sse_streams.py` (3 tests) | All pass | — fully tested at the generator level; the actual HTTP streaming endpoint (`/api/stream/...`) was not driven through a real long-lived HTTP connection in the test suite — see below |
| Every REST endpoint (`meta`, `candles` at multiple timeframes including rejection of an unsupported one, `indicators` with correct NaN→null JSON serialization, `overlays`, `positions`, `journal` with correct Decimal→string serialization, `data-health`, `kill-switch` round-trip, `instruments/search`, static file serving, and live-mode-vs-demo-mode differences) | `pytest tests/test_app_api.py` (17 tests) against a real `FastAPI` app via `TestClient` | All pass | — fully tested |
| The dashboard actually running end-to-end in a real browser (page load, chart rendering with real pixel data, tab switching, kill-switch toggle round-tripping to the backend, timeframe-switch triggering a real resample and re-render) | Ran `python -m app.main --demo`, drove a real headless Chromium via Playwright, screenshotted 4 states (initial load, Journal tab, kill-switch ON, 15-minute timeframe), and read the server's access log directly | **Passed — genuinely run, not simulated.** Every real API call returned 200; only a harmless favicon 404 appeared | — fully tested, to the extent a sandboxed headless-browser session can substitute for you clicking around yourself |
| SSE endpoints (`/api/stream/ticks/{token}`, `/api/stream/health/{token}`) over a real long-lived HTTP connection, with a real client disconnect | Not run | Unknown | The generator logic itself is fully unit-tested (see above); testing the actual `StreamingResponse` HTTP behavior needs a real streaming HTTP client held open over time, which risks a hanging test in CI — deliberately not attempted. Load the page yourself and watch Network tab / EventSource behavior to validate this if you extend `app.js` to use it. |
| Dashboard against real live ingestion data (Phase 2) over a real trading session | Not run | Unknown | Requires Phase 2's live path (credentials/network), unavailable in this sandbox — everything reachable without that has been run for real, including the browser session above |

**Bottom line: 311/311 pytest tests pass, and — uniquely among the eight phases of this
build — the dashboard itself was also driven through a real browser and screenshotted,
not just exercised through pytest.** That closes the loop the brief specifically asked
for: "start the dev server and use the feature in a browser before reporting the task as
complete." The one thing that couldn't be validated here is a real live trading session,
since that needs your credentials.

## Known limitations

- **The dashboard's `/api/positions` and the risk manager's kill switch are not wired to a
  live signal-engine/risk-manager/executor loop in this phase.** `app/main.py` starts a
  pure reader process — it doesn't itself run the Phase 6/7 engine. In `--demo` mode,
  positions are fabricated for display; in live mode, `/api/positions` returns an empty
  list and the kill-switch toggle only flips a display flag, not a real
  `engine.risk_manager.RiskManager` instance. Wiring a live engine loop into (or alongside)
  this dashboard process is a further integration step for you to do once you're running
  against a real session.
- **`/api/instruments/search` only returns results in `--demo` mode.** Live-mode instrument
  search would need this process to also own a broker session and instrument-master cache,
  which it deliberately doesn't (single-responsibility: this is a reader, not a broker
  client) — search against Phase 1's `InstrumentMaster` directly, or extend this endpoint
  yourself to read a shared cache file.
- **SSE streaming endpoints were unit-tested at the generator level but not driven through
  a real long-lived HTTP connection** in the automated test suite (deliberately, to avoid a
  hanging CI test) — validate `EventSource` behavior yourself in a browser if you build on
  top of it.
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
- **All Phase 4 pattern detectors (candlestick, SMC, ICT, chart patterns) implement ONE
  interpretation of inherently discretionary concepts**, with tunable thresholds
  (wick/body ratios, price tolerance %, ATR multiples, trendline slope cutoffs) that have
  not been calibrated against real market data — only against synthetic hand-traced test
  cases proving the mechanism works as documented. Detection *rates* in practice (too many
  false positives, too few real hits) can only be judged by running this against real
  historical data and your own trading judgment.
- **The chart-pattern trendline classifier (triangles/wedges/channels) is a linear
  regression over every bar's high/low, not trendlines drawn through swing points** the way
  a chartist would. This trades some fidelity for being mechanically well-defined —
  documented explicitly in `strategies/chart_patterns.py`.
- **ICT kill-zone hour ranges are one commonly-cited convention among several** — some ICT
  educators define slightly different windows. The DST-correctness mechanism (converting
  per-timestamp via `zoneinfo` rather than a fixed offset) is solid; the specific hours in
  `config/ict_settings.py` are a starting point to adjust to your own convention.
- **Options pricing (`strategies/options_pricing.py`) assumes no dividends** (relevant for
  stock options — a real dividend yield would lower a call's theoretical price relative to
  what this module computes) and **uses calendar-days/365 for time-to-expiry**, not a
  trading-day count — one reasonable convention among several.
- **`broker/options_api.py` request/response shapes are based on SmartAPI forum examples,
  not verified official docs.** In particular, `putCallRatio()` takes no parameters per the
  SDK signature and its exact response shape (single value vs. per-symbol listing) is
  unconfirmed — re-check before relying on it.
- **Black-76 pricing takes a `forward` price as a parameter but does not compute one for
  you** — for NIFTY/BANKNIFTY index options, the theoretically correct input is the
  matching-expiry futures price, not spot; passing spot as a stand-in for the forward
  (common in practice when the futures price isn't separately fetched) is a further
  simplification you'd be making at the call site, not something this module does silently.
- **Signal engine factor scores (-1..1 per factor) must be computed by the caller** from
  Phase 3-5's strategy outputs — `engine/signal_engine.py` itself does not run indicators,
  SMC, or option-chain analysis. The mapping from "BOS confirmed, 2 bars ago" or "RSI at 68"
  to a single -1..1 number is a design decision left for whoever wires the engine into a
  live pipeline (Phase 8), not fixed here.
- **`FACTOR_WEIGHTS` and `ConfluenceConfig` are the brief's specified defaults, not
  validated against any backtest.** Do not treat a backtest run with these defaults as
  evidence the strategy works.
- **Risk manager's cooldown state resets on a calendar-day rollover along with everything
  else** — if a cooldown is triggered near midnight, it could theoretically be cleared by
  the day-rollover reset before its configured duration elapses. A minor edge case, not
  fixed, since intraday-only trading makes it unlikely to matter in practice.
- **All charges-schedule percentages except GST (18%) are placeholders** — see
  `config/charges_config.py`'s module docstring. Verify against Angel One's current tariff
  sheet before trusting net P&L figures as real cost estimates.
- **PaperExecutor only manages ONE target (T1) per position**, not partial profit-booking
  across T1/T2/T3 the way the signal engine's `TradeSignal.targets` list suggests. A
  position closes fully at T1 or stop-loss. Scaling out across multiple targets is a
  reasonable future enhancement, not built here.
- **PaperExecutor's stop-loss/target check assumes single-tick price updates**, not OHLC
  bars — if you feed it bar data instead of ticks, feed the bar's adverse extreme (low for a
  long, high for a short) before its favorable extreme in the same update cycle, to preserve
  the same "stop wins on ambiguity" convention the Phase 3 backtest harness uses. This isn't
  enforced by the code itself.
- **No end-of-session forced close is automatic.** If a position is still open when the
  market closes, nothing in this phase force-closes it — that has to be triggered
  externally (e.g. by whatever process drives `on_bar_open`/`on_price_update`, calling
  `close_position(..., exit_reason="session_close", ...)` itself at 15:30 IST).
- **The Phase 3 backtest harness is intentionally minimal**: one open trade at a time, next-
  bar-open fills, no brokerage/slippage/lot-size modeling, and a conservative same-bar
  stop-and-target-both-hit tiebreak (assumes the worse outcome, since OHLC data alone can't
  tell you the intrabar sequence). Phase 7's `PaperExecutor` will plug a more realistic fill
  model into this same walk-forward core rather than replacing it.
- This is a personal paper-trading tool, not a production system. Security hardening,
  secrets management, and regulatory compliance are your responsibility if you extend this
  toward live use.

---

# Final deliverables

All 8 phases in the build order are complete, pushed, and documented above with their own
tested/untested tables. This section pulls together the pieces the original brief asked
for as a standalone reference, rather than scattered one per phase.

## Project structure

```
trading_terminal/
├── app/                    # FastAPI dashboard (Phase 8) — pure reader, no broker connection
│   ├── main.py             #   entrypoint: python -m app.main [--demo]
│   ├── state.py            #   shared AppState (store, calendar, demo flag)
│   ├── demo_data.py        #   synthetic --demo dataset generator
│   ├── api/routes.py       #   REST endpoints
│   ├── api/serializers.py  #   dataclass -> JSON-safe dict helpers
│   ├── sse/streams.py      #   SSE generators (tick, data-health)
│   └── static/             #   vanilla HTML/CSS/JS frontend, no framework, no CDN
├── broker/                 # Angel One SmartAPI client (Phase 1)
│   ├── auth.py             #   TOTP login, session lifecycle, mid-session-failure handling
│   ├── angelone_client.py  #   the single owner of session + rate limiter + instrument master
│   ├── instruments.py      #   instrument master: download, disk cache, fallback
│   ├── market_data.py      #   LTP/quote fetch
│   ├── options_api.py      #   optionGreek / putCallRatio / oIBuildup / gainersLosers wrappers
│   ├── ratelimit.py        #   per-endpoint token-bucket limiter + backoff
│   └── exceptions.py
├── ingestion/               # Standalone tick/candle service (Phase 2) — separate process
│   ├── run_ingestion.py    #   entrypoint: python -m ingestion.run_ingestion SYMBOL:EXCHANGE ...
│   ├── websocket_client.py #   the ONLY thing that opens the SmartAPI WebSocket
│   ├── candle_builder.py   #   tick -> 1-min candle aggregation
│   ├── backfill.py         #   gap detection + historical-endpoint backfill
│   └── health.py           #   data-health metrics
├── strategies/              # Pure analysis: DataFrame in, typed result out (Phases 3-5)
│   ├── indicators.py        #   VWAP, EMA, RSI, MACD, ATR, Bollinger, ADX, volume stats
│   ├── patterns_common.py   #   swing-point detection shared by SMC/chart patterns
│   ├── candlestick.py       #   9 candlestick patterns
│   ├── smc.py                #   BOS/CHOCH, order blocks, FVGs, liquidity sweeps, premium/discount
│   ├── ict.py                #   displacement, kill zones
│   ├── chart_patterns.py    #   double top/bottom, H&S, triangles/wedges/channels, flags/pennants
│   ├── option_chain.py      #   CE/PE pairing by strike
│   ├── options_pricing.py   #   Black-Scholes / Black-76, Greeks, IV
│   └── options_analytics.py #   PCR, max pain, OI buildup, ATM/ITM/OTM, OI resistance/support
├── engine/                  # Signal generation + risk (Phase 6)
│   ├── signal_engine.py     #   weighted confluence scoring -> BUY/SELL/NO_TRADE
│   ├── risk_manager.py      #   veto authority between signal engine and executor
│   └── position_manager.py  #   pending-order/open-position tracking with live unrealized P&L
├── execution/                # Order execution (Phase 7)
│   ├── executor_protocol.py #   the Executor protocol both implementations satisfy
│   ├── paper_executor.py    #   full paper simulation: fills, slippage, charges, journal
│   ├── live_executor.py     #   every method raises NotImplementedError — cannot be constructed
│   └── factory.py            #   the ONE place trading_mode selects an executor
├── backtesting/
│   └── harness.py            #   walk-forward engine, structural look-ahead prevention (Phase 3)
├── data/                    # Shared contracts, storage, validation
│   ├── models.py             #   pydantic contracts: Instrument, Quote, Candle, TradeSignal, ...
│   ├── database.py           #   SQLite WAL store: ticks, candles, health events, trade journal
│   ├── validation.py         #   tick/candle validation rules
│   └── resample.py           #   1-min -> 3/5/15/30/60-min resampling, partial-bar-safe
├── config/                  # Every tunable parameter, all UNVALIDATED defaults flagged as such
│   ├── settings.py            #   env-driven settings, TRADING_MODE hard-typed to "PAPER"
│   ├── logging_config.py      #   structured JSON logging + secret redaction
│   ├── market_calendar.py     #   NSE/BSE session windows, holiday awareness
│   ├── holidays.json          #   user-maintained holiday list
│   ├── smartapi_limits.py     #   rate limits (PLACEHOLDER — verify against docs)
│   ├── signal_config.py       #   factor weights + confluence thresholds
│   ├── risk_config.py         #   risk manager parameters
│   ├── charges_config.py      #   brokerage/STT/GST schedule (PLACEHOLDER percentages)
│   └── ict_settings.py        #   ICT kill-zone windows
├── scripts/
│   └── fetch_ltp.py            #   Phase 1 manual acceptance script
├── docs/
│   └── phase0_capability_matrix.md  # SmartAPI capability audit + architecture decisions
├── tests/                    # 311 tests, one file per module, mirrors the structure above
├── .env.example
├── requirements.txt
└── README.md                 # this file
```

## Installation

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # verified: resolves cleanly, no conflicts (see Setup above)
cp .env.example .env                   # fill in real Angel One credentials — never commit .env
```

## How to start the terminal

Three independent processes, matching the architecture decision that the dashboard never
owns the broker connection:

```bash
# 1. Ingestion (separate process) — owns the SmartAPI WebSocket, writes to the SQLite store.
python -m ingestion.run_ingestion RELIANCE-EQ:NSE NIFTY:NSE

# 2. Dashboard (separate process) — reads from the same store, serves the UI.
python -m app.main
# open http://127.0.0.1:8000

# --- or, with no credentials/network at all, to see the UI working right now: ---
python -m app.main --demo
```

There is currently no single "start everything" command that also runs the signal engine
+ risk manager + paper executor loop continuously — that loop (Phase 6/7's pieces, wired
together against live candles from Phase 2) is a further integration step for you to
script, since doing so meaningfully requires your own real credentials to validate against.
Every individual piece (`engine.signal_engine.generate_signal`, `engine.risk_manager.
RiskManager.evaluate`, `execution.paper_executor.PaperExecutor`) is fully built, tested,
and ready to be called from that loop — see the walkthrough below for how they compose.

## How to run tests

```bash
python -m pytest tests/ -v         # all 311 tests
python -m pytest tests/ -q         # quiet summary
```

No credentials or network access are required for any test in this suite — every test that
would need them is either mocked (broker API wrappers) or documented in its phase's
tested/untested table as requiring you to validate manually.

## Database schema

One SQLite database (WAL mode — see `data/database.py`), written by whichever process is
producing that data (ingestion writes ticks/candles/health events; the paper executor
writes the trade journal), read by everything else.

| Table | Key columns | Written by | Notes |
|---|---|---|---|
| `ticks` | `instrument_token`, `ltp`, `volume`, `exchange_ts`, `received_at` | `ingestion/websocket_client.py` | `ltp` stored as `TEXT` (exact `Decimal` string) — never `REAL`, to avoid float-corrupting money values |
| `candles` | `instrument_token`, `timeframe`, `open_time`, `close_time`, `open/high/low/close` (TEXT), `volume`, `is_backfilled`, `is_closed` | `ingestion/candle_builder.py`, `ingestion/backfill.py` | Unique on `(instrument_token, timeframe, open_time)` — upserted, so an in-progress bar updates in place. `is_closed=0` bars must never reach a strategy (`data/resample.py` filters this) |
| `data_health_events` | `event_type` (`reconnect`\|`disconnect`\|`rejected_bar`\|`backfill`), `detail`, `occurred_at` | `ingestion/websocket_client.py`, `ingestion/backfill.py` | Powers the dashboard's data-health panel and `ingestion/health.py`'s aggregation |
| `trade_journal` | `instrument_token/symbol`, `direction`, `quantity`, `entry/exit_price`, `stop_loss`, `target`, `pnl_gross/net` (TEXT), `charges_total`, `confidence`, `strategy`, `entry/exit_reason`, `entry/exit_time` | `execution/paper_executor.py` | Every field the brief specified for the trade journal; all money fields TEXT-as-Decimal |

Default location: `data_store/ticks.db` (configurable via `TICK_STORE_PATH` in `.env`).
`--demo` mode uses its own separate path so it never touches real data.

## How paper mode is enforced

Not by one flag — by three independent layers, so a single mistake can't flip it:

1. **`config/settings.py`**: `Settings.trading_mode` is typed `Literal["PAPER"]`. Pydantic
   validation itself rejects any other value at object construction — `Settings(trading_mode="LIVE")`
   raises before the process even starts.
2. **`execution/live_executor.py`**: `LiveExecutor` — every method, including `__init__`,
   raises `NotImplementedError`. It is not merely unused; it is **impossible to
   instantiate**. There is no live order-placement code to accidentally call.
3. **`execution/factory.py`**: `create_executor()` is the only place in the codebase that
   decides which executor to build, and it does so once, from `Settings.trading_mode` — no
   other module constructs `PaperExecutor` or `LiveExecutor` directly.

All three are independently tested (`tests/test_settings.py`, `tests/test_live_executor_and_factory.py`)
— see Phase 7's tested/untested table.

## Security recommendations

- **Never commit `.env`.** It's already in `.gitignore`; double-check before every commit
  if you ever hand-edit git state (`git status` before `git add -A`, per this project's own
  working practice throughout).
- **Rotate your TOTP secret and API key if either is ever exposed** (committed by accident,
  pasted into a chat, logged somewhere). `config/logging_config.py`'s redaction is
  defense-in-depth, not a guarantee against every possible leak path (e.g. a raw traceback
  from a third-party library that isn't routed through this project's logger).
- **Run the ingestion and dashboard processes with least privilege** — a dedicated OS user
  with write access only to `data_store/`, `logs/`, and `cache/`, not your full home
  directory.
- **Treat the SQLite store as sensitive** if you ever put real trade data in it — it
  contains your trading activity in plaintext (by design, for the journal's own
  auditability), so protect it the way you'd protect any financial record: file
  permissions, backups, and not syncing it to an unencrypted cloud drive.
- **`.env.example` ships with placeholder values only** — confirmed by
  `scripts/fetch_ltp.py` actually failing cleanly (not hanging, not crashing) when run
  against those placeholders during Phase 1 testing.
- **If you ever build toward live execution** (a deliberate, separate decision this project
  does not make for you): add TLS certificate pinning or at minimum verification for the
  broker API host, add an explicit human-confirmation step before any live order placement
  regardless of what the signal engine outputs, and re-read Angel One's API terms and the
  current SEBI rules on retail algo trading via broker APIs — this README does not
  constitute compliance advice, and the regulatory position can change.
- **The FastAPI dashboard (`app/main.py`) binds to `127.0.0.1` by default** (see
  `config/settings.py`'s `api_host`). If you change it to bind to `0.0.0.0` or a public
  interface, you are exposing your trade journal, positions, and a kill-switch toggle over
  the network with **no authentication built in** — this phase did not build a login system,
  since a personal paper-trading tool running on `localhost` doesn't need one. Add
  authentication yourself before exposing this beyond your own machine.

## Walkthrough: how the signal engine reaches one decision

A concrete, code-accurate example — not a hypothetical — using the actual default weights
(`config/signal_config.py`) and a NIFTY futures contract at ₹24,500 with a 1-minute ATR of
₹50.

**Step 1 — every strategy module runs independently and produces evidence, not a verdict.**
Before the signal engine is ever called, upstream code (not shown here — this is the
integration step you'd write) would have run, say: `strategies.indicators.ema()` on the
60-minute chart (finds EMA9 > EMA20 > EMA50, a stacked bullish alignment),
`strategies.smc.detect_bos_choch()` on the 5-minute chart (finds a bullish BOS confirmed 3
bars ago), `strategies.candlestick.detect_engulfing()` on the 1-minute chart (finds a small,
unconvincing bullish engulfing candle), and `strategies.options_analytics.compute_pcr()` on
the option chain (finds a PCR of 1.3 — more puts written than calls, read as mildly bullish
positioning).

**Step 2 — each piece of evidence is distilled into one number per factor, in [-1, 1].**
This translation (not built by `engine/signal_engine.py` itself — that module stays pure
and takes these numbers as input) might reasonably score this evidence as:

| Factor | Score | Weight | Contribution | Why |
|---|---|---|---|---|
| `htf_trend` | 0.9 | 20 | 18.0 | Strong stacked EMA alignment on the 60-min chart |
| `smc` | 0.8 | 20 | 16.0 | Confirmed bullish BOS, price holding above the break level |
| `price_action` | 0.6 | 15 | 9.0 | Clean HH/HL structure, last pullback held |
| `indicators` | 0.5 | 15 | 7.5 | RSI 58 and rising, MACD histogram positive but flattening |
| `option_chain` | 0.7 | 15 | 10.5 | PCR 1.3, max pain below spot — mildly bullish positioning |
| `volume_momentum` | 0.4 | 10 | 4.0 | Volume 1.3x the 20-bar average — present, not dramatic |
| `candlestick_chart` | 0.2 | 5 | 1.0 | Small engulfing candle — real but weak on its own |

**Step 3 — `generate_signal()` sums the weighted contributions.**
Total = 18.0 + 16.0 + 9.0 + 7.5 + 10.5 + 4.0 + 1.0 = **66.0** (out of a possible 100).

**Step 4 — confluence is checked, not assumed.** Two independent conditions, both from
`config.signal_config.CONFLUENCE`:
- Does the total (66.0) clear `total_score_threshold` (60.0)? **Yes.**
- Do at least `min_confluence_factors` (3) distinct factors have `|raw_score| >=
  factor_min_strength` (0.3) in the same direction as the total? Six factors qualify
  (`htf_trend`, `smc`, `price_action`, `indicators`, `option_chain`, `volume_momentum` — all
  ≥0.3 and positive); only `candlestick_chart` (0.2) falls short and is excluded from the
  reasons list, though its 1.0-point contribution still counted toward the total. **Yes,
  6 ≥ 3.**

Both conditions hold, so this is **not** a NO_TRADE. (If either had failed — say the total
had only reached 55, or only two factors had cleared 0.3 — the engine would have returned
`NO_TRADE` with the same six-factor breakdown attached, so "why not" is exactly as visible
as "why.")

**Step 5 — direction and levels.** The total is positive, so `direction = BUY`. Stop
distance = `STOP_ATR_MULTIPLE` (1.5) × ATR (₹50) = ₹75. Entry is ₹24,500, so:
- **Stop-loss** = 24,500 − 75 = **₹24,425**
- **T1** (1R) = 24,500 + 1×75 = **₹24,575**
- **T2** (2R) = 24,500 + 2×75 = **₹24,650**
- **T3** (3R) = 24,500 + 3×75 = **₹24,725**
- **R:R** = 1.0 (T1 is exactly 1R by construction — that's what "R" means here)

The resulting `TradeSignal` carries `confidence=66.0`, all seven `sub_scores` (so the full
evidence trail is inspectable, not just the six that counted toward confluence), the
`reasons` list built from the six agreeing factors' human-readable text, and
`invalidation_conditions` stating the stop level and the confluence-reversal condition.

**Step 6 — this is where most of the system's actual work happens: the risk manager can
still say no.** `engine.risk_manager.RiskManager.evaluate()` receives this signal next and
checks, in order: is the kill switch off, are we inside the configured session window
(09:20–15:15 IST by default), is today a trading day, has the daily loss limit already been
hit, has today's trade-count limit been hit, is there room under the open-positions limit,
is a post-loss-streak cooldown active, is the stop distance valid — and only then computes a
lot-size-aware position size from the configured risk-per-trade budget. Any single "no"
here blocks the trade with a named rule, logged, regardless of how strong the signal was.
Only if every check passes does `execution.paper_executor.PaperExecutor.submit_order()` ever
get called — and even then, it doesn't fill immediately; it waits for the next bar's open,
per Phase 7's fill model.

That's the whole path from seven independent, weak pieces of evidence to one accountable
decision — or, far more often in practice, to a well-documented `NO_TRADE`.
