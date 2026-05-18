from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_platform.domain.models import (
    AgentInput,
    AgentManifest,
    AgentRequest,
    AgentSpec,
    ManifestKnowledge,
    ManifestKnowledgeSource,
    ManifestMetadata,
    ManifestModelConfig,
    ManifestOutput,
    ManifestRuntime,
    ManifestTools,
    ManifestVersion,
    RuntimeRequest,
)
from agent_platform.knowledge.service import (
    KnowledgeResult,
    KnowledgeService,
    WeaviateKnowledgeBackend,
)
from agent_platform.persistence.memory import (
    InMemoryAgentRunRepository,
    InMemoryAgentSessionRepository,
)
from agent_platform.runtime.manager import RuntimeManager
from agent_platform.runtime.model_gateway import ModelGateway
from agent_platform.tools.executor import ToolExecutor
from agent_platform.tools.registry import create_default_tool_registry


def _make_rag_spec() -> AgentSpec:
    return AgentSpec(
        manifest=AgentManifest(
            api_version="agent.platform/v1",
            kind="AgentPackage",
            metadata=ManifestMetadata(id="rag_agent", name="RAG Agent"),
            version=ManifestVersion(package_version="0.1.0"),
            runtime=ManifestRuntime(backend="native", max_iterations=2),
            models={"default": ManifestModelConfig(provider="stub_provider", model="stub")},
            tools=ManifestTools(),
            knowledge=ManifestKnowledge(
                sources=[
                    ManifestKnowledgeSource(
                        id="test_products",
                        type="vector",
                        backend="weaviate",
                        collection="Products",
                        filters={},
                    )
                ]
            ),
            output=ManifestOutput(),
        ),
        package_path=Path("/tmp/rag_agent"),
    )

@pytest.mark.asyncio
async def test_knowledge_rag_injection():
    # 1. Provide Mock Gateway
    gw = ModelGateway()
    mock_provider = MagicMock()
    mock_provider.name = "stub_provider"
    
    def chat_side_effect(request, **kwargs):
        sys_msg = next((m.content for m in request.messages if m.role == "system"), "")
        assert "Specific Mocked Snippet" in sys_msg
        return MagicMock(content="RAG context received.", finish_reason="stop", tool_calls=[])
    
    mock_provider.chat = AsyncMock(side_effect=chat_side_effect)
    gw.register(mock_provider)

    # 2. Provide Knowledge Service with Weaviate Mock Backend
    knowledge_service = KnowledgeService()
    weaviate_mock = WeaviateKnowledgeBackend(url="http://fake-url")
    weaviate_mock.retrieve = AsyncMock(return_value=[
        KnowledgeResult(
            source_id="test_products",
            snippets=["[Weaviate] Specific Mocked Snippet for RAG"],
            score=0.9,
        ),
    ])
    knowledge_service.register(weaviate_mock)

    # 3. Init runtime
    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)
    
    session_repo = InMemoryAgentSessionRepository()
    run_repo = InMemoryAgentRunRepository()

    # Load Hermes
    from agent_platform.runtime.hermes import HermesRuntimeBackend
    backend = HermesRuntimeBackend(model_gateway=gw, tool_executor=executor)

    manager = RuntimeManager(
        model_gateway=gw,
        tool_executor=executor,
        session_store=session_repo,
        run_store=run_repo,
        knowledge_service=knowledge_service,
    )
    manager.register(backend)

    spec = _make_rag_spec()
    spec.manifest.runtime.backend = "hermes"
    req = RuntimeRequest(
        request=AgentRequest(
            request_id="rag-req-1",
            session_id="rag-sess-1",
            agent_id="rag_agent",
            input=AgentInput(query="What is the price of water?"),
        ),
        agent_spec=spec,
    )

    with patch("agent_platform.runtime.hermes.HERMES_AVAILABLE", True), \
         patch("agent_platform.runtime.hermes.AIAgent") as MockAgentClass, \
         patch("agent_platform.runtime.hermes._HermesAIAgent", MockAgentClass):
        mock_agent_instance = MockAgentClass.return_value
        mock_agent_instance.run_conversation.return_value = {
            "final_response": "The price of water is 1 USD.",
            "messages": [],
            "tool_calls": []
        }

        result = await manager.run(req)

        # 4. Verify Context Mapping
        assert "price of water" in result.response.output.text.display
        weaviate_mock.retrieve.assert_called_once()
        
        # Verify system_message got the RAG snippet
        mock_agent_instance.run_conversation.assert_called_once()
        _, kwargs = mock_agent_instance.run_conversation.call_args
        assert "Specific Mocked Snippet for RAG" in kwargs["system_message"]

