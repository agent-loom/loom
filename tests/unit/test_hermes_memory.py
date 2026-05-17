"""PlatformMemoryProvider 和 HermesMemoryBridge 单元测试。"""

from __future__ import annotations

import pytest

from agent_platform.domain.models import AgentSession
from agent_platform.persistence.memory import InMemoryAgentSessionRepository
from agent_platform.runtime.hermes_memory import (
    HermesMemoryBridge,
    PlatformMemoryProvider,
)

# ── 辅助 ─────────────────────────────────────────────────


def _make_provider(
    store: InMemoryAgentSessionRepository | None = None,
    agent_id: str = "test-agent",
    session_id: str = "sess-001",
) -> PlatformMemoryProvider:
    """构造带内存 store 的 PlatformMemoryProvider。"""
    return PlatformMemoryProvider(
        session_store=store or InMemoryAgentSessionRepository(),
        agent_id=agent_id,
        session_id=session_id,
    )


def _make_session(
    session_id: str = "sess-001",
    agent_id: str = "test-agent",
    messages: list[dict[str, str]] | None = None,
) -> AgentSession:
    """构造包含历史消息的 AgentSession。"""
    session = AgentSession(session_id=session_id, agent_id=agent_id)
    for msg in messages or []:
        session.add_message(msg["role"], msg["content"])
    return session


# ── PlatformMemoryProvider.load_async ─────────────────────


@pytest.mark.asyncio
async def test_load_async_empty_session():
    """加载不存在的会话应返回空列表。"""
    store = InMemoryAgentSessionRepository()
    provider = _make_provider(store)

    result = await provider.load_async("sess-nonexist")

    assert result == []


@pytest.mark.asyncio
async def test_load_async_with_history():
    """加载已有消息的会话应返回 Hermes 格式消息。"""
    store = InMemoryAgentSessionRepository()
    session = _make_session(messages=[
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！有什么可以帮你的？"},
    ])
    await store.save(session)
    provider = _make_provider(store)

    result = await provider.load_async("sess-001")

    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "你好"}
    assert result[1] == {"role": "assistant", "content": "你好！有什么可以帮你的？"}


@pytest.mark.asyncio
async def test_load_async_message_format():
    """确认返回的消息格式仅包含 role 和 content 字段。"""
    store = InMemoryAgentSessionRepository()
    session = _make_session(messages=[
        {"role": "system", "content": "你是一个助手"},
    ])
    await store.save(session)
    provider = _make_provider(store)

    result = await provider.load_async("sess-001")

    assert len(result) == 1
    msg = result[0]
    assert set(msg.keys()) == {"role", "content"}
    assert msg["role"] == "system"


# ── PlatformMemoryProvider.save_async ─────────────────────


@pytest.mark.asyncio
async def test_save_async_new_session():
    """保存到不存在的会话应自动创建新会话。"""
    store = InMemoryAgentSessionRepository()
    provider = _make_provider(store)

    messages = [
        {"role": "user", "content": "测试消息"},
        {"role": "assistant", "content": "收到"},
    ]
    await provider.save_async("sess-new", messages)

    session = await store.load("sess-new")
    assert session is not None
    assert len(session.history) == 2
    assert session.history[0].role == "user"
    assert session.history[0].content == "测试消息"


@pytest.mark.asyncio
async def test_save_async_overwrites_history():
    """保存时应替换而非追加历史消息。"""
    store = InMemoryAgentSessionRepository()
    session = _make_session(messages=[
        {"role": "user", "content": "旧消息"},
    ])
    await store.save(session)
    provider = _make_provider(store)

    new_messages = [
        {"role": "user", "content": "新消息1"},
        {"role": "assistant", "content": "新消息2"},
    ]
    await provider.save_async("sess-001", new_messages)

    reloaded = await store.load("sess-001")
    assert reloaded is not None
    assert len(reloaded.history) == 2
    assert reloaded.history[0].content == "新消息1"


# ── PlatformMemoryProvider.clear_async ────────────────────


@pytest.mark.asyncio
async def test_clear_async_removes_session():
    """清空操作应删除整个会话。"""
    store = InMemoryAgentSessionRepository()
    session = _make_session()
    await store.save(session)
    provider = _make_provider(store)

    await provider.clear_async("sess-001")

    result = await store.load("sess-001")
    assert result is None


