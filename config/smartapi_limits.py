"""SmartAPI per-endpoint rate limits and historical-data lookback windows.

STATUS: PLACEHOLDER — VERIFY AGAINST DOCS.

The official docs domains (smartapi.angelbroking.com, smartapi.angelone.in) were
unreachable from the development environment that built this (network egress
proxy blocked them — see docs/phase0_capability_matrix.md). Every value below
is a deliberately conservative placeholder assembled from SmartAPI forum reports,
not the authoritative rate-limit table. The system is deliberately more
cautious than necessary rather than risk exceeding a real limit.

Each value is tagged `verified=False`. Ratelimit and instrument-master code must
surface a "PLACEHOLDER LIMITS — VERIFY AGAINST DOCS" badge (dashboard, Phase 8)
for as long as any entry here is unverified. Update the numbers AND flip
`verified=True` once you've checked them against the live docs, then remove the
corresponding line from the README's Known Limitations section.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EndpointLimit:
    requests_per_second: float
    burst: int
    verified: bool


# Conservative placeholders. Real SmartAPI limits are understood to be tiered
# (per-second / per-minute / per-day) and to differ materially between quote,
# historical, order, and non-trading endpoints — this collapses that to a
# single per-second token bucket per endpoint class, which is intentionally
# the more restrictive simplification.
ENDPOINT_LIMITS: dict[str, EndpointLimit] = {
    "quote": EndpointLimit(requests_per_second=1.0, burst=1, verified=False),
    "historical": EndpointLimit(requests_per_second=0.5, burst=1, verified=False),
    "order": EndpointLimit(requests_per_second=1.0, burst=1, verified=False),
    "option_greek": EndpointLimit(requests_per_second=0.5, burst=1, verified=False),
    "gainers_losers": EndpointLimit(requests_per_second=0.3, burst=1, verified=False),
    "pcr": EndpointLimit(requests_per_second=0.3, burst=1, verified=False),
    "oi_buildup": EndpointLimit(requests_per_second=0.3, burst=1, verified=False),
    "login": EndpointLimit(requests_per_second=0.1, burst=1, verified=False),
    "websocket_subscribe": EndpointLimit(requests_per_second=1.0, burst=50, verified=False),
}

# Historical candle lookback window per interval, in calendar days. Forum
# reports (unverified) suggest roughly 30 days for ONE_MINUTE and 100 days for
# FIVE_MINUTE; everything else here is an extrapolated conservative guess, not
# a confirmed figure.
HISTORICAL_LOOKBACK_DAYS: dict[str, int] = {
    "ONE_MINUTE": 30,
    "THREE_MINUTE": 60,
    "FIVE_MINUTE": 100,
    "FIFTEEN_MINUTE": 100,
    "THIRTY_MINUTE": 100,
    "ONE_HOUR": 100,
    "ONE_DAY": 365,
}

WEBSOCKET_MAX_CONCURRENT_CONNECTIONS_PER_CLIENT = 1  # UNVERIFIED placeholder
WEBSOCKET_MAX_SUBSCRIPTIONS_PER_CONNECTION = 50  # UNVERIFIED placeholder

ANY_UNVERIFIED = True  # flip to False only when every entry above has verified=True
