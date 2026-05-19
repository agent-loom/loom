"""ImprovementProposal → DevelopmentTask 转换器。

将自进化引擎生成的提案转换为 DevFlow 可执行的 TaskPack，
使用提案的 allowed_paths/blocked_paths 收紧 PathGuard 范围。
"""
from __future__ import annotations

from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    MergeRequestSpec,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
    _sanitize_branch_name,
)

from .models import ImprovementProposal


class ProposalToTaskPackConverter:
    """将 ImprovementProposal 转换为 DevelopmentTask（TaskPack）。"""

    def __init__(self, *, gitlab_project_id: str, default_branch: str = "main") -> None:
        self._gitlab_project_id = gitlab_project_id
        self._default_branch = default_branch

    def convert(self, proposal: ImprovementProposal) -> DevelopmentTask:
        branch_suffix = _sanitize_branch_name(
            f"evo-{proposal.agent_id}-{proposal.proposal_id[-8:]}",
        )

        evidence_lines = "\n".join(
            f"- [{e.type}] {e.summary}" for e in proposal.evidence
        )
        changes_lines = "\n".join(
            f"- `{c.path}`: {c.description}" for c in proposal.proposed_changes
        )
        background = (
            f"## 自进化提案\n\n"
            f"**Proposal ID:** {proposal.proposal_id}\n"
            f"**根因:** {proposal.root_cause.category}（置信度 {proposal.root_cause.confidence}）\n"
            f"**说明:** {proposal.root_cause.explanation}\n\n"
            f"## 证据\n\n{evidence_lines}\n\n"
            f"## 预期修改\n\n{changes_lines}"
        )

        write_allowed = list(proposal.allowed_paths) if proposal.allowed_paths else [
            f"agents/{proposal.agent_id}/**",
            "tests/contract/**",
            "docs/**",
        ]
        write_denied = list(proposal.blocked_paths) if proposal.blocked_paths else [
            "src/agent_platform/**",
            "deploy/**",
            ".env",
            "secrets/**",
        ]

        validation_commands = list(proposal.validation.commands)
        if not any("eval" in cmd for cmd in validation_commands):
            validation_commands.append(
                f"python scripts/run_agent_eval.py --agent {proposal.agent_id} "
                "--report eval-report.json",
            )

        required_outputs = [c.path for c in proposal.proposed_changes]

        task = DevelopmentTask(
            metadata=TaskMetadata(
                task_id=proposal.proposal_id,
                title=proposal.title,
                type=proposal.task_type,
                priority="P2" if proposal.risk.level == "low" else "P1",
                source={
                    "system": "evolution_engine",
                    "proposal_id": proposal.proposal_id,
                    "risk_level": proposal.risk.level,
                },
            ),
            repository=RepositoryTarget(
                project_id=self._gitlab_project_id,
                default_branch=self._default_branch,
                work_branch=f"evo/{branch_suffix}",
                merge_request=MergeRequestSpec(
                    title=f"[Evolution] {proposal.title}",
                    labels=[
                        proposal.task_type,
                        "evolution",
                        f"risk:{proposal.risk.level}",
                        "ai-generated",
                    ],
                    reviewers=["backend-owner"],
                ),
            ),
            requirement=RequirementSpec(
                background=background,
                acceptance=[
                    "修改范围严格限制在 allowed_paths 内",
                    "现有 eval 回归测试通过",
                    "新增回归用例覆盖本次问题",
                ],
                non_goals=[
                    "不修改平台核心代码",
                    "不自动发布生产",
                ],
            ),
            agent={
                "agent_id": proposal.agent_id,
                "package_path": f"agents/{proposal.agent_id}",
                "runtime_backend": "native",
            },
            scope={
                "write_allowed": write_allowed,
                "write_denied": write_denied,
            },
            implementation={
                "required_outputs": required_outputs,
                "constraints": [
                    "不允许写入密钥",
                    "修改必须在 proposal 的 allowed_paths 范围内",
                    "新增或修改 prompt 必须补 eval",
                ],
            },
            validation={
                "commands": validation_commands,
                "required_reports": ["eval-report.json"],
                "regression_allowed": proposal.validation.existing_eval_regression_allowed,
            },
            review={
                "required_reviewers": ["backend-owner"],
                "checklist": [
                    "变更范围符合 proposal 的 allowed_paths",
                    "eval 回归通过",
                    "无密钥泄露",
                    f"根因 {proposal.root_cause.category} 已修复",
                ],
            },
        )
        task.repository.merge_request.description = task.merge_request_description()
        return task
