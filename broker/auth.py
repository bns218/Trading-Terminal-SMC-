"""SmartAPI session lifecycle: TOTP login, JWT/refresh handling, and explicit
mid-session auth-failure handling.

Ground rule: on mid-session auth failure, surface it and halt trading — never
silently retry forever. `SmartConnect.setSessionExpiryHook` is used for exactly
this: the hook we register does not attempt to re-login on its own; it flips
`SessionState.is_valid` to False and logs, so callers (engine/risk_manager in
later phases) can check `session.is_valid` before allowing a signal through.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import pyotp
from SmartApi import SmartConnect

from broker.exceptions import AuthenticationError, SessionExpiredError
from config.settings import Settings

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    jwt_token: str
    refresh_token: str
    feed_token: str
    client_code: str
    logged_in_at: datetime
    is_valid: bool = True


class AngelOneSession:
    """Owns exactly one SmartConnect instance and its session lifecycle.

    Credentials are read once from Settings (SecretStr) and never logged.
    """

    def __init__(self, settings: Settings):
        if not settings.credentials_present():
            raise AuthenticationError(
                "One or more Angel One credentials are missing from the environment. "
                "Populate .env from .env.example before starting a session."
            )
        self._settings = settings
        self._client = SmartConnect(api_key=settings.angel_api_key.get_secret_value())
        self._state: SessionState | None = None

    @property
    def client(self) -> SmartConnect:
        if self._state is None or not self._state.is_valid:
            raise SessionExpiredError(
                "No valid session. Call login() (or handle the expiry that was already "
                "logged) before making broker calls."
            )
        return self._client

    @property
    def state(self) -> SessionState | None:
        return self._state

    def login(self) -> SessionState:
        totp_secret = self._settings.angel_totp_secret.get_secret_value()
        try:
            totp = pyotp.TOTP(totp_secret).now()
        except Exception as exc:  # invalid base32 secret, etc.
            raise AuthenticationError("Failed to generate TOTP from configured secret.") from exc

        response = self._client.generateSession(
            self._settings.angel_client_code.get_secret_value(),
            self._settings.angel_password_or_pin.get_secret_value(),
            totp,
        )

        if not response or not response.get("status"):
            message = (response or {}).get("message", "unknown error")
            errorcode = (response or {}).get("errorcode", "")
            raise AuthenticationError(f"SmartAPI login failed: {message} (errorcode={errorcode})")

        data = response["data"]
        self._state = SessionState(
            jwt_token=data["jwtToken"],
            refresh_token=data["refreshToken"],
            feed_token=self._client.getfeedToken(),
            client_code=self._settings.angel_client_code.get_secret_value(),
            logged_in_at=datetime.now(timezone.utc),
            is_valid=True,
        )
        self._client.setSessionExpiryHook(self._on_session_expired)
        logger.info("SmartAPI session established for client_code=%s", self._mask_client_code())
        return self._state

    def _mask_client_code(self) -> str:
        code = self._settings.angel_client_code.get_secret_value()
        if len(code) <= 2:
            return "**"
        return code[0] + "*" * (len(code) - 2) + code[-1]

    def _on_session_expired(self) -> None:
        # Do NOT attempt silent re-login here. Mid-session auth failure must be
        # surfaced so the risk manager can halt trading, per ground rules.
        if self._state is not None:
            self._state.is_valid = False
        logger.error(
            "SmartAPI session expired mid-session (session expiry hook fired). "
            "Trading must halt until an explicit login() succeeds."
        )

    def refresh(self) -> SessionState:
        """Attempt a refresh-token-based renewal. This is an explicit, caller-
        initiated action — never called automatically from the expiry hook."""
        if self._state is None:
            raise SessionExpiredError("No prior session to refresh.")
        response = self._client.generateToken(self._state.refresh_token)
        if not response or not response.get("status"):
            self._state.is_valid = False
            message = (response or {}).get("message", "unknown error")
            raise SessionExpiredError(f"Refresh failed: {message}. Trading halted; re-login required.")
        data = response["data"]
        self._state.jwt_token = data["jwtToken"]
        self._state.refresh_token = data.get("refreshToken", self._state.refresh_token)
        self._state.is_valid = True
        logger.info("SmartAPI session refreshed for client_code=%s", self._mask_client_code())
        return self._state

    def logout(self) -> None:
        if self._state is not None:
            self._client.terminateSession(self._state.client_code)
            self._state.is_valid = False
            logger.info("SmartAPI session terminated.")
