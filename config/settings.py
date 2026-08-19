"""Central runtime configuration, loaded from environment / .env.

TRADING_MODE is hard-wired to PAPER for this build. There is no environment
variable override that can turn it into LIVE — flipping to live execution
requires a deliberate code change in engine/execution/, not a config flag,
per the project's paper-trading-safety-first ground rules.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Broker credentials — never logged, never printed. SecretStr repr shows "**********".
    angel_api_key: SecretStr = Field(default=SecretStr(""))
    angel_client_code: SecretStr = Field(default=SecretStr(""))
    angel_password_or_pin: SecretStr = Field(default=SecretStr(""))
    angel_totp_secret: SecretStr = Field(default=SecretStr(""))

    # Dhan credentials — used only for bulk historical backfill (ingestion/dhan_historical.py),
    # not for live trading or the paper-mode engine.
    dhan_client_id: SecretStr = Field(default=SecretStr(""))
    dhan_access_token: SecretStr = Field(default=SecretStr(""))

    # Trading mode is intentionally NOT settable to anything but PAPER here.
    # engine/execution/ enforces this independently — this field exists only
    # for logging/display, so a code review can grep for one source of truth.
    trading_mode: Literal["PAPER"] = "PAPER"

    display_timezone: str = "Asia/Kolkata"

    instrument_cache_dir: Path = REPO_ROOT / "cache" / "instruments"
    log_dir: Path = REPO_ROOT / "logs"
    holidays_file: Path = REPO_ROOT / "config" / "holidays.json"

    log_level: str = "INFO"

    tick_store_path: Path = REPO_ROOT / "data_store" / "ticks.db"

    api_host: str = "127.0.0.1"
    api_port: int = 8000

    def credentials_present(self) -> bool:
        return all(
            [
                self.angel_api_key.get_secret_value(),
                self.angel_client_code.get_secret_value(),
                self.angel_password_or_pin.get_secret_value(),
                self.angel_totp_secret.get_secret_value(),
            ]
        )


def get_settings() -> Settings:
    """Fresh read each call (cheap; avoids stale settings across long-lived processes in tests)."""
    return Settings()
