"""Centralised logging configuration.

The rest of the codebase only ever calls :func:`get_logger`; formatting, handlers
and third-party log noise are controlled here so that observability concerns do
not leak into routes, services or stores.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, Literal

__all__ = ["LogFormat", "configure_logging", "get_logger"]

LogFormat = Literal["console", "json"]

_CONFIGURED = False

#: Attributes present on every :class:`logging.LogRecord`. Anything else that a
#: caller attaches via ``extra=`` is treated as structured context.
_RESERVED_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)

#: Noisy third-party loggers downgraded unless debug logging is requested.
_NOISY_LOGGERS = (
    "httpcore",
    "httpx",
    "urllib3",
    "litellm",
    "LiteLLM",
    "opentelemetry",
    "google_genai",
    "crewai.telemetry",
    "chromadb",
    "asyncio",
)


class _ConsoleFormatter(logging.Formatter):
    """Human friendly, single line format for local development."""

    default_msec_format = "%s.%03d"

    def format(self, record: logging.LogRecord) -> str:
        context = _extract_context(record)
        timestamp = self.formatTime(record, self.datefmt)
        base = f"{timestamp} - {record.levelname:<8} - {record.name} - {record.getMessage()}"
        if context:
            base = f"{base} | {json.dumps(context, default=str)}"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, ready for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_extract_context(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _extract_context(record: logging.LogRecord) -> dict[str, Any]:
    return {key: value for key, value in record.__dict__.items() if key not in _RESERVED_ATTRS}


def configure_logging(
    level: str = "INFO",
    log_format: LogFormat = "console",
    *,
    force: bool = False,
) -> None:
    """Configure the root logger exactly once (idempotent unless ``force``)."""
    global _CONFIGURED

    if _CONFIGURED and not force:
        return

    formatter: logging.Formatter = _JsonFormatter() if log_format == "json" else _ConsoleFormatter()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(_coerce_level(level))

    verbose = _coerce_level(level) <= logging.DEBUG
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG if verbose else logging.WARNING)

    _CONFIGURED = True


def _coerce_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger. Logging is configured by the composition root."""
    return logging.getLogger(name)
