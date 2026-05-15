from __future__ import annotations

import logging
from typing import Any

from agent_platform.domain.models import (
    AgentRequest,
)

logger = logging.getLogger(__name__)

class RequestParser:
    """Protocol normalization: detects version, normalizes fields,
    handles backward compatibility."""

    SUPPORTED_VERSIONS = {"agent-chat/v1", "2.0", "1.0"}

    def parse(self, raw: dict[str, Any]) -> AgentRequest:
        """Parse raw request dict into a normalized AgentRequest."""
        version = raw.get("protocol_version", "agent-chat/v1")
        if version == "2.0":
            raw = self._normalize_v2(raw)
        elif version == "1.0":
            raw = self._normalize_v1(raw)
        return AgentRequest.model_validate(raw)

    def detect_version(self, raw: dict[str, Any]) -> str:
        return raw.get("protocol_version", "agent-chat/v1")

    def _normalize_v2(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize protocol v2 to internal format."""
        normalized = dict(raw)
        normalized["protocol_version"] = "agent-chat/v1"
        # v2 uses "meta" instead of "options"
        if "meta" in normalized and "options" not in normalized:
            meta = normalized.pop("meta")
            normalized["options"] = {
                "debug": meta.get("is_debug", False),
                "stream": meta.get("stream", False),
            }
        # v2 uses flat context
        if "context" in normalized and "tenant" not in normalized.get("context", {}):
            ctx = normalized["context"]
            normalized["context"] = {
                "tenant": {
                    "retailer_id": ctx.get("retailer_id"),
                    "tenant_id": ctx.get("tenant_id"),
                },
                "store": {
                    "store_id": ctx.get("store_id"),
                    "store_name": ctx.get("store_name"),
                },
                "channel": {
                    "channel_id": ctx.get("channel_id"),
                    "channel_type": ctx.get("channel_type"),
                },
                "device": {"device_id": ctx.get("device_id")},
                "user": {"user_id": ctx.get("user_id"), "member_id": ctx.get("member_id")},
            }
        return normalized

    def _normalize_v1(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize legacy v1 format."""
        normalized = dict(raw)
        normalized["protocol_version"] = "agent-chat/v1"
        # v1 might use "query" at top level
        if "query" in normalized and "input" not in normalized:
            normalized["input"] = {"query": normalized.pop("query")}
        return normalized
