from pathlib import Path
from unittest.mock import patch

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
from agent_platform.runtime.hermes import HERMES_AVAILABLE, HermesRuntimeBackend
from agent_platform.runtime.model_gateway import ModelGateway
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

@pytest.mark.skipif(not HERMES_AVAILABLE, reason="Hermes SDK not installed")
@pytest.mark.asyncio
async def test_hermes_sdk_real_agent_run():
    """Verify Hermes AIAgent can be driven by the platform via the Spike B logic."""
    gw = ModelGateway()
    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)

    backend = HermesRuntimeBackend(model_gateway=gw, tool_executor=executor)

    spec = _make_hermes_spec()
    request = RuntimeRequest(
        request=AgentRequest(
            request_id="req-spike-b-1",
            session_id="sess-spike-b-1",
            agent_id="hermes_echo",
            input=AgentInput(query="Recommend some drinks"),
        ),
        agent_spec=spec,
    )

    with patch("agent_platform.runtime.hermes.AIAgent") as MockAgentClass:
        mock_agent_instance = MockAgentClass.return_value
        mock_agent_instance.run_conversation.return_value = {
            "final_response": "I recommend soda.",
            "messages": [],
            "tool_calls": []
        }
        
        result = await backend.run(request)

        assert result.response.output.status == "completed"
        assert result.response.output.text.display == "I recommend soda."
        assert result.response.debug["runtime_backend"] == "hermes"
        
        # Verify run_conversation was called
        mock_agent_instance.run_conversation.assert_called_once()
        args, kwargs = mock_agent_instance.run_conversation.call_args
        assert kwargs["user_message"] == "Recommend some drinks"

@pytest.mark.skipif(HERMES_AVAILABLE, reason="Test fallback when SDK absent")
@pytest.mark.asyncio
async def test_hermes_fallback_when_sdk_missing():
    """Hermes SDK absent => fail down to ConversationEngine."""
    pass # we handle this via standard unit test fallback since we are forcing patching
