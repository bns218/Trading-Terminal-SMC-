"""Minimal Telegram Bot API client for pattern/signal alerts. Not a broker
integration — this only ever sends outbound text messages to one configured
chat, using the bot token/chat ID from Settings.

Best-effort by design: a failed send is logged and swallowed, never raised,
so a Telegram outage can't take down the pattern monitor loop that calls it.
"""
from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_API_BASE = "https://api.telegram.org"


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str):
        self._bot_token = bot_token
        self._chat_id = chat_id

    def send_message(self, text: str, timeout: float = 10.0) -> bool:
        if not self._bot_token or not self._chat_id:
            logger.warning("Telegram not configured (missing bot token/chat id); dropping alert.")
            return False
        sent = self._post(text, timeout)
        if sent is not None:
            return sent
        # _post returned None => the chat migrated and self._chat_id was
        # updated in place; retry once against the new id.
        return bool(self._post(text, timeout))

    def _post(self, text: str, timeout: float) -> bool | None:
        """Send once. Returns True/False normally, or None if the chat was
        migrated to a supergroup (in which case self._chat_id has been
        updated and the caller should retry).

        Telegram silently reassigns a group's chat id when it is upgraded to
        a supergroup — every subsequent send then fails with HTTP 400 and a
        `migrate_to_chat_id` parameter. Without handling that, alerts stop
        arriving with no obvious cause, so adopt the new id at runtime.
        """
        try:
            resp = requests.post(
                f"{_API_BASE}/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
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
                    self._chat_id, new_chat_id,
                )
                self._chat_id = str(new_chat_id)
                return None

            logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:300])
            return False
        except requests.RequestException as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False
