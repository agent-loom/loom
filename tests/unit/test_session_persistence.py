"""SqlAgentSessionRepository 持久化测试。

验证 SQL 实现满足 AgentSessionRepository 协议的所有方法，
包括保存/加载、更新消息、按条件查询、删除和不存在场景。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.domain.models import AgentSession
from agent_platform.persistence.sql import (
    SqlAgentSessionRepository,
)
from agent_platform.storage.base import Base

# -------------------------------------------------------------------
# 辅助函数
# -------------------------------------------------------------------


async def _sql_session_factory():
    """创建基于 SQLite 内存数据库的异步会话工厂。"""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(
        engine, expire_on_commit=False
    )


def _make_session(
    session_id: str = "sess-1",
    agent_id: str = "agent-a",
    tenant_id: str | None = None,
) -> AgentSession:
    """构造用于测试的 AgentSession 实例。"""
    return AgentSession(
        session_id=session_id,
        agent_id=agent_id,
        tenant_id=tenant_id,
    )


# -------------------------------------------------------------------
# Fixtures
# -------------------------------------------------------------------


@pytest_asyncio.fixture()
async def repo():
    """为每个测试创建独立的 SQL 仓库实例。"""
    sf = await _sql_session_factory()
    return SqlAgentSessionRepository(sf)


# -------------------------------------------------------------------
# 测试: 保存和加载 session
# -------------------------------------------------------------------


class TestSaveAndLoad:
    """保存和加载会话的基础测试。"""

    @pytest.mark.asyncio
    async def test_save_and_load(self, repo):
        """保存后可以按 session_id 正确加载。"""
        sess = _make_session()
        await repo.save(sess)
        loaded = await repo.load("sess-1")
        assert loaded is not None
        assert loaded.session_id == "sess-1"
        assert loaded.agent_id == "agent-a"

    @pytest.mark.asyncio
    async def test_save_with_history(self, repo):
        """保存包含消息历史的会话，加载后消息完整保留。"""
        sess = _make_session()
        sess.add_message("user", "你好")
        sess.add_message("assistant", "你好！有什么可以帮助你的？")
        await repo.save(sess)

        loaded = await repo.load("sess-1")
        assert loaded is not None
        assert len(loaded.history) == 2
        assert loaded.history[0].role == "user"
        assert loaded.history[0].content == "你好"
        assert loaded.history[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_save_with_state_snapshot(self, repo):
        """保存包含状态快照的会话，加载后状态完整保留。"""
        sess = _make_session()
        sess.state_snapshot = {"step": 3, "intent": "order"}
        await repo.save(sess)

        loaded = await repo.load("sess-1")
        assert loaded is not None
        assert loaded.state_snapshot["step"] == 3
        assert loaded.state_snapshot["intent"] == "order"

    @pytest.mark.asyncio
    async def test_save_with_tenant_id(self, repo):
        """保存带 tenant_id 的会话，加载后正确恢复。"""
        sess = _make_session(tenant_id="tenant-x")
        await repo.save(sess)

        loaded = await repo.load("sess-1")
        assert loaded is not None
        assert loaded.tenant_id == "tenant-x"


# -------------------------------------------------------------------
# 测试: 更新 session 消息
# -------------------------------------------------------------------


class TestUpdateMessages:
    """更新已有会话消息的测试。"""

    @pytest.mark.asyncio
    async def test_update_adds_new_messages(self, repo):
        """对已有会话追加新消息后重新保存，加载后消息数正确。"""
        sess = _make_session()
        sess.add_message("user", "第一条消息")
        await repo.save(sess)

        # 追加消息后再次保存（upsert）
        sess.add_message("assistant", "收到")
        sess.add_message("user", "第二条消息")
        await repo.save(sess)

        loaded = await repo.load("sess-1")
        assert loaded is not None
        assert len(loaded.history) == 3
        assert loaded.history[2].content == "第二条消息"

    @pytest.mark.asyncio
    async def test_update_preserves_session_id(self, repo):
        """更新后 session_id 不变。"""
        sess = _make_session()
        await repo.save(sess)
        sess.add_message("user", "新消息")
        await repo.save(sess)

        loaded = await repo.load("sess-1")
        assert loaded is not None
        assert loaded.session_id == "sess-1"

    @pytest.mark.asyncio
    async def test_update_state_snapshot(self, repo):
        """更新状态快照后重新保存，加载后状态为最新值。"""
        sess = _make_session()
        sess.state_snapshot = {"step": 1}
        await repo.save(sess)

        sess.state_snapshot = {"step": 2, "done": True}
        await repo.save(sess)

        loaded = await repo.load("sess-1")
        assert loaded is not None
        assert loaded.state_snapshot["step"] == 2
        assert loaded.state_snapshot["done"] is True


# -------------------------------------------------------------------
# 测试: 按 agent_id 列表查询
# -------------------------------------------------------------------


class TestListSessions:
    """列出会话并按条件过滤的测试。"""

    @pytest.mark.asyncio
    async def test_list_by_agent_id(self, repo):
        """按 agent_id 过滤，只返回匹配的会话。"""
        await repo.save(
            _make_session(session_id="s1", agent_id="a1")
        )
        await repo.save(
            _make_session(session_id="s2", agent_id="a2")
        )
        await repo.save(
            _make_session(session_id="s3", agent_id="a1")
        )

        results = await repo.list_sessions(agent_id="a1")
        assert len(results) == 2
        assert all(s.agent_id == "a1" for s in results)

    @pytest.mark.asyncio
    async def test_list_by_tenant_id(self, repo):
        """按 tenant_id 过滤，只返回匹配的会话。"""
        await repo.save(
            _make_session(
                session_id="s1",
                agent_id="a1",
                tenant_id="t1",
            )
        )
        await repo.save(
            _make_session(
                session_id="s2",
                agent_id="a1",
                tenant_id="t2",
            )
        )

        results = await repo.list_sessions(tenant_id="t1")
        assert len(results) == 1
        assert results[0].tenant_id == "t1"

    @pytest.mark.asyncio
    async def test_list_all_without_filter(self, repo):
        """不提供过滤条件时，返回所有会话。"""
        await repo.save(
            _make_session(session_id="s1", agent_id="a1")
        )
        await repo.save(
            _make_session(session_id="s2", agent_id="a2")
        )

        results = await repo.list_sessions()
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_list_empty_result(self, repo):
        """没有匹配的会话时返回空列表。"""
        results = await repo.list_sessions(
            agent_id="nonexistent"
        )
        assert results == []


# -------------------------------------------------------------------
# 测试: 删除 session
# -------------------------------------------------------------------


class TestDeleteSession:
    """删除会话的测试。"""

    @pytest.mark.asyncio
    async def test_delete_existing_session(self, repo):
        """删除已有会话后，load 返回 None。"""
        await repo.save(_make_session())
        await repo.delete("sess-1")
        assert await repo.load("sess-1") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(self, repo):
        """删除不存在的会话不应抛出异常。"""
        await repo.delete("nonexistent")  # 不应抛出异常

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_other(self, repo):
        """删除一个会话不影响其他会话。"""
        await repo.save(
            _make_session(session_id="s1", agent_id="a1")
        )
        await repo.save(
            _make_session(session_id="s2", agent_id="a1")
        )
        await repo.delete("s1")

        assert await repo.load("s1") is None
        assert await repo.load("s2") is not None


# -------------------------------------------------------------------
# 测试: 不存在的 session 返回 None
# -------------------------------------------------------------------


class TestLoadNonexistent:
    """加载不存在的会话的测试。"""

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self, repo):
        """加载不存在的 session_id 应返回 None。"""
        result = await repo.load("does-not-exist")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_after_delete_returns_none(self, repo):
        """删除后再加载同一 session_id 应返回 None。"""
        await repo.save(_make_session(session_id="s1"))
        await repo.delete("s1")
        result = await repo.load("s1")
        assert result is None
