"""
logging.py — Structured logging configuration for TN811 Monitor.

Uses Python stdlib logging with optional JSON formatting via structlog-style
output. All modules use standard logging.getLogger(__name__) — this module
configures the root logger at startup.

Call configure_logging(config) once at application startup.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Include extra fields attached via extra={...}
        skip_keys = {
            "args", "created", "exc_info", "exc_text", "filename",
            "funcName", "levelname", "levelno", "lineno", "message",
            "module", "msecs", "msg", "name", "pathname", "process",
            "processName", "relativeCreated", "stack_info", "taskName",
            "thread", "threadName",
        }
        for key, value in record.__dict__.items():
            if key not in skip_keys:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable log formatter."""

    FMT = "%(asctime)s %(levelname)-8s %(name)-30s %(message)s"
    DATEFMT = "%Y-%m-%d %H:%M:%S"

    def __init__(self) -> None:
        super().__init__(fmt=self.FMT, datefmt=self.DATEFMT)


def configure_logging(level: str = "INFO", fmt: str = "json", file: str | None = None) -> None:
    """
    Configure the root logger.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR).
        fmt:   Output format — "json" or "text".
        file:  Optional file path to write logs to (in addition to stdout).
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove any existing handlers
    root.handlers.clear()

    formatter: logging.Formatter = JSONFormatter() if fmt == "json" else TextFormatter()

    # Stdout handler
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    root.addHandler(stdout_handler)

    # Optional file handler
    if file:
        try:
            from pathlib import Path
            Path(file).parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(file)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as e:
            logging.warning(f"Could not open log file {file}: {e}")

    # Quieten noisy third-party loggers
    logging.getLogger("playwright").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pdfminer").setLevel(logging.WARNING)
