"""Structured JSON logging for the API and workers.

Every log record is emitted as a single JSON line to stdout with these fields:
  timestamp  - ISO 8601 UTC
  level      - DEBUG / INFO / WARNING / ERROR / CRITICAL
  logger     - dotted logger name
  message    - the formatted message
  request_id - from the ASGI scope state, when set via RequestIdMiddleware
  *          - any extra kwargs passed to the log call

No third-party logging library is required; this is built on the standard
`logging.Formatter`.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Let the base class format the message (applies %s substitution, exc_info, etc.)
        message = super().format(record)
        # Strip the traceback that super() appended; we put it in a separate key.
        if record.exc_info:
            # formatException is already cached on record after super().format()
            exc_text = self.formatException(record.exc_info)
            message = message[: -(len(exc_text) + 1)]  # strip trailing "\n<traceback>"

        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }

        # Inject the X-Request-Id threaded by RequestIdMiddleware, if present.
        request_id: str | None = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id

        # Include a stringified traceback when present.
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Merge any extra fields the caller passed (e.g. logger.info("…", extra={…})).
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value

        return json.dumps(payload, default=str)


# Fields that belong to the LogRecord internals and should not be re-emitted.
_RESERVED = frozenset(
    {
        "name",
        "msg",
        "args",
        "created",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "thread",
        "threadName",
        "exc_info",
        "exc_text",
        "stack_info",
        "taskName",
    }
)


def configure(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger.

    Call once at process startup, before the first log message.  Idempotent:
    calling it again replaces the handler rather than duplicating it.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    # Remove any handlers already installed (e.g. uvicorn's plain-text handler).
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Uvicorn installs its own loggers; keep them going through the root handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
