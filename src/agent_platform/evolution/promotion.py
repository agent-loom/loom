"""Candidate 晋升执行器。

负责将审批通过 (APPROVED) 的候选资产 (Candidate) 正式转化为平台生产资产：
1. memory_candidate -> EvolutionMemory
2. proposal_draft -> ImprovementProposal
3. skill_draft / eval_case_draft -> 启动 DevFlow 自动修复链路 (转换成特定 ImprovementProposal 并通过 DevFlow 创建 MR)
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from agent_platform.evolution.models import (
    Candidate,
    CandidateStatus,
    Evidence,
    EvidenceType,
    ImprovementProposal,
    ProposedChange,
    ProposalSource,
    ProposalStatus,
    PromotionTarget,
    RiskAssessment,
    RiskLevel,
    RootCause,
    RootCauseCategory,
    ValidationSpec,
)
from agent_platform.evolution.memory_models import EvolutionMemory, MemoryType, MemoryStatus
from agent_platform.evolution.repository import EvolutionProposalRepository
from agent_platform.evolution.memory_repository import EvolutionMemoryRepository

logger = logging.getLogger(__name__)


class PromotionError(Exception):
    """资产晋升异常。"""
    pass


class PromotionExecutor:
    """自进化资产晋升执行器。"""

    def __init__(
        self,
        *,
        proposal_repo: EvolutionProposalRepository,
        memory_repo: EvolutionMemoryRepository,
        evolution_engine: Any | None = None,
    ) -> None:
        """
        :param proposal_repo: 提案仓储。
        :param memory_repo: 自进化记忆仓储。
        :param evolution_engine: 可选的自进化引擎，用于触发低风险提案的自动 DevFlow 推进。
        """
        self._proposal_repo = proposal_repo
        self._memory_repo = memory_repo
        self._engine = evolution_engine

    async def promote(self, candidate: Candidate) -> dict[str, Any]:
        """将指定的 Candidate 晋升为正式资产。

        :param candidate: 状态必须为 CandidateStatus.APPROVED。
        :return: 包含晋升结果详情的字典。
        """
        if candidate.status != CandidateStatus.APPROVED:
            raise PromotionError(
                f"Candidate {candidate.candidate_id} 无法晋升，当前状态为 {candidate.status}，"
                f"必须为 {CandidateStatus.APPROVED} 状态。"
            )

        logger.info(
            "开始晋升候选资产: id=%s, type=%s, target=%s",
            candidate.candidate_id,
            candidate.candidate_type,
            candidate.promotion_target,
        )

        try:
            result: dict[str, Any] = {}

            # 1. 根据 promotion_target 路由具体转化逻辑
            if candidate.promotion_target == PromotionTarget.EVOLUTION_MEMORY:
                result = await self._promote_to_evolution_memory(candidate)
            elif candidate.promotion_target == PromotionTarget.IMPROVEMENT_PROPOSAL:
                result = await self._promote_to_proposal(candidate)
            elif candidate.promotion_target == PromotionTarget.EVAL_CASE:
                result = await self._promote_to_eval_case_mr(candidate)
            elif candidate.promotion_target == PromotionTarget.AGENT_SKILL:
                result = await self._promote_to_skill_mr(candidate)
            else:
                raise PromotionError(f"不支持的晋升目标: {candidate.promotion_target}")

            # 2. 晋升成功后，更新 Candidate 状态为 PROMOTED
            candidate.status = CandidateStatus.PROMOTED
            candidate.promoted_at = datetime.now(UTC)
            candidate.updated_at = datetime.now(UTC)

            return {
                "status": "success",
                "candidate_id": candidate.candidate_id,
                "promoted_target": candidate.promotion_target,
                **result,
            }

        except Exception as e:
            logger.exception("候选资产晋升发生异常: id=%s", candidate.candidate_id)
            raise PromotionError(f"资产晋升失败: {str(e)}") from e

    async def _promote_to_evolution_memory(self, candidate: Candidate) -> dict[str, Any]:
        payload = candidate.payload or {}

        # 字段映射
        mem_type_str = payload.get("memory_type", "pattern")
        try:
            mem_type = MemoryType(mem_type_str)
        except ValueError:
            mem_type = MemoryType.PATTERN

        # 创建 EvolutionMemory 正式条目
        memory = EvolutionMemory(
            agent_id=candidate.agent_id,
            tenant_id=candidate.tenant_id,
            type=mem_type,
            content=payload.get("summary", "no content"),
            confidence=payload.get("confidence", 0.7),
            trust_score=payload.get("trust_score", 0.5),
            status=MemoryStatus.ACTIVE,
            source_proposal_id=candidate.source_event_ids[0] if candidate.source_event_ids else None,
            source_type="candidate_promotion",
            tags=payload.get("tags", []),
            metadata={
                "candidate_id": candidate.candidate_id,
                "generated_by": candidate.generated_by,
                **payload.get("metadata", {}),
            }
        )

        await self._memory_repo.create(memory)
        logger.info(
            "Candidate %s 成功晋升为 EvolutionMemory %s",
            candidate.candidate_id,
            memory.memory_id,
        )
        return {"memory_id": memory.memory_id}

    async def _promote_to_proposal(self, candidate: Candidate) -> dict[str, Any]:
        payload = candidate.payload or {}

        rc_cat_str = payload.get("root_cause", "prompt_gap")
        try:
            rc_cat = RootCauseCategory(rc_cat_str)
        except ValueError:
            rc_cat = RootCauseCategory.PROMPT_GAP

        # 构建证据
        evidences: list[Evidence] = []
        for eid in candidate.evidence_ids:
            evidences.append(Evidence(
                type=EvidenceType.AGENT_RUN,
                id=eid,
                summary=f"Evidence for candidate {candidate.candidate_id}",
            ))
        if not evidences:
            evidences.append(Evidence(
                type=EvidenceType.PLANE_ITEM,
                id=candidate.candidate_id,
                summary=payload.get("summary", "no summary"),
            ))

        # 构建 ProposedChanges
        proposed_changes: list[ProposedChange] = []
        for change in payload.get("proposed_changes", []):
            proposed_changes.append(ProposedChange(
                type=change.get("type", "prompt_update"),
                path=change.get("path", ""),
                description=change.get("description", ""),
            ))

        # 构建验证命令
        val_spec = ValidationSpec(
            commands=payload.get("validation", {}).get("commands", [
                "pytest tests/unit -x -q",
            ]),
        )

        # 晋升为 ImprovementProposal
        proposal = ImprovementProposal(
            title=f"[{candidate.agent_id}] {payload.get('summary', 'Evolution Proposal')[:60]}",
            summary=payload.get("summary", "no content"),
            agent_id=candidate.agent_id,
            tenant_id=candidate.tenant_id,
            source=ProposalSource.FEEDBACK_INTELLIGENCE,
            risk=RiskAssessment(
                level=candidate.risk_level,
                reason=f"From Candidate {candidate.candidate_id}",
                requires_human_confirmation_before_devflow=candidate.risk_level != RiskLevel.LOW,
            ),
            root_cause=RootCause(
                category=rc_cat,
                confidence=payload.get("confidence", 0.8),
                explanation=payload.get("summary", "no content"),
            ),
            evidence=evidences,
            proposed_changes=proposed_changes,
            validation=val_spec,
        )

        await self._proposal_repo.create(proposal)
        logger.info(
            "Candidate %s 成功晋升为 ImprovementProposal %s",
            candidate.candidate_id,
            proposal.proposal_id,
        )

        # 如果自进化引擎存在，且属于低风险自动分发，尝试触发自动推进
        auto_dispatched = False
        if self._engine and proposal.risk.level == RiskLevel.LOW:
            try:
                # 状态设置为 READY 以便自动分发
                proposal.status = ProposalStatus.READY
                await self._proposal_repo.update_status(proposal.proposal_id, ProposalStatus.READY)
                dispatch_res = await self._engine.dispatch_to_plane(proposal.proposal_id)
                if dispatch_res.get("status") == "dispatched":
                    auto_dispatched = True
            except Exception:
                logger.warning(
                    "自动分发晋升后的 Low Risk 提案失败: id=%s",
                    proposal.proposal_id,
                    exc_info=True,
                )

        return {
            "proposal_id": proposal.proposal_id,
            "auto_dispatched": auto_dispatched,
        }

    async def _promote_to_eval_case_mr(self, candidate: Candidate) -> dict[str, Any]:
        """将 EvalCase 候选晋升为特定的自进化提案，从而通过 DevFlow MR 实现自动化添加。"""
        payload = candidate.payload or {}

        # 准备新增用例所需要的内容，并把 allowed_paths 设为 evals 目录
        changes = [
            ProposedChange(
                type="eval_case_add",
                path=f"agents/{candidate.agent_id}/evals/golden.yaml",
                description=f"新增自进化验证用例: {payload.get('name', 'new case')}",
            )
        ]

        # 晋升为 Low Risk 的特定修复提案，用于触发 DevFlow
        proposal = ImprovementProposal(
            title=f"[{candidate.agent_id}] 自动新增回归验证用例",
            summary=f"基于候选资产 {candidate.candidate_id} 自动补充 eval case。\n内容: {payload}",
            agent_id=candidate.agent_id,
            tenant_id=candidate.tenant_id,
            source=ProposalSource.EVAL_RUNNER,
            risk=RiskAssessment(
                level=RiskLevel.LOW,
                reason="纯测试/用例添加，无需人工确认安全阻断",
                requires_human_confirmation_before_devflow=False,
            ),
            root_cause=RootCause(
                category=RootCauseCategory.EVAL_GAP,
                confidence=0.9,
                explanation="自动识别到评估场景缺失，自动加固回归边界",
            ),
            evidence=[Evidence(
                type=EvidenceType.EVAL_FAILURE,
                id=candidate.candidate_id,
                summary="自进化用例缺失补齐",
            )],
            proposed_changes=changes,
            allowed_paths=[f"agents/{candidate.agent_id}/evals/**"],
            validation=ValidationSpec(
                commands=[
                    f"pytest agents/{candidate.agent_id}/tests/ -x -q"
                ]
            )
        )

        await self._proposal_repo.create(proposal)

        auto_dispatched = False
        if self._engine:
            try:
                proposal.status = ProposalStatus.READY
                await self._proposal_repo.update_status(proposal.proposal_id, ProposalStatus.READY)
                dispatch_res = await self._engine.dispatch_to_plane(proposal.proposal_id)
                if dispatch_res.get("status") == "dispatched":
                    auto_dispatched = True
            except Exception:
                logger.warning("自动分发 eval_case 晋升提案失败", exc_info=True)

        return {
            "proposal_id": proposal.proposal_id,
            "auto_dispatched": auto_dispatched,
            "description": "已将 eval_case 转换为低风险提案，启动 DevFlow 自动修复链路",
        }

    async def _promote_to_skill_mr(self, candidate: Candidate) -> dict[str, Any]:
        """将 Skill 候选晋升为自进化提案，走 DevFlow。"""
        payload = candidate.payload or {}

        changes = [
            ProposedChange(
                type="skill_create",
                path=f"agents/{candidate.agent_id}/skills/{payload.get('skill_id')}/",
                description=f"自动补充 Agent 过程性技能: {payload.get('title')}",
            )
        ]

        # 技能需要修改业务技能目录，属于 Medium Risk，需要人工在 Plane 页面确认触发 DevFlow
        proposal = ImprovementProposal(
            title=f"[{candidate.agent_id}] 自动创建过程技能: {payload.get('title')}",
            summary=f"基于自进化候选资产 {candidate.candidate_id} 创建正式技能规范。\n详细: {payload.get('description')}",
            agent_id=candidate.agent_id,
            tenant_id=candidate.tenant_id,
            source=ProposalSource.MANUAL,
            risk=RiskAssessment(
                level=RiskLevel.MEDIUM,
                reason="新增技能规范可能包含执行逻辑，需要人工确认",
                requires_human_confirmation_before_devflow=True,
            ),
            root_cause=RootCause(
                category=RootCauseCategory.PROMPT_GAP,
                confidence=0.8,
                explanation="从进化回路中自动汇总出的 Agent 技能模式",
            ),
            evidence=[Evidence(
                type=EvidenceType.LOG_PATTERN,
                id=candidate.candidate_id,
                summary="过程技能模式聚合",
            )],
            proposed_changes=changes,
            allowed_paths=[f"agents/{candidate.agent_id}/skills/**"],
            validation=ValidationSpec(
                commands=[
                    f"python scripts/validate_skill.py agents/{candidate.agent_id}/skills/{payload.get('skill_id')}/"
                ]
            )
        )

        await self._proposal_repo.create(proposal)
        return {
            "proposal_id": proposal.proposal_id,
            "auto_dispatched": False,
            "description": "已将 skill_draft 转换为中风险提案，待人工确认后即可触发自动编码",
        }
