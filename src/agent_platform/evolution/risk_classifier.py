"""风险分类器：根据 proposed_changes 路径和 root_cause 自动判断提案风险等级。"""
from __future__ import annotations

import fnmatch

from .models import (
    ImprovementProposal,
    ProposedChange,
    RiskAssessment,
    RiskLevel,
    RootCauseCategory,
)

_LOW_RISK_PATTERNS: list[str] = [
    "agents/*/prompts/**",
    "agents/*/evals/**",
    "tests/contract/**",
    "docs/**",
]

_MEDIUM_RISK_PATTERNS: list[str] = [
    "agents/*/knowledge/**",
    "agents/*/manifest.yaml",
    "agents/*/adapters/**",
    "agents/*/tools/**",
    "tests/unit/**",
    "tests/integration/**",
]

_BLOCKED_PATTERNS: list[str] = [
    "src/agent_platform/**",
    "deploy/**",
    "infra/**",
    "scripts/deploy/**",
    ".env",
    ".env.*",
    "secrets/**",
    "**/*secret*",
    "**/*token*",
]

_HIGH_RISK_ROOT_CAUSES: set[RootCauseCategory] = {
    RootCauseCategory.PLATFORM_BUG,
    RootCauseCategory.PRODUCT_REQUIREMENT,
}

_MEDIUM_RISK_ROOT_CAUSES: set[RootCauseCategory] = {
    RootCauseCategory.TOOL_SCHEMA_GAP,
    RootCauseCategory.TOOL_RUNTIME_ERROR,
    RootCauseCategory.ROUTING_ERROR,
    RootCauseCategory.FRONTEND_CONTRACT_GAP,
}


def _matches_any(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def classify_risk(
    changes: list[ProposedChange],
    root_cause: RootCauseCategory,
) -> RiskAssessment:
    paths = [c.path for c in changes]

    if any(_matches_any(p, _BLOCKED_PATTERNS) for p in paths):
        return RiskAssessment(
            level=RiskLevel.HIGH,
            reason="涉及平台核心代码、部署配置或敏感文件",
            requires_human_confirmation_before_devflow=True,
            requires_human_review_before_merge=True,
        )

    if root_cause in _HIGH_RISK_ROOT_CAUSES:
        return RiskAssessment(
            level=RiskLevel.HIGH,
            reason=f"根因类别 {root_cause} 需要人工评估",
            requires_human_confirmation_before_devflow=True,
            requires_human_review_before_merge=True,
        )

    if root_cause in _MEDIUM_RISK_ROOT_CAUSES:
        return RiskAssessment(
            level=RiskLevel.MEDIUM,
            reason=f"根因类别 {root_cause} 涉及工具或路由变更",
            requires_human_confirmation_before_devflow=True,
            requires_human_review_before_merge=True,
        )

    if any(_matches_any(p, _MEDIUM_RISK_PATTERNS) for p in paths):
        return RiskAssessment(
            level=RiskLevel.MEDIUM,
            reason="涉及 Agent 工具、adapter 或 manifest 变更",
            requires_human_confirmation_before_devflow=True,
            requires_human_review_before_merge=True,
        )

    if all(_matches_any(p, _LOW_RISK_PATTERNS) for p in paths):
        return RiskAssessment(
            level=RiskLevel.LOW,
            reason="仅修改 prompt、eval、docs 或 contract tests",
            requires_human_confirmation_before_devflow=False,
            requires_human_review_before_merge=True,
        )

    return RiskAssessment(
        level=RiskLevel.MEDIUM,
        reason="变更路径不完全在低风险白名单内",
        requires_human_confirmation_before_devflow=True,
        requires_human_review_before_merge=True,
    )


def populate_risk_and_paths(proposal: ImprovementProposal) -> ImprovementProposal:
    """根据 proposed_changes 和 root_cause 自动填充 risk、allowed_paths、blocked_paths。"""
    assessment = classify_risk(proposal.proposed_changes, proposal.root_cause.category)
    proposal.risk = assessment

    if not proposal.allowed_paths and assessment.level == RiskLevel.LOW:
        proposal.allowed_paths = [
            f"agents/{proposal.agent_id}/prompts/**",
            f"agents/{proposal.agent_id}/evals/**",
            "tests/contract/**",
            "docs/**",
        ]

    if not proposal.blocked_paths:
        proposal.blocked_paths = list(_BLOCKED_PATTERNS)

    return proposal
