"""Structured JSON logging configuration for the agent platform."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from agent_platform.observability.sanitizer import LogSanitizer


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """将日志记录格式化为单行 JSON 字符串。"""
        log_entry: dict = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
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

        serialized = json.dumps(log_entry, default=str, ensure_ascii=False)
        return LogSanitizer.sanitize(serialized)


def _parse_log_level(value: str | None, default: int) -> int:
    if not value:
        return default
    normalized = value.strip().upper()
    if normalized.isdigit():
        return int(normalized)
    return getattr(logging, normalized, default)


def _file_logging_enabled(value: str | None) -> bool:
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def setup_logging(
    log_level: int | None = None,
    *,
    log_file: str | Path | None = None,
) -> None:
    """Configure the root logger with the JSON formatter.

    Parameters
    ----------
    log_level:
        Minimum severity level to emit. Defaults to ``AGENT_PLATFORM_LOG_LEVEL``
        or ``INFO``.
    log_file:
        Optional log file path. Defaults to ``AGENT_PLATFORM_LOG_FILE`` or
        ``logs/agent-platform.log``. Set ``AGENT_PLATFORM_LOG_TO_FILE=false`` or
        ``AGENT_PLATFORM_LOG_FILE=none`` to disable file logging.
    """
    resolved_level = (
        log_level
        if log_level is not None
        else _parse_log_level(os.getenv("AGENT_PLATFORM_LOG_LEVEL"), logging.INFO)
    )
    root = logging.getLogger()
    root.setLevel(resolved_level)

    formatter = JSONFormatter()

    # Avoid adding duplicate stderr handlers when called more than once.
    if not any(getattr(h, "_agent_platform_stream", False) for h in root.handlers):
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setLevel(resolved_level)
        handler.setFormatter(formatter)
        handler._agent_platform_stream = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    file_env = os.getenv("AGENT_PLATFORM_LOG_FILE")
    file_logging_disabled = (
        not _file_logging_enabled(os.getenv("AGENT_PLATFORM_LOG_TO_FILE"))
        or (file_env is not None and file_env.strip().lower() in {"", "none", "off", "false", "0"})
    )
    if file_logging_disabled:
        return

    file_path = Path(log_file or file_env or "logs/agent-platform.log")
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        max_bytes = int(os.getenv("AGENT_PLATFORM_LOG_MAX_BYTES", str(10 * 1024 * 1024)))
        backup_count = int(os.getenv("AGENT_PLATFORM_LOG_BACKUP_COUNT", "5"))
        resolved_file = str(file_path.resolve())

        if any(
            getattr(h, "_agent_platform_file", None) == resolved_file
            for h in root.handlers
        ):
            return

        file_handler = RotatingFileHandler(
            resolved_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(formatter)
        file_handler._agent_platform_file = resolved_file  # type: ignore[attr-defined]
        root.addHandler(file_handler)
        logging.getLogger(__name__).info("File logging enabled: %s", resolved_file)
    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to configure file logging: %s",
            file_path,
            exc_info=True,
        )
