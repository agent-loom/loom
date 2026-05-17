"""知识检索服务，管理多后端的知识源检索与同步。"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_platform.domain.models import ManifestKnowledgeSource

logger = logging.getLogger(__name__)


class KnowledgeResult(BaseModel):
    """单条知识检索结果。"""
    source_id: str
    snippets: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0


@runtime_checkable
class KnowledgeBackend(Protocol):
    """知识后端协议，定义检索和同步接口。"""
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
    """Stub backend for development without a real vector store.

    生产环境应替换为真实后端（如 Weaviate）。
    """

    name = "stub"

    async def retrieve(
        self,
        query: str,
        source: ManifestKnowledgeSource,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[KnowledgeResult]:
        logger.warning(
            "Stub 知识后端被调用（collection=%s）— 生产环境请配置 Weaviate",
            source.collection,
        )
        return [
            KnowledgeResult(
                source_id=source.id,
                snippets=[f"[Stub] Knowledge result for '{query}' from {source.collection}"],
                score=0.0,
                metadata={"stub": True},
            )
        ]

    async def sync(self, source: ManifestKnowledgeSource) -> dict[str, Any]:
        return {"status": "stub_sync", "source_id": source.id}


class WeaviateKnowledgeBackend:
    """Weaviate vector store backend via httpx REST/GraphQL API."""

    name = "weaviate"

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
    ):
        import httpx

        self.url = (url or "http://localhost:8080").rstrip("/")
        self.api_key = api_key
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.url,
            headers=headers,
            timeout=timeout,
        )
        logger.info("WeaviateKnowledgeBackend initialized (url=%s)", self.url)

    async def retrieve(
        self,
        query: str,
        source: ManifestKnowledgeSource,
        *,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[KnowledgeResult]:
        collection = source.collection or source.id
        merged_filters = {**source.filters, **(filters or {})}

        where_clause = ""
        if merged_filters:
            operands = []
            for key, val in merged_filters.items():
                operands.append(
                    f'{{ path: ["{key}"], operator: Equal, '
                    f'valueText: "{val}" }}'
                )
            if len(operands) == 1:
                where_clause = f"where: {operands[0]}"
            else:
                joined = ", ".join(operands)
                where_clause = (
                    f"where: {{ operator: And, operands: [{joined}] }}"
                )

        graphql_query = {
            "query": "{"
            f"  Get {{"
            f"    {collection}("
            f'      nearText: {{ concepts: ["{query}"] }}'
            f"      limit: {top_k}"
            f"      {where_clause}"
            f"    ) {{"
            f"      _additional {{ id distance }}"
            f"      content"
            f"    }}"
            f"  }}"
            f"}}"
        }

        try:
            resp = await self._client.post("/v1/graphql", json=graphql_query)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception(
                "Weaviate retrieve failed: collection=%s", collection,
            )
            return []

        objects = (
            data.get("data", {}).get("Get", {}).get(collection, []) or []
        )
        results: list[KnowledgeResult] = []
        for obj in objects:
            additional = obj.get("_additional", {})
            distance = additional.get("distance", 1.0)
            score = max(0.0, 1.0 - float(distance))
            content = obj.get("content", "")
            if content:
                results.append(
                    KnowledgeResult(
                        source_id=source.id,
                        snippets=[content],
                        metadata={
                            "collection": collection,
                            "weaviate_id": additional.get("id", ""),
                            "distance": distance,
                        },
                        score=score,
                    )
                )
        return results

    async def sync(
        self,
        source: ManifestKnowledgeSource,
        documents: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        collection = source.collection or source.id

        if not documents:
            logger.info("Weaviate sync: no documents for %s", collection)
            return {
                "status": "no_documents",
                "source_id": source.id,
                "collection": collection,
            }

        batch_objects = []
        for doc in documents:
            batch_objects.append({
                "class": collection,
                "properties": {
                    "content": doc.get("content", ""),
                    "doc_id": doc.get("doc_id", ""),
                    **doc.get("metadata", {}),
                },
            })

        try:
            resp = await self._client.post(
                "/v1/batch/objects",
                json={"objects": batch_objects},
            )
            resp.raise_for_status()
            result_data = resp.json()
            return {
                "status": "synced",
                "source_id": source.id,
                "collection": collection,
                "objects_sent": len(batch_objects),
                "response_count": len(result_data) if isinstance(result_data, list) else 1,
            }
        except Exception:
            logger.exception("Weaviate batch sync failed: collection=%s", collection)
            return {
                "status": "error",
                "source_id": source.id,
                "collection": collection,
            }

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/v1/.well-known/ready")
            return resp.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()


class KnowledgeService:
    """Manages knowledge backends and retrieval across multiple sources."""

    def __init__(self) -> None:
        """初始化知识服务并注册默认 Stub 后端。"""
        self._backends: dict[str, KnowledgeBackend] = {}
        self.register(StubKnowledgeBackend())

    def register(self, backend: KnowledgeBackend) -> None:
        """注册一个知识后端。"""
        self._backends[backend.name] = backend

    async def retrieve(
        self,
        query: str,
        sources: list[ManifestKnowledgeSource],
        *,
        context_filters: dict[str, Any] | None = None,
        top_k: int = 5,
    ) -> list[KnowledgeResult]:
        """从多个知识源检索并按分数排序返回结果。"""
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
        """触发指定知识源的数据同步。"""
        backend = self._backends.get(source.backend)
        if not backend:
            return {"status": "error", "message": f"backend not found: {source.backend}"}
        return await backend.sync(source)
