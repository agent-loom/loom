from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field


def _sanitize_branch_name(raw: str) -> str:
    """Sanitize a string for use as a git branch name suffix."""
    s = raw.lower()
    s = re.sub(r"[^a-z0-9/_-]", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


class TaskMetadata(BaseModel):
    """
    任务的元数据信息。
    """
    task_id: str
    title: str
    type: str
    priority: str = "P2"
    source: dict[str, Any] = Field(default_factory=dict)


class MergeRequestSpec(BaseModel):
    """
    合并请求（MR）的基本配置规范。
    """
    title: str
    labels: list[str] = Field(default_factory=list)
    reviewers: list[str] = Field(default_factory=list)
    description: str = ""


class RepositoryTarget(BaseModel):
    """
    代码仓库目标配置，包含分支和 MR 信息。
    """
    provider: Literal["gitlab"] = "gitlab"
    project_id: str
    default_branch: str = "main"
    work_branch: str
    merge_request: MergeRequestSpec = Field(default_factory=lambda: MergeRequestSpec(title=""))


class RequirementSpec(BaseModel):
    """
    开发需求规范，包含背景、用户场景和验收标准等。
    """
    background: str
    user_scenarios: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)


class DevelopmentTask(BaseModel):
    """
    开发任务核心模型（Task Pack）。
    描述了完整开发过程的配置：元数据、目标代码库、需求规范以及实现、验证、审查环节的约束。
    """
    api_version: Literal["devflow.agent-platform/v1"] = "devflow.agent-platform/v1"
    kind: Literal["DevelopmentTask"] = "DevelopmentTask"
    metadata: TaskMetadata
    repository: RepositoryTarget
    requirement: RequirementSpec
    agent: dict[str, Any] = Field(default_factory=dict)
    scope: dict[str, list[str]] = Field(default_factory=dict)
    implementation: dict[str, Any] = Field(default_factory=dict)
    validation: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)

    def merge_request_description(self) -> str:
        """
        生成合并请求的 Markdown 描述文本。
        汇总来源任务、需求、预期变更、验证方式和风险。
        """
        source = self.metadata.source
        source_label = source.get("url") or source.get("issue_id") or self.metadata.task_id
        changes = self.implementation.get("required_outputs", [])
        validation_commands = self.validation.get("commands", [])
        checklist = self.review.get("checklist", [])
        risk = self.requirement.non_goals or ["No known production release impact in MVP scope."]

        return "\n".join(
            [
                "## Source Task",
                str(source_label),
                "",
                "## Requirement Summary",
                self.requirement.background,
                "",
                "## Changes",
                *[f"- {item}" for item in changes],
                "",
                "## Validation",
                *[f"- `{command}`" for command in validation_commands],
                "",
                "## Risk",
                *[f"- {item}" for item in risk],
                "",
                "## Human Review Checklist",
                *[f"- [ ] {item}" for item in checklist],
                "",
            ]
        )


class TaskPackGenerator:
    """
    任务包生成器。
    用于将外部原始需求转换为标准的 DevelopmentTask (Task Pack) 模型，
    内置了平台级别的验证命令、审查项及范围控制等默认策略。
    """
    def from_requirement(
        self,
        *,
        task_id: str,
        title: str,
        task_type: str,
        project_id: str,
        background: str,
        agent_id: str | None = None,
        source: dict[str, Any] | None = None,
        user_scenarios: list[str] | None = None,
        acceptance: list[str] | None = None,
        non_goals: list[str] | None = None,
        reviewers: list[str] | None = None,
    ) -> DevelopmentTask:
        """
        通过给定的需求字段组合生成一个完整的开发任务对象。

        :param task_id: 外部任务的唯一标识。
        :param title: 任务标题。
        :param task_type: 任务的分类（例如 platform:change）。
        :param project_id: 代码仓库项目 ID。
        :param background: 需求背景描述。
        :param agent_id: 指定的 AI Agent 的 ID（如有）。
        :param source: 任务来源信息字典。
        ...
        """
        branch_suffix = _sanitize_branch_name(task_id)
        source = source or {"system": "manual", "issue_id": task_id}
        reviewers = reviewers or ["backend-owner", "product-owner"]
        agent_package_path = f"agents/{agent_id}" if agent_id else "agents/<agent_id>"
        
        # 定义验证流程涉及的基础命令
        validation_commands = [
            "pytest tests/unit",
            "pytest tests/contract",
            f"python scripts/validate_manifest.py {agent_package_path}/manifest.yaml",
            (
                f"python scripts/run_agent_eval.py --agent {agent_id or '<agent_id>'} "
                "--report eval-report.json"
            ),
        ]
        
        task = DevelopmentTask(
            metadata=TaskMetadata(task_id=task_id, title=title, type=task_type, source=source),
            repository=RepositoryTarget(
                project_id=project_id,
                work_branch=f"feat/{branch_suffix}",
                merge_request=MergeRequestSpec(
                    title=title,
                    labels=[task_type, "ai-generated"],
                    reviewers=reviewers,
                ),
            ),
            requirement=RequirementSpec(
                background=background,
                user_scenarios=user_scenarios or [],
                acceptance=acceptance
                or [
                    "实现范围符合 task pack",
                    "返回标准 AgentResponse",
                    "新增或修改 Agent 的 eval gate 通过",
                ],
                non_goals=non_goals
                or [
                    "不自动发布生产",
                    "不修改未列入 scope 的模块",
                ],
            ),
            agent={
                "agent_id": agent_id,
                "package_path": agent_package_path,
                "runtime_backend": "native",
            }
            if agent_id
            else {},
            # 限定允许修改的代码范围
            scope={
                "write_allowed": ["src/agent_platform/**", "agents/**", "tests/**", "docs/**"],
                "write_denied": [".env", "secrets/**", "deploy/prod/**", "infra/prod/**"],
            },
            implementation={
                "required_outputs": [
                    f"{agent_package_path}/manifest.yaml",
                    f"{agent_package_path}/prompts/orchestrator.md",
                    f"{agent_package_path}/evals/golden.yaml",
                    "tests/unit",
                    "docs update if contract changes",
                ],
                "constraints": [
                    "不允许写入密钥",
                    "修改核心契约必须同步 docs/contracts",
                    "新增或修改 Agent 必须补 eval",
                    "新增工具必须声明 timeout",
                    "输出 command 必须在 manifest command_allowlist 内",
                ]
            },
            validation={
                "commands": validation_commands,
                "required_reports": ["eval-report.json"],
            },
            review={
                "required_reviewers": reviewers,
                "checklist": [
                    "manifest 已校验",
                    "tests 已补充",
                    "eval 已通过",
                    "无密钥泄露",
                    "可回滚",
                ],
            },
        )
        task.repository.merge_request.description = task.merge_request_description()
        return task
