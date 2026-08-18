from types import SimpleNamespace

import pytest

from broker.options_api import (
    get_gainers_losers_api,
    get_oi_buildup_api,
    get_option_greeks,
    get_pcr_api,
)
from broker.ratelimit import RateLimiter


def make_fake_client(method_name, response):
    fake_smartconnect = SimpleNamespace(**{method_name: lambda *a, **k: response})
    session = SimpleNamespace(client=fake_smartconnect)
    return SimpleNamespace(session=session, rate_limiter=RateLimiter())


def test_get_option_greeks_returns_data():
    rows = [{"strike": "24500", "delta": "0.55", "iv": "14.2"}]
    client = make_fake_client("optionGreek", {"status": True, "data": rows})
    result = get_option_greeks(client, "NIFTY", "25APR2024")
    assert result == rows


def test_get_pcr_api_returns_data():
    rows = [{"tradingSymbol": "NIFTY", "pcr": "0.85"}]
    client = make_fake_client("putCallRatio", {"status": True, "data": rows})
    assert get_pcr_api(client) == rows


def test_get_oi_buildup_api_returns_data():
    rows = [{"symbolToken": "26000", "tradingSymbol": "NIFTY24AUGFUT"}]
    client = make_fake_client("oIBuildup", {"status": True, "data": rows})
    assert get_oi_buildup_api(client) == rows


def test_get_gainers_losers_api_returns_data():
    rows = [{"tradingSymbol": "NIFTY", "percentChange": "5.2"}]
    client = make_fake_client("gainersLosers", {"status": True, "data": rows})
    assert get_gainers_losers_api(client) == rows


def test_raises_on_failed_status():
    client = make_fake_client("optionGreek", {"status": False, "message": "bad request", "errorcode": "AB1000"})
    with pytest.raises(RuntimeError, match="bad request"):
        get_option_greeks(client, "NIFTY", "25APR2024")


def test_raises_on_none_response():
    client = make_fake_client("optionGreek", None)
    with pytest.raises(RuntimeError):
        get_option_greeks(client, "NIFTY", "25APR2024")
