"""Structured JSON logging configuration for the agent platform."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from agent_platform.observability.sanitizer import LogSanitizer


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """将日志记录格式化为单行 JSON 字符串。"""
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include exception info if present.
        if record.exc_info:
            exc_info = record.exc_info
            if isinstance(exc_info, bool):
                # bool means "capture current exception"; resolve it.
                import sys as _sys
                exc_info = _sys.exc_info()
            if exc_info[0] is not None:
                log_entry["exception"] = self.formatException(exc_info)

        # Merge any extra context attached to the record.
        for key, value in record.__dict__.items():
            if key not in {
                "name",
                "msg",
                "args",
                "created",
                "relativeCreated",
                "exc_info",
                "exc_text",
                "stack_info",
                "lineno",
                "funcName",
                "pathname",
                "filename",
                "module",
                "levelno",
                "levelname",
                "msecs",
                "process",
                "processName",
                "thread",
                "threadName",
                "taskName",
                "message",
            }:
                log_entry[key] = value

        serialized = json.dumps(log_entry, default=str)
        return LogSanitizer.sanitize(serialized)


def setup_logging(log_level: int = logging.INFO) -> None:
    """Configure the root logger with the JSON formatter.

    Parameters
    ----------
    log_level:
        Minimum severity level to emit (default ``logging.INFO``).
    """
    root = logging.getLogger()
    root.setLevel(log_level)

    # Avoid adding duplicate handlers when called more than once.
    if any(isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JSONFormatter)
           for h in root.handlers):
        return

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JSONFormatter())
    root.addHandler(handler)
