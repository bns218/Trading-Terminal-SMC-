from pathlib import Path

import pytest

from config.settings import Settings
from data.database import TickStore
from execution.factory import create_executor
from execution.live_executor import LiveExecutor
from execution.paper_executor import PaperExecutor


def test_live_executor_cannot_even_be_constructed():
    with pytest.raises(NotImplementedError):
        LiveExecutor()


def test_factory_returns_paper_executor_for_paper_mode(tmp_path: Path):
    settings = Settings(tick_store_path=tmp_path / "t.db")
    store = TickStore(settings.tick_store_path)
    executor = create_executor(settings, store)
    assert isinstance(executor, PaperExecutor)


def test_trading_mode_is_the_only_switch_and_it_is_hardwired_to_paper():
    """Settings.trading_mode is typed Literal["PAPER"] — this test documents
    and enforces that there is no way to construct a Settings instance
    requesting LIVE mode, which is what keeps execution/factory.py's live
    branch unreachable in practice."""
    with pytest.raises(Exception):
        Settings(trading_mode="LIVE")
