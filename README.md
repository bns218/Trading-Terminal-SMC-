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
  instrument-master download, and LTP fetch are implemented and exercised against real
  failure modes (network blocked, invalid TOTP secret) but never against a real successful
  broker session. You must validate the happy path yourself.
- This is a personal paper-trading tool, not a production system. Security hardening,
  secrets management, and regulatory compliance are your responsibility if you extend this
  toward live use.
