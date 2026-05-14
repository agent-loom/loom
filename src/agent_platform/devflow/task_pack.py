from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TaskMetadata(BaseModel):
    task_id: str
    title: str
    type: str
    priority: str = "P2"
    source: dict[str, Any] = Field(default_factory=dict)


class MergeRequestSpec(BaseModel):
    title: str
    labels: list[str] = Field(default_factory=list)
    reviewers: list[str] = Field(default_factory=list)
    description: str = ""


class RepositoryTarget(BaseModel):
    provider: Literal["gitlab"] = "gitlab"
    project_id: str
    default_branch: str = "main"
    work_branch: str
    merge_request: MergeRequestSpec = Field(default_factory=lambda: MergeRequestSpec(title=""))


class RequirementSpec(BaseModel):
    background: str
    user_scenarios: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)


class DevelopmentTask(BaseModel):
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
        branch_suffix = task_id.lower().replace("_", "-")
        source = source or {"system": "manual", "issue_id": task_id}
        reviewers = reviewers or ["backend-owner", "product-owner"]
        agent_package_path = f"agents/{agent_id}" if agent_id else "agents/<agent_id>"
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
