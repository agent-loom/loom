"""Tests for the admin API endpoints at /api/v1/admin/."""

from fastapi.testclient import TestClient

from agent_platform.api.app import app

client = TestClient(app)


def _ensure_agent_chat():
    """Trigger a chat so that agents are discovered and a run+session is created."""
    resp = client.post(
        "/api/v1/agent/chat",
        json={
            "request_id": "req_admin_test",
            "agent_id": "myj",
            "session_id": "sess_admin_test",
            "context": {"tenant": {"retailer_id": "myj"}},
            "input": {"query": "推荐低糖饮料"},
        },
    )
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Agent Management
# ---------------------------------------------------------------------------


def test_list_agents_returns_correct_data():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/agents")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    agent = next((a for a in data if a["agent_id"] == "myj"), None)
    assert agent is not None
    assert "manifest" in agent
    assert "name" in agent
    assert "version" in agent


def test_get_agent_returns_full_details():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/agents/myj")
    assert response.status_code == 200
    data = response.json()
    assert data["agent_id"] == "myj"
    assert "manifest" in data
    assert "deployments" in data
    assert isinstance(data["deployments"], list)
    assert "recent_runs" in data
    assert isinstance(data["recent_runs"], list)


def test_get_agent_unknown_returns_404():
    response = client.get("/api/v1/admin/agents/nonexistent_agent_xyz")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# System Status
# ---------------------------------------------------------------------------


def test_system_status_returns_expected_structure():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/status")
    assert response.status_code == 200
    data = response.json()
    assert "agents" in data
    assert "deployments" in data
    assert "active_sessions" in data
    assert "total_runs" in data
    assert isinstance(data["agents"], int)
    assert isinstance(data["deployments"], int)
    assert isinstance(data["active_sessions"], int)
    assert isinstance(data["total_runs"], int)
    assert data["agents"] > 0


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def test_list_runs_works():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/runs")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "run_id" in data[0]
    assert "agent_id" in data[0]


def test_list_runs_filter_by_agent_id():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/runs", params={"agent_id": "myj"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    for run in data:
        assert run["agent_id"] == "myj"


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def test_list_sessions_works():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/sessions")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert "session_id" in data[0]


def test_list_sessions_filter_by_agent_id():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/sessions", params={"agent_id": "myj"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    for session in data:
        assert session["agent_id"] == "myj"


def test_delete_session_works():
    chat_data = _ensure_agent_chat()
    session_id = chat_data.get("session_id") or "sess_admin_test"

    # Verify session exists
    sessions = client.get("/api/v1/admin/sessions").json()
    assert any(s["session_id"] == session_id for s in sessions)

    # Delete session
    response = client.delete(f"/api/v1/admin/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"

    # Verify deleted
    sessions_after = client.get("/api/v1/admin/sessions").json()
    assert not any(s["session_id"] == session_id for s in sessions_after)


def test_delete_session_not_found():
    response = client.delete("/api/v1/admin/sessions/nonexistent_session_xyz")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_list_tools_works():
    _ensure_agent_chat()
    response = client.get("/api/v1/admin/tools")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    # After a chat the agent's tools should be registered
    if data:
        tool = data[0]
        assert "name" in tool
        assert "risk_level" in tool
        assert "owner" in tool
