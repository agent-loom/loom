"""EvolutionEngine：从运行反馈到改进提案的核心引擎。

职责：
1. 接收归一化事件（eval failure, feedback, runtime anomaly）
2. 去重和聚合
3. 生成 ImprovementProposal（自动风险分类 + 证据绑定）
4. 可选分发到 Plane
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import (
    Evidence,
    EvidenceType,
    EvolutionEvent,
    ImprovementProposal,
    ProposalSource,
    ProposalStatus,
    ProposedChange,
    RiskAssessment,
    RiskLevel,
    RootCause,
    RootCauseCategory,
    ValidationSpec,
)
from .repository import EvolutionProposalRepository
from .risk_classifier import populate_risk_and_paths

logger = logging.getLogger(__name__)

_DEDUP_WINDOW = timedelta(hours=24)

_ROOT_CAUSE_MAP: dict[str, RootCauseCategory] = {
    "eval_failure": RootCauseCategory.EVAL_GAP,
    "user_feedback": RootCauseCategory.PROMPT_GAP,
    "tool_error": RootCauseCategory.TOOL_RUNTIME_ERROR,
    "runtime_anomaly": RootCauseCategory.TOOL_RUNTIME_ERROR,
    "routing_error": RootCauseCategory.ROUTING_ERROR,
}


def _dedup_key(event: EvolutionEvent) -> str:
    msg_hash = hashlib.md5(event.summary.encode(), usedforsecurity=False).hexdigest()[:12]
    window = event.created_at.strftime("%Y%m%d%H")
    return f"{event.tenant_id}:{event.agent_id}:{event.event_type}:{msg_hash}:{window}"


class EvolutionEngine:
    """从运行反馈生成改进提案的引擎。"""

    def __init__(
        self,
        repo: EvolutionProposalRepository,
        *,
        plane_adapter: Any | None = None,
        plane_project_id: str | None = None,
        ai_developing_state_id: str | None = None,
    ) -> None:
        self._repo = repo
        self._plane = plane_adapter
        self._plane_project_id = plane_project_id
        self._ai_developing_state_id = ai_developing_state_id
        self._seen_keys: dict[str, datetime] = {}

    def _is_duplicate(self, event: EvolutionEvent) -> bool:
        key = _dedup_key(event)
        now = datetime.now(UTC)
        if key in self._seen_keys:
            if now - self._seen_keys[key] < _DEDUP_WINDOW:
                return True
        self._seen_keys[key] = now
        self._gc_seen_keys(now)
        return False

    def _gc_seen_keys(self, now: datetime) -> None:
        expired = [k for k, t in self._seen_keys.items() if now - t > _DEDUP_WINDOW]
        for k in expired:
            del self._seen_keys[k]

    async def process_event(self, event: EvolutionEvent) -> ImprovementProposal | None:
        if self._is_duplicate(event):
            logger.debug("去重跳过: %s", event.summary[:80])
            return None

        proposal = self._event_to_proposal(event)
        proposal = populate_risk_and_paths(proposal)

        await self._repo.create(proposal)
        logger.info(
            "生成提案: %s agent=%s risk=%s",
            proposal.proposal_id, proposal.agent_id, proposal.risk.level,
        )
        return proposal

    async def dispatch_to_plane(self, proposal_id: str) -> dict[str, Any]:
        proposal = await self._repo.get(proposal_id)
        if proposal is None:
            return {"error": f"proposal {proposal_id} not found"}

        if proposal.status == ProposalStatus.DISPATCHED:
            return {"error": "already dispatched", "plane_work_item_id": proposal.plane_work_item_id}

        if proposal.risk.level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            return {"error": f"risk level {proposal.risk.level} 不允许自动分发到 Plane"}

        if not self._plane or not self._plane_project_id:
            return {"error": "Plane adapter 未配置"}

        body = self._build_plane_body(proposal)
        custom_props = {
            "agent_id": proposal.agent_id,
            "task_type": proposal.task_type,
            "proposal_id": proposal.proposal_id,
            "risk_level": proposal.risk.level,
            "evolution_source": proposal.source,
        }

        try:
            result = await self._plane.create_work_item(
                project_id=self._plane_project_id,
                name=proposal.title,
                description=body,
                labels=[],
                custom_properties=custom_props,
            )
            work_item_id = result.get("id", "")
            await self._repo.update_status(
                proposal_id,
                ProposalStatus.DISPATCHED,
                plane_work_item_id=work_item_id,
            )
            proposal.plane_work_item_id = work_item_id
            logger.info("提案已分发到 Plane: %s -> %s", proposal_id, work_item_id)

            dispatch_result: dict[str, Any] = {
                "status": "dispatched",
                "plane_work_item_id": work_item_id,
            }

            if (
                proposal.risk.level == RiskLevel.LOW
                and not proposal.risk.requires_human_confirmation_before_devflow
                and self._ai_developing_state_id
                and work_item_id
            ):
                try:
                    await self._plane.update_work_item_state(
                        project_id=self._plane_project_id,
                        work_item_id=work_item_id,
                        state_id=self._ai_developing_state_id,
                    )
                    dispatch_result["auto_devflow"] = True
                    logger.info(
                        "低风险提案自动推进 Ready for AI Dev: %s", proposal_id,
                    )
                except Exception:
                    logger.warning(
                        "自动推进 Ready for AI Dev 失败: %s", proposal_id,
                        exc_info=True,
                    )

            return dispatch_result
        except Exception:
            logger.exception("分发到 Plane 失败: %s", proposal_id)
            return {"error": "Plane API 调用失败"}

    async def auto_dispatch_if_low_risk(self, proposal: ImprovementProposal) -> dict[str, Any] | None:
        if (
            proposal.risk.level == RiskLevel.LOW
            and not proposal.risk.requires_human_confirmation_before_devflow
            and self._plane
        ):
            proposal.status = ProposalStatus.READY
            return await self.dispatch_to_plane(proposal.proposal_id)
        return None

    async def dismiss(self, proposal_id: str, reason: str = "") -> None:
        await self._repo.update_status(
            proposal_id, ProposalStatus.DISMISSED, outcome=reason or "dismissed",
        )

    def _event_to_proposal(self, event: EvolutionEvent) -> ImprovementProposal:
        root_cause_cat = _ROOT_CAUSE_MAP.get(
            event.event_type, RootCauseCategory.PROMPT_GAP,
        )

        evidence_type = {
            "eval_failure": EvidenceType.EVAL_FAILURE,
            "user_feedback": EvidenceType.USER_FEEDBACK,
            "tool_error": EvidenceType.TOOL_ERROR,
            "runtime_anomaly": EvidenceType.TOOL_ERROR,
        }.get(event.event_type, EvidenceType.AGENT_RUN)

        evidence = Evidence(
            type=evidence_type,
            id=event.details.get("id", f"evt_{event.created_at.strftime('%Y%m%d%H%M%S')}"),
            summary=event.summary,
            trace_id=event.details.get("trace_id"),
            tool_name=event.details.get("tool_name"),
        )

        changes: list[ProposedChange] = []
        if root_cause_cat in (RootCauseCategory.PROMPT_GAP, RootCauseCategory.EVAL_GAP):
            changes = [
                ProposedChange(
                    type="prompt_update",
                    path=f"agents/{event.agent_id}/prompts/orchestrator.md",
                    description="根据反馈优化 prompt",
                ),
                ProposedChange(
                    type="eval_case_add",
                    path=f"agents/{event.agent_id}/evals/golden.yaml",
                    description="新增回归用例",
                ),
            ]

        return ImprovementProposal(
            title=f"[{event.agent_id}] {event.summary[:60]}",
            summary=event.summary,
            agent_id=event.agent_id,
            tenant_id=event.tenant_id,
            source=ProposalSource.EVOLUTION_ENGINE,
            risk=RiskAssessment(level=RiskLevel.MEDIUM, reason="pending classification"),
            root_cause=RootCause(
                category=root_cause_cat,
                confidence=0.7,
                explanation=event.summary,
            ),
            evidence=[evidence],
            proposed_changes=changes,
            validation=ValidationSpec(
                commands=[
                    "pytest tests/unit -x -q",
                    f"python scripts/validate_manifest.py agents/{event.agent_id}/manifest.yaml",
                ],
            ),
        )

    @staticmethod
    def _build_plane_body(proposal: ImprovementProposal) -> str:
        evidence_lines = "\n".join(
            f"- **{e.type}** ({e.id}): {e.summary}" for e in proposal.evidence
        )
        changes_lines = "\n".join(
            f"- `{c.path}`: {c.description}" for c in proposal.proposed_changes
        )
        validation_lines = "\n".join(
            f"- `{cmd}`" for cmd in proposal.validation.commands
        )
        return (
            f"# Evolution Proposal\n\n"
            f"**Proposal ID:** {proposal.proposal_id}\n"
            f"**Agent:** {proposal.agent_id}\n"
            f"**Risk:** {proposal.risk.level}\n"
            f"**Root Cause:** {proposal.root_cause.category}\n\n"
            f"## Summary\n\n{proposal.summary}\n\n"
            f"## Evidence\n\n{evidence_lines}\n\n"
            f"## Proposed Changes\n\n{changes_lines}\n\n"
            f"## Validation\n\n{validation_lines}\n"
        )
