"""FeedbackIntelligence → EvolutionEngine 适配器。

将 FeedbackMiner 产出的 RequirementProposal 转换为 EvolutionEvent，
统一进入 EvolutionEngine 的事件处理管线。
"""
from __future__ import annotations

from agent_platform.feedback.miner import RequirementProposal

from .models import EvolutionEvent


_PROPOSAL_TYPE_TO_EVENT_TYPE: dict[str, str] = {
    "bug": "tool_error",
    "feature": "user_feedback",
    "optimization": "runtime_anomaly",
    "knowledge_gap": "user_feedback",
    "eval_gap": "eval_failure",
}


def requirement_to_evolution_event(proposal: RequirementProposal) -> EvolutionEvent:
    """将 RequirementProposal 转换为 EvolutionEvent。"""
    event_type = _PROPOSAL_TYPE_TO_EVENT_TYPE.get(
        proposal.proposal_type, "user_feedback",
    )

    evidence_ids = [e.get("run_id", "") for e in proposal.evidence[:3]]

    return EvolutionEvent(
        event_type=event_type,
        agent_id=proposal.agent_id,
        summary=proposal.title,
        details={
            "source": "feedback_intelligence",
            "proposal_type": proposal.proposal_type,
            "severity": proposal.severity,
            "confidence": proposal.confidence,
            "impact": proposal.impact,
            "evidence_run_ids": evidence_ids,
            "suggested_task_type": proposal.suggested_task_type,
        },
    )


def batch_convert(proposals: list[RequirementProposal]) -> list[EvolutionEvent]:
    """批量转换 RequirementProposal 为 EvolutionEvent。"""
    return [requirement_to_evolution_event(p) for p in proposals]
