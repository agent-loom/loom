from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DesignBrief(BaseModel):
    decision: str
    reason: str
    components: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    landing_type: str = "agent:new"


class GeneratedTestPlan(BaseModel):
    agent_id: str
    test_files: list[str] = Field(default_factory=list)
    eval_cases: list[dict[str, Any]] = Field(default_factory=list)
    coverage_notes: str = ""


class ArchitectureDesignAgent:
    """Judges where a requirement should land in the platform architecture.

    Per design doc §9: determines if the change is prompt-only, new tool,
    new knowledge source, protocol change, or a full new Agent Package.
    """

    LANDING_RULES = [
        {
            "keywords": ["话术", "prompt", "回复风格", "语气"],
            "type": "prompt_change",
            "component": "prompts/",
        },
        {
            "keywords": ["新意图", "intent", "意图识别"],
            "type": "routing_change",
            "component": "policies/routing.yaml",
        },
        {
            "keywords": ["新工具", "tool", "API", "接口"],
            "type": "tool:new",
            "component": "tools/",
        },
        {
            "keywords": ["知识", "knowledge", "数据", "向量"],
            "type": "knowledge:sync",
            "component": "knowledge/",
        },
        {
            "keywords": ["协议", "protocol", "前端", "命令"],
            "type": "protocol:change",
            "component": "output",
        },
        {
            "keywords": ["新Agent", "新助手", "新业务"],
            "type": "agent:new",
            "component": "agent package",
        },
    ]

    def analyze(self, requirement_text: str, context: dict[str, Any] | None = None) -> DesignBrief:
        landing_type = "agent:change"
        components: list[str] = []
        risks: list[str] = []

        for rule in self.LANDING_RULES:
            if any(kw in requirement_text for kw in rule["keywords"]):
                landing_type = rule["type"]
                components.append(rule["component"])

        if not components:
            components = ["needs manual analysis"]
            risks.append("unable to automatically determine landing point")

        if landing_type == "agent:new":
            components.extend(["manifest.yaml", "adapter.py", "prompts/", "evals/", "tests/"])
            risks.append("new agent requires full eval suite before deployment")

        if landing_type == "protocol:change":
            risks.append("protocol changes require frontend coordination")

        reason = f"Based on keyword analysis, this requirement maps to: {landing_type}"

        return DesignBrief(
            decision=f"Implement as {landing_type}",
            reason=reason,
            components=components,
            risks=risks,
            landing_type=landing_type,
        )


class TestGenerationAgent:
    """Generates test plans and eval cases for agent changes.

    Per design doc §11: covers unit tests, integration tests, contract tests,
    eval cases, and regression tests.
    """

    def generate_plan(
        self,
        agent_id: str,
        change_type: str,
        changed_files: list[str] | None = None,
    ) -> GeneratedTestPlan:
        test_files: list[str] = []
        eval_cases: list[dict[str, Any]] = []

        if change_type in ("agent:new", "agent:change"):
            test_files.extend([
                f"tests/unit/test_{agent_id}_routing.py",
                f"tests/unit/test_{agent_id}_tools.py",
                f"tests/integration/test_{agent_id}_e2e.py",
            ])
            eval_cases.append({
                "id": f"{agent_id}_basic_001",
                "input": {"query": "hello"},
                "expected": {"output_contains": [agent_id]},
            })

        if change_type == "tool:new":
            test_files.append(f"tests/unit/test_{agent_id}_tools.py")
            eval_cases.append({
                "id": f"{agent_id}_tool_001",
                "input": {"query": "test tool invocation"},
                "expected": {"must_call_tools": []},
            })

        if change_type in ("knowledge:sync",):
            test_files.append(f"tests/integration/test_{agent_id}_knowledge.py")

        coverage_notes = (
            f"Change type '{change_type}' for agent '{agent_id}'. "
            f"Generated {len(test_files)} test file(s) and {len(eval_cases)} eval case(s)."
        )

        return GeneratedTestPlan(
            agent_id=agent_id,
            test_files=test_files,
            eval_cases=eval_cases,
            coverage_notes=coverage_notes,
        )
