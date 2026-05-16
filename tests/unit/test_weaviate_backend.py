"""Tests for WeaviateKnowledgeBackend with httpx mock transport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from agent_platform.domain.models import ManifestKnowledgeSource
from agent_platform.knowledge.service import WeaviateKnowledgeBackend


def _source(
    source_id: str = "src_wv",
    collection: str = "Products",
    filters: dict[str, Any] | None = None,
) -> ManifestKnowledgeSource:
    return ManifestKnowledgeSource(
        id=source_id,
        type="vector",
        backend="weaviate",
        collection=collection,
        filters=filters or {},
    )


def _make_backend(handler) -> WeaviateKnowledgeBackend:
    backend = WeaviateKnowledgeBackend.__new__(WeaviateKnowledgeBackend)
    backend.url = "http://weaviate.test:8080"
    backend.api_key = None
    backend._client = httpx.AsyncClient(
        base_url=backend.url,
        transport=httpx.MockTransport(handler),
        headers={"Content-Type": "application/json"},
    )
    return backend


# ---------------------------------------------------------------------------
# Retrieve
# ---------------------------------------------------------------------------


class TestWeaviateRetrieve:
    @pytest.mark.asyncio
    async def test_retrieve_parses_graphql_response(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "data": {
                    "Get": {
                        "Products": [
                            {
                                "content": "Product A description",
                                "_additional": {"id": "uuid-1", "distance": 0.2},
                            },
                            {
                                "content": "Product B description",
                                "_additional": {"id": "uuid-2", "distance": 0.5},
                            },
                        ]
                    }
                }
            })

        backend = _make_backend(handler)
        results = await backend.retrieve("product search", _source(), top_k=5)

        assert len(results) == 2
        assert results[0].snippets == ["Product A description"]
        assert results[0].score == pytest.approx(0.8, abs=0.01)
        assert results[0].metadata["weaviate_id"] == "uuid-1"
        assert results[1].score == pytest.approx(0.5, abs=0.01)

    @pytest.mark.asyncio
    async def test_retrieve_with_filters(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"Get": {"Docs": []}}})

        backend = _make_backend(handler)
        source = _source(collection="Docs", filters={"category": "tech"})
        await backend.retrieve("test", source, filters={"tenant": "abc"})

        assert len(captured_body) == 1
        query_str = captured_body[0]["query"]
        assert "category" in query_str
        assert "tenant" in query_str

    @pytest.mark.asyncio
    async def test_retrieve_empty_collection(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"Get": {"Empty": []}}})

        backend = _make_backend(handler)
        results = await backend.retrieve("query", _source(collection="Empty"))
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_handles_server_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        backend = _make_backend(handler)
        results = await backend.retrieve("query", _source())
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_handles_connection_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        backend = _make_backend(handler)
        results = await backend.retrieve("query", _source())
        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_uses_source_id_as_fallback_collection(self):
        captured_body: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_body.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"Get": {"src_no_coll": []}}})

        backend = _make_backend(handler)
        source = ManifestKnowledgeSource(
            id="src_no_coll", type="vector", backend="weaviate",
        )
        await backend.retrieve("query", source)
        assert "src_no_coll" in captured_body[0]["query"]

    @pytest.mark.asyncio
    async def test_retrieve_skips_empty_content(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "data": {
                    "Get": {
                        "Products": [
                            {"content": "", "_additional": {"id": "u1", "distance": 0.1}},
                            {
                                "content": "Real content",
                                "_additional": {"id": "u2", "distance": 0.3},
                            },
                        ]
                    }
                }
            })

        backend = _make_backend(handler)
        results = await backend.retrieve("test", _source())
        assert len(results) == 1
        assert results[0].snippets == ["Real content"]


# ---------------------------------------------------------------------------
# Sync (batch import)
# ---------------------------------------------------------------------------


class TestWeaviateSync:
    @pytest.mark.asyncio
    async def test_sync_sends_batch_objects(self):
        captured: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(200, json=[{"id": "new-1"}, {"id": "new-2"}])

        backend = _make_backend(handler)
        documents = [
            {"doc_id": "d1", "content": "Hello world", "metadata": {"lang": "en"}},
            {"doc_id": "d2", "content": "Bonjour monde", "metadata": {"lang": "fr"}},
        ]
        result = await backend.sync(_source(collection="Docs"), documents=documents)

        assert result["status"] == "synced"
        assert result["objects_sent"] == 2
        assert result["response_count"] == 2
        assert len(captured) == 1
        batch = captured[0]["objects"]
        assert batch[0]["class"] == "Docs"
        assert batch[0]["properties"]["content"] == "Hello world"
        assert batch[0]["properties"]["doc_id"] == "d1"

    @pytest.mark.asyncio
    async def test_sync_no_documents(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        backend = _make_backend(handler)
        result = await backend.sync(_source())
        assert result["status"] == "no_documents"

    @pytest.mark.asyncio
    async def test_sync_empty_list(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        backend = _make_backend(handler)
        result = await backend.sync(_source(), documents=[])
        assert result["status"] == "no_documents"

    @pytest.mark.asyncio
    async def test_sync_handles_server_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Server Error")

        backend = _make_backend(handler)
        result = await backend.sync(
            _source(), documents=[{"doc_id": "d1", "content": "test"}],
        )
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestWeaviateHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_healthy(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if ".well-known/ready" in str(request.url):
                return httpx.Response(200)
            return httpx.Response(404)

        backend = _make_backend(handler)
        assert await backend.health_check() is True

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        backend = _make_backend(handler)
        assert await backend.health_check() is False

    @pytest.mark.asyncio
    async def test_health_check_connection_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        backend = _make_backend(handler)
        assert await backend.health_check() is False


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------


class TestWeaviateInit:
    def test_default_url(self):
        backend = WeaviateKnowledgeBackend()
        assert backend.url == "http://localhost:8080"

    def test_custom_url_trailing_slash_stripped(self):
        backend = WeaviateKnowledgeBackend(url="http://weaviate:9090/")
        assert backend.url == "http://weaviate:9090"

    def test_api_key_set_in_headers(self):
        backend = WeaviateKnowledgeBackend(api_key="test-key-123")
        assert backend._client.headers["authorization"] == "Bearer test-key-123"

    def test_name_is_weaviate(self):
        assert WeaviateKnowledgeBackend.name == "weaviate"

    @pytest.mark.asyncio
    async def test_close(self):
        backend = WeaviateKnowledgeBackend()
        await backend.close()
