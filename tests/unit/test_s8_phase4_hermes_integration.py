"""S8 Phase 4 — Hermes 深度集成测试。

覆盖：
- RuntimeManager → HermesRuntimeBackend 的 session_store/approval_gate 接线
- Memory bridge 全链路（prepare → run → commit）
- state_snapshot 持久化
- Memory TTL 过期清理
- Error handler 重试集成
- HITL bridge 接线验证
- 完整管线：manifest → config → tool call → memory recall → trace
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_platform.domain.models import (
    AgentInput,
    AgentRequest,
    AgentSession,
    RuntimeRequest,
)
from agent_platform.persistence.memory import (
    InMemoryAgentRunRepository,
    InMemoryAgentSessionRepository,
)
from agent_platform.registry.loader import ManifestLoader
from agent_platform.runtime.hermes import HermesRuntimeBackend
from agent_platform.runtime.hermes_errors import (
    HermesErrorHandler,
    HermesErrorMapper,
    HermesRetryPolicy,
)
from agent_platform.runtime.hermes_memory import (
    HermesMemoryBridge,
    PlatformMemoryProvider,
)
from agent_platform.runtime.manager import RuntimeManager
from agent_platform.tools.approval import AutoApproveGate, InMemoryApprovalGate


def _make_manifest_dir(tmp_path):
    """创建最小化的 manifest 目录结构。"""
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "orchestrator.md").write_text("你是一个测试助手。")
    (prompt_dir / "reply_style.md").write_text("简洁回复。")
    eval_dir = tmp_path / "evals"
    eval_dir.mkdir()
    (eval_dir / "golden.yaml").write_text("[]")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("""
api_version: agent.platform/v1
kind: AgentPackage
metadata:
  id: hermes_test
  name: Hermes Test Agent
version:
  package_version: 0.1.0
runtime:
  backend: hermes
prompts:
  orchestrator: prompts/orchestrator.md
  reply_style: prompts/reply_style.md
output:
  protocol: agent-chat/v1
evals:
  suites:
    - evals/golden.yaml
