"""Tests for knowledge retrieval wiring in RuntimeManager."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent_platform.domain.models import (
    AgentIdentity,
    AgentInput,
    AgentManifest,
    AgentOutput,
    AgentRequest,
    AgentResponse,
    AgentSpec,
    ManifestKnowledge,
    ManifestKnowledgeSource,
    ManifestMetadata,
    ManifestOutput,
    ManifestVersion,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)
from agent_platform.knowledge.service import KnowledgeResult, KnowledgeService
from agent_platform.runtime.manager import RuntimeManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _manifest(sources: list[ManifestKnowledgeSource] | None = None) -> AgentManifest:
    return AgentManifest(
        api_version="agent.platform/v1",
        kind="AgentPackage",
        metadata=ManifestMetadata(id="test_agent", name="Test Agent"),
        version=ManifestVersion(package_version="0.1.0"),
        knowledge=ManifestKnowledge(sources=sources or []),
        output=ManifestOutput(protocol="agent-chat/v1"),
    )


def _spec(sources: list[ManifestKnowledgeSource] | None = None) -> AgentSpec:
    return AgentSpec(manifest=_manifest(sources), package_path=Path("/tmp/fake"))


def _source(
    source_id: str = "src_1",
    backend: str = "stub",
    collection: str = "docs",
) -> ManifestKnowledgeSource:
    return ManifestKnowledgeSource(
        id=source_id,
        type="vector",
        backend=backend,
        collection=collection,
    )


def _runtime_request(
    query: str = "hello",
    sources: list[ManifestKnowledgeSource] | None = None,
) -> RuntimeRequest:
    return RuntimeRequest(
        request=AgentRequest(
            agent_id="test_agent",
            input=AgentInput(query=query),
        ),
        agent_spec=_spec(sources),
    )


def _ok_response(request: RuntimeRequest) -> RuntimeResponse:
    return RuntimeResponse(
        response=AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id="test_agent",
                agent_version="0.1.0",
            ),
            output=AgentOutput(
                text=ResponseText(display="ok", tts="ok"),
            ),
            trace=ResponseTrace(),
        ),
    )


class StubBackend:
    name = "native"

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        return _ok_response(request)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestKnowledgeServiceStored:
    def test_knowledge_service_stored_on_init(self) -> None:
        ks = KnowledgeService()
        mgr = RuntimeManager(knowledge_service=ks)
        assert mgr.knowledge_service is ks

    def test_knowledge_service_defaults_to_none(self) -> None:
        mgr = RuntimeManager()
        assert mgr.knowledge_service is None


class TestKnowledgeRetrievalInRun:
    @pytest.mark.asyncio
    async def test_retrieve_called_when_sources_present(self) -> None:
        ks = KnowledgeService()
        ks.retrieve = AsyncMock(return_value=[
            KnowledgeResult(source_id="src_1", snippets=["snippet_a", "snippet_b"], score=0.9),
        ])
        mgr = RuntimeManager(knowledge_service=ks)
        mgr.register(StubBackend())

        sources = [_source()]
        request = _runtime_request(query="What is X?", sources=sources)
        await mgr.run(request)

        ks.retrieve.assert_awaited_once_with(
            query="What is X?",
            sources=sources,
        )
        assert request.knowledge_context == ["snippet_a", "snippet_b"]

    @pytest.mark.asyncio
    async def test_retrieve_not_called_when_no_sources(self) -> None:
        ks = KnowledgeService()
        ks.retrieve = AsyncMock()
        mgr = RuntimeManager(knowledge_service=ks)
        mgr.register(StubBackend())

        request = _runtime_request(query="What is X?", sources=[])
        await mgr.run(request)

        ks.retrieve.assert_not_awaited()
        assert request.knowledge_context == []

    @pytest.mark.asyncio
    async def test_retrieve_not_called_when_no_knowledge_service(self) -> None:
        mgr = RuntimeManager(knowledge_service=None)
        mgr.register(StubBackend())

        sources = [_source()]
        request = _runtime_request(query="What is X?", sources=sources)
        await mgr.run(request)

        assert request.knowledge_context == []

    @pytest.mark.asyncio
    async def test_multiple_results_flatten_snippets(self) -> None:
        ks = KnowledgeService()
        ks.retrieve = AsyncMock(return_value=[
            KnowledgeResult(source_id="s1", snippets=["a"], score=0.9),
            KnowledgeResult(source_id="s2", snippets=["b", "c"], score=0.8),
        ])
        mgr = RuntimeManager(knowledge_service=ks)
        mgr.register(StubBackend())

        request = _runtime_request(query="q", sources=[_source()])
        await mgr.run(request)

        assert request.knowledge_context == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_retrieve_failure_does_not_block_run(self) -> None:
        ks = KnowledgeService()
        ks.retrieve = AsyncMock(side_effect=RuntimeError("backend down"))
        mgr = RuntimeManager(knowledge_service=ks)
        mgr.register(StubBackend())

        request = _runtime_request(query="q", sources=[_source()])
        response = await mgr.run(request)

        assert response.response.output.text.display == "ok"
        assert request.knowledge_context == []


class TestRuntimeRequestKnowledgeContext:
    def test_default_empty(self) -> None:
        req = _runtime_request()
        assert req.knowledge_context == []

    def test_can_set_knowledge_context(self) -> None:
        req = _runtime_request()
        req.knowledge_context = ["snippet_1"]
        assert req.knowledge_context == ["snippet_1"]
