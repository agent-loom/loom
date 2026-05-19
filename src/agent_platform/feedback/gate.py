"""ProposalGate：按阈值决定哪些候选需求提案可以发布到 Plane。"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_platform.feedback.miner import RequirementProposal


@dataclass
class GateConfig:
    """门控配置，所有字段均有合理默认值。"""

    # 最低置信度，低于此值的提案会被拒绝
    min_confidence: float = 0.6
    # 最少影响会话数，低于此值的提案会被拒绝
    min_affected_sessions: int = 3
    # 每日每 agent 最大发布提案数，超出后进入配额限制拒绝
    max_daily_proposals: int = 10
    # 被屏蔽的 agent 列表，这些 agent 的所有提案均被拒绝
    blocked_agents: list[str] = field(default_factory=list)
    # 允许通过的严重程度列表；不在列表中的（如 "low"）会被拒绝
    allowed_severities: list[str] = field(
        default_factory=lambda: ["medium", "high", "critical"]
    )


@dataclass
class GateDecision:
    """单个提案的门控决策结果。"""

    # 被评估的原始提案
    proposal: RequirementProposal
    # 是否通过门控
    approved: bool
    # 拒绝/通过原因，固定枚举值：
    # "approved" | "low_confidence" | "insufficient_impact"
    # | "quota_exceeded" | "agent_blocked"
    reason: str


class ProposalGate:
    """按阈值决定哪些提案可以发布到 Plane。

    使用示例::

        gate = ProposalGate(GateConfig(min_confidence=0.7))
        decisions = gate.evaluate(proposals)
        approved = [d for d in decisions if d.approved]
    """

    def __init__(self, config: GateConfig | None = None) -> None:
        self.config = config or GateConfig()
        # agent_id → 当前日期内已批准的提案数
        self._daily_counts: dict[str, int] = {}

    def evaluate(self, proposals: list[RequirementProposal]) -> list[GateDecision]:
        """对每个提案做门控决策，顺序处理以正确累积配额计数。

        Args:
            proposals: 待评估的提案列表。

        Returns:
            与 proposals 等长的决策列表，顺序一一对应。
        """
        decisions: list[GateDecision] = []
        for proposal in proposals:
            decision = self._check(proposal)
            # 通过的提案才计入当日配额
            if decision.approved:
                self._daily_counts[proposal.agent_id] = (
                    self._daily_counts.get(proposal.agent_id, 0) + 1
                )
            decisions.append(decision)
        return decisions

    def _check(self, proposal: RequirementProposal) -> GateDecision:
        """检查单个提案，按优先级依次判断：

        agent_blocked > low_confidence > insufficient_impact > quota_exceeded > approved

        Args:
            proposal: 待检查的提案。

        Returns:
            门控决策结果。
        """
        # 1. agent 屏蔽检查
        if proposal.agent_id in self.config.blocked_agents:
            return GateDecision(
                proposal=proposal,
                approved=False,
                reason="agent_blocked",
            )

        # 2. 置信度检查
        if proposal.confidence < self.config.min_confidence:
            return GateDecision(
                proposal=proposal,
                approved=False,
                reason="low_confidence",
            )

        # 3. 影响范围检查（affected_sessions 不足 或 severity 不在允许列表）
        affected_sessions = proposal.impact.get("affected_sessions", 0)
        if (
            affected_sessions < self.config.min_affected_sessions
            or proposal.severity not in self.config.allowed_severities
        ):
            return GateDecision(
                proposal=proposal,
                approved=False,
                reason="insufficient_impact",
            )

        # 4. 日配额检查（此处使用已通过数，尚未把本次计入）
        current_count = self._daily_counts.get(proposal.agent_id, 0)
        if current_count >= self.config.max_daily_proposals:
            return GateDecision(
                proposal=proposal,
                approved=False,
                reason="quota_exceeded",
            )

        # 5. 全部通过
        return GateDecision(
            proposal=proposal,
            approved=True,
            reason="approved",
        )
