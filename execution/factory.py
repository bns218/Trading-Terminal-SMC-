"""Executor selection: happens exactly once, at process startup, from config.
No other code path in the system should construct PaperExecutor or
LiveExecutor directly — go through this so "how is the mode decided" has
exactly one answer.
"""
from __future__ import annotations

from config.charges_config import ChargesConfig
from config.settings import Settings
from data.database import TickStore
from execution.executor_protocol import Executor
from execution.paper_executor import PaperExecutor


def create_executor(settings: Settings, store: TickStore, charges_config: ChargesConfig = ChargesConfig()) -> Executor:
    if settings.trading_mode == "PAPER":
        return PaperExecutor(store, charges_config)
    # Unreachable given Settings.trading_mode: Literal["PAPER"] — kept as an explicit
    # runtime guard rather than trusting the type checker alone, since this is exactly
    # the boundary the project's paper-trading-safety ground rule cares about.
    raise NotImplementedError(
        f"Unsupported trading_mode={settings.trading_mode!r}. Only PAPER is supported in this build."
    )
