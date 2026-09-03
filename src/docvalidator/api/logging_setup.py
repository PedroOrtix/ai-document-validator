"""Structured JSON logging for the HTTP API."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the API logger to emit one JSON object per record."""
    logger = logging.getLogger("docvalidator.api")
    logger.setLevel(level)
    logger.propagate = False

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)

    return logger


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "log_data", {}))
        return json.dumps(payload, separators=(",", ":"), default=str)
