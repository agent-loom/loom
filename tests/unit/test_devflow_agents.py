"""Tests for ArchitectureDesignAgent + TestGenerationAgent — src/agent_platform/devflow/agents.py"""

from __future__ import annotations

import pytest

from agent_platform.devflow.agents import (
    ArchitectureDesignAgent,
    DesignBrief,
    TestGenerationAgent,
    TestPlan,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def architect() -> ArchitectureDesignAgent:
    return ArchitectureDesignAgent()


@pytest.fixture
def test_agent() -> TestGenerationAgent:
    return TestGenerationAgent()


# ---------------------------------------------------------------------------
# Tests — ArchitectureDesignAgent.analyze()
# ---------------------------------------------------------------------------

def test_analyze_returns_design_brief(architect: ArchitectureDesignAgent):
    brief = architect.analyze("need a new prompt for customer greeting")
    assert isinstance(brief, DesignBrief)
    assert brief.decision
    assert brief.reason
    assert brief.landing_type


def test_analyze_detects_prompt_change(architect: ArchitectureDesignAgent):
    brief = architect.analyze("修改话术，让回复更友好")
    assert brief.landing_type == "prompt_change"
    assert "prompts/" in brief.components


def test_analyze_detects_routing_change(architect: ArchitectureDesignAgent):
    brief = architect.analyze("添加新意图识别规则")
    assert brief.landing_type == "routing_change"
    assert "policies/routing.yaml" in brief.components


def test_analyze_detects_tool_new(architect: ArchitectureDesignAgent):
    brief = architect.analyze("需要接入新的API接口")
    assert brief.landing_type == "tool:new"
    assert "tools/" in brief.components


def test_analyze_detects_knowledge_sync(architect: ArchitectureDesignAgent):
    brief = architect.analyze("更新知识库数据")
    assert brief.landing_type == "knowledge:sync"
    assert "knowledge/" in brief.components


def test_analyze_detects_protocol_change(architect: ArchitectureDesignAgent):
    brief = architect.analyze("修改前端协议格式")
    assert brief.landing_type == "protocol:change"
    assert "output" in brief.components
    assert any("frontend coordination" in r for r in brief.risks)


def test_analyze_detects_agent_new(architect: ArchitectureDesignAgent):
    brief = architect.analyze("创建新Agent处理退货业务")
    assert brief.landing_type == "agent:new"
    assert "manifest.yaml" in brief.components
    assert "adapter.py" in brief.components
    assert "prompts/" in brief.components
    assert "evals/" in brief.components
    assert "tests/" in brief.components
    assert any("full eval suite" in r for r in brief.risks)


def test_analyze_fallback_for_unknown_requirement(architect: ArchitectureDesignAgent):
    """When no keyword matches, should return agent:change with manual analysis note."""
    brief = architect.analyze("something completely unrelated")
    assert brief.landing_type == "agent:change"
    assert "needs manual analysis" in brief.components
    assert any("unable to automatically determine" in r for r in brief.risks)


def test_analyze_empty_input(architect: ArchitectureDesignAgent):
    """Empty input should not crash and should return a valid DesignBrief."""
    brief = architect.analyze("")
    assert isinstance(brief, DesignBrief)
    assert brief.landing_type == "agent:change"
    assert "needs manual analysis" in brief.components


def test_analyze_with_context(architect: ArchitectureDesignAgent):
    """Context parameter is accepted even if not currently used."""
    brief = architect.analyze(
        "需要新工具",
        context={"project": "retail"},
    )
    assert isinstance(brief, DesignBrief)
    assert brief.landing_type == "tool:new"


def test_analyze_multiple_keyword_matches(architect: ArchitectureDesignAgent):
    """When multiple keywords match, all matching components should appear."""
    # "新工具" matches tool:new, "API" also matches tool:new
    brief = architect.analyze("新工具对接API")
    assert brief.landing_type == "tool:new"
    assert "tools/" in brief.components


# ---------------------------------------------------------------------------
# Tests — TestGenerationAgent.generate_plan()
# ---------------------------------------------------------------------------

def test_generate_plan_returns_test_plan(test_agent: TestGenerationAgent):
    plan = test_agent.generate_plan(agent_id="myj", change_type="agent:new")
    assert isinstance(plan, TestPlan)
    assert plan.agent_id == "myj"


def test_generate_plan_agent_new(test_agent: TestGenerationAgent):
    plan = test_agent.generate_plan(agent_id="myj", change_type="agent:new")

    assert len(plan.test_files) == 3
    assert "tests/unit/test_myj_routing.py" in plan.test_files
    assert "tests/unit/test_myj_tools.py" in plan.test_files
    assert "tests/integration/test_myj_e2e.py" in plan.test_files
    assert len(plan.eval_cases) == 1
    assert plan.eval_cases[0]["id"] == "myj_basic_001"


def test_generate_plan_agent_change(test_agent: TestGenerationAgent):
    plan = test_agent.generate_plan(agent_id="echo", change_type="agent:change")

    assert len(plan.test_files) == 3
    assert "tests/unit/test_echo_routing.py" in plan.test_files
    assert len(plan.eval_cases) == 1


def test_generate_plan_tool_new(test_agent: TestGenerationAgent):
    plan = test_agent.generate_plan(agent_id="myj", change_type="tool:new")

    assert len(plan.test_files) == 1
    assert "tests/unit/test_myj_tools.py" in plan.test_files
    assert len(plan.eval_cases) == 1
    assert plan.eval_cases[0]["id"] == "myj_tool_001"


def test_generate_plan_knowledge_sync(test_agent: TestGenerationAgent):
    plan = test_agent.generate_plan(agent_id="myj", change_type="knowledge:sync")

    assert len(plan.test_files) == 1
    assert "tests/integration/test_myj_knowledge.py" in plan.test_files
    assert len(plan.eval_cases) == 0


def test_generate_plan_unknown_change_type(test_agent: TestGenerationAgent):
    """Unknown change type should produce empty test files and eval cases."""
    plan = test_agent.generate_plan(agent_id="myj", change_type="prompt_change")

    assert len(plan.test_files) == 0
    assert len(plan.eval_cases) == 0
    assert "prompt_change" in plan.coverage_notes


def test_generate_plan_coverage_notes(test_agent: TestGenerationAgent):
    plan = test_agent.generate_plan(agent_id="myj", change_type="agent:new")

    assert "agent:new" in plan.coverage_notes
    assert "myj" in plan.coverage_notes
    assert "3 test file(s)" in plan.coverage_notes
    assert "1 eval case(s)" in plan.coverage_notes


def test_generate_plan_with_changed_files(test_agent: TestGenerationAgent):
    """changed_files parameter should be accepted even if not used in current impl."""
    plan = test_agent.generate_plan(
        agent_id="myj",
        change_type="agent:change",
        changed_files=["agents/myj/adapter.py", "agents/myj/prompts/orchestrator.md"],
    )
    assert isinstance(plan, TestPlan)
    assert plan.agent_id == "myj"


def test_generate_plan_empty_agent_id(test_agent: TestGenerationAgent):
    """Empty agent_id should not crash."""
    plan = test_agent.generate_plan(agent_id="", change_type="agent:new")
    assert isinstance(plan, TestPlan)
    assert plan.agent_id == ""
