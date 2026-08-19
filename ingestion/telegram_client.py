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
        try:
            resp = requests.post(
                f"{_API_BASE}/bot{self._bot_token}/sendMessage",
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
                timeout=timeout,
            )
            if not resp.ok:
                logger.warning("Telegram send failed: %s %s", resp.status_code, resp.text[:300])
                return False
            return True
        except requests.RequestException as exc:
            logger.warning("Telegram send failed: %s", exc)
            return False
