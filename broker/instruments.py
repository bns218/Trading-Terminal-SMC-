"""Instrument master: cached to disk, refreshed once daily, falls back to the
previous day's file if today's download fails. Never fetched per request.

Source: https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
(confirmed location per Phase 0 research — not from official docs directly,
since the docs domain was unreachable; re-verify this URL against docs).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests

from broker.exceptions import InstrumentMasterError
from data.models import Exchange, Instrument, InstrumentType

logger = logging.getLogger(__name__)

SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

_INSTRUMENT_TYPE_MAP = {
    "EQ": InstrumentType.EQ,
    "FUTSTK": InstrumentType.FUTSTK,
    "FUTIDX": InstrumentType.FUTIDX,
    "OPTSTK": InstrumentType.OPTSTK,
    "OPTIDX": InstrumentType.OPTIDX,
    "INDEX": InstrumentType.INDEX,
    "": InstrumentType.EQ,  # scrip master leaves this blank for plain equities
}


class InstrumentMaster:
    def __init__(self, cache_dir: Path):
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._instruments: list[Instrument] = []
        self._loaded_from: str | None = None

    def _cache_path_for(self, day: date) -> Path:
        return self._cache_dir / f"scrip_master_{day.isoformat()}.json"

    def _latest_cache_file(self) -> Path | None:
        files = sorted(self._cache_dir.glob("scrip_master_*.json"))
        return files[-1] if files else None

    def refresh(self, timeout: float = 30.0) -> None:
        """Download today's scrip master. On failure, fall back to the most
        recent cached file. Raises InstrumentMasterError only if neither works.
        """
        today = datetime.now(timezone.utc).date()
        cache_path = self._cache_path_for(today)

        raw_records: list[dict] | None = None
        try:
            response = requests.get(SCRIP_MASTER_URL, timeout=timeout)
            response.raise_for_status()
            raw_records = response.json()
            cache_path.write_text(json.dumps(raw_records), encoding="utf-8")
            self._loaded_from = f"network:{today.isoformat()}"
            logger.info("Instrument master downloaded fresh: %d records", len(raw_records))
        except Exception as exc:
            logger.warning("Instrument master download failed (%s); falling back to disk cache.", exc)
            fallback = self._latest_cache_file()
            if fallback is None:
                raise InstrumentMasterError(
                    "Instrument master download failed and no cached file exists on disk. "
                    "Cannot proceed without a scrip master."
                ) from exc
            raw_records = json.loads(fallback.read_text(encoding="utf-8"))
            self._loaded_from = f"cache_fallback:{fallback.name}"
            logger.warning("Using fallback instrument master file: %s", fallback.name)

        self._instruments = [self._parse_record(r) for r in raw_records if self._parse_record(r) is not None]
        logger.info("Instrument master loaded (%s): %d usable records", self._loaded_from, len(self._instruments))

    @staticmethod
    def _parse_record(record: dict) -> Instrument | None:
        try:
            exch_seg = record.get("exch_seg", "")
            if exch_seg not in Exchange.__members__:
                return None  # e.g. unrecognized/derivative segments not yet modeled
            instrument_type_raw = record.get("instrumenttype", "")
            instrument_type = _INSTRUMENT_TYPE_MAP.get(instrument_type_raw)
            if instrument_type is None:
                return None

            expiry_raw = record.get("expiry") or None
            expiry = None
            if expiry_raw:
                # SmartAPI format observed: "02SEP2021" (ddMMMyyyy)
                expiry = datetime.strptime(expiry_raw, "%d%b%Y").replace(tzinfo=timezone.utc)

            strike_raw = record.get("strike")
            strike = None
            if strike_raw is not None and Decimal(str(strike_raw)) > 0:
                # Strike scaled by 100 in the scrip master: a sample record found during
                # Phase 0 research showed strike="1750000.000000" for a 17500-strike NIFTY
                # option, consistent with /100 scaling. Not from official docs — re-verify.
                strike = Decimal(str(strike_raw)) / Decimal(100)

            tick_size_raw = record.get("tick_size")
            # Same /100 scaling observed in the same sample record (tick_size="5.000000" -> 0.05).
            # Still UNVERIFIED against official docs — re-check before trusting for order rounding.
            tick_size = Decimal(str(tick_size_raw)) / Decimal(100) if tick_size_raw else Decimal("0.05")

            lot_size_raw = record.get("lotsize")
            lot_size = int(lot_size_raw) if lot_size_raw else 1

            return Instrument(
                token=str(record["token"]),
                symbol=record["symbol"],
                name=record.get("name", record["symbol"]),
                exchange=Exchange(exch_seg),
                instrument_type=instrument_type,
                lot_size=lot_size,
                tick_size=tick_size,
                expiry=expiry,
                strike=strike,
                freeze_quantity=None,  # not present in this source; see Phase 0 matrix
            )
        except (KeyError, InvalidOperation, ValueError) as exc:
            logger.debug("Skipping unparseable instrument record %r: %s", record, exc)
            return None

    def find_by_token(self, token: str) -> Instrument | None:
        for inst in self._instruments:
            if inst.token == token:
                return inst
        return None

    def find_option_chain(self, name: str, expiry: date) -> list[Instrument]:
        return [
            inst
            for inst in self._instruments
            if inst.name == name
            and inst.instrument_type in (InstrumentType.OPTSTK, InstrumentType.OPTIDX)
            and inst.expiry is not None
            and inst.expiry.date() == expiry
        ]

    def all_expiries(self, name: str) -> list[date]:
        expiries = {
            inst.expiry.date()
            for inst in self._instruments
            if inst.name == name
            and inst.instrument_type in (InstrumentType.OPTSTK, InstrumentType.OPTIDX)
            and inst.expiry is not None
        }
        return sorted(expiries)

    def __len__(self) -> int:
        return len(self._instruments)