@pytest.mark.asyncio
async def test_clear_async_nonexistent_session():
    """清空不存在的会话不应抛出异常。"""
    store = InMemoryAgentSessionRepository()
    provider = _make_provider(store)

    # 不应抛出异常
    await provider.clear_async("sess-nonexist")


# ── HermesMemoryBridge.prepare ────────────────────────────


@pytest.mark.asyncio
async def test_bridge_prepare_empty():
    """桥接器 prepare 空会话应返回空列表。"""
    store = InMemoryAgentSessionRepository()
    provider = _make_provider(store)
    bridge = HermesMemoryBridge(provider=provider)

    result = await bridge.prepare("sess-001")

    assert result == []


@pytest.mark.asyncio
async def test_bridge_prepare_with_messages():
    """桥接器 prepare 应返回已有消息。"""
    store = InMemoryAgentSessionRepository()
    session = _make_session(messages=[
        {"role": "user", "content": "问题1"},
        {"role": "assistant", "content": "回答1"},
    ])
    await store.save(session)
    provider = _make_provider(store)
    bridge = HermesMemoryBridge(provider=provider)

    result = await bridge.prepare("sess-001")

    assert len(result) == 2


@pytest.mark.asyncio
async def test_bridge_prepare_truncates_max_history():
    """桥接器 prepare 应截断超出 max_history 的旧消息。"""
    store = InMemoryAgentSessionRepository()
    session = _make_session(messages=[
        {"role": "user", "content": f"消息{i}"}
        for i in range(10)
    ])
    await store.save(session)
    provider = _make_provider(store)
    bridge = HermesMemoryBridge(provider=provider, max_history=5)

    result = await bridge.prepare("sess-001")

    assert len(result) == 5
    # 应保留最新的 5 条
    assert result[0]["content"] == "消息5"
    assert result[-1]["content"] == "消息9"


# ── HermesMemoryBridge.commit ─────────────────────────────


@pytest.mark.asyncio
async def test_bridge_commit_saves_messages():
    """桥接器 commit 应保存消息到 store。"""
    store = InMemoryAgentSessionRepository()
    provider = _make_provider(store)
    bridge = HermesMemoryBridge(provider=provider)

    messages = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！"},
    ]
    await bridge.commit("sess-001", messages)

    session = await store.load("sess-001")
    assert session is not None
    assert len(session.history) == 2


@pytest.mark.asyncio
async def test_bridge_commit_truncates_max_history():
    """桥接器 commit 应截断超出 max_history 的消息。"""
    store = InMemoryAgentSessionRepository()
    provider = _make_provider(store)
    bridge = HermesMemoryBridge(provider=provider, max_history=3)

    messages = [
        {"role": "user", "content": f"消息{i}"}
        for i in range(6)
    ]
    await bridge.commit("sess-001", messages)

    session = await store.load("sess-001")
    assert session is not None
    assert len(session.history) == 3
    # 应保留最新的 3 条
    assert session.history[0].content == "消息3"


@pytest.mark.asyncio
async def test_bridge_prepare_then_commit_roundtrip():
    """prepare → commit 往返应保持消息一致性。"""
    store = InMemoryAgentSessionRepository()
    provider = _make_provider(store)
    bridge = HermesMemoryBridge(provider=provider)

    # 首次 commit
    messages_v1 = [
        {"role": "user", "content": "问题"},
        {"role": "assistant", "content": "回答"},
    ]
    await bridge.commit("sess-001", messages_v1)

    # prepare 加载
    loaded = await bridge.prepare("sess-001")
    assert len(loaded) == 2

    # 追加新消息后再 commit
    loaded.append({"role": "user", "content": "追问"})
    loaded.append({"role": "assistant", "content": "再答"})
    await bridge.commit("sess-001", loaded)

    # 再次加载验证
    final = await bridge.prepare("sess-001")
    assert len(final) == 4
    assert final[-1]["content"] == "再答"


@pytest.mark.asyncio
async def test_session_to_hermes_messages_preserves_roles():
    """_session_to_hermes_messages 应正确保留所有消息角色。"""
    session = AgentSession(session_id="s1", agent_id="a1")
    session.add_message("system", "系统提示")
    session.add_message("user", "用户输入")
    session.add_message("assistant", "助手回复")
    session.add_message("tool", "工具输出")

    result = PlatformMemoryProvider._session_to_hermes_messages(session)

    assert len(result) == 4
    assert [m["role"] for m in result] == ["system", "user", "assistant", "tool"]
