"""Structured application logging with contextual fields.

Every log record can carry ``request_id``, ``job_id`` and ``user_id`` through
context variables so that operations staff can correlate API requests,
background provisioning work and audit events.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("job_id", default=None)
user_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_id", default=None)

_REDACTED_KEYS = ("password", "secret", "token", "api_key", "private_key", "credential", "authorization")


def redact(value: Any) -> Any:
    """Recursively redact obviously sensitive keys from structured payloads."""
    if isinstance(value, dict):
        return {
            key: ("[REDACTED]" if any(marker in key.lower() for marker in _REDACTED_KEYS) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    return value


class ContextFilter(logging.Filter):
    """Attach correlation context vars to every record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.request_id = request_id_var.get()  # type: ignore[attr-defined]
        record.job_id = job_id_var.get()  # type: ignore[attr-defined]
        record.user_id = user_id_var.get()  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Minimal dependency-free JSON log formatter."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("request_id", "job_id", "user_id"):
            value = getattr(record, field, None)
            if value:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        ctx = []
        for field in ("request_id", "job_id", "user_id"):
            value = getattr(record, field, None)
            if value:
                ctx.append(f"{field}={value}")
        prefix = f" [{' '.join(ctx)}]" if ctx else ""
        base = (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} "
            f"{record.name}: {record.getMessage()}{prefix}"
        )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", log_format: str = "json") -> None:
    """Configure the root logger once at application startup."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(ContextFilter())
    if log_format.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(ConsoleFormatter())
    root.handlers.clear()
    root.addHandler(handler)

    # Third-party noise reduction
    for noisy in ("uvicorn.access", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def bind_logging_context(
    request_id: str | None = None, job_id: str | None = None, user_id: str | None = None
) -> None:
    """Set correlation ids for the current execution context."""
    if request_id is not None:
        request_id_var.set(request_id)
    if job_id is not None:
        job_id_var.set(job_id)
    if user_id is not None:
        user_id_var.set(user_id)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


__all__ = [
    "ConsoleFormatter",
    "ContextFilter",
    "JsonFormatter",
    "bind_logging_context",
    "configure_logging",
    "get_logger",
    "job_id_var",
    "redact",
    "request_id_var",
    "user_id_var",
]
