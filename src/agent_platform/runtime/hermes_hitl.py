"""Hermes HITL（Human-in-the-Loop）事件映射层。

将 Hermes 运行时的 HITL 事件（如 tool_confirmation、human_input_required、
safety_check 等）映射为平台 ApprovalGate 审批流，实现人机协同审批。

本模块提供两层抽象：
- HermesHITLMapper：底层映射器，负责事件识别和 ApprovalRequest 转换
- HermesHITLBridge：高层桥接器，集成到 HermesRuntimeBackend 的事件处理流程
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from agent_platform.tools.approval import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalStatus,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hermes HITL 事件类型常量
# ---------------------------------------------------------------------------

# 需要人工确认的工具调用
_TOOL_CONFIRMATION_TYPES = frozenset({
    "tool_confirmation",
    "tool_confirm",
    "confirm_tool_call",
})

# 需要人工输入的请求
_HUMAN_INPUT_TYPES = frozenset({
    "human_input_required",
    "human_input",
    "request_human_input",
})

# 安全检查事件
_SAFETY_CHECK_TYPES = frozenset({
    "safety_check",
    "safety_review",
    "content_review",
})

# 所有 HITL 事件类型的合集
_ALL_HITL_TYPES = _TOOL_CONFIRMATION_TYPES | _HUMAN_INPUT_TYPES | _SAFETY_CHECK_TYPES

# HITL 事件类型到风险级别的映射
_RISK_LEVEL_MAP: dict[str, str] = {
    "tool_confirmation": "high",
    "tool_confirm": "high",
    "confirm_tool_call": "high",
    "human_input_required": "medium",
    "human_input": "medium",
    "request_human_input": "medium",
    "safety_check": "critical",
    "safety_review": "critical",
    "content_review": "critical",
}


class HermesHITLMapper:
    """将 Hermes HITL 事件映射为平台 ApprovalRequest。

    通过事件类型名称进行匹配，不依赖真实 Hermes SDK 导入。
    支持 tool_confirmation、human_input_required、safety_check 三类事件。
    """

    def is_hitl_event(self, event: dict[str, Any]) -> bool:
        """判断事件是否为 HITL 事件。

        Args:
            event: Hermes 事件字典，必须包含 "type" 字段

        Returns:
            是否为需要人工审批的 HITL 事件
        """
        event_type = event.get("type", "")
        return event_type in _ALL_HITL_TYPES

    def map_hitl_event(self, event: dict[str, Any]) -> ApprovalRequest | None:
        """将 Hermes HITL 事件转换为平台 ApprovalRequest。

        如果事件不是 HITL 事件，返回 None。

        Args:
            event: Hermes 事件字典

        Returns:
            ApprovalRequest 实例，或 None（非 HITL 事件时）
        """
        event_type = event.get("type", "")
        if event_type not in _ALL_HITL_TYPES:
            return None

        # 提取事件数据
        data = event.get("data", {})
        tool_name = self._extract_tool_name(event_type, data)
        risk_level = _RISK_LEVEL_MAP.get(event_type, "medium")
        reason = self._extract_reason(event_type, data)
        request_id = data.get("request_id") or str(uuid.uuid4())

        return ApprovalRequest(
            request_id=request_id,
            tool_name=tool_name,
            risk_level=risk_level,
            payload=data,
            agent_id=data.get("agent_id"),
            run_id=data.get("run_id"),
            reason=reason,
        )

    async def handle_hitl_callback(
        self,
        approval_gate: ApprovalGate,
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """提交审批请求并等待结果，返回给 Hermes 的响应。

        Args:
            approval_gate: 审批门实例
            event: Hermes HITL 事件

        Returns:
            Hermes 需要的响应字典

        Raises:
            ValueError: 如果事件不是 HITL 事件
        """
        request = self.map_hitl_event(event)
        if request is None:
            msg = f"非 HITL 事件无法处理: {event.get('type', 'unknown')}"
            raise ValueError(msg)

        # 提交审批请求
        status = await approval_gate.request_approval(request)

        # 根据审批结果构建 Hermes 响应
        return self._build_hermes_response(event, request, status)

    def _extract_tool_name(
        self, event_type: str, data: dict[str, Any],
    ) -> str:
        """从事件数据中提取工具名称。

        Args:
            event_type: 事件类型
            data: 事件数据

        Returns:
            工具名称
        """
        if event_type in _TOOL_CONFIRMATION_TYPES:
            return data.get("tool_name", data.get("name", "unknown_tool"))
        if event_type in _HUMAN_INPUT_TYPES:
            return data.get("source", "human_input")
        if event_type in _SAFETY_CHECK_TYPES:
            return data.get("check_type", "safety_check")
        return "unknown"

    def _extract_reason(
        self, event_type: str, data: dict[str, Any],
    ) -> str:
        """从事件数据中提取审批原因。

        Args:
            event_type: 事件类型
            data: 事件数据

        Returns:
            审批原因描述
        """
        # 优先使用事件自带的 reason 字段
        if "reason" in data:
            return str(data["reason"])

        if event_type in _TOOL_CONFIRMATION_TYPES:
            tool = data.get("tool_name", data.get("name", "unknown"))
            return f"工具 '{tool}' 需要人工确认后才能执行"
        if event_type in _HUMAN_INPUT_TYPES:
            return data.get("prompt", "Agent 请求人工输入")
        if event_type in _SAFETY_CHECK_TYPES:
            return data.get("description", "内容安全检查需要人工审核")
        return "Hermes HITL 事件需要人工审批"

    @staticmethod
    def _build_hermes_response(
        event: dict[str, Any],
        request: ApprovalRequest,
        status: ApprovalStatus,
    ) -> dict[str, Any]:
        """根据审批结果构建 Hermes 响应。

        Args:
            event: 原始 Hermes 事件
            request: 审批请求
            status: 审批状态

        Returns:
            Hermes 需要的响应字典
        """
        event_type = event.get("type", "")
        approved = status == ApprovalStatus.APPROVED

        response: dict[str, Any] = {
            "request_id": request.request_id,
            "approved": approved,
            "status": str(status),
            "event_type": event_type,
        }

        if approved:
            response["action"] = "proceed"
            if event_type in _TOOL_CONFIRMATION_TYPES:
                response["tool_confirmed"] = True
            elif event_type in _HUMAN_INPUT_TYPES:
                response["human_response"] = request.payload.get(
                    "default_response", "",
                )
            elif event_type in _SAFETY_CHECK_TYPES:
                response["safety_approved"] = True
        else:
            response["action"] = "abort"
            if event_type in _TOOL_CONFIRMATION_TYPES:
                response["tool_confirmed"] = False
            elif event_type in _HUMAN_INPUT_TYPES:
                response["human_response"] = None
            elif event_type in _SAFETY_CHECK_TYPES:
                response["safety_approved"] = False
            response["rejection_reason"] = (
                f"审批被拒绝: {request.tool_name} (状态: {status})"
            )

        return response


class HermesHITLBridge:
    """高层 HITL 桥接器，集成到 HermesRuntimeBackend 的事件处理流程。

    拦截 Hermes 事件，如果是 HITL 事件则走审批流，返回 Hermes 需要的响应；
    非 HITL 事件返回 None，表示不拦截。
    """

    def __init__(
        self,
        approval_gate: ApprovalGate,
        timeout_seconds: int = 300,
    ) -> None:
        """初始化 HermesHITLBridge。

        Args:
            approval_gate: 审批门实例（满足 ApprovalGate Protocol）
            timeout_seconds: 审批超时时间（秒），默认 300
        """
        self._approval_gate = approval_gate
        self._timeout_seconds = timeout_seconds
        self._mapper = HermesHITLMapper()

    @property
    def approval_gate(self) -> ApprovalGate:
        """获取审批门实例。"""
        return self._approval_gate

    @property
    def mapper(self) -> HermesHITLMapper:
        """获取 HITL 映射器。"""
        return self._mapper

    async def intercept(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """拦截 Hermes 事件，如果是 HITL 事件则走审批流。

        非 HITL 事件返回 None，表示不拦截，事件应继续正常处理。
        HITL 事件会提交审批请求，等待审批结果（带超时），然后返回
        Hermes 需要的响应字典。

        Args:
            event: Hermes 事件字典

        Returns:
            Hermes 响应字典（HITL 事件）或 None（非 HITL 事件）
        """
        if not self._mapper.is_hitl_event(event):
            return None

        event_type = event.get("type", "unknown")
        logger.info("拦截到 HITL 事件: %s", event_type)

        try:
            response = await asyncio.wait_for(
                self._mapper.handle_hitl_callback(
                    self._approval_gate, event,
                ),
                timeout=self._timeout_seconds,
            )
            logger.info(
                "HITL 事件 %s 审批完成: approved=%s",
                event_type,
                response.get("approved"),
            )
            return response
        except TimeoutError:
            logger.warning(
                "HITL 事件 %s 审批超时（%d 秒）",
                event_type,
                self._timeout_seconds,
            )
            # 超时时构建拒绝响应
            request = self._mapper.map_hitl_event(event)
            request_id = request.request_id if request else "unknown"
            return {
                "request_id": request_id,
                "approved": False,
                "status": str(ApprovalStatus.EXPIRED),
                "event_type": event_type,
                "action": "abort",
                "rejection_reason": f"审批超时（{self._timeout_seconds} 秒）",
            }
        except Exception:
            logger.exception("HITL 事件 %s 处理异常", event_type)
            return {
                "request_id": "error",
                "approved": False,
                "status": "error",
                "event_type": event_type,
                "action": "abort",
                "rejection_reason": "HITL 处理过程中发生异常",
            }
