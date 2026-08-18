import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from broker.exceptions import InstrumentMasterError
from broker.instruments import InstrumentMaster

SAMPLE_RECORDS = [
    {
        "token": "2885",
        "symbol": "RELIANCE-EQ",
        "name": "RELIANCE",
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": "",
        "exch_seg": "NSE",
        "tick_size": "5.000000",
    },
    {
        "token": "37743",
        "symbol": "NIFTY02SEP2117500PE",
        "name": "NIFTY",
        "expiry": "02SEP2021",
        "strike": "1750000.000000",
        "lotsize": "50",
        "instrumenttype": "OPTIDX",
        "exch_seg": "NFO",
        "tick_size": "5.000000",
    },
    {
        "token": "37744",
        "symbol": "NIFTY02SEP2117500CE",
        "name": "NIFTY",
        "expiry": "02SEP2021",
        "strike": "1750000.000000",
        "lotsize": "50",
        "instrumenttype": "OPTIDX",
        "exch_seg": "NFO",
        "tick_size": "5.000000",
    },
    {"symbol": "BROKEN", "exch_seg": "NSE", "instrumenttype": ""},  # missing required "token" key
]


def test_parses_valid_records_and_skips_broken(tmp_path: Path):
    master = InstrumentMaster(tmp_path)
    parsed = [InstrumentMaster._parse_record(r) for r in SAMPLE_RECORDS]
    parsed = [p for p in parsed if p is not None]
    assert len(parsed) == 3  # the record missing "token" is skipped


def test_strike_and_tick_size_scaling():
    inst = InstrumentMaster._parse_record(SAMPLE_RECORDS[1])
    assert inst.strike == Decimal("17500")
    assert inst.tick_size == Decimal("0.05")


def test_equity_record_has_no_strike_or_expiry():
    inst = InstrumentMaster._parse_record(SAMPLE_RECORDS[0])
    assert inst.strike is None
    assert inst.expiry is None
    assert inst.lot_size == 1


def test_freeze_quantity_always_none_from_this_source():
    for record in SAMPLE_RECORDS[:3]:
        inst = InstrumentMaster._parse_record(record)
        assert inst.freeze_quantity is None


def test_option_chain_filter(tmp_path: Path, monkeypatch):
    master = InstrumentMaster(tmp_path)
    master._instruments = [InstrumentMaster._parse_record(r) for r in SAMPLE_RECORDS[:3]]
    master._instruments = [i for i in master._instruments if i is not None]
    chain = master.find_option_chain("NIFTY", date(2021, 9, 2))
    assert len(chain) == 2
    assert {i.symbol for i in chain} == {"NIFTY02SEP2117500PE", "NIFTY02SEP2117500CE"}


def test_refresh_falls_back_to_cache_when_network_fails(tmp_path: Path, monkeypatch):
    cache_file = tmp_path / "scrip_master_2026-08-17.json"
    cache_file.write_text(json.dumps(SAMPLE_RECORDS[:2]), encoding="utf-8")

    master = InstrumentMaster(tmp_path)

    def broken_get(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr("broker.instruments.requests.get", broken_get)
    master.refresh()
    assert len(master) == 2
    assert master._loaded_from.startswith("cache_fallback:")


def test_refresh_raises_when_no_network_and_no_cache(tmp_path: Path, monkeypatch):
    master = InstrumentMaster(tmp_path)

    def broken_get(*args, **kwargs):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr("broker.instruments.requests.get", broken_get)
    with pytest.raises(InstrumentMasterError):
        master.refresh()