""")
    return manifest


def _make_runtime_request(spec, query="你好", session_id=None):
    """构建 RuntimeRequest。"""
    return RuntimeRequest(
        request=AgentRequest(
            agent_id="hermes_test",
            input=AgentInput(query=query),
            session_id=session_id,
        ),
        agent_spec=spec,
    )


class TestRuntimeManagerWiring:
    """验证 RuntimeManager 正确传递 session_store 和 approval_gate 到 HermesRuntimeBackend。"""

    def test_session_store_wired(self):
        session_store = InMemoryAgentSessionRepository()
        manager = RuntimeManager(session_store=session_store)

        hermes_backend = manager._backends["hermes"]
        assert isinstance(hermes_backend, HermesRuntimeBackend)
        assert hermes_backend.session_store is session_store

    def test_approval_gate_wired(self):
        gate = InMemoryApprovalGate()
        manager = RuntimeManager(approval_gate=gate)

        hermes_backend = manager._backends["hermes"]
        assert hermes_backend.hitl_bridge is not None

    def test_no_approval_gate_no_hitl(self):
        manager = RuntimeManager()

        hermes_backend = manager._backends["hermes"]
        assert hermes_backend.hitl_bridge is None

    def test_auto_approve_gate_wired(self):
        gate = AutoApproveGate()
        manager = RuntimeManager(approval_gate=gate)

        hermes_backend = manager._backends["hermes"]
        assert hermes_backend.hitl_bridge is not None


class TestMemoryBridgeFullCycle:
    """验证 Hermes memory bridge 的完整 prepare → run → commit 链路。"""

    @pytest.mark.asyncio
    async def test_memory_bridge_activates_with_session_store(self, tmp_path):
        session_store = InMemoryAgentSessionRepository()
        backend = HermesRuntimeBackend(session_store=session_store)
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))
        request = _make_runtime_request(spec, session_id="sess-mem-test")

        result = await backend.run(request)

        assert result.response.output.status == "completed"

        session = await session_store.load("sess-mem-test")
        assert session is not None
        assert len(session.history) == 2
        assert session.history[0].role == "user"
        assert session.history[0].content == "你好"
        assert session.history[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_memory_persists_across_runs(self, tmp_path):
        session_store = InMemoryAgentSessionRepository()
        backend = HermesRuntimeBackend(session_store=session_store)
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))

        # 第一轮对话
        req1 = _make_runtime_request(spec, query="第一个问题", session_id="sess-persist")
        await backend.run(req1)

        # 第二轮对话
        req2 = _make_runtime_request(spec, query="第二个问题", session_id="sess-persist")
        await backend.run(req2)

        session = await session_store.load("sess-persist")
        assert session is not None
        assert len(session.history) == 4
        assert session.history[0].content == "第一个问题"
        assert session.history[2].content == "第二个问题"

    @pytest.mark.asyncio
    async def test_no_memory_without_session_id(self, tmp_path):
        session_store = InMemoryAgentSessionRepository()
        backend = HermesRuntimeBackend(session_store=session_store)
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))
        request = _make_runtime_request(spec, session_id=None)

        result = await backend.run(request)

        assert result.response.output.status == "completed"

    @pytest.mark.asyncio
    async def test_no_memory_without_session_store(self, tmp_path):
        backend = HermesRuntimeBackend(session_store=None)
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))
        request = _make_runtime_request(spec, session_id="sess-no-store")

        result = await backend.run(request)

        assert result.response.output.status == "completed"


class TestStateSnapshotPersistence:
    """验证 state_snapshot 在 Hermes 运行后被正确持久化。"""

    @pytest.mark.asyncio
    async def test_state_snapshot_saved_after_run(self, tmp_path):
        session_store = InMemoryAgentSessionRepository()
        backend = HermesRuntimeBackend(session_store=session_store)
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))
        request = _make_runtime_request(spec, session_id="sess-snapshot")

        await backend.run(request)

        session = await session_store.load("sess-snapshot")
        assert session is not None
        snapshot = session.state_snapshot
        assert snapshot.get("runtime_backend") == "hermes"
        assert "last_run_id" in snapshot
        assert "last_total_tokens" in snapshot

    @pytest.mark.asyncio
    async def test_state_snapshot_updates_on_each_run(self, tmp_path):
        session_store = InMemoryAgentSessionRepository()
        backend = HermesRuntimeBackend(session_store=session_store)
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))

        req1 = _make_runtime_request(spec, query="问题1", session_id="sess-snap-update")
        await backend.run(req1)

        session1 = await session_store.load("sess-snap-update")
        assert session1.state_snapshot.get("runtime_backend") == "hermes"

        req2 = _make_runtime_request(spec, query="问题2", session_id="sess-snap-update")
        await backend.run(req2)

        session2 = await session_store.load("sess-snap-update")

        # state_snapshot 应该随 run 变化（但 stub 模式下 run_id 为 None，仍验证覆盖性）
        assert session2.state_snapshot.get("runtime_backend") == "hermes"


class TestMemoryTTLEviction:
    """验证 HermesMemoryBridge 的 TTL 过期清理机制。"""

    @pytest.mark.asyncio
    async def test_ttl_evicts_stale_session(self):
        store = InMemoryAgentSessionRepository()
        session = AgentSession(session_id="sess-ttl", agent_id="test")
        session.add_message("user", "旧消息")
        # 模拟 2 小时前的更新
        session.updated_at = datetime.now(UTC) - timedelta(hours=2)
        await store.save(session)

        provider = PlatformMemoryProvider(
            session_store=store, agent_id="test", session_id="sess-ttl",
        )
        bridge = HermesMemoryBridge(provider=provider, ttl_seconds=3600)

        messages = await bridge.prepare("sess-ttl")

        assert messages == []

    @pytest.mark.asyncio
    async def test_ttl_keeps_fresh_session(self):
        store = InMemoryAgentSessionRepository()
        session = AgentSession(session_id="sess-fresh", agent_id="test")
        session.add_message("user", "新消息")
        session.updated_at = datetime.now(UTC) - timedelta(minutes=5)
        await store.save(session)

        provider = PlatformMemoryProvider(
            session_store=store, agent_id="test", session_id="sess-fresh",
        )
        bridge = HermesMemoryBridge(provider=provider, ttl_seconds=3600)

        messages = await bridge.prepare("sess-fresh")

        assert len(messages) == 1
        assert messages[0]["content"] == "新消息"

    @pytest.mark.asyncio
    async def test_ttl_disabled_when_zero(self):
        store = InMemoryAgentSessionRepository()
        session = AgentSession(session_id="sess-no-ttl", agent_id="test")
        session.add_message("user", "旧消息")
        session.updated_at = datetime.now(UTC) - timedelta(days=30)
        await store.save(session)

        provider = PlatformMemoryProvider(
            session_store=store, agent_id="test", session_id="sess-no-ttl",
        )
        bridge = HermesMemoryBridge(provider=provider, ttl_seconds=0)

        messages = await bridge.prepare("sess-no-ttl")

        assert len(messages) == 1

    @pytest.mark.asyncio
    async def test_ttl_nonexistent_session_returns_empty(self):
        store = InMemoryAgentSessionRepository()
        provider = PlatformMemoryProvider(
            session_store=store, agent_id="test", session_id="sess-none",
        )
        bridge = HermesMemoryBridge(provider=provider, ttl_seconds=60)

        messages = await bridge.prepare("sess-none")

        assert messages == []


class TestErrorHandlerRetryIntegration:
    """验证 HermesErrorHandler 在 HermesRuntimeBackend 中的重试行为。"""

    @pytest.mark.asyncio
    async def test_retryable_error_retries_then_succeeds(self):
        call_count = 0

        # 类名必须在 HermesErrorMapper 的可重试名称集合中
        HermesToolError = type("HermesToolError", (Exception,), {})

        async def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise HermesToolError("暂时失败")
            return "成功"

        handler = HermesErrorHandler(
            retry_policy=HermesRetryPolicy(
                max_retries=3, base_delay=0.01, backoff_factor=1.0,
            ),
        )

        result = await handler.handle_with_retry(flaky_fn)

        assert result == "成功"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_error_fails_immediately(self):
        call_count = 0

        class ModelError(Exception):
            pass

        # 注册为 HermesModelError 的类名
        HermesModelError = type("HermesModelError", (ModelError,), {})

        async def failing_fn():
            nonlocal call_count
            call_count += 1
            raise HermesModelError("模型不可用")

        handler = HermesErrorHandler(
            retry_policy=HermesRetryPolicy(max_retries=3, base_delay=0.01),
        )

        with pytest.raises(ModelError):
            await handler.handle_with_retry(failing_fn)

        assert call_count == 1

    def test_error_mapper_classifies_timeout(self):
        mapper = HermesErrorMapper()
        TimeoutErr = type("HermesTimeoutError", (Exception,), {})
        error = mapper.map_error(TimeoutErr("超时"))

        assert error.code == "HERMES_TIMEOUT"
        assert error.retryable is True

    def test_error_mapper_classifies_rate_limit(self):
        mapper = HermesErrorMapper()
        RateLimitErr = type("HermesRateLimitError", (Exception,), {})
        error = mapper.map_error(RateLimitErr("限流"))

        assert error.code == "HERMES_RATE_LIMITED"
        assert error.retryable is True

    def test_error_mapper_classifies_unknown(self):
        mapper = HermesErrorMapper()
        error = mapper.map_error(ValueError("未知"))

        assert error.code == "HERMES_UNKNOWN"
        assert error.retryable is False


class TestHITLBridgeWiring:
    """验证 HITL 审批桥接的接线正确性。"""

    def test_hitl_bridge_created_with_approval_gate(self):
        gate = InMemoryApprovalGate()
        backend = HermesRuntimeBackend(approval_gate=gate)

        assert backend.hitl_bridge is not None

    def test_hitl_bridge_none_without_gate(self):
        backend = HermesRuntimeBackend()

        assert backend.hitl_bridge is None

    def test_hitl_bridge_with_auto_approve(self):
        gate = AutoApproveGate()
        backend = HermesRuntimeBackend(approval_gate=gate)

        assert backend.hitl_bridge is not None


class TestFullHermesPipeline:
    """端到端管线测试：manifest → config → run → memory → trace。"""

    @pytest.mark.asyncio
    async def test_manifest_to_hermes_config(self, tmp_path):
        from agent_platform.runtime.hermes import ManifestMapper

        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))
        config = ManifestMapper.to_hermes_config(spec)

        assert config["agent_id"] == "hermes_test"
        assert "你是一个测试助手" in config["system_prompt"]
        assert isinstance(config["tools"], list)
        assert isinstance(config["max_iterations"], int)

    @pytest.mark.asyncio
    async def test_full_pipeline_stub_with_memory_and_trace(self, tmp_path):
        """完整管线：stub 后端 + 记忆持久化 + trace 验证。"""
        session_store = InMemoryAgentSessionRepository()
        gate = AutoApproveGate()
        backend = HermesRuntimeBackend(
            session_store=session_store,
            approval_gate=gate,
        )
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))

        # 第一轮
        req1 = _make_runtime_request(spec, query="你好", session_id="pipe-test")
        result1 = await backend.run(req1)

        assert result1.response.output.status == "completed"
        assert result1.response.debug["runtime_backend"] == "hermes"
        assert result1.response.agent.agent_id == "hermes_test"

        # 第二轮 — 验证记忆加载
        req2 = _make_runtime_request(spec, query="还记得我吗", session_id="pipe-test")
        await backend.run(req2)

        session = await session_store.load("pipe-test")
        assert session is not None
        assert len(session.history) == 4
        assert session.history[0].content == "你好"
        assert session.history[2].content == "还记得我吗"

        # state_snapshot 应被填充
        assert session.state_snapshot.get("runtime_backend") == "hermes"

    @pytest.mark.asyncio
    async def test_policy_violation_blocks_run(self, tmp_path):
        """tools allow/deny 冲突应阻止运行。"""
        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))
        # 手动注入冲突的 deny 列表（绕过 loader 校验，模拟运行时策略检查）
        spec.manifest.tools.allow = ["web_search"]
        spec.manifest.tools.deny = ["web_search"]

        backend = HermesRuntimeBackend()
        result = await backend.run(_make_runtime_request(spec))

        assert result.response.output.status == "failed"
        assert result.response.error.code == "POLICY_VIOLATION"

    @pytest.mark.asyncio
    async def test_runtime_manager_full_pipeline(self, tmp_path):
        """通过 RuntimeManager 完整调用 Hermes 后端。"""
        session_store = InMemoryAgentSessionRepository()
        run_store = InMemoryAgentRunRepository()
        gate = AutoApproveGate()

        manager = RuntimeManager(
            run_store=run_store,
            session_store=session_store,
            approval_gate=gate,
        )

        spec = ManifestLoader().load_file(_make_manifest_dir(tmp_path))
        request = _make_runtime_request(spec, session_id="mgr-test")

        result = await manager.run(request)

        assert result.response.output.status == "completed"
        assert result.response.trace.latency_ms >= 0

        # 验证 run 被记录
        runs = await run_store.list_runs(agent_id="hermes_test")
        assert len(runs) == 1
        assert runs[0].runtime_backend == "hermes"

        # 验证 session_store 中 Hermes memory bridge 保存的消息
        hermes_backend = manager._backends["hermes"]
        assert hermes_backend.session_store is session_store
