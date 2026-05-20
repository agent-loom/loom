"""Candidate 校验管道与状态机规则。

用于对 Hermes 生成的各类 Candidate 进行 schema 校验、证据校验、安全扫描 (PII/Secret/Prompt 注入)，
并控制 Candidate 状态机的流转。
"""
from __future__ import annotations

import re
from typing import Any

from agent_platform.evolution.models import (
    Candidate,
    CandidateStatus,
    CandidateType,
)

# 敏感信息扫描模式 (检测 API Keys、Secrets 等)
_SECRET_PATTERNS = [
    re.compile(r"(?i)api[-_]?key"),
    re.compile(r"(?i)secret[-_]?(key)?"),
    re.compile(r"(?i)password"),
    re.compile(r"(?i)token"),
    re.compile(r"(?i)passwd"),
]

# 简单的注入扫描 (检查敏感词组合，比如 System Instruction Override 尝试)
_INJECTION_PATTERNS = [
    re.compile(r"(?i)ignore\s+previous\s+instructions"),
    re.compile(r"(?i)system\s+prompt\s+bypass"),
    re.compile(r"(?i)you\s+are\s+now\s+an\s+admin"),
]


class CandidateValidator:
    """Candidate 校验管道。"""

    def validate(self, candidate: Candidate) -> list[str]:
        """对 Candidate 执行多级安全与语义规则校验。

        :param candidate: 待校验的 Candidate。
        :return: 错误消息列表，若无错误则返回空列表 []。
        """
        errors: list[str] = []

        # 1. 基础字段非空校验
        if not candidate.agent_id:
            errors.append("agent_id 不能为空")
        if not candidate.tenant_id:
            errors.append("tenant_id 不能为空")

        # 2. 证据完整性校验
        # 绝大部分候选资产必须有证据关联
        if candidate.candidate_type in (
            CandidateType.MEMORY_CANDIDATE,
            CandidateType.PROPOSAL_DRAFT,
            CandidateType.REVIEW_REPORT,
        ):
            if not candidate.evidence_ids and not candidate.source_event_ids:
                errors.append(f"{candidate.candidate_type} 类型的候选资产必须绑定至少一个证据 ID 或源事件 ID")

        # 3. 各类型 Payload 校验
        self._validate_payload(candidate, errors)

        # 4. 安全与合规性扫描
        self._scan_security(candidate, errors)

        return errors

    def _validate_payload(self, candidate: Candidate, errors: list[str]) -> None:
        payload = candidate.payload or {}

        if candidate.candidate_type == CandidateType.MEMORY_CANDIDATE:
            if "summary" not in payload or not str(payload["summary"]).strip():
                errors.append("memory_candidate payload 必须包含非空 summary 字段")
            if "memory_type" not in payload or not str(payload["memory_type"]).strip():
                errors.append("memory_candidate payload 必须包含非空 memory_type 字段")

        elif candidate.candidate_type == CandidateType.SKILL_DRAFT:
            if "skill_id" not in payload or not str(payload["skill_id"]).strip():
                errors.append("skill_draft payload 必须包含非空 skill_id 字段")
            if "title" not in payload or not str(payload["title"]).strip():
                errors.append("skill_draft payload 必须包含非空 title 字段")
            if "description" not in payload or not str(payload["description"]).strip():
                errors.append("skill_draft payload 必须包含非空 description 字段")

        elif candidate.candidate_type == CandidateType.EVAL_CASE_DRAFT:
            if "name" not in payload or not str(payload["name"]).strip():
                errors.append("eval_case_draft payload 必须包含非空 name 字段")
            if "input" not in payload:
                errors.append("eval_case_draft payload 必须包含 input 字段")
            if "expected" not in payload:
                errors.append("eval_case_draft payload 必须包含 expected 期望结果字段")

        elif candidate.candidate_type == CandidateType.PROPOSAL_DRAFT:
            if "summary" not in payload or not str(payload["summary"]).strip():
                errors.append("proposal_draft payload 必须包含非空 summary 字段")
            if "root_cause" not in payload or not str(payload["root_cause"]).strip():
                errors.append("proposal_draft payload 必须包含非空 root_cause 归因字段")

        elif candidate.candidate_type == CandidateType.REVIEW_REPORT:
            if "summary" not in payload or not str(payload["summary"]).strip():
                errors.append("review_report payload 必须包含非空 summary 字段")
            if "verdict" not in payload or not str(payload["verdict"]).strip():
                errors.append("review_report payload 必须包含非空 verdict 评审结论")

        elif candidate.candidate_type == CandidateType.RELEASE_RISK_REPORT:
            if "risk_level" not in payload or not str(payload["risk_level"]).strip():
                errors.append("release_risk_report payload 必须包含非空 risk_level 字段")

        elif candidate.candidate_type == CandidateType.TASK_PACK_DRAFT:
            if "title" not in payload or not str(payload["title"]).strip():
                errors.append("task_pack_draft payload 必须包含非空 title 字段")

    def _scan_security(self, candidate: Candidate, errors: list[str]) -> None:
        """对 Payload 中的所有文本进行 PII/敏感信息扫描与注入扫描。"""
        payload = candidate.payload or {}

        # 扁平化收集所有文本内容
        text_values: list[str] = []

        def collect_texts(val: Any) -> None:
            if isinstance(val, str):
                text_values.append(val)
            elif isinstance(val, dict):
                for k, v in val.items():
                    collect_texts(k)
                    collect_texts(v)
            elif isinstance(val, list):
                for item in val:
                    collect_texts(item)

        collect_texts(payload)

        combined_text = " ".join(text_values)

        # 1. 扫描疑似泄露的密钥/凭证信息
        # 如果是简单的包含类似 'api_key: sk-1234...' 则予以拦截
        for pattern in _SECRET_PATTERNS:
            if pattern.search(combined_text):
                # 进一步检测是否含有具体的敏感分配值，比如 sk-...
                # 如果只是普通名字 'requires_api_key'，可以忽略，如果是 'api_key=xyz' 则拦截
                matches = re.findall(r"(?i)(api[-_]?key|secret|token|password)\s*[:=]\s*['\"]?[\w-]{8,}['\"]?", combined_text)
                if matches:
                    errors.append("安全扫描失败: 候选资产的 payload 中疑似包含明文凭证/密钥或敏感数据")
                    break

        # 2. 扫描指令注入
        for pattern in _INJECTION_PATTERNS:
            if pattern.search(combined_text):
                errors.append("安全扫描失败: 检测到潜在的 Prompt 注入或指令劫持行为")
                break


class CandidateStateMachine:
    """Candidate 状态流转控制。

    规则：
    1. [*] -> draft
    2. draft -> validated (通过校验管道)
    3. draft -> rejected (未通过校验)
    4. validated -> approved (策略自动或者人工审批通过)
    5. validated -> rejected (审批拒绝)
    6. approved -> promoted (正式写入/转换为平台资产)
    7. approved -> superseded (被更新的候选资产覆盖)
    """

    @staticmethod
    def can_transition(current: CandidateStatus, target: CandidateStatus) -> bool:
        """验证状态是否允许流转。"""
        transitions = {
            CandidateStatus.DRAFT: {CandidateStatus.VALIDATED, CandidateStatus.REJECTED},
            CandidateStatus.VALIDATED: {CandidateStatus.APPROVED, CandidateStatus.REJECTED},
            CandidateStatus.APPROVED: {CandidateStatus.PROMOTED, CandidateStatus.SUPERSEDED},
            CandidateStatus.PROMOTED: set(),
            CandidateStatus.REJECTED: set(),
            CandidateStatus.SUPERSEDED: set(),
        }
        return target in transitions.get(current, set())
