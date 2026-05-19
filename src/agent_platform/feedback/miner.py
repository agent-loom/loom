"""反馈挖掘器：从运行时异常中提炼候选需求提案。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from agent_platform.feedback.collector import FeedbackSignal


@dataclass
class RequirementProposal:
    """候选需求提案，由反馈挖掘器产出，经过门控后发布到 Plane。"""

    # 提案类型：bug / feature / optimization / knowledge_gap / eval_gap
    proposal_type: str
    # 需求标题
    title: str
    # 产生此提案的 agent 标识
    agent_id: str
    # 严重程度：low / medium / high / critical
    severity: str
    # 置信度，范围 [0, 1]
    confidence: float
    # 支撑证据列表，每条为任意字段的字典（脱敏：只含 run_id 和 summary）
    evidence: list[dict]
    # 影响范围：affected_tenants, affected_sessions, first_seen, last_seen
    impact: dict
    # 建议的任务类型
    suggested_task_type: str
    # 建议的验收标准列表
    suggested_acceptance: list[str] = field(default_factory=list)


# 触发 bug 提案的最小错误重复次数
_BUG_THRESHOLD = 3

# 触发 optimization 提案的最小 fallback 次数
_FALLBACK_THRESHOLD = 5

# 错误摘要截断长度（字符），用于分组聚合键
_ERROR_KEY_LENGTH = 50


class FeedbackMiner:
    """将 FeedbackSignal 列表聚合为 RequirementProposal 候选需求列表。"""

    def mine(self, signals: list[FeedbackSignal]) -> list[RequirementProposal]:
        """按 agent_id + 错误模式聚合，返回候选需求列表。

        聚合规则：
        - 同 agent_id 下，同类 error_message（strip 取前 50 chars）出现 ≥ 3 次 → bug 候选
        - 同 agent_id 下，fallback 出现 ≥ 5 次 → optimization 候选
        - confidence = min(出现次数 / 总信号数, 0.95)
        - evidence 只保留 run_id，不含 session_id / tenant_id
        """
        total = len(signals)
        proposals: list[RequirementProposal] = []
        proposals.extend(self._mine_bugs(signals, total))
        proposals.extend(self._mine_fallbacks(signals, total))
        return proposals

    # ------------------------------------------------------------------
    # 私有聚合方法
    # ------------------------------------------------------------------

    def _mine_bugs(
        self, signals: list[FeedbackSignal], total: int
    ) -> list[RequirementProposal]:
        """聚合 error 信号，生成 bug 提案。"""
        # key: (agent_id, error_key) → 信号列表
        buckets: dict[tuple[str, str], list[FeedbackSignal]] = defaultdict(list)

        for sig in signals:
            if sig.signal_type != "error":
                continue
            error_key = _normalize_error(sig.error_message)
            buckets[(sig.agent_id, error_key)].append(sig)

        proposals: list[RequirementProposal] = []
        for (agent_id, error_key), group in buckets.items():
            if len(group) < _BUG_THRESHOLD:
                continue

            count = len(group)
            confidence = min(count / max(total, 1), 0.95)
            severity = _severity_from_count(count)
            timestamps = [s.occurred_at for s in group]

            proposals.append(
                RequirementProposal(
                    proposal_type="bug",
                    title=f"[Bug] {agent_id}: {error_key[:60]}",
                    agent_id=agent_id,
                    severity=severity,
                    confidence=confidence,
                    evidence=_build_evidence(group),
                    impact={
                        "affected_tenants": len({s.tenant_id for s in group}),
                        "affected_sessions": len(
                            {s.session_id for s in group if s.session_id}
                        ),
                        "first_seen": _fmt_dt(min(timestamps)),
                        "last_seen": _fmt_dt(max(timestamps)),
                    },
                    suggested_task_type="agent:change",
                    suggested_acceptance=[
                        f"重现 {error_key[:40]} 不再出现",
                        "相关 agent_runs 无新增同类错误（连续 24h）",
                    ],
                )
            )
        return proposals

    def _mine_fallbacks(
        self, signals: list[FeedbackSignal], total: int
    ) -> list[RequirementProposal]:
        """聚合 fallback 信号，生成 optimization 提案。"""
        # key: agent_id → 信号列表
        buckets: dict[str, list[FeedbackSignal]] = defaultdict(list)

        for sig in signals:
            if sig.signal_type != "fallback":
                continue
            buckets[sig.agent_id].append(sig)

        proposals: list[RequirementProposal] = []
        for agent_id, group in buckets.items():
            if len(group) < _FALLBACK_THRESHOLD:
                continue

            count = len(group)
            confidence = min(count / max(total, 1), 0.95)
            timestamps = [s.occurred_at for s in group]

            proposals.append(
                RequirementProposal(
                    proposal_type="optimization",
                    title=f"[Optimization] {agent_id}: fallback 激增（{count} 次）",
                    agent_id=agent_id,
                    severity=_severity_from_count(count),
                    confidence=confidence,
                    evidence=_build_evidence(group),
                    impact={
                        "affected_tenants": len({s.tenant_id for s in group}),
                        "affected_sessions": len(
                            {s.session_id for s in group if s.session_id}
                        ),
                        "first_seen": _fmt_dt(min(timestamps)),
                        "last_seen": _fmt_dt(max(timestamps)),
                    },
                    suggested_task_type="agent:change",
                    suggested_acceptance=[
                        f"{agent_id} fallback 率降至 <5%（连续 24h）",
                        "主路由成功率提升至 ≥95%",
                    ],
                )
            )
        return proposals


# ------------------------------------------------------------------
# 工具函数
# ------------------------------------------------------------------


def _normalize_error(error_message: str | None) -> str:
    """去首尾空白后截断，生成用于聚合分组的规范化键。"""
    if not error_message:
        return "<unknown>"
    return error_message.strip()[:_ERROR_KEY_LENGTH]


def _severity_from_count(count: int) -> str:
    """根据出现次数推断严重级别。"""
    if count >= 20:
        return "critical"
    if count >= 10:
        return "high"
    if count >= 5:
        return "medium"
    return "low"


def _fmt_dt(dt: datetime) -> str:
    """将 datetime 格式化为 ISO 8601 字符串。"""
    return dt.isoformat()


def _build_evidence(group: list[FeedbackSignal]) -> list[dict]:
    """构建脱敏证据列表，只保留 run_id 和错误摘要，不含 session_id/tenant_id。"""
    seen: set[str] = set()
    evidence: list[dict] = []
    for sig in group:
        if sig.run_id in seen:
            continue
        seen.add(sig.run_id)
        evidence.append(
            {
                "run_id": sig.run_id,
                "summary": (sig.error_message or sig.signal_type)[:100],
            }
        )
    return evidence
