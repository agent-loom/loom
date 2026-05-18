import json
import logging
from pathlib import Path

from agent_platform.observability.logging_config import setup_logging


def test_setup_logging_writes_json_file(tmp_path, monkeypatch):
    log_file = tmp_path / "agent-platform.log"
    monkeypatch.setenv("AGENT_PLATFORM_LOG_FILE", str(log_file))
    monkeypatch.setenv("AGENT_PLATFORM_LOG_TO_FILE", "true")

    root = logging.getLogger()
    before = list(root.handlers)
    setup_logging(log_level=logging.INFO)

    try:
        logging.getLogger("agent_platform.test").info(
            "hello file log",
            extra={"request_id": "req-1"},
        )
        for handler in root.handlers:
            handler.flush()

        lines = log_file.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines]
        assert any(
            record["message"] == "hello file log"
            and record["request_id"] == "req-1"
            for record in records
        )
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()


def test_setup_logging_can_disable_file_logging(tmp_path, monkeypatch):
    log_file = tmp_path / "disabled.log"
    monkeypatch.setenv("AGENT_PLATFORM_LOG_FILE", str(log_file))
    monkeypatch.setenv("AGENT_PLATFORM_LOG_TO_FILE", "false")

    root = logging.getLogger()
    before = list(root.handlers)
    setup_logging(log_level=logging.INFO)

    try:
        logging.getLogger("agent_platform.test").info("should not create file")
        for handler in root.handlers:
            handler.flush()
        assert not Path(log_file).exists()
    finally:
        for handler in list(root.handlers):
            if handler not in before:
                root.removeHandler(handler)
                handler.close()
