"""Tests for admin eval + audit endpoints and pre-deploy validation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.admin import router as admin_router
from agent_platform.api.admin_deps import AdminDeps


def _make_app(eval_repo=None, audit_log=None) -> FastAPI:
    app = FastAPI()
    app.state.admin_deps = AdminDeps(
        registry=MagicMock(),
        runtime_manager=MagicMock(),
        audit_log=audit_log or MagicMock(),
        tool_registry=MagicMock(),
        metrics=MagicMock(),
        eval_repo=eval_repo,
    )
    app.include_router(admin_router)
    return app


class TestListEvalRuns:
    def test_no_eval_repo_returns_501(self):
        client = TestClient(_make_app(eval_repo=None))
        resp = client.get("/api/v1/admin/evals")
        assert resp.status_code == 501

    def test_list_eval_runs_returns_array(self):
        repo = MagicMock()
        repo.list_runs = AsyncMock(return_value=[
            {"agent_id": "echo", "total": 4, "passed": 4},
        ])
        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get("/api/v1/admin/evals")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_eval_runs_with_agent_filter(self):
        repo = MagicMock()
        repo.list_runs = AsyncMock(return_value=[])
        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get("/api/v1/admin/evals?agent_id=echo")
        assert resp.status_code == 200
        repo.list_runs.assert_called_once_with(agent_id="echo", limit=50)


class TestGetLatestEval:
    def test_no_eval_repo_returns_501(self):
        client = TestClient(_make_app(eval_repo=None))
        resp = client.get("/api/v1/admin/evals/echo/latest")
        assert resp.status_code == 501

    def test_latest_eval_found(self):
        repo = MagicMock()
        repo.get_latest = AsyncMock(return_value={
            "agent_id": "echo", "pass_rate": 1.0,
        })
        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get("/api/v1/admin/evals/echo/latest")
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "echo"

    def test_latest_eval_not_found(self):
        repo = MagicMock()
        repo.get_latest = AsyncMock(return_value=None)
        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get("/api/v1/admin/evals/nonexistent/latest")
        assert resp.status_code == 404


class TestAuditEndpoint:
    def test_list_audit_events(self):
        event = MagicMock()
        event.model_dump.return_value = {
            "event_type": "deploy",
            "agent_id": "echo",
        }
        audit_log = MagicMock()
        audit_log.list_events = AsyncMock(return_value=[event])
        client = TestClient(_make_app(audit_log=audit_log))
        resp = client.get("/api/v1/admin/audit")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["event_type"] == "deploy"

    def test_list_audit_with_filters(self):
        audit_log = MagicMock()
        audit_log.list_events = AsyncMock(return_value=[])
        client = TestClient(_make_app(audit_log=audit_log))
        resp = client.get(
            "/api/v1/admin/audit?agent_id=echo&channel=staging&limit=10"
        )
        assert resp.status_code == 200
        audit_log.list_events.assert_called_once_with(
            agent_id="echo", channel="staging", limit=10,
        )
