"""Tests for agent_platform.runtime.response_builder."""

from __future__ import annotations

from pathlib import Path

from agent_platform.domain.models import (
    AgentManifest,
    AgentRequest,
    AgentSpec,
    ManifestMetadata,
    ManifestOutput,
    ManifestVersion,
    OutputStatus,
    RequestOptions,
    ResponseTrace,
    RuntimeRequest,
)
from agent_platform.runtime.response_builder import ResponseBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime_request(
    *,
    debug: bool = False,
    command_allowlist: list[str] | None = None,
    route_reason: str | None = "test_route",
) -> RuntimeRequest:
    return RuntimeRequest(
        request=AgentRequest(
            request_id="req-1",
            session_id="sess-1",
            input={"query": "hello"},
            options=RequestOptions(debug=debug),
        ),
        agent_spec=AgentSpec(
            manifest=AgentManifest(
                api_version="agent.platform/v1",
                kind="AgentPackage",
                metadata=ManifestMetadata(id="test-agent", name="Test Agent"),
                version=ManifestVersion(package_version="1.0.0"),
                output=ManifestOutput(
                    command_allowlist=command_allowlist or [],
                ),
            ),
            package_path=Path("/tmp/test-agent"),
        ),
        route_reason=route_reason,
        deployment_id="deploy-1",
    )


# ---------------------------------------------------------------------------
# build() tests
# ---------------------------------------------------------------------------


class TestResponseBuilderBuild:
    def test_creates_proper_agent_response(self):
        builder = ResponseBuilder()
        req = _make_runtime_request()

        resp = builder.build(req, display="Hello, world!")

        assert resp.request_id == "req-1"
        assert resp.session_id == "sess-1"
        assert resp.agent.agent_id == "test-agent"
        assert resp.agent.agent_version == "1.0.0"
        assert resp.agent.deployment_id == "deploy-1"
        assert resp.output.status == OutputStatus.COMPLETED
        assert resp.output.text.display == "Hello, world!"
        assert resp.output.text.tts == "Hello, world!"  # tts defaults to display
        assert resp.output.cards == []
        assert resp.output.commands == []

    def test_custom_tts(self):
        builder = ResponseBuilder()
        req = _make_runtime_request()

        resp = builder.build(req, display="visual text", tts="spoken text")

        assert resp.output.text.display == "visual text"
        assert resp.output.text.tts == "spoken text"

    def test_custom_status(self):
        builder = ResponseBuilder()
        req = _make_runtime_request()

        resp = builder.build(req, display="failed", status=OutputStatus.FAILED)
        assert resp.output.status == OutputStatus.FAILED

    def test_cards_included(self):
        builder = ResponseBuilder()
        req = _make_runtime_request()

        resp = builder.build(
            req,
            display="here are results",
            cards=[{"type": "product", "title": "Item A"}],
        )
        assert len(resp.output.cards) == 1
        assert resp.output.cards[0].type == "product"
        assert resp.output.cards[0].title == "Item A"

    def test_trace_defaults_to_route_reason(self):
        builder = ResponseBuilder()
        req = _make_runtime_request(route_reason="intent_match")

        resp = builder.build(req, display="ok")
        assert resp.trace is not None
        assert resp.trace.route_reason == "intent_match"

    def test_custom_trace(self):
        builder = ResponseBuilder()
        req = _make_runtime_request()
        custom_trace = ResponseTrace(route_reason="custom", model="gpt-4o")

        resp = builder.build(req, display="ok", trace=custom_trace)
        assert resp.trace.route_reason == "custom"
        assert resp.trace.model == "gpt-4o"

    def test_debug_included_when_flag_set(self):
        builder = ResponseBuilder()
        req = _make_runtime_request(debug=True)

        resp = builder.build(req, display="debug info", debug={"timing": 42})
        assert resp.debug == {"timing": 42}

    def test_debug_excluded_when_flag_not_set(self):
        builder = ResponseBuilder()
        req = _make_runtime_request(debug=False)

        resp = builder.build(req, display="no debug", debug={"timing": 42})
        assert resp.debug is None


# ---------------------------------------------------------------------------
# Command allowlist filtering
# ---------------------------------------------------------------------------


class TestCommandAllowlist:
    def test_all_commands_pass_when_no_allowlist(self):
        builder = ResponseBuilder()
        req = _make_runtime_request(command_allowlist=[])

        resp = builder.build(
            req,
            display="ok",
            commands=[
                {"name": "human.handoff", "data": {}},
                {"name": "cart.add", "data": {}},
            ],
        )
        assert len(resp.output.commands) == 2

    def test_filters_commands_by_allowlist(self):
        builder = ResponseBuilder()
        req = _make_runtime_request(command_allowlist=["human.handoff"])

        resp = builder.build(
            req,
            display="ok",
            commands=[
                {"name": "human.handoff", "data": {"reason": "user request"}},
                {"name": "cart.add", "data": {"sku": "123"}},
                {"name": "navigate.page", "data": {}},
            ],
        )
        assert len(resp.output.commands) == 1
        assert resp.output.commands[0].name == "human.handoff"

    def test_allowlist_filters_all_when_none_match(self):
        builder = ResponseBuilder()
        req = _make_runtime_request(command_allowlist=["allowed.only"])

        resp = builder.build(
            req,
            display="ok",
            commands=[{"name": "denied.cmd", "data": {}}],
        )
        assert len(resp.output.commands) == 0


# ---------------------------------------------------------------------------
# build_handoff() tests
# ---------------------------------------------------------------------------


class TestBuildHandoff:
    def test_creates_handoff_response(self):
        builder = ResponseBuilder()
        req = _make_runtime_request(command_allowlist=[])

        resp = builder.build_handoff(req, reason="User wants human agent")

        assert resp.output.status == OutputStatus.HANDOFF_REQUIRED
        assert resp.output.text.display == "User wants human agent"
        assert len(resp.output.commands) == 1
        assert resp.output.commands[0].name == "human.handoff"
        assert resp.output.commands[0].data == {"reason": "User wants human agent"}


# ---------------------------------------------------------------------------
# build_clarification() tests
# ---------------------------------------------------------------------------


class TestBuildClarification:
    def test_creates_clarification_response(self):
        builder = ResponseBuilder()
        req = _make_runtime_request()

        resp = builder.build_clarification(req, question="Could you be more specific?")

        assert resp.output.status == OutputStatus.CLARIFICATION_REQUIRED
        assert resp.output.text.display == "Could you be more specific?"
        assert resp.output.commands == []
