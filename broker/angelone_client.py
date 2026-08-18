"""The single owner of: SmartConnect session, RateLimiter, InstrumentMaster.

Everything else in broker/ and above should go through an AngelOneClient
instance rather than constructing SmartConnect directly, so the rate limiter
is genuinely shared (ground rule: "a single shared client owns the limiter").
"""
from __future__ import annotations

import logging

from broker.auth import AngelOneSession
from broker.instruments import InstrumentMaster
from broker.ratelimit import RateLimiter
from config.settings import Settings

logger = logging.getLogger(__name__)


class AngelOneClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.session = AngelOneSession(settings)
        self.rate_limiter = RateLimiter()
        self.instrument_master = InstrumentMaster(settings.instrument_cache_dir)

    def start(self) -> None:
        """Login and load the instrument master. Call once at process startup."""
        self.session.login()
        self.instrument_master.refresh()
        logger.info(
            "AngelOneClient started: %d instruments loaded, session for client_code active.",
            len(self.instrument_master),
        )
