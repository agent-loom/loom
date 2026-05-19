"""PlanePublisher：把通过门控的候选需求提案发布为 Plane Work Item。"""

from __future__ import annotations

import logging

from agent_platform.feedback.gate import GateDecision
from agent_platform.feedback.miner import RequirementProposal
from agent_platform.integrations.plane.adapter import PlaneAdapter

logger = logging.getLogger(__name__)

# severity → Plane priority 的映射表
_SEVERITY_TO_PRIORITY: dict[str, str] = {
    "critical": "urgent",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


class PlanePublisher:
    """把通过门控的 RequirementProposal 发布到 Plane 作为候选需求 Work Item。

    使用示例::

        publisher = PlanePublisher(plane_adapter, project_id="proj-xxx")
        created_items = await publisher.publish(decisions)
    """

    def __init__(self, plane: PlaneAdapter, project_id: str) -> None:
        # Plane API 适配器
        self.plane = plane
        # 目标 Plane 项目 ID
        self.project_id = project_id

    async def publish(self, decisions: list[GateDecision]) -> list[dict]:
        """只发布 approved=True 的提案，跳过被拒绝的提案。

        Args:
            decisions: 门控决策列表，来自 ProposalGate.evaluate()。

        Returns:
            成功创建的 Plane Work Item 字典列表。
        """
        created: list[dict] = []

        for decision in decisions:
            # 跳过未通过门控的提案
            if not decision.approved:
                logger.debug(
                    "跳过未通过门控的提案: title=%s reason=%s",
                    decision.proposal.title,
                    decision.reason,
                )
                continue

            proposal = decision.proposal
            work_item = await self.plane.create_work_item(
                self.project_id,
                name=proposal.title,
                description=self._build_description(proposal),
                priority=_SEVERITY_TO_PRIORITY.get(proposal.severity, "medium"),
                labels=[],  # label 需在 Plane 预先创建，此处置空
                properties=self._build_custom_properties(proposal),
            )
            logger.info(
                "已发布 Plane Work Item: id=%s title=%s",
                work_item.get("id"),
                proposal.title,
            )
            created.append(work_item)

        return created

    def _build_description(self, proposal: RequirementProposal) -> str:
        """生成 Plane Work Item 描述的 Markdown 文本。

        包含证据摘要、影响范围和建议验收标准三个部分。

        Args:
            proposal: 待描述的提案。

        Returns:
            格式化后的 Markdown 字符串。
        """
        lines: list[str] = []

        # ── 基本信息 ──
        lines.append(f"## 提案概述")
        lines.append(f"")
        lines.append(f"- **提案类型**: {proposal.proposal_type}")
        lines.append(f"- **Agent**: `{proposal.agent_id}`")
        lines.append(f"- **严重程度**: {proposal.severity}")
        lines.append(f"- **置信度**: {proposal.confidence:.2f}")
        lines.append(f"")

        # ── 证据摘要 ──
        lines.append("## 证据摘要")
        lines.append("")
        if proposal.evidence:
            for idx, ev in enumerate(proposal.evidence, start=1):
                lines.append(f"**证据 {idx}**")
                for key, value in ev.items():
                    lines.append(f"- {key}: {value}")
        else:
            lines.append("_暂无证据_")
        lines.append("")

        # ── 影响范围 ──
        lines.append("## 影响范围")
        lines.append("")
        impact = proposal.impact
        lines.append(f"- **受影响租户数**: {impact.get('affected_tenants', 'N/A')}")
        lines.append(f"- **受影响会话数**: {impact.get('affected_sessions', 'N/A')}")
        lines.append(f"- **首次出现**: {impact.get('first_seen', 'N/A')}")
        lines.append(f"- **最近出现**: {impact.get('last_seen', 'N/A')}")
        lines.append("")

        # ── 建议验收标准 ──
        lines.append("## 建议验收标准")
        lines.append("")
        if proposal.suggested_acceptance:
            for criterion in proposal.suggested_acceptance:
                lines.append(f"- [ ] {criterion}")
        else:
            lines.append("_暂无建议验收标准_")
        lines.append("")

        return "\n".join(lines)

    def _build_custom_properties(self, proposal: RequirementProposal) -> dict[str, str]:
        """构建 Plane Work Item 的自定义属性字典。

        Args:
            proposal: 来源提案。

        Returns:
            字符串键值对字典，所有值均为字符串类型。
        """
        return {
            "source": "runtime_feedback",
            "agent_id": proposal.agent_id,
            "proposal_type": proposal.proposal_type,
            "confidence": str(proposal.confidence),
            "affected_sessions": str(proposal.impact.get("affected_sessions", 0)),
        }
