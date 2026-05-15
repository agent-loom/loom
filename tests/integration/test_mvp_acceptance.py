"""Integration tests covering MVP acceptance criteria §6.1–§6.3."""
import json

from fastapi.testclient import TestClient

from agent_platform.api.app import app


class TestProductionPipeline:
    """MVP §6.1 — 生产链路验收: register → chat → trace → agent run."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_register_chat_trace_flow(self):
        agents = self.client.get("/api/v1/agents")
        assert agents.status_code == 200
        agent_ids = [a["agent_id"] for a in agents.json()]
        assert "myj" in agent_ids

        chat = self.client.post(
            "/api/v1/agent/chat",
            json={
                "request_id": "req_int_001",
                "agent_id": "myj",
                "session_id": "sess_int_001",
                "context": {"tenant": {"retailer_id": "myj"}},
                "input": {"query": "帮我推荐一瓶低糖饮料"},
            },
        )
        assert chat.status_code == 200
        data = chat.json()

        assert data["agent"]["agent_id"] == "myj"
        assert data["output"]["status"] == "completed"
        assert "低糖" in data["output"]["text"]["display"]

        assert data["trace"] is not None
        assert data["trace"]["run_id"] is not None
        assert "worker:" in data["trace"]["route_reason"]
        assert len(data["trace"]["tool_calls"]) > 0
        assert data["trace"]["tool_calls"][0]["tool_name"] == "myj.goods_search"

        runs = self.client.get("/api/v1/agent-runs")
        assert runs.status_code == 200
        run_list = runs.json()
        matched = [r for r in run_list if r["request_id"] == "req_int_001"]
        assert len(matched) >= 1
        assert matched[0]["agent_id"] == "myj"
        assert matched[0]["status"] == "succeeded"

    def test_router_routes_by_retailer_id(self):
        chat = self.client.post(
            "/api/v1/agent/chat",
            json={
                "request_id": "req_int_002",
                "context": {"tenant": {"retailer_id": "myj"}},
                "input": {"query": "test routing"},
            },
        )
        assert chat.status_code == 200
        assert chat.json()["agent"]["agent_id"] == "myj"
        # In orchestrator_workers mode, route_reason reflects the worker selection
        assert "worker:" in chat.json()["trace"]["route_reason"]

    def test_unknown_agent_returns_standard_error(self):
        chat = self.client.post(
            "/api/v1/agent/chat",
            json={
                "agent_id": "nonexistent",
                "input": {"query": "hello"},
            },
        )
        assert chat.status_code == 404
        data = chat.json()
        assert data["error"]["code"] == "AGENT_NOT_FOUND"
        assert data["output"]["status"] == "failed"

    def test_missing_required_context(self):
        chat = self.client.post(
            "/api/v1/agent/chat",
            json={
                "agent_id": "myj",
                "input": {"query": "hello"},
            },
        )
        assert chat.status_code == 400
        data = chat.json()
        assert data["error"]["code"] == "INVALID_REQUEST"
        assert "context.tenant.retailer_id" in data["error"]["message"]


class TestEvalPipeline:
    """MVP §6.2 — 评测链路验收: load cases → run → report → gate."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_eval_run_returns_report(self):
        response = self.client.post(
            "/api/v1/evals/run",
            json={"agent_id": "myj"},
        )
        assert response.status_code == 200
        report = response.json()
        assert report["agent_id"] == "myj"
        assert report["total"] >= 1
        assert report["gate_passed"] is True
        assert report["pass_rate"] >= report["required_pass_rate"]

    def test_eval_echo_agent(self):
        response = self.client.post(
            "/api/v1/evals/run",
            json={"agent_id": "echo"},
        )
        assert response.status_code == 200
        report = response.json()
        assert report["agent_id"] == "echo"
        assert report["gate_passed"] is True

    def test_eval_unknown_agent_returns_404(self):
        response = self.client.post(
            "/api/v1/evals/run",
            json={"agent_id": "nonexistent"},
        )
        assert response.status_code == 404


class TestDevFlowPipeline:
    """MVP §6.3 — 研发链路验收: task pack → deploy."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_task_pack_generation(self):
        response = self.client.post(
            "/api/v1/devflow/task-packs",
            json={
                "task_id": "AGENT-999",
                "title": "新增 FAQ Agent",
                "task_type": "agent:new",
                "project_id": "meiyijia_agent_hw",
                "background": "需要一个 FAQ agent 回答常见问题",
                "agent_id": "faq",
            },
        )
        assert response.status_code == 200
        task = response.json()
        assert task["api_version"] == "devflow.agent-platform/v1"
        assert task["kind"] == "DevelopmentTask"
        assert task["metadata"]["task_id"] == "AGENT-999"
        assert task["repository"]["work_branch"] == "feat/agent-999"
        assert task["agent"]["agent_id"] == "faq"
        assert any("validate_manifest" in cmd for cmd in task["validation"]["commands"])
        assert any("run_agent_eval" in cmd for cmd in task["validation"]["commands"])

    def test_deploy_with_eval_gate(self):
        deploy_no_eval = self.client.post(
            "/api/v1/agent-packages/myj/versions/0.1.0/deploy",
            json={"channel": "staging", "eval_passed": False},
        )
        assert deploy_no_eval.status_code == 409

        deploy_ok = self.client.post(
            "/api/v1/agent-packages/myj/versions/0.1.0/deploy",
            json={"channel": "staging", "eval_passed": True},
        )
        assert deploy_ok.status_code == 200
        data = deploy_ok.json()
        assert data["status"] == "staging"
        assert data["agent_id"] == "myj"

    def test_deploy_prod_canary(self):
        response = self.client.post(
            "/api/v1/agent-packages/myj/versions/0.1.0/deploy",
            json={"channel": "prod", "traffic_percent": 5, "eval_passed": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "prod_canary"
        assert data["traffic_percent"] == 5

    def test_webhook_accepts_without_secret(self):
        response = self.client.post(
            "/api/v1/integrations/plane/webhook",
            content=json.dumps({"data": {"id": "test"}}),
            headers={
                "X-Plane-Delivery": "del-001",
                "X-Plane-Event": "work_item.updated",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["event"] == "work_item.updated"


class TestExtensibility:
    """MVP §7 — 成功标准验证."""

    def setup_method(self):
        self.client = TestClient(app)

    def test_second_agent_works_without_core_changes(self):
        response = self.client.post(
            "/api/v1/agent/chat",
            json={
                "agent_id": "echo",
                "input": {"query": "extensibility test"},
            },
        )
        assert response.status_code == 200
        assert "extensibility test" in response.json()["output"]["text"]["display"]

    def test_agents_list_contains_both(self):
        response = self.client.get("/api/v1/agents")
        ids = {a["agent_id"] for a in response.json()}
        assert ids >= {"myj", "echo"}
