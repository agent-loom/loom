"""FeedbackIntelligenceService：串联反馈采集、挖掘、门控、发布的顶层编排器。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from agent_platform.feedback.collector import FeedbackCollector
from agent_platform.feedback.gate import GateConfig, GateDecision, ProposalGate
from agent_platform.feedback.miner import FeedbackMiner, RequirementProposal
from agent_platform.feedback.publisher import PlanePublisher

logger = logging.getLogger(__name__)


@dataclass
class FeedbackRunResult:
    """单次反馈闭环运行的汇总结果。"""

    signals_collected: int = 0
    proposals_generated: int = 0
    proposals_approved: int = 0
    proposals_rejected: int = 0
    work_items_created: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)


class FeedbackIntelligenceService:
    """生产反馈 → 候选需求的完整闭环编排器。

    完整链路：
        FeedbackCollector（采集） → FeedbackMiner（聚合提案）
        → ProposalGate（门控决策） → PlanePublisher（发布到 Plane）
    """

    def __init__(
        self,
        collector: FeedbackCollector,
        miner: FeedbackMiner,
        gate: ProposalGate,
        publisher: PlanePublisher,
    ) -> None:
        self._collector = collector
        self._miner = miner
        self._gate = gate
        self._publisher = publisher

    async def run(self, hours: int = 24) -> FeedbackRunResult:
        """执行一次反馈闭环，返回本次运行汇总。

        Args:
            hours: 采集最近多少小时的运行记录。
        """
        result = FeedbackRunResult()

        # 1. 采集反馈信号
        try:
            signals = await self._collector.collect_recent(hours=hours)
        except Exception:
            logger.exception("反馈信号采集失败，跳过本次闭环")
            return result

        result.signals_collected = len(signals)
        logger.info("采集到 %d 条反馈信号（最近 %dh）", result.signals_collected, hours)

        if not signals:
            return result

        # 2. 聚合提案
        proposals: list[RequirementProposal] = self._miner.mine(signals)
        result.proposals_generated = len(proposals)
        logger.info("生成 %d 个候选需求提案", result.proposals_generated)

        if not proposals:
            return result

        # 3. 门控决策
        decisions: list[GateDecision] = self._gate.evaluate(proposals)
        approved = [d for d in decisions if d.approved]
        rejected = [d for d in decisions if not d.approved]

        result.proposals_approved = len(approved)
        result.proposals_rejected = len(rejected)

        # 统计拒绝原因分布
        for d in rejected:
            result.rejection_reasons[d.reason] = (
                result.rejection_reasons.get(d.reason, 0) + 1
            )

        logger.info(
            "门控结果: 通过=%d 拒绝=%d（原因分布=%s）",
            result.proposals_approved,
            result.proposals_rejected,
            result.rejection_reasons,
        )

        if not approved:
            return result

        # 4. 发布到 Plane
        try:
            created = await self._publisher.publish(decisions)
            result.work_items_created = len(created)
            logger.info("已创建 %d 个 Plane Work Item", result.work_items_created)
        except Exception:
            logger.exception("发布到 Plane 失败，本次闭环部分完成")

        return result
