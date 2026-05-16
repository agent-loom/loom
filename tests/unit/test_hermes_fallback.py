from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.domain.models import (
    AgentInput,
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
)
from agent_platform.runtime.model_gateway import ModelGateway, ModelResponse
from agent_platform.tools.executor import ToolExecutor
from agent_platform.tools.registry import create_default_tool_registry


def _make_hermes_spec() -> AgentSpec:
    return AgentSpec(
        manifest=AgentManifest(
            api_version="agent.platform/v1",
            kind="AgentPackage",
            metadata=ManifestMetadata(id="hermes_echo", name="Hermes Echo"),
            version=ManifestVersion(package_version="0.1.0"),
            runtime=ManifestRuntime(backend="hermes", max_iterations=2),
            models={"default": ManifestModelConfig(provider="stub", model="stub")},
            tools=ManifestTools(allow=["myj.goods_search"]),
            output=ManifestOutput(),
        ),
        package_path=Path("/tmp/hermes_echo"),
    )


def _make_runtime_request() -> RuntimeRequest:
    return RuntimeRequest(
        request=AgentRequest(
            request_id="req-spike-b-fall",
            session_id="sess-spike-b-fall",
            agent_id="hermes_echo",
            input=AgentInput(query="Fallback check"),
        ),
        agent_spec=_make_hermes_spec(),
    )


# ---- P1-5 test 1: when SDK is NOT available, Spike A path is used -----------

@pytest.mark.asyncio
@patch("agent_platform.runtime.hermes.HERMES_AVAILABLE", False)
async def test_hermes_fallback_when_sdk_missing():
    from agent_platform.runtime.hermes import HermesRuntimeBackend

    gw = ModelGateway()
    mock_provider = MagicMock()
    mock_provider.name = "stub"
    mock_provider.chat = AsyncMock(
        return_value=ModelResponse(
            content="Fallback Works",
            finish_reason="stop",
            tool_calls=[],
            model="stub",
        )
    )
    gw.register(mock_provider)

    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)

    backend = HermesRuntimeBackend(model_gateway=gw, tool_executor=executor)

    result = await backend.run(_make_runtime_request())

    assert result.response.output.status == "completed"
    assert result.response.debug["runtime_backend"] == "hermes"
    assert "Fallback Works" in result.response.output.text.display


@pytest.mark.asyncio
@patch("agent_platform.runtime.hermes.HERMES_AVAILABLE", False)
async def test_fallback_uses_engine_converse():
    """Verify that ConversationEngine.converse is actually called on fallback."""
    from agent_platform.runtime.hermes import HermesRuntimeBackend

    backend = HermesRuntimeBackend()
    request = _make_runtime_request()
    result = await backend.run(request)

    assert result.response.output.text.display.startswith("[Hermes-stub]")
    assert result.response.debug["runtime_backend"] == "hermes"


# ---- P1-5 test 2: result normalization with sample Hermes-like data ----------

def test_normalize_hermes_result_dict():
    from agent_platform.runtime.hermes import normalize_hermes_result

    sample = {
        "final_response": "The answer is 42.",
        "api_calls": [{"model": "gpt-4o"}, {"model": "gpt-4o"}],
        "input_tokens": 150,
        "output_tokens": 80,
        "estimated_cost_usd": 0.0042,
        "tool_calls": [
            {"name": "calculator", "status": "success", "latency_ms": 12},
            {"name": "search", "status": "failed", "latency_ms": 200},
        ],
        "run_id": "run-abc-123",
        "messages": [{"role": "user"}, {"role": "assistant"}, {"role": "tool"}],
        "model": "gpt-4o",
    }

    result = normalize_hermes_result(sample)

    assert result["text"] == "The answer is 42."
    assert result["prompt_tokens"] == 150
    assert result["completion_tokens"] == 80
    assert result["total_tokens"] == 230
    assert result["estimated_cost_usd"] == 0.0042
    assert result["run_id"] == "run-abc-123"
    assert result["model"] == "gpt-4o"
    assert result["model_calls"] == 2
    assert result["iterations"] == 3

    assert len(result["tool_calls"]) == 2
    assert result["tool_calls"][0]["name"] == "calculator"
    assert result["tool_calls"][1]["status"] == "failed"


def test_normalize_hermes_result_missing_fields():
    from agent_platform.runtime.hermes import normalize_hermes_result

    result = normalize_hermes_result({})

    assert isinstance(result["text"], str)
    assert result["prompt_tokens"] == 0
    assert result["completion_tokens"] == 0
    assert result["total_tokens"] == 0
    assert result["tool_calls"] == []
    assert result["estimated_cost_usd"] is None


def test_normalize_hermes_result_object_style():
    """Verify normalization works when the SDK returns an object, not a dict."""
    from agent_platform.runtime.hermes import normalize_hermes_result

    class FakeResponse:
        def __init__(self):
            self.final_response = "Object response"
            self.input_tokens = 50
            self.output_tokens = 30
            self.total_tokens = 80
            self.estimated_cost_usd = 0.001
            self.tool_calls = []
            self.api_calls = []
            self.run_id = "run-obj"
            self.model = "claude-3"

    result = normalize_hermes_result(FakeResponse())

    assert result["text"] == "Object response"
    assert result["prompt_tokens"] == 50
    assert result["completion_tokens"] == 30
    assert result["total_tokens"] == 80
    assert result["model"] == "claude-3"


# ---- P1-2 test: register_platform_tools_to_hermes is a no-op w/o SDK --------

@patch("agent_platform.runtime.hermes.HERMES_AVAILABLE", False)
def test_register_platform_tools_noop_without_sdk():
    from agent_platform.runtime.hermes import register_platform_tools_to_hermes

    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)

    deregister = register_platform_tools_to_hermes(executor, "test_agent")

    assert callable(deregister)
    deregister()  # should not raise
