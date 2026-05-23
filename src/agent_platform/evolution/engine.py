"""自进化生命周期引擎：信号收集、决策熔断与风险分流控制中心。

设计定位：
  自进化系统 (Self-Evolution Loop) 的首个控制面核心引擎 (EvolutionEngine)。
  对应 docs/07-evolution/candidate-contract.md 中的"自进化决策引擎"组件。
  负责接收各管道的运行时反馈事件 (EvolutionEvent)，执行基于内存滑动窗口的去重 (Dedup)，
  基于回归分析的多轮故障熔断 (Circuit Breaker) 以及连续人类拒绝的动态降级 (Fallback) 策略，
  产出结构化的改进提案 (ImprovementProposal)，并在满足安全条件的前提下通过三方项目看板系统 (Plane) 分发研发任务。
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

# 事件去重聚合的滑动时间窗口设定（默认为 24 小时）
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
    return f"{event.tenant_id}:{event.agent_id}:{event.event_type}:{msg_hash}"


class EvolutionEngine:
    """自进化决策与任务分发引擎 (Evolution Engine)

    控制面生命周期的守卫大脑，管控低风险 AI 进化的自动触发与高风险的隔离。
    """

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
        # TODO Design Gap:
        # _seen_keys 滑动去重窗口目前纯在内存中维护，
        # 在多实例、负载均衡副本部署环境下无法共享去重记录。
        # 当服务重启或容器扩缩容时，去重缓存将完全丢失，易引发提案重复生成的“自进化风暴”。
        # 长期方案应重构为分布式去重（例如利用 Redis EXPIRE Key 锁定模式）。
        self._seen_keys: dict[str, datetime] = {}
        self._manually_suspended: set[str] = set()

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

    async def is_agent_suspended(self, agent_id: str) -> bool:
        """检查 Agent 是否被挂起/暂停自进化触发。"""
        if agent_id in self._manually_suspended:
            return True

        # 检查是否连续引入回归 (Circuit Breaker)
        proposals = await self._repo.list_by_agent(agent_id, limit=5)
        closed_proposals = [
            p for p in proposals
            if p.status in (ProposalStatus.CLOSED, ProposalStatus.DISMISSED)
        ]

        # TODO Design Gap:
        # 回归分析高度依赖 outcome 文字描述匹配检测 `"regression" in outcome`。
        # 这种模式极其脆弱且容易产生漏判（如果人工或自动化工具写成了 "re-regression" 或 "regressed" 将可能绕过熔断）。
        # 未来版本应在模型层面引入强类型的 `outcome_type` (如 REGRESSION, FIXED, DISMISSED)。
        regression_count = 0
        for p in closed_proposals:
            outcome = (p.outcome or "").lower()
            if "regression" in outcome:
                regression_count += 1
                if regression_count >= 2:
                    logger.warning("Agent %s 连续两次引入回归，自动熔断自进化触发", agent_id)
                    return True
            else:
                break
        return False

    async def _should_require_human_confirmation(self, agent_id: str) -> bool:
        """检查同类提案最近是否连续两次被人类拒绝，如果是则降级为人工确认。"""
        proposals = await self._repo.list_by_agent(agent_id, limit=5)
        dismissed_proposals = [p for p in proposals if p.status == ProposalStatus.DISMISSED]

        reject_count = 0
        for p in dismissed_proposals:
            outcome = (p.outcome or "").lower()
            if "rejected" in outcome or "dismissed" in outcome:
                reject_count += 1
                if reject_count >= 2:
                    return True
            else:
                break
        return False

    def suspend_agent(self, agent_id: str) -> None:
        """手动挂起/暂停指定 Agent 的自进化触发。"""
        self._manually_suspended.add(agent_id)
        logger.info("已手动挂起 Agent %s 的自进化触发", agent_id)

    def resume_agent(self, agent_id: str) -> None:
        """手动恢复被挂起/暂停的 Agent 自进化触发。"""
        if agent_id in self._manually_suspended:
            self._manually_suspended.remove(agent_id)
        logger.info("已手动恢复 Agent %s 的自进化触发", agent_id)

    async def process_event(self, event: EvolutionEvent) -> ImprovementProposal | None:
        if await self.is_agent_suspended(event.agent_id):
            logger.warning("Agent %s 自进化触发已熔断/挂起，跳过本次执行", event.agent_id)
            return None

        if self._is_duplicate(event):
            logger.debug("去重跳过: %s", event.summary[:80])
            return None

        proposal = self._event_to_proposal(event)
        proposal = populate_risk_and_paths(proposal)

        # 连续被拒降级策略
        if await self._should_require_human_confirmation(event.agent_id):
            logger.info("Agent %s 提案连续被驳回，自动降级为需要人工确认", event.agent_id)
            proposal.risk.requires_human_confirmation_before_devflow = True

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
                self._plane_project_id,
                name=proposal.title,
                description=body,
                labels=[],
                properties=custom_props,
            )
            work_item_id = result.get("id", "")

            # TODO Design Gap:
            # 状态双写非原子性。
            # update_status 操作是在本地 DB 执行的，而 create_work_item 发生在 Plane 系统。
            # 倘若 update_status 抛出异常崩溃，Plane 那边任务已然成功创建，导致两端状态产生不一致。
            # 另外在 dispatch_to_plane 中将 sha / id 直接内存赋值给已从 repo 取出的 proposal，
            # 这种就地对象变异如果在后续流程发生崩溃也是脆弱的。
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
            await self._repo.update_status(proposal.proposal_id, ProposalStatus.READY)
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
        # TODO Design Gap:
        # 目前仅仅当自进化触发类型为 PROMPT_GAP 或 EVAL_GAP 时，才会填充 changes。
        # 如果是 TOOL_RUNTIME_ERROR 或 ROUTING_ERROR，生成提案时 changes 会被留空，
        # 这将导致在风险评估阶段因为匹配不到任何修改路径 (paths) 而默认被升级为 MEDIUM 或更高风险，
        # 进而丧失在 LOW 风险下的自动化 DevFlow 机会。
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
