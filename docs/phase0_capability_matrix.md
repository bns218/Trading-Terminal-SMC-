# Phase 0 — Angel One SmartAPI Capability Matrix, Architecture Decision, Dependency Resolution

Status: DRAFT FOR REVIEW. Do not proceed to Phase 1 until this is approved.

Research basis: official SmartAPI docs (`smartapi.angelbroking.com` / `smartapi.angelone.in`) were
**unreachable from this environment** (network egress proxy blocks that domain). The matrix below is
built from the `angel-one/smartapi-python` GitHub source, SmartAPI's public forum announcement threads,
and marketcalls.in tutorials — all cited inline. Anything I could not corroborate from at least one of
these is marked `UNVERIFIED` and must be confirmed against the live docs (which you can reach, or I can
re-check once a docs domain is allow-listed) before Phase 1 auth code is trusted in production.

---

## 1. Capability matrix

| Field | SmartAPI endpoint (exact name) | DERIVED (from what) | UNSUPPORTED | NEEDS ALTERNATE SOURCE |
|---|---|---|---|---|
| LTP | `Market Quote API` — mode `LTP` (`/rest/secure/angelbroking/order/v1/getLtpData`) | | | |
| OHLC | `Market Quote API` — mode `OHLC` | | | |
| Volume | `Market Quote API` — mode `FULL`, field `tradeVolume` | | | |
| Historical candles | `Historical Data API` — `getCandleData` (`/rest/secure/angelbroking/historical/v1/getCandleData`), params `exchange/symboltoken/interval/fromdate/todate` | | | Rate/window limited: ~30 days back for `ONE_MINUTE`, ~100 days for `FIVE_MINUTE` per forum reports (UNVERIFIED exact table — confirm current limits in docs before relying on them) |
| Open interest | `Market Quote API` — mode `FULL`, field `opnInterest` (derivatives only) | | | |
| Option chain | — | **DERIVED**: filter `OpenAPIScripMaster.json` by `name` + `expiry` + `instrumenttype=OPTIDX/OPTSTK`, then batch `getLtpData`/quote FULL mode on the resulting tokens for OI/LTP | No single "option chain" endpoint exists | |
| Implied volatility | `Option Greeks API` (`/rest/secure/angelbroking/marketData/v1/optionGreek`), params `name`+`expirydate` — confirmed live in SmartAPI forum announcement (1.4.8 changelog: "Option Greek" API) | **Fallback DERIVED**: where the endpoint has no value for an instrument/strike, compute IV via Black-Scholes (equity/stock options) or Black-76 (index options, since these are cash-settled European-style on a forward) from LTP, rate, and time-to-expiry | | Confirm current coverage (live contracts only per forum note; no expired-contract data) |
| Option Greeks (Delta/Gamma/Theta/Vega) | Same `optionGreek` endpoint | **Fallback DERIVED** when endpoint gaps exist: standard BS/Black-76 closed-form Greeks from the IV computed above | | |
| PCR | `PCR API` (announced alongside gainers/losers; forum: "PCR Volume API ... ratio of Put-Call ... Options Contracts") — appears to be a **fetched, pre-aggregated ratio**, not something you build yourself | **We will still compute PCR client-side from the constructed option chain's OI** rather than trust the fetched aggregate, so the number's `source` is always known and matches your OI data | | Exact endpoint path/params UNVERIFIED — confirm in docs; regardless, spec below computes it client-side for consistency |
| Max pain | — | **DERIVED**: computed client-side from the constructed option chain (sum of ITM value lost by option writers at each candidate strike) | No endpoint | |
| OI buildup classification (long buildup / short buildup / short covering / long unwinding) | `OI Buildup API` exists per forum announcement (adjacent to gainers/losers/PCR) — but is **derivatives-OI based with its own datatype params**, not a per-contract label | **We compute it ourselves** client-side from the price-change × OI-change matrix, per contract, so every option/future in your universe gets a label, not just what the endpoint's fixed datatype list covers | | |
| India VIX | `Market Quote API`, token `99926017`, symbol `INDIAVIX`/`India VIX` on NSE indices segment (per SmartAPI forum: "real-time market data for 120 indices across NSE/BSE/MCX") | | | Confirm exact `tradingsymbol`/`exch_seg` combo works with your API key tier before wiring it in |
| Top gainers/losers | `Top Gainers/Losers API` (`/rest/secure/angelbroking/marketData/v1/gainersLosers`), params `datatype` ∈ {`PercOIGainers`,`PercOILosers`,`PercPriceGainers`,`PercPriceLosers`}, `expirytype` ∈ {`NEAR`,`NEXT`,`FAR`} | | **Confirmed: derivatives/F&O universe only** — this is not a cash-market equity ranking, matches your prior belief | |
| Market breadth (advance/decline) | — | **DERIVED only if we define a universe**: e.g. Nifty 50/Nifty 500 constituents, quote each, count advances/declines. Requires maintaining a constituent list ourselves (not provided by SmartAPI) | No direct endpoint | Universe file must be sourced/maintained separately (e.g., NSE index constituent CSVs) and documented in the UI |
| Sector performance | — | **DERIVED only if we define sector→symbol mapping** ourselves; no SmartAPI sector taxonomy exists | No direct endpoint | Sector mapping file must be sourced/maintained separately; mark `UNSUPPORTED` until you approve a mapping source |
| BSE/SENSEX coverage | `Market Quote API`/`Historical Data API` with `exch_seg=BSE` | | | Depth/liquidity on BSE derivatives is thinner; SENSEX index quote should work the same way as NSE indices (token-based) — confirm SENSEX token in docs |
| Lot size | Instrument master `OpenAPIScripMaster.json`, field `lotsize` | | | |
| Tick size | Instrument master, field `tick_size` (paise-scaled string, e.g. `"5.000000"` → ₹0.05 after /100 — confirm scaling convention in docs, this is a common source of bugs) | | | |
| Freeze quantity | — | | **Not present in `OpenAPIScripMaster.json`** (confirmed — fields are `token/symbol/name/expiry/strike/lotsize/instrumenttype/exch_seg/tick_size`, no freeze qty) | **NSE contract file** (`NSE_FO_contract_ddmmyyyy.csv.gz` from nseindia.com) or NSE circulars, refreshed on the same daily cadence as the instrument master; must be a separate config-driven fetch |
| Expiry dates | Instrument master, field `expiry`, enumerated per `name` | | | |

