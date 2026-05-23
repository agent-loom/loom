"""Candidate 准入校验与合规审计：执行防泄漏扫描与自进化生命周期流转。

设计定位：
  自进化系统的数据面安全与语义合规看门狗 (Candidate Validator)。
  对应 docs/07-evolution/candidate-contract.md 中的"候选资产校验与准入"组件。
  在自进化系统通过 LLM/Hermes 自动生成潜在的自进化包 (Candidate) 之后，
  本模块负责对各个候选类型（Memory、Skill、Eval 提案）进行 Payload 字段格式对齐、
  审计源事件/证据链绑定情况，执行基于正则的 PII/API 密钥泄漏扫描及注入拦截攻击审计，
  最后通过基于有限状态自动机 (FSM) 的 CandidateStateMachine 控制发布周期。
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
    """Candidate 校验管道 (Candidate Validator)

    自进化准入控制面的权威防火墙，防止恶意生成物注入系统。
    """

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
        # TODO Design Gap:
        # 1. SKILL_DRAFT 类型的候选资产未被强制要求绑定证据链。
        #    如果一个 Skill 被自动推入生成，理论上同样应当具有触发它的 EvolutionEvent 引用或评测证据。
        #    目前的豁免可能造成生成恶意 Skill 时证据无从追溯。
        # 2. TASK_PACK_DRAFT 与 RELEASE_RISK_REPORT 同样缺乏证据绑定校验，
        #    未来应当对所有具有生产侵入性的 Candidate 建立统一的追溯性断言。
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
        # TODO Design Gap:
        # 这里对各种 Candidate 类型的 Payload 字段提取进行了手动提取校验 (Hardcoded keys check)，
        # 而在之后的晋升阶段 (PromotionExecutor) 中，又会单独拉起一次不相干的 Payload 字段读取解析。
        # 如果在此处增加了新字段或重命名了字段，极其容易造成校验与实际物化逻辑的“逻辑分裂 (Desync)”，
        # 应该将 Payload 统一通过强类型的 Pydantic 模型进行绑定校验。
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
        # TODO Design Gap:
        # 1. 静态正则泄露检测手段非常有限，且仅支持简单的键值模式匹配。如果 API 密钥的表达形式更为晦涩，
        #    例如写成了无 key 关联的纯字符串 raw entropy，正则将完全漏过。
        # 2. 目前不支持中文敏感词与大陆 PII（例如身份证号、手机号、商户银行账户）的高灵敏度检测，
        #    未来应当接入专门的大模型语义防泄漏引擎（如 Llama Guard 或 Presidio Analyzer）来做深度脱敏。
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
    """Candidate 有限状态机流转控制 (Candidate State Machine)

    规则：
    1. [*] -> DRAFT
    2. DRAFT -> VALIDATED (通过校验管道)
    3. DRAFT -> REJECTED (未通过校验)
    4. VALIDATED -> APPROVED (策略自动或者人工审批通过)
    5. VALIDATED -> REJECTED (审批拒绝)
    6. APPROVED -> PROMOTED (正式写入/转换为平台资产)
    7. APPROVED -> SUPERSEDED (被更新的候选资产覆盖)
    """

    @staticmethod
    def can_transition(current: CandidateStatus, target: CandidateStatus) -> bool:
        """验证状态是否允许流转。"""
        # TODO Design Gap:
        # CandidateStateMachine 仅作为一个静态的 transition 路由可达表辅助，
        # 在底层的 PromotionExecutor 中目前并没有强行调用并依据此状态机拦截非 APPROVED
        # 对象的晋升操作。换而言之，如果其他进程强行修改数据库将处于 REJECTED 状态的
        # Candidate 推入晋升流，现有的 promotion.py 并不会依据本 FSM 进行状态拦截阻断，
        # 存在安全一致性隐患，后续需在持久化侧引入基于防篡改状态转移的事务守卫。
        transitions = {
            CandidateStatus.DRAFT: {CandidateStatus.VALIDATED, CandidateStatus.REJECTED},
            CandidateStatus.VALIDATED: {CandidateStatus.APPROVED, CandidateStatus.REJECTED},
            CandidateStatus.APPROVED: {CandidateStatus.PROMOTED, CandidateStatus.SUPERSEDED},
            CandidateStatus.PROMOTED: set(),
            CandidateStatus.REJECTED: set(),
            CandidateStatus.SUPERSEDED: set(),
        }
        return target in transitions.get(current, set())
