"""Integration test: hermes_echo agent through the full platform stack."""
import pytest
from httpx import ASGITransport, AsyncClient

from agent_platform.api.app import create_app


@pytest.mark.asyncio
async def test_hermes_echo_returns_non_stub_response():
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/agent/chat",
            json={
                "agent_id": "hermes_echo",
                "context": {
                    "tenant": {"tenant_id": "t1"},
                },
                "input": {"query": "Hello World"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        display = data["output"]["text"]["display"]
        # The stub provider echoes back the user message
        # prefixed with "[Stub LLM] Received: ...".
        # Crucially it must NOT contain the old
        # "[Hermes-stub]" canned fallback marker.
        assert "[Hermes-stub]" not in display
        assert "Hello World" in display
