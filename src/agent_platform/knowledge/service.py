from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_platform.domain.models import ManifestKnowledgeSource

logger = logging.getLogger(__name__)


class KnowledgeResult(BaseModel):
    source_id: str
    snippets: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0


@runtime_checkable
class KnowledgeBackend(Protocol):
    name: str

    async def retrieve(
        self,
        query: str,
        source: ManifestKnowledgeSource,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[KnowledgeResult]: ...

    async def sync(self, source: ManifestKnowledgeSource) -> dict[str, Any]: ...


class StubKnowledgeBackend:
    """Stub backend for development without a real vector store."""

    name = "stub"

    async def retrieve(
        self,
        query: str,
        source: ManifestKnowledgeSource,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[KnowledgeResult]:
        return [
            KnowledgeResult(
                source_id=source.id,
                snippets=[f"[Stub] Knowledge result for '{query}' from {source.collection}"],
                score=0.5,
            )
        ]

    async def sync(self, source: ManifestKnowledgeSource) -> dict[str, Any]:
        return {"status": "stub_sync", "source_id": source.id}


class WeaviateKnowledgeBackend:
    """Weaviate vector store backend — real implementation placeholder."""

    name = "weaviate"

    def __init__(self, url: str | None = None, api_key: str | None = None):
        self.url = url or "http://localhost:8080"
        self.api_key = api_key
        logger.info("WeaviateKnowledgeBackend initialized (url=%s)", self.url)

    async def retrieve(
        self,
        query: str,
        source: ManifestKnowledgeSource,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[KnowledgeResult]:
        logger.info(
            "Weaviate retrieve: collection=%s query=%s top_k=%d",
            source.collection,
            query[:50],
            top_k,
        )
        merged_filters = {**source.filters, **(filters or {})}
        # Real implementation would call weaviate client here
        return [
            KnowledgeResult(
                source_id=source.id,
                snippets=[f"[Weaviate] Result from {source.collection} for '{query}'"],
                metadata={"collection": source.collection, "filters": merged_filters},
                score=0.8,
            )
        ]

    async def sync(self, source: ManifestKnowledgeSource) -> dict[str, Any]:
        logger.info("Weaviate sync: collection=%s", source.collection)
        return {"status": "sync_scheduled", "source_id": source.id, "collection": source.collection}


class KnowledgeService:
    """Manages knowledge backends and retrieval across multiple sources."""

    def __init__(self) -> None:
        self._backends: dict[str, KnowledgeBackend] = {}
        self.register(StubKnowledgeBackend())

    def register(self, backend: KnowledgeBackend) -> None:
        self._backends[backend.name] = backend

    async def retrieve(
        self,
        query: str,
        sources: list[ManifestKnowledgeSource],
        *,
        context_filters: dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> list[KnowledgeResult]:
        all_results: list[KnowledgeResult] = []
        for source in sources:
            backend = self._backends.get(source.backend)
            if not backend:
                logger.warning("knowledge backend not found: %s", source.backend)
                continue
            results = await backend.retrieve(
                query, source, top_k=top_k, filters=context_filters,
            )
            all_results.extend(results)
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results

    async def sync_source(self, source: ManifestKnowledgeSource) -> dict[str, Any]:
        backend = self._backends.get(source.backend)
        if not backend:
            return {"status": "error", "message": f"backend not found: {source.backend}"}
        return await backend.sync(source)
