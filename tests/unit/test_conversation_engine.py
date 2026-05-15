"""Tests for agent_platform.runtime.conversation."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.domain.models import (
    AgentManifest,
    AgentRequest,
    AgentSpec,
    ManifestMetadata,
    ManifestModelConfig,
    ManifestOutput,
    ManifestRuntime,
    ManifestTools,
    ManifestVersion,
    RuntimeRequest,
    ToolCallTrace,
)
from agent_platform.runtime.context_builder import RuntimeContext
from agent_platform.runtime.conversation import ConversationEngine, ConversationResult
from agent_platform.runtime.model_gateway import (
    ModelGateway,
    ModelMessage,
    ModelResponse,
    ToolCall,
)
from agent_platform.tools.executor import ToolExecutionResult, ToolExecutor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    *,
    max_iterations: int = 4,
    provider: str = "stub",
    model: str = "stub",
    tools_allow: list[str] | None = None,
) -> AgentSpec:
    return AgentSpec(
        manifest=AgentManifest(
            api_version="agent.platform/v1",
            kind="AgentPackage",
            metadata=ManifestMetadata(id="test-agent", name="Test Agent"),
            version=ManifestVersion(package_version="0.1.0"),
            runtime=ManifestRuntime(max_iterations=max_iterations),
            models={
                "default": ManifestModelConfig(provider=provider, model=model),
            },
            tools=ManifestTools(allow=tools_allow or []),
            output=ManifestOutput(),
        ),
        package_path=Path("/tmp/test-agent"),
    )


def _make_context(
    query: str = "hello",
    knowledge: list[str] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> RuntimeContext:
    return RuntimeContext(
        system_prompt="You are a test agent.",
        messages=[{"role": "user", "content": query}],
        tools=tools or [],
        knowledge_snippets=knowledge or [],
    )


def _make_request(query: str = "hello") -> RuntimeRequest:
    return RuntimeRequest(
        request=AgentRequest(
            request_id="req-1",
            session_id="sess-1",
            input={"query": query},
        ),
        agent_spec=_make_spec(),
    )


# ---------------------------------------------------------------------------
# ConversationResult tests
# ---------------------------------------------------------------------------


class TestConversationResult:
    def test_fields_default(self):
        result = ConversationResult(display="hi")
        assert result.display == "hi"
        assert result.tool_traces == []
        assert result.model_used is None
        assert result.total_iterations == 0

    def test_fields_with_traces(self):
        trace = ToolCallTrace(tool_name="search", status="success")
        result = ConversationResult(
            display="result",
            tool_traces=[trace],
            model_used="gpt-4o",
            total_iterations=2,
        )
        assert len(result.tool_traces) == 1
        assert result.model_used == "gpt-4o"
        assert result.total_iterations == 2


# ---------------------------------------------------------------------------
# ConversationEngine tests
# ---------------------------------------------------------------------------


class TestConversationEngine:
    @pytest.mark.asyncio
    async def test_run_returns_immediately_when_no_tool_calls(self):
        """Stub returns a text response with no tool_calls, so the loop exits at iteration 1."""
        gw = ModelGateway()  # has stub registered
        executor = MagicMock(spec=ToolExecutor)
        engine = ConversationEngine(model_gateway=gw, tool_executor=executor)

        spec = _make_spec(provider="stub", model="stub")
        ctx = _make_context(query="What is 2+2?")
        req = _make_request(query="What is 2+2?")

        result = await engine.run(ctx, spec, req.request)

        assert "[Stub LLM]" in result.display
        assert "What is 2+2?" in result.display
        assert result.total_iterations == 1
        assert result.tool_traces == []
        # Tool executor should NOT be called when there are no tool calls
        executor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_model_configured_returns_error_message(self):
        """If the spec has no 'default' model config, return an error message."""
        gw = ModelGateway()
        executor = MagicMock(spec=ToolExecutor)
        engine = ConversationEngine(model_gateway=gw, tool_executor=executor)

        spec = AgentSpec(
            manifest=AgentManifest(
                api_version="agent.platform/v1",
                kind="AgentPackage",
                metadata=ManifestMetadata(id="no-model", name="No Model"),
                version=ManifestVersion(package_version="0.1.0"),
                models={},  # no default
                output=ManifestOutput(),
            ),
            package_path=Path("/tmp/no-model"),
        )
        ctx = _make_context()
        req = _make_request()

        result = await engine.run(ctx, spec, req.request)
        assert "no model configured" in result.display.lower()

    @pytest.mark.asyncio
    async def test_budget_control_max_iterations(self):
        """When every LLM call returns tool_calls, the engine should stop after max_iterations."""
        tool_response = ModelResponse(
            content="",
            tool_calls=[ToolCall(id="tc-1", name="search", arguments={"q": "test"})],
            finish_reason="tool_use",
            model="mock",
        )
        final_response = ModelResponse(
            content="Budget exhausted",
            finish_reason="stop",
            model="mock",
        )

        mock_provider = MagicMock()
        mock_provider.name = "mock"
        call_count = 0

        async def mock_chat(messages, *, model, temperature, max_tokens, tools=None):
            nonlocal call_count
            call_count += 1
            # First N calls return tool calls, the final fallback returns text
            if call_count <= 2:
                return tool_response
            return final_response

        mock_provider.chat = mock_chat

        gw = ModelGateway()
        gw.register(mock_provider)

        mock_executor = AsyncMock(spec=ToolExecutor)
        mock_executor.execute.return_value = ToolExecutionResult(
            tool_name="search",
            output={"summary": "result"},
            trace=ToolCallTrace(tool_name="search", status="success"),
        )

        engine = ConversationEngine(model_gateway=gw, tool_executor=mock_executor)
        spec = _make_spec(
            max_iterations=2, provider="mock",
            model="mock-model", tools_allow=["search"],
        )
        ctx = _make_context(
            query="search something",
            tools=[{"name": "search", "type": "function"}],
        )
        req = _make_request(query="search something")

        result = await engine.run(ctx, spec, req.request)

        assert result.total_iterations == 2
        assert result.display == "Budget exhausted"
        assert len(result.tool_traces) == 2  # one tool call per iteration

    @pytest.mark.asyncio
    async def test_knowledge_injection_in_context(self):
        """Knowledge snippets should be injected as system messages."""
        captured_messages: list[ModelMessage] = []

        async def capture_chat(messages, *, model, temperature, max_tokens, tools=None):
            captured_messages.extend(messages)
            return ModelResponse(content="ok", model=model, finish_reason="stop")

        mock_provider = MagicMock()
        mock_provider.name = "capture"
        mock_provider.chat = capture_chat

        gw = ModelGateway()
        gw.register(mock_provider)

        executor = MagicMock(spec=ToolExecutor)
        engine = ConversationEngine(model_gateway=gw, tool_executor=executor)

        spec = _make_spec(provider="capture", model="capture-model")
        knowledge = ["Fact A: the sky is blue", "Fact B: water is wet"]
        ctx = _make_context(query="tell me a fact", knowledge=knowledge)
        req = _make_request(query="tell me a fact")

        await engine.run(ctx, spec, req.request)

        # There should be a system message with knowledge snippets
        system_msgs = [m for m in captured_messages if m.role == "system"]
        assert len(system_msgs) >= 2  # original system + knowledge
        knowledge_msg = system_msgs[1]
        assert "Fact A" in knowledge_msg.content
        assert "Fact B" in knowledge_msg.content
