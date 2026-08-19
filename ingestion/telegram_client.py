"""Minimal Telegram Bot API client for pattern/signal alerts. Not a broker
integration — this only ever sends outbound text messages to the configured
chat(s), using the bot token/chat ID(s) from Settings.

Supports fan-out to several destinations (e.g. a group AND a private chat):
pass a comma-separated TELEGRAM_CHAT_ID and every alert goes to each one.

Best-effort by design: a failed send is logged and swallowed, never raised,
so a Telegram outage can't take down the pattern monitor loop that calls it.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"


def parse_chat_ids(raw: str) -> list[str]:
    """Split a comma-separated chat-id string into a clean list.

    Accepts a single id ("-1001234") or several ("-1001234, 5678"), tolerating
    stray whitespace and trailing commas.
    """
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


class TelegramClient:
    def __init__(self, bot_token: str, chat_ids: str | list[str]):
        self._bot_token = bot_token
        self._chat_ids = parse_chat_ids(chat_ids) if isinstance(chat_ids, str) else list(chat_ids)

    @property
    def chat_ids(self) -> list[str]:
        return list(self._chat_ids)

    def send_message(self, text: str, timeout: float = 10.0) -> bool:
        """Send `text` to every configured chat.

        Returns True if at least one destination accepted the message — one
        alert delivered to N chats is still one alert, so callers counting
        alerts don't inflate their totals by the number of destinations. A
        per-destination failure is logged individually.
        """
        if not self._bot_token or not self._chat_ids:
            logger.warning("Telegram not configured (missing bot token/chat id); dropping alert.")
            return False

        any_delivered = False
        for index, chat_id in enumerate(self._chat_ids):
            sent = self._post(index, text, timeout)
            if sent is None:
                # Chat migrated to a supergroup; self._chat_ids[index] has been
                # updated in place, so retry once against the new id.
                sent = bool(self._post(index, text, timeout))
            any_delivered = any_delivered or bool(sent)
        return any_delivered

    def _post(self, index: int, text: str, timeout: float) -> bool | None:
        """Send once to `self._chat_ids[index]`. Returns True/False normally,
        or None if that chat was migrated to a supergroup (in which case the
        id has been updated in place and the caller should retry).

        Telegram silently reassigns a group's chat id when it is upgraded to
        a supergroup — every subsequent send then fails with HTTP 400 and a
        `migrate_to_chat_id` parameter. Without handling that, alerts stop
        arriving with no obvious cause, so adopt the new id at runtime.
        """
        chat_id = self._chat_ids[index]
        try:
            resp = requests.post(
                f"{_API_BASE}/bot{self._bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=timeout,
            )
            if resp.ok:
                return True

            new_chat_id = None
            try:
                new_chat_id = (resp.json().get("parameters") or {}).get("migrate_to_chat_id")
            except ValueError:
                pass
            if new_chat_id:
                logger.warning(
                    "Telegram chat %s migrated to supergroup %s; adopting the new id. "
                    "Update TELEGRAM_CHAT_ID in .env to persist this across restarts.",
                    chat_id, new_chat_id,
                )
                self._chat_ids[index] = str(new_chat_id)
                return None

            logger.warning("Telegram send to %s failed: %s %s", chat_id, resp.status_code, resp.text[:300])
            return False
        except requests.RequestException as exc:
            logger.warning("Telegram send to %s failed: %s", chat_id, exc)
            return False
