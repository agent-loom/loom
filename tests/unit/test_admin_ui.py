"""S8 Phase 5 — Admin UI 测试。

覆盖:
- Admin 路由可访问 (全部页面返回 200)
- 模板渲染不报错
- Admin router 可导入
- 页面包含关键元素
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    import os
    os.environ.setdefault("AGENT_PLATFORM_API_KEY", "")
    from agent_platform.api.app import create_app
    app = create_app()
    return TestClient(app)


class TestAdminRoutes:
    def test_dashboard_accessible(self, client):
        resp = client.get("/admin/")
        assert resp.status_code == 200
        assert "Agent Platform" in resp.text
        assert "仪表盘" in resp.text

    def test_agents_panel(self, client):
        resp = client.get("/admin/agents")
        assert resp.status_code == 200
        assert "Agent 管理" in resp.text

    def test_evals_panel(self, client):
        resp = client.get("/admin/evals")
        assert resp.status_code == 200
        assert "Eval" in resp.text

    def test_devflow_panel(self, client):
        resp = client.get("/admin/devflow")
        assert resp.status_code == 200
        assert "DevFlow" in resp.text

    def test_deployments_panel(self, client):
        resp = client.get("/admin/deployments")
        assert resp.status_code == 200
        assert "部署" in resp.text

    def test_sessions_panel(self, client):
        resp = client.get("/admin/sessions")
        assert resp.status_code == 200
        assert "会话" in resp.text

    def test_observability_panel(self, client):
        resp = client.get("/admin/observability")
        assert resp.status_code == 200
        assert "可观测性" in resp.text
        assert "Prometheus" in resp.text
        assert "Grafana" in resp.text

    def test_login_page(self, client):
        resp = client.get("/admin/login")
        assert resp.status_code == 200
        assert "API Key" in resp.text


class TestAdminRouterImportable:
    def test_router_import(self):
        from agent_platform.admin.routes import router
        assert router is not None
        assert router.prefix == "/admin"

    def test_template_dir_exists(self):
        from pathlib import Path

        from agent_platform.admin.routes import _TEMPLATE_DIR
        assert Path(_TEMPLATE_DIR).is_dir()
        assert (Path(_TEMPLATE_DIR) / "base.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "dashboard.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "agents.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "evals.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "devflow.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "observability.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "sessions.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "deployments.html").is_file()
        assert (Path(_TEMPLATE_DIR) / "login.html").is_file()


class TestAdminNavigation:
    def test_dashboard_has_nav_links(self, client):
        resp = client.get("/admin/")
        html = resp.text
        assert "/admin/agents" in html
        assert "/admin/evals" in html
        assert "/admin/devflow" in html
        assert "/admin/observability" in html
        assert "/admin/sessions" in html
        assert "/admin/deployments" in html

    def test_sidebar_present_on_all_pages(self, client):
        for path in ["/admin/", "/admin/agents", "/admin/evals",
                     "/admin/devflow", "/admin/observability"]:
            resp = client.get(path)
            assert "管理控制台" in resp.text, f"{path} 缺少侧边栏"
