from agent_platform.devflow.task_pack import TaskPackGenerator


def test_task_pack_generator():
    task = TaskPackGenerator().from_requirement(
        task_id="AGENT-123",
        title="新增促销推荐 Agent",
        task_type="agent:new",
        project_id="agent-platform",
        background="新增一个促销推荐 Agent",
        agent_id="promo_recommendation",
    )

    assert task.api_version == "devflow.agent-platform/v1"
    assert task.repository.work_branch == "feat/agent-123"
    assert task.agent["agent_id"] == "promo_recommendation"
    assert task.agent["package_path"] == "agents/promo_recommendation"
    assert task.repository.merge_request.labels == ["agent:new", "ai-generated"]
    assert task.repository.merge_request.reviewers == ["backend-owner", "product-owner"]
    assert "## Source Task" in task.repository.merge_request.description
    assert "## Human Review Checklist" in task.repository.merge_request.description
    assert "python scripts/run_agent_eval.py --agent promo_recommendation" in (
        task.repository.merge_request.description
    )
    assert task.validation["required_reports"] == ["eval-report.json"]
    assert "pyproject.toml" in task.scope["write_allowed"]
    assert "uv.lock" in task.scope["write_allowed"]
    assert "eval-report.json" in task.scope["write_allowed"]
