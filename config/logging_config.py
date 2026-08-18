"""Structured JSON logging with per-signal correlation IDs and secret redaction.

Every log record crossing a module boundary should go through this logger, not
`print()`. Correlation IDs let one trade decision be traced end to end across
broker/, engine/, and execution/ log lines.
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

# Patterns for values that must never reach a log line, even if a caller
# accidentally passes a raw secret instead of a SecretStr. Applied to the
# final rendered message string as a last line of defense.
_REDACT_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key\"?\s*[:=]\s*\"?)([^\s\"&,}]{4,})"),
    re.compile(r"(?i)(totp[_-]?secret\"?\s*[:=]\s*\"?)([^\s\"&,}]{4,})"),
    re.compile(r"(?i)(password\"?\s*[:=]\s*\"?)([^\s\"&,}]{4,})"),
    re.compile(r"(?i)(jwttoken\"?\s*[:=]\s*\"?)([^\s\"&,}]{8,})"),
    re.compile(r"(?i)(refreshtoken\"?\s*[:=]\s*\"?)([^\s\"&,}]{8,})"),
    re.compile(r"(?i)(feedtoken\"?\s*[:=]\s*\"?)([^\s\"&,}]{8,})"),
]

# Field names that are always fully masked when passed as structured `extra`
# fields, regardless of whether their value happens to match a text pattern
# above. This covers the log_with_fields(..., jwttoken=...) case where the
# key/value are separate, not embedded together in one string.
_SENSITIVE_FIELD_NAMES = {
    "api_key",
    "apikey",
    "totp_secret",
    "totpsecret",
    "password",
    "pin",
    "jwttoken",
    "jwt_token",
    "refreshtoken",
    "refresh_token",
    "feedtoken",
    "feed_token",
}


def redact(text: str) -> str:
    redacted = text
    for pattern in _REDACT_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}***REDACTED***", redacted)
    return redacted


def set_correlation_id(correlation_id: str | None) -> None:
    _correlation_id.set(correlation_id)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "correlation_id": get_correlation_id(),
        }
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        extra = getattr(record, "extra_fields", None)
        if extra:
            for key, value in extra.items():
                if key.lower() in _SENSITIVE_FIELD_NAMES:
                    payload[key] = "***REDACTED***"
                elif isinstance(value, str):
                    payload[key] = redact(value)
                else:
                    payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(JsonFormatter())
    root.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_dir / "trading_terminal.jsonl", encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_with_fields(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"extra_fields": fields})
