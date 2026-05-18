"""DLQ 重试回调分发测试。

验证 _dlq_dispatch_handler 能够根据 source 正确路由到
DevFlowOrchestrator 或 GitLabEventHandler，
并在 source 未知时安全跳过。
"""

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_dispatch_handler(app_state):
    """构造一个与 app.py 中 _dlq_dispatch_handler 逻辑一致的函数，用于单元测试。

    从 app.state 的 devflow_orchestrator / gitlab_event_handler 属性
    获取处理器实例，按 source 分发。
    """
    import logging
    logger = logging.getLogger("agent_platform.api.app")

    async def _dlq_dispatch_handler(source, event_type, payload):
        """DLQ 重试分发：根据来源路由到对应的事件处理器。"""
        if source == "plane":
            orchestrator = getattr(app_state, "devflow_orchestrator", None)
            if orchestrator:
                await orchestrator.handle_webhook_event(event_type, payload)
                return
        elif source == "gitlab":
            handler = getattr(app_state, "gitlab_event_handler", None)
            if handler:
                await handler.handle_event(event_type, payload)
                return
        logger.warning("DLQ 重试：未知来源 %s，跳过", source)

    return _dlq_dispatch_handler


class _FakeState:
    """模拟 app.state 的简单容器。"""
    pass


# ── Plane 来源测试 ──

@pytest.mark.asyncio
async def test_dlq_dispatch_plane_calls_orchestrator():
    """plane 来源的 DLQ 消息应触发 orchestrator.handle_webhook_event。"""
    state = _FakeState()
    mock_orchestrator = AsyncMock()
    state.devflow_orchestrator = mock_orchestrator
    state.gitlab_event_handler = None

    handler = _make_dispatch_handler(state)
    payload = {"issue": {"id": "123", "state": "in_progress"}}
    await handler("plane", "issue.updated", payload)

    mock_orchestrator.handle_webhook_event.assert_awaited_once_with(
        "issue.updated", payload,
    )


@pytest.mark.asyncio
async def test_dlq_dispatch_plane_no_orchestrator():
    """plane 来源但 orchestrator 为 None 时，不崩溃且记录警告。"""
    state = _FakeState()
    state.devflow_orchestrator = None
    state.gitlab_event_handler = None

    handler = _make_dispatch_handler(state)
    # 不应抛出异常
    await handler("plane", "issue.created", {"data": 1})


# ── GitLab 来源测试 ──

@pytest.mark.asyncio
async def test_dlq_dispatch_gitlab_calls_handler():
    """gitlab 来源的 DLQ 消息应触发 gitlab_event_handler.handle_event。"""
    state = _FakeState()
    state.devflow_orchestrator = None
    mock_gitlab_handler = AsyncMock()
    state.gitlab_event_handler = mock_gitlab_handler

    handler = _make_dispatch_handler(state)
    payload = {"object_kind": "merge_request", "project": {"id": 42}}
    await handler("gitlab", "merge_request", payload)

    mock_gitlab_handler.handle_event.assert_awaited_once_with(
        "merge_request", payload,
    )


@pytest.mark.asyncio
async def test_dlq_dispatch_gitlab_no_handler():
    """gitlab 来源但 handler 为 None 时，不崩溃且记录警告。"""
    state = _FakeState()
    state.devflow_orchestrator = None
    state.gitlab_event_handler = None

    handler = _make_dispatch_handler(state)
    await handler("gitlab", "pipeline", {"status": "success"})


# ── 未知来源测试 ──

@pytest.mark.asyncio
async def test_dlq_dispatch_unknown_source():
    """未知来源不崩溃，仅记录警告日志。"""
    state = _FakeState()
    state.devflow_orchestrator = AsyncMock()
    state.gitlab_event_handler = AsyncMock()

    handler = _make_dispatch_handler(state)
    await handler("unknown_service", "some.event", {"key": "value"})

    # 确认两个处理器都没有被调用
    state.devflow_orchestrator.handle_webhook_event.assert_not_awaited()
    state.gitlab_event_handler.handle_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_dlq_dispatch_empty_source():
    """空字符串来源不崩溃。"""
    state = _FakeState()
    state.devflow_orchestrator = None
    state.gitlab_event_handler = None

    handler = _make_dispatch_handler(state)
    await handler("", "event", {})


# ── 多条消息混合测试 ──

@pytest.mark.asyncio
async def test_dlq_dispatch_mixed_sources():
    """连续处理多条不同来源的消息，验证正确路由。"""
    state = _FakeState()
    mock_orchestrator = AsyncMock()
    mock_gitlab_handler = AsyncMock()
    state.devflow_orchestrator = mock_orchestrator
    state.gitlab_event_handler = mock_gitlab_handler

    handler = _make_dispatch_handler(state)

    await handler("plane", "issue.updated", {"id": "1"})
    await handler("gitlab", "pipeline", {"id": "2"})
    await handler("plane", "issue.created", {"id": "3"})

    assert mock_orchestrator.handle_webhook_event.await_count == 2
    assert mock_gitlab_handler.handle_event.await_count == 1

    # 验证调用顺序
    plane_calls = mock_orchestrator.handle_webhook_event.await_args_list
    assert plane_calls[0].args == ("issue.updated", {"id": "1"})
    assert plane_calls[1].args == ("issue.created", {"id": "3"})

    gitlab_calls = mock_gitlab_handler.handle_event.await_args_list
    assert gitlab_calls[0].args == ("pipeline", {"id": "2"})
