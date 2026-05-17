"""Admin API eval 增强端点的单元测试。

覆盖：
- POST /api/v1/admin/evals/{agent_id}/run  — 按需触发评测
- GET  /api/v1/admin/evals/compare         — 跨版本对比
- GET  /api/v1/admin/status                — 增强字段校验
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.admin import router as admin_router
from agent_platform.api.admin_deps import AdminDeps
from agent_platform.registry.registry import AgentNotFoundError

# ---------------------------------------------------------------------------
# 辅助工厂：构建测试用 FastAPI 应用
# ---------------------------------------------------------------------------


def _make_app(
    *,
    eval_repo=None,
    eval_runner=None,
    registry=None,
    quota_manager=None,
    runtime_manager=None,
    started_at: float | None = None,
    platform_version: str = "0.1.0-test",
) -> FastAPI:
    """构建一个最小 FastAPI 应用，用于测试 admin 路由。"""
    app = FastAPI()

    # 设置 runtime_manager 的默认 mock
    if runtime_manager is None:
        runtime_manager = MagicMock()
        runtime_manager.list_sessions = AsyncMock(return_value=[])
        runtime_manager.list_runs = AsyncMock(return_value=[])

    # 设置 registry 的默认 mock
    if registry is None:
        registry = MagicMock()
        registry.list_agents = AsyncMock(return_value=[])
        registry.list_deployments = AsyncMock(return_value=[])

    app.state.admin_deps = AdminDeps(
        registry=registry,
        runtime_manager=runtime_manager,
        audit_log=MagicMock(),
        tool_registry=MagicMock(),
        metrics=MagicMock(),
        eval_repo=eval_repo,
        eval_runner=eval_runner,
        quota_manager=quota_manager,
    )

    # 平台版本号：通过 app.version 属性注入
    app.version = platform_version

    # 启动时间戳：通过 app.state.started_at 注入
    if started_at is not None:
        app.state.started_at = started_at

    app.include_router(admin_router)
    return app


def _make_eval_report_dict(agent_id: str = "echo") -> dict:
    """生成模拟的 EvalReport.model_dump() 输出。"""
    return {
        "agent_id": agent_id,
        "agent_version": "1.0.0",
        "total": 4,
        "passed": 3,
        "pass_rate": 0.75,
        "required_pass_rate": 0.8,
        "gate_passed": False,
        "results": [
            {"id": "case_1", "passed": True, "reason": None, "scores": {}, "tags": []},
            {"id": "case_2", "passed": True, "reason": None, "scores": {}, "tags": []},
            {"id": "case_3", "passed": True, "reason": None, "scores": {}, "tags": []},
            {"id": "case_4", "passed": False, "reason": "输出不匹配", "scores": {}, "tags": []},
        ],
        "summary": {},
        "dataset_sources": [],
    }


# ---------------------------------------------------------------------------
# POST /api/v1/admin/evals/{agent_id}/run — 按需触发评测运行
# ---------------------------------------------------------------------------


class TestTriggerEvalRun:
    """按需触发评测运行端点测试。"""

    def test_success_returns_report(self):
        """正常路径：eval_runner 可用，agent 存在，返回评测报告。"""
        # 构造模拟 spec
        mock_spec = MagicMock()

        # 构造 registry：正常返回 spec
        registry = MagicMock()
        registry.list_agents = AsyncMock(return_value=[])
        registry.list_deployments = AsyncMock(return_value=[])
        registry.get = AsyncMock(return_value=mock_spec)

        # 构造 eval_runner：返回模拟的 EvalReport
        mock_report = MagicMock()
        mock_report.model_dump.return_value = _make_eval_report_dict("echo")
        runner = MagicMock()
        runner.run_agent = AsyncMock(return_value=mock_report)

        client = TestClient(
            _make_app(eval_runner=runner, registry=registry)
        )
        resp = client.post("/api/v1/admin/evals/echo/run")

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "echo"
        assert data["total"] == 4
        assert data["passed"] == 3
        assert data["pass_rate"] == 0.75
        # 确认 runner.run_agent 被正确调用
        runner.run_agent.assert_awaited_once_with(mock_spec)

    def test_agent_not_found_returns_404(self):
        """agent 不存在时返回 404。"""
        registry = MagicMock()
        registry.list_agents = AsyncMock(return_value=[])
        registry.list_deployments = AsyncMock(return_value=[])
        registry.get = AsyncMock(
            side_effect=AgentNotFoundError("agent not found: nonexistent")
        )

        runner = MagicMock()
        runner.run_agent = AsyncMock()

        client = TestClient(
            _make_app(eval_runner=runner, registry=registry)
        )
        resp = client.post("/api/v1/admin/evals/nonexistent/run")

        assert resp.status_code == 404
        assert "nonexistent" in resp.json()["detail"]
        # 确认 run_agent 未被调用
        runner.run_agent.assert_not_awaited()

    def test_eval_runner_none_returns_501(self):
        """eval_runner 为 None 时返回 501。"""
        client = TestClient(_make_app(eval_runner=None))
        resp = client.post("/api/v1/admin/evals/echo/run")

        assert resp.status_code == 501
        assert "eval runner not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/v1/admin/evals/compare — 跨版本对比
# ---------------------------------------------------------------------------


class TestCompareEvalRuns:
    """跨版本对比端点测试。"""

    def _make_run_records(self) -> list[dict]:
        """生成两次运行记录，用于对比测试。"""
        return [
            {
                "id": "run_a",
                "agent_id": "echo",
                "pass_rate": 0.75,
                "results": [
                    {"id": "case_1", "passed": True},
                    {"id": "case_2", "passed": True},
                    {"id": "case_3", "passed": True},
                    {"id": "case_4", "passed": False},
                ],
            },
            {
                "id": "run_b",
                "agent_id": "echo",
                "pass_rate": 1.0,
                "results": [
                    {"id": "case_1", "passed": True},
                    {"id": "case_2", "passed": True},
                    {"id": "case_3", "passed": True},
                    {"id": "case_4", "passed": True},
                ],
            },
        ]

    def test_compare_success(self):
        """正常路径：两个 run_id 都存在，返回对比结果。"""
        repo = MagicMock()
        repo.list_runs = AsyncMock(return_value=self._make_run_records())

        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get(
            "/api/v1/admin/evals/compare",
            params={"run_id_a": "run_a", "run_id_b": "run_b"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id_a"] == "run_a"
        assert data["run_id_b"] == "run_b"
        assert data["pass_rate_a"] == 0.75
        assert data["pass_rate_b"] == 1.0
        # pass_rate_delta = 1.0 - 0.75 = 0.25
        assert data["pass_rate_delta"] == 0.25
        # case_4 在 A 中失败，在 B 中通过 → 属于 fixed
        assert "case_4" in data["fixed"]
        # 没有新增失败
        assert data["new_failures"] == []

    def test_compare_detects_new_failures(self):
        """正确识别新增失败的用例。"""
        records = [
            {
                "id": "run_a",
                "agent_id": "echo",
                "pass_rate": 1.0,
                "results": [
                    {"id": "case_1", "passed": True},
                    {"id": "case_2", "passed": True},
                ],
            },
            {
                "id": "run_b",
                "agent_id": "echo",
                "pass_rate": 0.5,
                "results": [
                    {"id": "case_1", "passed": True},
                    {"id": "case_2", "passed": False},
                ],
            },
        ]
        repo = MagicMock()
        repo.list_runs = AsyncMock(return_value=records)

        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get(
            "/api/v1/admin/evals/compare",
            params={"run_id_a": "run_a", "run_id_b": "run_b"},
        )

        assert resp.status_code == 200
        data = resp.json()
        # case_2 在 A 中通过但在 B 中失败 → new_failures
        assert "case_2" in data["new_failures"]
        assert data["fixed"] == []
        assert data["pass_rate_delta"] == -0.5

    def test_compare_run_a_not_found_returns_404(self):
        """run_id_a 不存在时返回 404。"""
        repo = MagicMock()
        # 只返回 run_b 的记录
        repo.list_runs = AsyncMock(return_value=[
            {"id": "run_b", "agent_id": "echo", "pass_rate": 1.0, "results": []},
        ])

        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get(
            "/api/v1/admin/evals/compare",
            params={"run_id_a": "missing_run", "run_id_b": "run_b"},
        )

        assert resp.status_code == 404
        assert "missing_run" in resp.json()["detail"]

    def test_compare_run_b_not_found_returns_404(self):
        """run_id_b 不存在时返回 404。"""
        repo = MagicMock()
        # 只返回 run_a 的记录
        repo.list_runs = AsyncMock(return_value=[
            {"id": "run_a", "agent_id": "echo", "pass_rate": 0.75, "results": []},
        ])

        client = TestClient(_make_app(eval_repo=repo))
        resp = client.get(
            "/api/v1/admin/evals/compare",
            params={"run_id_a": "run_a", "run_id_b": "missing_run"},
        )

        assert resp.status_code == 404
        assert "missing_run" in resp.json()["detail"]

    def test_compare_eval_repo_none_returns_501(self):
        """eval_repo 为 None 时返回 501。"""
        client = TestClient(_make_app(eval_repo=None))
        resp = client.get(
            "/api/v1/admin/evals/compare",
            params={"run_id_a": "run_a", "run_id_b": "run_b"},
        )

        assert resp.status_code == 501
        assert "eval repo not configured" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/v1/admin/status — 增强字段校验
# ---------------------------------------------------------------------------


class TestSystemStatusEnhanced:
    """系统状态端点增强字段测试。"""

    def test_status_contains_platform_version(self):
        """返回中包含 platform_version 字段。"""
        client = TestClient(
            _make_app(platform_version="2.3.1")
        )
        resp = client.get("/api/v1/admin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "platform_version" in data
        assert data["platform_version"] == "2.3.1"

    def test_status_contains_uptime_seconds(self):
        """返回中包含 uptime_seconds 字段，且为正数。"""
        # 将 started_at 设置为 100 秒前
        started = time.time() - 100.0
        client = TestClient(
            _make_app(started_at=started)
        )
        resp = client.get("/api/v1/admin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "uptime_seconds" in data
        # 运行时间应大于等于 100 秒
        assert data["uptime_seconds"] >= 100.0

    def test_status_uptime_none_when_started_at_missing(self):
        """当 started_at 未设置时，uptime_seconds 应为 None。"""
        client = TestClient(_make_app())
        resp = client.get("/api/v1/admin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "uptime_seconds" in data
        assert data["uptime_seconds"] is None

    def test_status_contains_middleware_count(self):
        """返回中包含 middleware_count 字段，且为非负整数。"""
        client = TestClient(_make_app())
        resp = client.get("/api/v1/admin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "middleware_count" in data
        assert isinstance(data["middleware_count"], int)
        assert data["middleware_count"] >= 0

    def test_status_quota_configured_true(self):
        """当 quota_manager 不为 None 时，quota_configured 应为 True。"""
        client = TestClient(
            _make_app(quota_manager=MagicMock())
        )
        resp = client.get("/api/v1/admin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "quota_configured" in data
        assert data["quota_configured"] is True

    def test_status_quota_configured_false(self):
        """当 quota_manager 为 None 时，quota_configured 应为 False。"""
        client = TestClient(
            _make_app(quota_manager=None)
        )
        resp = client.get("/api/v1/admin/status")

        assert resp.status_code == 200
        data = resp.json()
        assert "quota_configured" in data
        assert data["quota_configured"] is False

    def test_status_contains_all_enhanced_fields(self):
        """一次性验证所有增强字段都出现在返回中。"""
        started = time.time() - 60.0
        client = TestClient(
            _make_app(
                platform_version="3.0.0",
                started_at=started,
                quota_manager=MagicMock(),
            )
        )
        resp = client.get("/api/v1/admin/status")

        assert resp.status_code == 200
        data = resp.json()

        # 基础字段
        assert "agents" in data
        assert "deployments" in data
        assert "active_sessions" in data
        assert "total_runs" in data

        # 增强字段
        assert "platform_version" in data
        assert "uptime_seconds" in data
        assert "middleware_count" in data
        assert "quota_configured" in data

        # 类型检查
        assert isinstance(data["platform_version"], str)
        assert isinstance(data["uptime_seconds"], float)
        assert isinstance(data["middleware_count"], int)
        assert isinstance(data["quota_configured"], bool)
