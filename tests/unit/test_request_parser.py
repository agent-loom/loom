"""Tests for agent_platform.runtime.request_parser."""

from __future__ import annotations

from agent_platform.runtime.request_parser import RequestParser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_request(**overrides) -> dict:
    """Minimal valid AgentRequest dict with nested context."""
    base = {
        "protocol_version": "agent-chat/v1",
        "request_id": "req-1",
        "agent_id": "test",
        "session_id": "sess-1",
        "context": {
            "tenant": {"tenant_id": "t1", "retailer_id": "r1"},
            "store": {"store_id": "s1"},
            "channel": {"channel_id": "ch1"},
            "device": {"device_id": "d1"},
            "user": {"user_id": "u1"},
        },
        "input": {"query": "hello"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Protocol version detection
# ---------------------------------------------------------------------------


class TestDetectVersion:
    def test_default_version(self):
        parser = RequestParser()
        assert parser.detect_version({}) == "agent-chat/v1"

    def test_v1(self):
        parser = RequestParser()
        assert parser.detect_version({"protocol_version": "1.0"}) == "1.0"

    def test_v2(self):
        parser = RequestParser()
        assert parser.detect_version({"protocol_version": "2.0"}) == "2.0"

    def test_agent_chat_v1(self):
        parser = RequestParser()
        assert parser.detect_version({"protocol_version": "agent-chat/v1"}) == "agent-chat/v1"


# ---------------------------------------------------------------------------
# Already-normalized (agent-chat/v1) passthrough
# ---------------------------------------------------------------------------


class TestNormalizedPassthrough:
    def test_parse_already_normalized(self):
        parser = RequestParser()
        raw = _base_request()
        result = parser.parse(raw)

        assert result.protocol_version == "agent-chat/v1"
        assert result.input.query == "hello"
        assert result.request_id == "req-1"
        assert result.context.tenant.tenant_id == "t1"


# ---------------------------------------------------------------------------
# v2 normalization
# ---------------------------------------------------------------------------


class TestV2Normalization:
    def test_flat_context_nested(self):
        parser = RequestParser()
        raw = {
            "protocol_version": "2.0",
            "request_id": "req-2",
            "session_id": "sess-2",
            "context": {
                "tenant_id": "t2",
                "retailer_id": "r2",
                "store_id": "s2",
                "store_name": "Store Two",
                "channel_id": "ch2",
                "channel_type": "web",
                "device_id": "d2",
                "user_id": "u2",
                "member_id": "m2",
            },
            "input": {"query": "v2 query"},
        }
        result = parser.parse(raw)

        assert result.protocol_version == "agent-chat/v1"
        assert result.context.tenant.tenant_id == "t2"
        assert result.context.tenant.retailer_id == "r2"
        assert result.context.store.store_id == "s2"
        assert result.context.store.store_name == "Store Two"
        assert result.context.channel.channel_id == "ch2"
        assert result.context.channel.channel_type == "web"
        assert result.context.device.device_id == "d2"
        assert result.context.user.user_id == "u2"
        assert result.context.user.member_id == "m2"

    def test_meta_to_options(self):
        parser = RequestParser()
        raw = {
            "protocol_version": "2.0",
            "request_id": "req-3",
            "session_id": "sess-3",
            "context": {
                "tenant": {"tenant_id": "t1"},
                "store": {},
                "channel": {},
                "device": {},
                "user": {},
            },
            "meta": {"is_debug": True, "stream": True},
            "input": {"query": "debug query"},
        }
        result = parser.parse(raw)

        assert result.options.debug is True
        assert result.options.stream is True

    def test_meta_not_overwritten_if_options_present(self):
        parser = RequestParser()
        raw = {
            "protocol_version": "2.0",
            "request_id": "req-4",
            "session_id": "sess-4",
            "context": {
                "tenant": {"tenant_id": "t1"},
                "store": {},
                "channel": {},
                "device": {},
                "user": {},
            },
            "meta": {"is_debug": True},
            "options": {"debug": False, "stream": False},
            "input": {"query": "test"},
        }
        result = parser.parse(raw)
        # options already present, so meta should NOT override
        assert result.options.debug is False

    def test_already_nested_context_untouched(self):
        """If v2 request already has nested tenant, don't re-nest."""
        parser = RequestParser()
        raw = {
            "protocol_version": "2.0",
            "request_id": "req-5",
            "session_id": "sess-5",
            "context": {
                "tenant": {"tenant_id": "t-nested"},
                "store": {"store_id": "s-nested"},
                "channel": {},
                "device": {},
                "user": {},
            },
            "input": {"query": "nested test"},
        }
        result = parser.parse(raw)
        assert result.context.tenant.tenant_id == "t-nested"


# ---------------------------------------------------------------------------
# v1 normalization
# ---------------------------------------------------------------------------


class TestV1Normalization:
    def test_top_level_query_to_input(self):
        parser = RequestParser()
        raw = {
            "protocol_version": "1.0",
            "request_id": "req-v1",
            "session_id": "sess-v1",
            "query": "legacy query",
            "context": {
                "tenant": {"tenant_id": "t1"},
                "store": {},
                "channel": {},
                "device": {},
                "user": {},
            },
        }
        result = parser.parse(raw)

        assert result.protocol_version == "agent-chat/v1"
        assert result.input.query == "legacy query"

    def test_v1_with_input_already_present(self):
        parser = RequestParser()
        raw = {
            "protocol_version": "1.0",
            "request_id": "req-v1b",
            "session_id": "sess-v1b",
            "input": {"query": "already has input"},
            "context": {
                "tenant": {},
                "store": {},
                "channel": {},
                "device": {},
                "user": {},
            },
        }
        result = parser.parse(raw)
        assert result.input.query == "already has input"
