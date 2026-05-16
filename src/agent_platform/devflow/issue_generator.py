"""根据解析后的需求自动生成 Issue。"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from agent_platform.devflow.requirement_parser import ParsedRequirement

logger = logging.getLogger(__name__)


class GeneratedIssue(BaseModel):
    """自动生成的 Issue 数据模型。"""
    title: str
    type: str
    description: str
    labels: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    agent_id: str | None = None


class IssueGenerator:
    """根据解析后的需求生成结构化 Issue 列表。"""
    ISSUE_TEMPLATE = """## 背景

{background}

## 用户场景

{scenarios}

## 期望行为

{expected}

## 非目标

{non_goals}

## 涉及 Agent

{agent_id}

## 验收标准

{acceptance}

## 测试要求

- 单元测试覆盖核心逻辑
- eval case 覆盖关键意图
- 集成测试验证端到端

## 风险点

{risks}

## 发布计划

staging → eval gate → human review → prod canary → prod
"""

    def generate(
        self,
        requirement: ParsedRequirement,
        project_context: dict[str, Any] | None = None,
    ) -> list[GeneratedIssue]:
        """根据需求类型生成对应的 Issue 列表。"""
        project_context = project_context or {}
        issues: list[GeneratedIssue] = []

        if requirement.suggested_type == "agent:new":
            issues.extend(self._generate_new_agent_issues(requirement))
        elif requirement.suggested_type == "agent:change":
            issues.extend(self._generate_change_issues(requirement))
        elif requirement.suggested_type == "tool:new":
            issues.extend(self._generate_tool_issues(requirement))
        else:
            issues.append(self._generate_generic_issue(requirement))

        return issues

    def _generate_new_agent_issues(self, req: ParsedRequirement) -> list[GeneratedIssue]:
        agent_id = req.suggested_agent_id or "new_agent"
        return [
            GeneratedIssue(
                title=f"创建 {agent_id} Agent manifest 和目录结构",
                type="agent:new",
                description=self._format_description(req, "创建 Agent Package 基础结构"),
                labels=["agent:new", "ai-generated"],
                acceptance=[
                    "manifest.yaml 通过校验",
                    "目录结构完整",
                    "prompts 文件已创建",
                ],
                agent_id=agent_id,
            ),
            GeneratedIssue(
                title=f"实现 {agent_id} Agent 适配器",
                type="agent:new",
                description=self._format_description(req, "实现 Agent 运行时适配器"),
                labels=["agent:new", "ai-generated"],
                acceptance=[
                    "adapter 实现 run() 方法",
                    "工具调用正常",
                    "单元测试通过",
                ],
                dependencies=[f"创建 {agent_id} Agent manifest 和目录结构"],
                agent_id=agent_id,
            ),
            GeneratedIssue(
                title=f"创建 {agent_id} Agent eval cases",
                type="eval:add",
                description=self._format_description(req, "创建评测用例"),
                labels=["eval:add", "ai-generated"],
                acceptance=[
                    "eval 覆盖关键意图",
                    "pass rate >= required",
                ],
                dependencies=[f"实现 {agent_id} Agent 适配器"],
                agent_id=agent_id,
            ),
        ]

    def _generate_change_issues(self, req: ParsedRequirement) -> list[GeneratedIssue]:
        return [
            GeneratedIssue(
                title=req.title,
                type="agent:change",
                description=self._format_description(req, "修改已有 Agent"),
                labels=["agent:change", "ai-generated"],
                acceptance=req.acceptance or ["修改后 eval gate 通过"],
                agent_id=req.suggested_agent_id,
            ),
        ]

    def _generate_tool_issues(self, req: ParsedRequirement) -> list[GeneratedIssue]:
        return [
            GeneratedIssue(
                title=req.title,
                type="tool:new",
                description=self._format_description(req, "新增业务工具"),
                labels=["tool:new", "ai-generated"],
                acceptance=req.acceptance or ["工具有超时和参数校验", "单元测试通过"],
                agent_id=req.suggested_agent_id,
            ),
        ]

    def _generate_generic_issue(self, req: ParsedRequirement) -> GeneratedIssue:
        return GeneratedIssue(
            title=req.title,
            type=req.suggested_type,
            description=self._format_description(req, "平台变更"),
            labels=[req.suggested_type, "ai-generated"],
            acceptance=req.acceptance or ["变更后测试通过"],
            agent_id=req.suggested_agent_id,
        )

    def _format_description(self, req: ParsedRequirement, summary: str) -> str:
        return self.ISSUE_TEMPLATE.format(
            background=f"{summary}: {req.goal}",
            scenarios="\n".join(f"- {u}" for u in req.users) or "待补充",
            expected="\n".join(f"- {o}" for o in req.outputs) or "待补充",
            non_goals="- 不自动发布生产\n- 不修改未列入 scope 的模块",
            agent_id=req.suggested_agent_id or "待确定",
            acceptance="\n".join(f"- {a}" for a in req.acceptance) or "待补充",
            risks="- 待评估",
        )
