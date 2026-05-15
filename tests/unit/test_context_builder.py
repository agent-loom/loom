"""Tests for agent_platform.runtime.context_builder."""

from __future__ import annotations

from pathlib import Path

from agent_platform.domain.models import (
    AgentInput,
    AgentManifest,
    AgentRequest,
    AgentSpec,
    ChannelContext,
    ManifestMetadata,
    ManifestOutput,
    ManifestSession,
    ManifestTools,
    ManifestVersion,
    RequestContext,
    SessionMessage,
    StoreContext,
    TenantContext,
    UserContext,
)
from agent_platform.runtime.context_builder import ContextBuilder, RuntimeContext

# ---------------------------------------------------------------------------
# RuntimeContext model tests
# ---------------------------------------------------------------------------


class TestRuntimeContext:
    def test_defaults(self):
        ctx = RuntimeContext()
        assert ctx.system_prompt == ""
        assert ctx.messages == []
        assert ctx.tools == []
        assert ctx.knowledge_snippets == []
        assert ctx.metadata == {}

    def test_with_values(self):
        ctx = RuntimeContext(
            system_prompt="You are helpful.",
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"name": "search", "type": "function"}],
            knowledge_snippets=["fact1"],
            metadata={"agent_id": "a1"},
        )
        assert ctx.system_prompt == "You are helpful."
        assert len(ctx.messages) == 1
        assert len(ctx.tools) == 1
        assert ctx.knowledge_snippets == ["fact1"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    prompts: dict[str, str] | None = None,
    tools_allow: list[str] | None = None,
    history_window: int = 20,
    package_path: Path = Path("/tmp/test-agent"),
) -> AgentSpec:
    return AgentSpec(
        manifest=AgentManifest(
            api_version="agent.platform/v1",
            kind="AgentPackage",
            metadata=ManifestMetadata(id="test-agent", name="Test Agent"),
            version=ManifestVersion(package_version="0.1.0"),
            prompts=prompts or {},
            tools=ManifestTools(allow=tools_allow or []),
            session=ManifestSession(history_window=history_window),
            output=ManifestOutput(),
        ),
        package_path=package_path,
    )


def _make_request(query: str = "hello") -> AgentRequest:
    return AgentRequest(
        request_id="req-1",
        session_id="sess-1",
        context=RequestContext(
            tenant=TenantContext(tenant_id="t1", retailer_id="r1"),
            store=StoreContext(store_id="s1"),
            channel=ChannelContext(channel_id="ch1"),
            user=UserContext(user_id="u1"),
            locale="en-US",
            timezone="America/New_York",
        ),
        input=AgentInput(query=query),
    )


# ---------------------------------------------------------------------------
# ContextBuilder tests
# ---------------------------------------------------------------------------


class TestContextBuilderSystemPrompt:
    def test_fallback_when_no_orchestrator_prompt(self):
        builder = ContextBuilder()
        spec = _make_spec(prompts={})
        request = _make_request()

        ctx = builder.build(spec, request)
        assert ctx.system_prompt == "You are agent test-agent."

    def test_loads_prompt_from_file(self, tmp_path: Path):
        prompt_file = tmp_path / "orchestrator.md"
        prompt_file.write_text("Custom system prompt content", encoding="utf-8")

        spec = _make_spec(
            prompts={"orchestrator": "orchestrator.md"},
            package_path=tmp_path,
        )
        request = _make_request()
        builder = ContextBuilder()

        ctx = builder.build(spec, request)
        assert ctx.system_prompt == "Custom system prompt content"

    def test_fallback_when_prompt_file_missing(self, tmp_path: Path):
        spec = _make_spec(
            prompts={"orchestrator": "nonexistent.md"},
            package_path=tmp_path,
        )
        request = _make_request()
        builder = ContextBuilder()

        ctx = builder.build(spec, request)
        assert ctx.system_prompt == "You are agent test-agent."


class TestContextBuilderMessages:
    def test_builds_messages_with_user_query(self):
        builder = ContextBuilder()
        spec = _make_spec()
        request = _make_request(query="What time is it?")

        ctx = builder.build(spec, request)

        assert len(ctx.messages) == 1
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[0]["content"] == "What time is it?"

    def test_includes_session_history(self):
        builder = ContextBuilder()
        spec = _make_spec(history_window=10)
        request = _make_request(query="follow up")
        history = [
            SessionMessage(role="user", content="first question"),
            SessionMessage(role="assistant", content="first answer"),
        ]

        ctx = builder.build(spec, request, session_history=history)

        # 2 history messages + 1 user query = 3
        assert len(ctx.messages) == 3
        assert ctx.messages[0]["content"] == "first question"
        assert ctx.messages[1]["content"] == "first answer"
        assert ctx.messages[2]["content"] == "follow up"

    def test_window_limits_history(self):
        builder = ContextBuilder()
        spec = _make_spec(history_window=2)
        request = _make_request(query="latest")
        history = [
            SessionMessage(role="user", content="msg1"),
            SessionMessage(role="assistant", content="reply1"),
            SessionMessage(role="user", content="msg2"),
            SessionMessage(role="assistant", content="reply2"),
            SessionMessage(role="user", content="msg3"),
        ]

        ctx = builder.build(spec, request, session_history=history)

        # window=2 keeps last 2 history msgs + 1 new user msg = 3
        assert len(ctx.messages) == 3
        assert ctx.messages[0]["content"] == "reply2"
        assert ctx.messages[1]["content"] == "msg3"
        assert ctx.messages[2]["content"] == "latest"


class TestContextBuilderToolDefs:
    def test_builds_tool_definitions(self):
        builder = ContextBuilder()
        spec = _make_spec(tools_allow=["search", "calculator"])
        request = _make_request()

        ctx = builder.build(spec, request)

        assert len(ctx.tools) == 2
        names = {t["name"] for t in ctx.tools}
        assert names == {"search", "calculator"}
        for t in ctx.tools:
            assert t["type"] == "function"

    def test_empty_tools_when_none_allowed(self):
        builder = ContextBuilder()
        spec = _make_spec(tools_allow=[])
        request = _make_request()

        ctx = builder.build(spec, request)
        assert ctx.tools == []


class TestContextBuilderKnowledge:
    def test_knowledge_snippets_passed_through(self):
        builder = ContextBuilder()
        spec = _make_spec()
        request = _make_request()
        knowledge = ["snippet 1", "snippet 2"]

        ctx = builder.build(spec, request, knowledge_results=knowledge)

        assert ctx.knowledge_snippets == ["snippet 1", "snippet 2"]

    def test_no_knowledge(self):
        builder = ContextBuilder()
        spec = _make_spec()
        request = _make_request()

        ctx = builder.build(spec, request)
        assert ctx.knowledge_snippets == []


class TestContextBuilderMetadata:
    def test_metadata_includes_context_fields(self):
        builder = ContextBuilder()
        spec = _make_spec()
        request = _make_request()

        ctx = builder.build(spec, request)

        assert ctx.metadata["agent_id"] == "test-agent"
        assert ctx.metadata["tenant_id"] == "t1"
        assert ctx.metadata["retailer_id"] == "r1"
        assert ctx.metadata["store_id"] == "s1"
        assert ctx.metadata["channel_id"] == "ch1"
        assert ctx.metadata["user_id"] == "u1"
        assert ctx.metadata["locale"] == "en-US"
        assert ctx.metadata["timezone"] == "America/New_York"
        assert ctx.metadata["session_scope"] == "session"
