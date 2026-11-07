"""Unit tests for the structured JSON logger."""

import json
import logging
from io import StringIO

from app.core import logging as app_logging


def test_json_formatter_emits_valid_json() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(app_logging._JsonFormatter())

    logger = logging.getLogger("test.json.formatter")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("Hello, world!")

    line = stream.getvalue().strip()
    data = json.loads(line)

    assert data["level"] == "INFO"
    assert data["logger"] == "test.json.formatter"
    assert data["message"] == "Hello, world!"
    assert "timestamp" in data


def test_json_formatter_injects_request_id() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(app_logging._JsonFormatter())

    logger = logging.getLogger("test.json.request_id")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("Processing request", extra={"request_id": "req-abc-123"})

    line = stream.getvalue().strip()
    data = json.loads(line)

    assert data["request_id"] == "req-abc-123"
    assert data["message"] == "Processing request"


def test_json_formatter_includes_exception() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(app_logging._JsonFormatter())

    logger = logging.getLogger("test.json.exception")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    try:
        raise ValueError("Something went wrong")
    except ValueError:
        logger.exception("An error occurred")

    line = stream.getvalue().strip()
    data = json.loads(line)

    assert data["level"] == "ERROR"
    assert data["message"] == "An error occurred"
    assert "exc" in data
    assert "ValueError: Something went wrong" in data["exc"]
