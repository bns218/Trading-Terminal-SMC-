"""Thin, rate-limited wrappers around SmartAPI's derivatives-analytics
endpoints: Option Greeks, PCR, OI Buildup, Top Gainers/Losers.

Confirmed to exist as real SDK methods (introspected from the installed
`smartapi-python==1.5.5` package: `optionGreek`, `putCallRatio`, `oIBuildup`,
`gainersLosers` — see docs/phase0_capability_matrix.md). Their exact request/
response shapes are NOT fully confirmed against official docs (that domain
was network-blocked during development) — request params below follow forum
examples; response rows are returned as-is (list of dicts) rather than
reshaped into a specific schema, so a shape mismatch surfaces immediately as
missing/extra keys rather than being silently misparsed.

Everything here returns broker-layer raw data. Wrapping a specific field into
a `DerivedMetric` with `source="angelone_api"` is the caller's job (typically
in strategies/options_analytics.py or the dashboard layer), consistent with
this module's role: fetch and rate-limit, not interpret.
"""
from __future__ import annotations

import logging

from broker.angelone_client import AngelOneClient
from broker.exceptions import RateLimitedError
from broker.ratelimit import call_with_retry

logger = logging.getLogger(__name__)

_THROTTLE_ERROR_CODES = {"AB1004", "AB2000"}


def _call(client: AngelOneClient, endpoint_class: str, method_name: str, *args) -> list[dict] | dict:
    def _do():
        method = getattr(client.session.client, method_name)
        response = method(*args)
        if not response or not response.get("status"):
            errorcode = (response or {}).get("errorcode", "")
            if errorcode in _THROTTLE_ERROR_CODES:
                raise RateLimitedError(f"Throttled calling {method_name}: {errorcode}")
            message = (response or {}).get("message", "unknown error")
            raise RuntimeError(f"{method_name} failed: {message} (errorcode={errorcode})")
        return response.get("data")

    return call_with_retry(_do, endpoint_class, client.rate_limiter)


def get_option_greeks(client: AngelOneClient, name: str, expiry_date: str) -> list[dict]:
    """expiry_date format per forum examples: "25APR2024" (ddMMMyyyy, matching
    the instrument master's expiry format)."""
    return _call(client, "option_greek", "optionGreek", {"name": name, "expirydate": expiry_date})


def get_pcr_api(client: AngelOneClient) -> list[dict]:
    """No parameters per the SDK signature — appears to return a market-wide
    PCR listing rather than a single value. Shape unverified against docs."""
    return _call(client, "pcr", "putCallRatio")


def get_oi_buildup_api(client: AngelOneClient, expiry_type: str = "NEAR", datatype: str = "Long Built Up") -> list[dict]:
    """`datatype` values per forum reports include OI-buildup category labels
    (e.g. "Long Built Up", "Short Built Up", "Short Covering", "Long Unwinding")
    — unverified exact strings against docs, re-check before relying on this."""
    return _call(client, "oi_buildup", "oIBuildup", {"expirytype": expiry_type, "datatype": datatype})


def get_gainers_losers_api(client: AngelOneClient, datatype: str = "PercOIGainers", expiry_type: str = "NEAR") -> list[dict]:
    """datatype in {PercOIGainers, PercOILosers, PercPriceGainers, PercPriceLosers},
    expirytype in {NEAR, NEXT, FAR} — confirmed via SmartAPI forum announcement
    (Phase 0). This is derivatives/F&O data, NOT a cash-market ranking."""
    return _call(client, "gainers_losers", "gainersLosers", {"datatype": datatype, "expirytype": expiry_type})
