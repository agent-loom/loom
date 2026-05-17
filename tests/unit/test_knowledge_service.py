"""Tests for KnowledgeService, StubKnowledgeBackend, and KnowledgeResult."""

from __future__ import annotations

from typing import Any

import pytest

from agent_platform.domain.models import ManifestKnowledgeSource
from agent_platform.knowledge.service import (
    KnowledgeResult,
    KnowledgeService,
    StubKnowledgeBackend,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


class FakeBackend:
    """Deterministic backend for testing multi-backend retrieval."""

    name = "fake"

    def __init__(self, results: list[KnowledgeResult] | None = None) -> None:
        self._results = results or []

    async def retrieve(
        self,
        query: str,
        source: ManifestKnowledgeSource,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[KnowledgeResult]:
        return self._results

    async def sync(self, source: ManifestKnowledgeSource) -> dict[str, Any]:
        return {"status": "fake_sync", "source_id": source.id}


# ---------------------------------------------------------------------------
# KnowledgeResult model
# ---------------------------------------------------------------------------

class TestKnowledgeResult:
    def test_defaults(self) -> None:
        kr = KnowledgeResult(source_id="s1")
        assert kr.source_id == "s1"
        assert kr.snippets == []
        assert kr.metadata == {}
        assert kr.score == 0.0

    def test_full_construction(self) -> None:
        kr = KnowledgeResult(
            source_id="s2",
            snippets=["a", "b"],
            metadata={"k": "v"},
            score=0.9,
        )
        assert kr.score == 0.9
        assert len(kr.snippets) == 2


# ---------------------------------------------------------------------------
# StubKnowledgeBackend
# ---------------------------------------------------------------------------

class TestStubKnowledgeBackend:
    @pytest.mark.asyncio
    async def test_retrieve_returns_single_result(self) -> None:
        backend = StubKnowledgeBackend()
        source = _source()
        results = await backend.retrieve("test query", source)
        assert len(results) == 1
        assert results[0].source_id == source.id
        assert results[0].score == 0.0
        assert "Stub" in results[0].snippets[0]

    @pytest.mark.asyncio
    async def test_sync_returns_stub_status(self) -> None:
        backend = StubKnowledgeBackend()
        source = _source()
        result = await backend.sync(source)
        assert result["status"] == "stub_sync"
        assert result["source_id"] == source.id


# ---------------------------------------------------------------------------
# KnowledgeService.retrieve
# ---------------------------------------------------------------------------

class TestKnowledgeServiceRetrieve:
    @pytest.mark.asyncio
    async def test_retrieve_single_source(self) -> None:
        svc = KnowledgeService()
        results = await svc.retrieve("hello", [_source()])
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_retrieve_multiple_sources_same_backend(self) -> None:
        svc = KnowledgeService()
        sources = [_source("s1"), _source("s2", collection="faq")]
        results = await svc.retrieve("hello", sources)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_retrieve_skips_unknown_backend(self) -> None:
        svc = KnowledgeService()
        source = _source(backend="nonexistent")
        results = await svc.retrieve("hello", [source])
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_across_multiple_backends(self) -> None:
        svc = KnowledgeService()
        fake_results = [
            KnowledgeResult(source_id="fake_1", snippets=["fake"], score=0.95),
        ]
        svc.register(FakeBackend(results=fake_results))

        sources = [_source(source_id="s_stub"), _source(source_id="s_fake", backend="fake")]
        results = await svc.retrieve("query", sources)

        assert len(results) == 2
        # Results should be sorted by score descending
        assert results[0].score >= results[1].score

    @pytest.mark.asyncio
    async def test_retrieve_results_sorted_by_score(self) -> None:
        svc = KnowledgeService()
        fake_results = [
            KnowledgeResult(source_id="f1", snippets=["low"], score=0.1),
            KnowledgeResult(source_id="f2", snippets=["high"], score=0.99),
        ]
        svc.register(FakeBackend(results=fake_results))

        results = await svc.retrieve("q", [_source(backend="fake")])
        assert results[0].score == 0.99
        assert results[-1].score == 0.1

    @pytest.mark.asyncio
    async def test_retrieve_empty_sources_list(self) -> None:
        svc = KnowledgeService()
        results = await svc.retrieve("query", [])
        assert results == []


# ---------------------------------------------------------------------------
# KnowledgeService.sync_source
# ---------------------------------------------------------------------------

class TestKnowledgeServiceSync:
    @pytest.mark.asyncio
    async def test_sync_with_known_backend(self) -> None:
        svc = KnowledgeService()
        result = await svc.sync_source(_source())
        assert result["status"] == "stub_sync"

    @pytest.mark.asyncio
    async def test_sync_with_unknown_backend(self) -> None:
        svc = KnowledgeService()
        result = await svc.sync_source(_source(backend="missing"))
        assert result["status"] == "error"
        assert "not found" in result["message"]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestKnowledgeServiceRegister:
    def test_register_replaces_existing(self) -> None:
        svc = KnowledgeService()
        fake = FakeBackend()
        svc.register(fake)
        assert svc._backends["fake"] is fake

    def test_stub_registered_by_default(self) -> None:
        svc = KnowledgeService()
        assert "stub" in svc._backends
