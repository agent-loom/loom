"""HermesHITLMapper 和 HermesHITLBridge 单元测试。

测试 Hermes HITL 事件到平台 ApprovalGate 审批流的映射。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_platform.runtime.hermes_hitl import (
    HermesHITLBridge,
    HermesHITLMapper,
)
from agent_platform.tools.approval import (
    ApprovalRequest,
    ApprovalStatus,
    AutoApproveGate,
    InMemoryApprovalGate,
)

# ── 辅助工具 ──────────────────────────────────────────────


def _tool_confirmation_event(
    tool_name: str = "dangerous_tool",
    run_id: str = "run-1",
    request_id: str | None = None,
) -> dict:
    """构建 tool_confirmation 类型的 HITL 事件。"""
    data: dict = {
        "tool_name": tool_name,
        "run_id": run_id,
        "args": {"param": "value"},
    }
    if request_id:
        data["request_id"] = request_id
    return {"type": "tool_confirmation", "data": data}


def _human_input_event(
    prompt: str = "请输入确认信息",
    source: str = "user_prompt",
) -> dict:
    """构建 human_input_required 类型的 HITL 事件。"""
    return {
        "type": "human_input_required",
        "data": {
            "prompt": prompt,
            "source": source,
            "run_id": "run-2",
        },
    }


def _safety_check_event(
    check_type: str = "content_safety",
    description: str = "检测到敏感内容",
) -> dict:
    """构建 safety_check 类型的 HITL 事件。"""
    return {
        "type": "safety_check",
        "data": {
            "check_type": check_type,
            "description": description,
            "run_id": "run-3",
        },
    }


def _non_hitl_event() -> dict:
    """构建非 HITL 事件。"""
    return {
        "type": "text_chunk",
        "data": {"content": "hello world"},
    }


# ── HermesHITLMapper.is_hitl_event ────────────────────────


class TestHermesHITLMapperIsHITL:
    """测试 HITL 事件识别。"""

    def setup_method(self) -> None:
        self.mapper = HermesHITLMapper()

    def test_tool_confirmation_is_hitl(self):
        """tool_confirmation 事件应被识别为 HITL 事件。"""
        assert self.mapper.is_hitl_event(_tool_confirmation_event()) is True

    def test_tool_confirm_alias_is_hitl(self):
        """tool_confirm 别名也应被识别为 HITL 事件。"""
        event = {"type": "tool_confirm", "data": {}}
        assert self.mapper.is_hitl_event(event) is True

    def test_human_input_required_is_hitl(self):
        """human_input_required 事件应被识别为 HITL 事件。"""
        assert self.mapper.is_hitl_event(_human_input_event()) is True

    def test_safety_check_is_hitl(self):
        """safety_check 事件应被识别为 HITL 事件。"""
        assert self.mapper.is_hitl_event(_safety_check_event()) is True

    def test_non_hitl_event_is_not_hitl(self):
        """非 HITL 事件不应被识别为 HITL 事件。"""
        assert self.mapper.is_hitl_event(_non_hitl_event()) is False

    def test_empty_type_is_not_hitl(self):
        """无 type 字段的事件不应被识别为 HITL 事件。"""
        assert self.mapper.is_hitl_event({"data": {}}) is False

    def test_unknown_type_is_not_hitl(self):
        """未知 type 的事件不应被识别为 HITL 事件。"""
        assert self.mapper.is_hitl_event({"type": "llm_response", "data": {}}) is False


# ── HermesHITLMapper.map_hitl_event ───────────────────────


class TestHermesHITLMapperMapEvent:
    """测试 HITL 事件到 ApprovalRequest 的转换。"""

    def setup_method(self) -> None:
        self.mapper = HermesHITLMapper()

    def test_map_tool_confirmation(self):
        """tool_confirmation 事件应正确转换为 ApprovalRequest。"""
        event = _tool_confirmation_event(tool_name="rm_rf", request_id="req-001")
        request = self.mapper.map_hitl_event(event)

        assert request is not None
        assert isinstance(request, ApprovalRequest)
        assert request.request_id == "req-001"
        assert request.tool_name == "rm_rf"
        assert request.risk_level == "high"
        assert request.run_id == "run-1"
        assert request.status == ApprovalStatus.PENDING

    def test_map_human_input_required(self):
        """human_input_required 事件应正确转换为 ApprovalRequest。"""
        event = _human_input_event(prompt="确认操作？", source="dialog")
        request = self.mapper.map_hitl_event(event)

        assert request is not None
        assert request.tool_name == "dialog"
        assert request.risk_level == "medium"
        assert "确认操作" in request.reason

    def test_map_safety_check(self):
        """safety_check 事件应正确转换为 ApprovalRequest。"""
        event = _safety_check_event(check_type="toxicity", description="检测到不当内容")
        request = self.mapper.map_hitl_event(event)

        assert request is not None
        assert request.tool_name == "toxicity"
        assert request.risk_level == "critical"
        assert "检测到不当内容" in request.reason

    def test_map_non_hitl_returns_none(self):
        """非 HITL 事件应返回 None。"""
        result = self.mapper.map_hitl_event(_non_hitl_event())
        assert result is None

    def test_map_generates_request_id(self):
        """没有 request_id 的事件应自动生成 UUID。"""
        event = _tool_confirmation_event(request_id=None)
        request = self.mapper.map_hitl_event(event)

        assert request is not None
        assert len(request.request_id) > 0  # 自动生成的 UUID

    def test_map_custom_reason(self):
        """事件数据中的自定义 reason 应优先使用。"""
        event = {
            "type": "tool_confirmation",
            "data": {
                "tool_name": "exec",
                "reason": "自定义审批原因",
            },
        }
        request = self.mapper.map_hitl_event(event)

        assert request is not None
        assert request.reason == "自定义审批原因"


# ── HermesHITLMapper.handle_hitl_callback ─────────────────


class TestHermesHITLMapperCallback:
    """测试审批回调处理。"""

    @pytest.mark.asyncio
    async def test_callback_approved(self):
        """审批通过应返回 approved=True 的响应。"""
        mapper = HermesHITLMapper()
        gate = AutoApproveGate()
        event = _tool_confirmation_event(tool_name="deploy")

        response = await mapper.handle_hitl_callback(gate, event)

        assert response["approved"] is True
        assert response["action"] == "proceed"
        assert response["tool_confirmed"] is True

    @pytest.mark.asyncio
    async def test_callback_rejected(self):
        """审批拒绝应返回 approved=False 的响应。"""
        mapper = HermesHITLMapper()
        gate = InMemoryApprovalGate(auto_approve=False)

        # InMemoryApprovalGate 默认返回 PENDING，不是 APPROVED
        event = _tool_confirmation_event(tool_name="delete_db")
        response = await mapper.handle_hitl_callback(gate, event)

        # PENDING 不等于 APPROVED，所以应返回 abort
        assert response["approved"] is False
        assert response["action"] == "abort"
        assert response["tool_confirmed"] is False

    @pytest.mark.asyncio
    async def test_callback_non_hitl_raises(self):
        """对非 HITL 事件调用 callback 应抛出 ValueError。"""
        mapper = HermesHITLMapper()
        gate = AutoApproveGate()

        with pytest.raises(ValueError, match="非 HITL 事件"):
            await mapper.handle_hitl_callback(gate, _non_hitl_event())

    @pytest.mark.asyncio
    async def test_callback_human_input_approved(self):
        """human_input 审批通过应包含 human_response 字段。"""
        mapper = HermesHITLMapper()
        gate = AutoApproveGate()
        event = _human_input_event()

        response = await mapper.handle_hitl_callback(gate, event)

        assert response["approved"] is True
        assert "human_response" in response

    @pytest.mark.asyncio
    async def test_callback_safety_check_approved(self):
        """safety_check 审批通过应包含 safety_approved 字段。"""
        mapper = HermesHITLMapper()
        gate = AutoApproveGate()
        event = _safety_check_event()

        response = await mapper.handle_hitl_callback(gate, event)

        assert response["approved"] is True
        assert response["safety_approved"] is True


# ── HermesHITLBridge ──────────────────────────────────────


class TestHermesHITLBridge:
    """测试 HermesHITLBridge 集成。"""

    @pytest.mark.asyncio
    async def test_intercept_non_hitl_returns_none(self):
        """非 HITL 事件应返回 None（不拦截）。"""
        bridge = HermesHITLBridge(approval_gate=AutoApproveGate())
        result = await bridge.intercept(_non_hitl_event())
        assert result is None

    @pytest.mark.asyncio
    async def test_intercept_hitl_event_approved(self):
        """HITL 事件在 AutoApproveGate 下应返回 approved 响应。"""
        bridge = HermesHITLBridge(approval_gate=AutoApproveGate())
        event = _tool_confirmation_event(tool_name="deploy_prod")

        result = await bridge.intercept(event)

        assert result is not None
        assert result["approved"] is True
        assert result["action"] == "proceed"

    @pytest.mark.asyncio
    async def test_intercept_hitl_event_pending(self):
        """HITL 事件在 InMemoryApprovalGate（非自动审批）下应返回 abort。"""
        gate = InMemoryApprovalGate(auto_approve=False)
        bridge = HermesHITLBridge(approval_gate=gate)
        event = _safety_check_event()

        result = await bridge.intercept(event)

        assert result is not None
        assert result["approved"] is False

    @pytest.mark.asyncio
    async def test_intercept_timeout(self):
        """审批超时应返回 expired 状态的拒绝响应。"""

        class SlowGate:
            """模拟永远不响应的审批门。"""

            async def request_approval(self, request: ApprovalRequest) -> ApprovalStatus:
                await asyncio.sleep(10)  # 模拟长时间等待
                return ApprovalStatus.APPROVED

            async def check_status(self, request_id: str) -> ApprovalStatus:
                return ApprovalStatus.PENDING

            async def resolve(
                self, request_id: str, status: ApprovalStatus, actor: str,
            ) -> None:
                pass

            async def list_pending(self) -> list[ApprovalRequest]:
                return []

        bridge = HermesHITLBridge(
            approval_gate=SlowGate(),
            timeout_seconds=1,  # 1 秒超时（测试用）
        )
        event = _tool_confirmation_event()

        result = await bridge.intercept(event)

        assert result is not None
        assert result["approved"] is False
        assert result["status"] == str(ApprovalStatus.EXPIRED)
        assert "超时" in result.get("rejection_reason", "")

    @pytest.mark.asyncio
    async def test_intercept_exception_handling(self):
        """审批过程中抛出异常应返回 error 状态。"""

        class BrokenGate:
            """模拟抛出异常的审批门。"""

            async def request_approval(self, request: ApprovalRequest) -> ApprovalStatus:
                msg = "审批系统内部错误"
                raise RuntimeError(msg)

            async def check_status(self, request_id: str) -> ApprovalStatus:
                return ApprovalStatus.PENDING

            async def resolve(
                self, request_id: str, status: ApprovalStatus, actor: str,
            ) -> None:
                pass

            async def list_pending(self) -> list[ApprovalRequest]:
                return []

        bridge = HermesHITLBridge(approval_gate=BrokenGate())
        event = _tool_confirmation_event()

        result = await bridge.intercept(event)

        assert result is not None
        assert result["approved"] is False
        assert result["status"] == "error"
        assert result["action"] == "abort"

    @pytest.mark.asyncio
    async def test_bridge_properties(self):
        """Bridge 属性应正确暴露内部组件。"""
        gate = AutoApproveGate()
        bridge = HermesHITLBridge(approval_gate=gate, timeout_seconds=600)

        assert bridge.approval_gate is gate
        assert bridge.mapper is not None

    @pytest.mark.asyncio
    async def test_intercept_multiple_event_types(self):
        """不同类型的 HITL 事件都应被正确拦截处理。"""
        bridge = HermesHITLBridge(approval_gate=AutoApproveGate())

        # tool_confirmation
        r1 = await bridge.intercept(_tool_confirmation_event())
        assert r1 is not None and r1["approved"] is True

        # human_input_required
        r2 = await bridge.intercept(_human_input_event())
        assert r2 is not None and r2["approved"] is True

        # safety_check
        r3 = await bridge.intercept(_safety_check_event())
        assert r3 is not None and r3["approved"] is True

    @pytest.mark.asyncio
    async def test_intercept_aliases(self):
        """HITL 事件的别名也应被正确拦截。"""
        bridge = HermesHITLBridge(approval_gate=AutoApproveGate())

        # tool_confirm 别名
        event = {"type": "tool_confirm", "data": {"tool_name": "exec"}}
        result = await bridge.intercept(event)
        assert result is not None and result["approved"] is True

        # human_input 别名
        event2 = {"type": "human_input", "data": {"source": "chat"}}
        result2 = await bridge.intercept(event2)
        assert result2 is not None and result2["approved"] is True

        # safety_review 别名
        event3 = {"type": "safety_review", "data": {"check_type": "pii"}}
        result3 = await bridge.intercept(event3)
        assert result3 is not None and result3["approved"] is True


# ── HermesRuntimeBackend 集成 ─────────────────────────────


class TestHermesRuntimeBackendHITLIntegration:
    """测试 HermesRuntimeBackend 中 HITL 集成的初始化。"""

    def test_backend_without_approval_gate(self):
        """不传 approval_gate 时 hitl_bridge 应为 None。"""
        from agent_platform.runtime.hermes import HermesRuntimeBackend

        backend = HermesRuntimeBackend()
        assert backend.hitl_bridge is None

    def test_backend_with_approval_gate(self):
        """传入 approval_gate 后 hitl_bridge 应被正确初始化。"""
        from agent_platform.runtime.hermes import HermesRuntimeBackend

        gate = AutoApproveGate()
        backend = HermesRuntimeBackend(approval_gate=gate)

        assert backend.hitl_bridge is not None
        assert isinstance(backend.hitl_bridge, HermesHITLBridge)
        assert backend.hitl_bridge.approval_gate is gate
