from pathlib import Path

import pytest

from agent_platform.domain.models import AgentRequest, AgentResponse, RuntimeRequest
from agent_platform.registry.loader import ManifestLoader
from agent_platform.runtime.manager import RuntimeManager


def test_agent_request_contract_accepts_documented_shape():
    request = AgentRequest.model_validate(
        {
            "protocol_version": "agent-chat/v1",
            "request_id": "req_001",
            "agent_id": "myj",
            "session_id": "sess_001",
            "context": {
                "tenant": {"tenant_id": "tenant_myj", "retailer_id": "myj"},
                "store": {"store_id": "V01031", "store_name": "美宜佳测试门店"},
                "channel": {"channel_id": "store_screen", "channel_type": "device"},
                "device": {"device_id": "device_001", "device_type": "pos_screen"},
                "user": {"user_id": "anonymous", "member_id": None},
                "locale": "zh-CN",
                "timezone": "Asia/Shanghai",
            },
            "input": {
                "type": "text",
                "query": "帮我推荐一瓶低糖饮料",
                "messages": [],
                "attachments": [],
                "capabilities": ["text", "cards", "product.recommend"],
            },
            "options": {"stream": False, "debug": False, "max_latency_ms": 5000},
            "metadata": {"source": "frontend", "traceparent": None},
        }
    )

    assert request.protocol_version == "agent-chat/v1"
    assert request.context.tenant.retailer_id == "myj"


def test_agent_manifest_contract_accepts_myj_package():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))

    assert spec.manifest.api_version == "agent.platform/v1"
    assert spec.manifest.output.protocol == "agent-chat/v1"
    assert spec.manifest.evals.required_pass_rate == 0.9


async def _run_myj_response() -> AgentResponse:
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    request = AgentRequest.model_validate(
        {
            "request_id": "req_contract",
            "agent_id": "myj",
            "context": {"tenant": {"retailer_id": "myj"}},
            "input": {"query": "可乐在哪里"},
        }
    )
    result = await RuntimeManager().run(
        RuntimeRequest(request=request, agent_spec=spec, route_reason="agent_id")
    )
    return result.response


@pytest.mark.asyncio
async def test_agent_response_contract_returns_trace():
    response = await _run_myj_response()

    assert response.protocol_version == "agent-chat/v1"
    assert response.agent.agent_id == "myj"
    assert response.output.status == "completed"
    assert response.trace is not None
    assert response.trace.run_id is not None
    assert response.trace.tool_calls[0].tool_name == "myj.goods_location"