---

## 2. Key corrections to your stated assumptions

Your brief said "verify each against docs rather than trusting me" — here's where the evidence updates your priors:

1. **Option Greeks: a real endpoint exists** (`optionGreek`), not purely a client-side computation. It appears reliable for *live* contracts. We still keep the Black-Scholes/Black-76 fallback for the (currently unverified) cases where the endpoint has no value for a given strike — and we label `source: "api"` vs `source: "computed_bs76"` on every IV/Greek value in the data model, exactly as you specified.
2. **PCR and OI-buildup have dedicated endpoints too**, but their `datatype` parameter lists look aggregate/curated (fixed buckets like "near/next/far expiry," not "every strike in my chain"). **Decision (confirmed by user 2026-08-18): show both.** The data model carries two parallel values for PCR (overall and, where meaningful, per-strike) and for each contract's OI-buildup label:
   - `pcr_computed` / `oi_buildup_computed` — derived client-side from our own constructed option chain (price-vs-OI-change matrix), `source="computed_chain"`.
   - `pcr_api` / `oi_buildup_api` — fetched directly from Angel One's PCR / OI-Buildup endpoints, `source="angelone_api"`.
   Both are surfaced in the UI side by side with their `source` and `computed_at`/`fetched_at` fields on hover, per the "every derived metric carries a source" rule. Neither silently overrides the other. If the two disagree materially, that disagreement is itself useful signal (e.g., stale local chain vs. Angel One's aggregate window) and will be visible, not hidden.
3. **Gainers/losers confirmed derivatives-OI based**, matching your belief — not a cash-market ranking. `datatype` is one of `PercOIGainers/PercOILosers/PercPriceGainers/PercPriceLosers`, `expirytype` one of `NEAR/NEXT/FAR`.
4. **Freeze quantity confirmed absent from the instrument master.** Needs the separate NSE contract file, refreshed on its own daily schedule, cached with the same "fallback to yesterday's file" rule as the instrument master.
5. **Market breadth / sector performance: confirmed no endpoint.** Per your instruction, these are `UNSUPPORTED` unless you approve a specific constituent universe and sector-mapping source for me to hard-configure (not fabricate).

## 3. Open items I could not verify (docs domain blocked)

- Exact historical-candle lookback limits per interval (rate/window table).
- Exact tick_size decimal-scaling convention (divide by 100? already in rupees?) — must confirm before any `Decimal` rounding code is written, since this is the classic source of silent price corruption.
- Exact PCR/OI-Buildup endpoint paths and full param lists.
- SENSEX index token/symbol.
- Per-endpoint rate limits (quote vs historical vs order vs websocket subscription cap) — needed to size the token-bucket limiter correctly.

**Decision (confirmed by user 2026-08-18): conservative placeholder config.** Phase 1 will read these five values from `config/smartapi_limits.py` (or `.yaml`), each entry commented `# VERIFY AGAINST DOCS — placeholder, conservative`, with a startup log line and a dashboard data-health badge that stays visible for any value still in placeholder state. Values will be deliberately conservative (e.g., assume the tighter of any two lookback numbers seen in forum reports, assume a lower rate limit than observed) so the system fails toward "too cautious" rather than toward silently exceeding a real limit. README will list these under "known limitations" until each is confirmed and the placeholder flag is removed.

---

## 4. Architecture decision: Streamlit vs FastAPI+SSE

**Decision (confirmed by user 2026-08-18): FastAPI + SSE.**

Same underlying data-ownership rule still applies and is actually simpler to enforce here than under Streamlit: the ingestion service is the only process that ever opens the SmartAPI WebSocket and is the only writer to the SQLite (WAL) / Redis tick+candle store. FastAPI is a separate reader process — it never owns the broker WebSocket either. It exposes:
- REST endpoints for one-shot reads (positions, journal, instrument search, historical candles for chart load).
- An SSE stream per logical channel (ticks for the active watchlist, candle-close events, data-health metrics, signal-engine output, risk-manager rejections) that the frontend subscribes to and reconnects to independently, so one dropped stream doesn't take down the whole page.

Frontend: a lightweight single-page app (plain JS/HTML + a charting library, e.g. Lightweight Charts, no heavy framework) served as static files by FastAPI, since this is a personal single-user terminal and a full SPA framework would be unjustified surface area. `EventSource` handles SSE reconnect natively.

Implication for the directory layout in the brief: `app/` becomes the FastAPI service (`app/api/`, `app/sse/`, `app/static/` for the frontend) instead of Streamlit pages. This will be finalized in Phase 8 (dashboard), but auth/session and data-health plumbing built in earlier phases will expose plain Python objects/functions that both a REST handler and an SSE generator can call — no dashboard-specific logic leaks into `broker/` or `engine/`.

## 5. Dependency resolution

Pinned `requirements.txt` will target **Python 3.11**. Verified/researched constraints:

- `smartapi-python`'s own `setup.py` (v1.5.5) only hard-pins `requests>=2.18.4`, `six>=1.11.0`, `python-dateutil>=2.6.1` — but its README separately instructs installing `pyotp`, `logzero`, `websocket-client`, `pycryptodome` for TOTP auth, structured logging, and the WebSocket client respectively. These are **soft/documentation-only dependencies**, not enforced by the package — meaning version conflicts are on us to pin and test, since pip won't catch them.
- Known friction points to test explicitly in Phase 1 (I cannot claim these are resolved without actually running `pip install` — this goes in the Phase 1 tested/untested table, not here):
  - `websocket-client` version compatibility with `SmartWebSocketV2`'s use of `WebSocketApp.run_forever()`.
  - `logzero` pulls its own transitive deps; conflicts are usually with newer `colorama`/`six` pins from other libraries in the stack (pandas, streamlit).
  - `pyotp` is lightweight and rarely conflicts.
- Rest of the stack: `pandas`, `numpy`, `pydantic` v2, `streamlit`, `plotly` (for charts), `sqlite3` (stdlib, WAL mode), optionally `redis`+`redis-py` if you choose Redis over SQLite for the tick store, `python-dotenv`, `pytest`, `scipy` (Black-Scholes/Black-76 `norm.cdf`), `tenacity` or hand-rolled backoff for retries.
- I will produce the fully pinned `requirements.txt` and actually run `pip install` + `pip check` in Phase 1, and report the real resolved tree (and any conflict workaround) in that phase's tested/untested table — not here, since Phase 0 is analysis-only per your build order.

---

## 5-table Phase 0 status

| Component | How tested | Result | Untested because |
|---|---|---|---|
| Capability matrix field list | Cross-checked against `smartapi-python` GitHub source, SmartAPI forum announcement threads, marketcalls.in tutorials | Populated, with explicit UNVERIFIED/DERIVED/UNSUPPORTED labels | Official docs domain (`smartapi.angelbroking.com`, `smartapi.angelone.in`) blocked by this environment's egress proxy — could not read the canonical docs directly |
| Instrument master field names | Confirmed via forum post showing real JSON sample | `token/symbol/name/expiry/strike/lotsize/instrumenttype/exch_seg/tick_size` confirmed; no freeze qty field | Did not download the live file — no broker session exists yet (Phase 1 auth not built) |
| `smartapi-python` dependency tree | Read `setup.py` source only | Hard deps identified; soft deps identified from README | Have not run `pip install` — no venv created yet, deliberately deferred to Phase 1 per build order |
| Tick size decimal scaling | Not tested | Unknown — flagged as open item | Requires docs access or a live sample record I can decimal-check by hand |
| Rate limits per endpoint | Not tested | Unknown — flagged as open item | Requires docs access; not observable without hitting real endpoints against a live session |
| Architecture choice (Streamlit) | Reasoning only, no code run | Recommended, not yet built | Architecture decisions in Phase 0 are analysis, not implementation, per your build order |
