from fastapi.testclient import TestClient

from agent_platform.api.app import app


def test_health():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_chat_myj():
    client = TestClient(app)

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "request_id": "req_test",
            "agent_id": "myj",
            "session_id": "sess_test",
            "context": {"tenant": {"retailer_id": "myj"}},
            "input": {"query": "可乐在哪里"},
            "options": {"debug": True},
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["agent"]["agent_id"] == "myj"
    assert data["agent"]["deployment_id"] == "dep_myj_dev_default"
    assert data["output"]["status"] == "completed"
    display = data["output"]["text"]["display"]
    assert "冷柜" in display or "饮料" in display
    assert data["trace"]["tool_calls"][0]["tool_name"] == "myj.goods_location"


def test_agent_runs_are_recorded():
    client = TestClient(app)

    chat = client.post(
        "/api/v1/agent/chat",
        json={
            "request_id": "req_runs",
            "agent_id": "myj",
            "session_id": "sess_runs",
            "context": {"tenant": {"retailer_id": "myj"}},
            "input": {"query": "推荐低糖饮料"},
        },
    )
    assert chat.status_code == 200

    response = client.get("/api/v1/agent-runs")

    assert response.status_code == 200
    runs = response.json()
    assert any(run["request_id"] == "req_runs" for run in runs)


def test_unknown_agent_returns_standard_error_response():
    client = TestClient(app)

    response = client.post(
        "/api/v1/agent/chat",
        json={"agent_id": "missing", "input": {"query": "hello"}},
    )

    assert response.status_code == 404
    data = response.json()
    assert data["output"]["status"] == "failed"
    assert data["error"]["code"] == "AGENT_NOT_FOUND"
    assert data["trace"]["run_id"].startswith("run_")
    assert data["trace"]["tool_calls"] == []


def test_chat_unknown_agent_returns_404():
    client = TestClient(app)

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "request_id": "req_missing",
            "agent_id": "missing",
            "input": {"query": "hello"},
        },
    )

    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "AGENT_NOT_FOUND"
    assert data["output"]["status"] == "failed"


def test_agent_runs_endpoint_records_chat_run():
    client = TestClient(app)

    chat = client.post(
        "/api/v1/agent/chat",
        json={
            "request_id": "req_runs",
            "agent_id": "myj",
            "context": {"tenant": {"retailer_id": "myj"}},
            "input": {"query": "低糖饮料推荐"},
        },
    )
    assert chat.status_code == 200
    run_id = chat.json()["trace"]["run_id"]

    response = client.get("/api/v1/agent-runs")

    assert response.status_code == 200
    runs = response.json()
    assert any(run["run_id"] == run_id and run["agent_id"] == "myj" for run in runs)


def test_chat_missing_required_context_returns_400():
    client = TestClient(app)

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "request_id": "req_context_missing",
            "agent_id": "myj",
            "input": {"query": "可乐在哪里"},
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert data["agent"]["agent_id"] == "myj"
    assert data["error"]["code"] == "INVALID_REQUEST"
    assert "context.tenant.retailer_id" in data["error"]["message"]


def test_deploy_agent_and_route_staging_profile():
    client = TestClient(app)

    deploy = client.post(
        "/api/v1/agent-packages/myj/versions/0.1.0/deploy",
        json={"channel": "staging", "traffic_percent": 100, "eval_passed": True},
    )

    assert deploy.status_code == 200
    deployment = deploy.json()
    assert deployment["deployment_id"] == "dep_myj_staging_default"
    assert deployment["status"] == "staging"

    chat = client.post(
        "/api/v1/agent/chat",
        json={
            "agent_id": "myj",
            "context": {"tenant": {"retailer_id": "myj"}},
            "input": {"query": "可乐在哪里"},
            "options": {"runtime_profile": "staging"},
        },
    )

    assert chat.status_code == 200
    assert chat.json()["agent"]["deployment_id"] == "dep_myj_staging_default"

    audit = client.get(
        "/api/v1/deployments/audit",
        params={"agent_id": "myj", "channel": "staging"},
    )
    assert audit.status_code == 200
    assert any(event["event_type"] == "deploy" for event in audit.json())
