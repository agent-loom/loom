"""S9 Phase 7: Background Review Fork 单元与集成测试。"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient

from agent_platform.evolution.models import (
    Candidate,
    CandidateStatus,
    CandidateType,
    PromotionTarget,
    RiskLevel,
)
from agent_platform.evolution.repository import (
    InMemoryCandidateRepository,
    InMemoryProposalRepository,
)
from agent_platform.runtime.model_gateway import ChatResult, ModelGateway, ModelMessage, ToolCall
from agent_platform.evolution.review_fork import (
    BackgroundReviewFork,
    InMemoryReviewForkAuditRepository,
    ReviewForkEvent,
    ReviewForkEventType,
    ReviewForkAudit,
)


@pytest.fixture
def candidate_repo() -> InMemoryCandidateRepository:
    return InMemoryCandidateRepository()


@pytest.fixture
def audit_repo() -> InMemoryReviewForkAuditRepository:
    return InMemoryReviewForkAuditRepository()


@pytest.fixture
def proposal_repo() -> InMemoryProposalRepository:
    return InMemoryProposalRepository()


@pytest.fixture
def mock_gateway() -> ModelGateway:
    gateway = MagicMock(spec=ModelGateway)
    gateway._default_provider = "stub"
    return gateway


@pytest.fixture
def review_fork(mock_gateway, candidate_repo, audit_repo, proposal_repo) -> BackgroundReviewFork:
    return BackgroundReviewFork(
        model_gateway=mock_gateway,
        candidate_repo=candidate_repo,
        audit_repo=audit_repo,
        proposal_repo=proposal_repo,
        window_size=10,
        rejection_threshold=0.5,
        min_candidates=4,  # 设置得小一些方便测试
        timeout_seconds=0.2,  # 设置超时秒数非常短，以便于测试超时控制
    )


def _make_candidate(
    agent_id: str,
    status: CandidateStatus,
) -> Candidate:
    return Candidate(
        candidate_type=CandidateType.MEMORY_CANDIDATE,
        agent_id=agent_id,
        tenant_id="default",
        generated_by="background_review_fork",
        source_event_ids=["evt_123"],
        evidence_ids=["evt_123"],
        payload={"summary": "test content", "memory_type": "pattern"},
        risk_level=RiskLevel.LOW,
        promotion_target=PromotionTarget.EVOLUTION_MEMORY,
        status=status,
    )


class TestBackgroundReviewForkCore:
    @pytest.mark.asyncio
    async def test_stub_execution_flow(self, review_fork, candidate_repo, audit_repo):
        """测试在没有真实 LLM provider 的情况下使用 Stub 执行评审并记录审计。"""
        # 确保 default_provider 是 stub，且没有 API keys
        review_fork._gateway._default_provider = "stub"

        event = ReviewForkEvent(
            event_type=ReviewForkEventType.AGENT_RUN_COMPLETED,
            agent_id="test_agent",
            evidence_summary="这是测试运行摘要",
            payload={"run_id": "run_123"},
        )

        # 触发异步评审 (trigger 内部使用 create_task，所以我们需要等待其完成或直接调用内部私有任务方法)
        await review_fork._run_fork_task(event)

        # 检查是否成功提报了 Candidate
        candidates = await candidate_repo.list_all(agent_id="test_agent")
        assert len(candidates) == 1
        assert candidates[0].candidate_type == CandidateType.MEMORY_CANDIDATE
        assert candidates[0].payload["summary"] == "从 Agent test_agent 的运行事件中提取并归约出的高频会话模式。"

        # 检查是否成功写入了 Audit 记录
        audits = await audit_repo.list_all(agent_id="test_agent")
        assert len(audits) == 1
        assert audits[0].status == "success"
        assert audits[0].output_type == "memory_candidate"
        assert audits[0].candidate_id == candidates[0].candidate_id

    @pytest.mark.asyncio
    async def test_real_llm_flow_success(self, review_fork, candidate_repo, audit_repo):
        """模拟真实的 LLM 评审流，验证正确的 Scoped Tools 被正常触发，且只生成合法的 Candidate。"""
        # 强制设置有真实 provider 且模拟环境变量
        review_fork._gateway._default_provider = "openai"

        # 模拟 ModelGateway.chat 的响应
        # 第一次返回 tool_calls
        tool_call1 = ToolCall(
            id="call_1",
            name="memory_write",
            arguments={
                "summary": "通过 LLM 沉淀的高价值经验模式",
                "memory_type": "pattern",
                "confidence": 0.9,
                "tags": ["llm_discovered"],
            },
        )
        first_result = ChatResult(
            content="分析完毕，我发现该会话存在可提取的规律模式，将调用 memory_write 工具。",
            tool_calls=[tool_call1],
        )

        # 第二次没有 tool_calls
        second_result = ChatResult(
            content="我已成功记录候选资产，自进化评审任务完成。",
            tool_calls=[],
        )

        # 模拟 Gateway 的 chat 返回
        chat_mock = AsyncMock()
        chat_mock.side_effect = [first_result, second_result]
        review_fork._gateway.chat = chat_mock

        event = ReviewForkEvent(
            event_type=ReviewForkEventType.AGENT_RUN_COMPLETED,
            agent_id="llm_agent",
            evidence_summary="LLM 异常 Trace 摘要",
            payload={"trace_id": "t_999"},
        )

        with patch("os.getenv", return_value="mock_api_key"):
            await review_fork._run_fork_task(event)

        # 验证是否提报了 Candidate
        candidates = await candidate_repo.list_all(agent_id="llm_agent")
        assert len(candidates) == 1
        assert candidates[0].candidate_type == CandidateType.MEMORY_CANDIDATE
        assert candidates[0].payload["summary"] == "通过 LLM 沉淀的高价值经验模式"

        # 验证是否写入了 Audit
        audits = await audit_repo.list_all(agent_id="llm_agent")
        assert len(audits) == 1
        assert audits[0].status == "success"
        assert audits[0].output_type == "memory_candidate"
        assert audits[0].candidate_id == candidates[0].candidate_id

    @pytest.mark.asyncio
    async def test_restricted_toolset_interception(self, review_fork, candidate_repo, audit_repo):
        """验证除了允许的 4 个 Scoped Tools 外，若 LLM 试图调用其他危险工具将被安全拦截或返回错误。"""
        review_fork._gateway._default_provider = "openai"

        # 模拟 LLM 试图调用 shell_exec
        tool_call_malicious = ToolCall(
            id="call_bad",
            name="shell_exec",
            arguments={"command": "rm -rf /"},
        )
        malicious_result = ChatResult(
            content="我试图修改系统配置以达成优化效果。",
            tool_calls=[tool_call_malicious],
        )

        second_result = ChatResult(
            content="无法继续危险的工具调用，优化任务中止。",
            tool_calls=[],
        )

        chat_mock = AsyncMock()
        chat_mock.side_effect = [malicious_result, second_result]
        review_fork._gateway.chat = chat_mock

        event = ReviewForkEvent(
            event_type=ReviewForkEventType.USER_FEEDBACK_RECEIVED,
            agent_id="strict_agent",
        )

        with patch("os.getenv", return_value="mock_api_key"):
            await review_fork._run_fork_task(event)

        # 确保没有 Candidate 被生成
        candidates = await candidate_repo.list_all(agent_id="strict_agent")
        assert len(candidates) == 0

        # 检查审计日志，说明它由于没产生候选资产且完成了对话，写了一条空 Candidate 的 success 审计（或者包含报错的 success 审计）
        audits = await audit_repo.list_all(agent_id="strict_agent")
        assert len(audits) == 1
        assert audits[0].status == "success"
        assert audits[0].candidate_id is None

        # 检查传入 chat 列表中的对话上下文，最后一次工具调用的输出应该是安全拦截提示
        call_history = chat_mock.call_args_list
        # 获取第二次调用的 messages 参数
        second_call_messages = call_history[1][1]["messages"]
        tool_resp_message = second_call_messages[-2]
        assert tool_resp_message.role == "tool"
        assert "错误: 拦截越权或未知的工具调用" in tool_resp_message.content

    @pytest.mark.asyncio
    async def test_timeout_isolation(self, review_fork, audit_repo):
        """测试 LLM 接口调用超时的情况。此时应优雅记录 failed 审计，且绝不阻塞/影响主请求。"""
        review_fork._gateway._default_provider = "openai"

        # 模拟一个长期挂起的 chat 接口
        async def hang_chat(*args, **kwargs):
            await asyncio.sleep(10.0)  # 大于 0.2s 的 timeout_seconds
            return ChatResult(content="不会执行到这", tool_calls=[])

        review_fork._gateway.chat = hang_chat

        event = ReviewForkEvent(
            event_type=ReviewForkEventType.AGENT_RUN_COMPLETED,
            agent_id="hang_agent",
        )

        # 触发评审，并验证它能在很短的 timeout_seconds (0.2s) 后安全返回且记录失败
        with patch("os.getenv", return_value="mock_api_key"):
            # 如果不加以隔离，该方法可能耗费 10s；由于设置了 asyncio.wait_for 和 0.2s 的超时限制，应该在 0.2s 左右跑完
            await review_fork._run_fork_task(event)

        audits = await audit_repo.list_all(agent_id="hang_agent")
        assert len(audits) == 1
        assert audits[0].status == "failed"
        assert "timed out after" in audits[0].error_message or "Execution timed out" in audits[0].error_message

    @pytest.mark.asyncio
    async def test_exception_isolation(self, review_fork, audit_repo):
        """测试 LLM 接口调用发生未捕获异常。此时应该捕捉异常并记录到 failed 审计。"""
        review_fork._gateway._default_provider = "openai"

        # 模拟报错
        chat_mock = AsyncMock(side_effect=RuntimeError("API 连接超时或密钥错误"))
        review_fork._gateway.chat = chat_mock

        event = ReviewForkEvent(
            event_type=ReviewForkEventType.AGENT_RUN_COMPLETED,
            agent_id="err_agent",
        )

        with patch("os.getenv", return_value="mock_api_key"):
            await review_fork._run_fork_task(event)

        audits = await audit_repo.list_all(agent_id="err_agent")
        assert len(audits) == 1
        assert audits[0].status == "failed"
        assert "API 连接超时或密钥错误" in audits[0].error_message


class TestQualityCircuitBreaker:
    @pytest.mark.asyncio
    async def test_circuit_breaker_auto_suspends_and_skips(self, review_fork, candidate_repo, audit_repo):
        """测试当候选结算拒绝率超标时自动触发熔断阻断。"""
        agent_id = "breaker_agent"

        # 注入已结算候选资产（总共 4 个结算资产，超过 min_candidates=4 的限制）：3个 REJECTED，1个 APPROVED
        # 拒绝率 3 / 4 = 75%，大于 50% 阈值
        await candidate_repo.create(_make_candidate(agent_id, CandidateStatus.REJECTED))
        await candidate_repo.create(_make_candidate(agent_id, CandidateStatus.REJECTED))
        await candidate_repo.create(_make_candidate(agent_id, CandidateStatus.REJECTED))
        await candidate_repo.create(_make_candidate(agent_id, CandidateStatus.APPROVED))

        # 确保 draft 状态的候选人不计入统计
        await candidate_repo.create(_make_candidate(agent_id, CandidateStatus.DRAFT))

        # 验证自动熔断处于激活状态
        assert await review_fork.is_suspended(agent_id) is True

        # 触发运行，验证它应该被熔断拦截直接跳过，记录 skipped_circuit_breaker
        event = ReviewForkEvent(
            event_type=ReviewForkEventType.AGENT_RUN_COMPLETED,
            agent_id=agent_id,
        )
        await review_fork._run_fork_task(event)

        audits = await audit_repo.list_all(agent_id=agent_id)
        assert len(audits) == 1
        assert audits[0].status == "skipped_circuit_breaker"
        assert "Circuit breaker suspended this agent" in audits[0].error_message

    @pytest.mark.asyncio
    async def test_circuit_breaker_resume_and_manual_suspend(self, review_fork, candidate_repo, audit_repo):
        """测试手动恢复与手动挂起功能。"""
        agent_id = "manual_agent"

        # 自动情况：样本数少于 min_candidates 故本应该不熔断
        assert await review_fork.is_suspended(agent_id) is False

        # 1. 手动挂起
        await review_fork.suspend_manually(agent_id)
        assert await review_fork.is_suspended(agent_id) is True

        # 触发一次，验证被熔断跳过
        event = ReviewForkEvent(
            event_type=ReviewForkEventType.AGENT_RUN_COMPLETED,
            agent_id=agent_id,
        )
        await review_fork._run_fork_task(event)
        audits = await audit_repo.list_all(agent_id=agent_id)
        assert len(audits) == 1
        assert audits[0].status == "skipped_circuit_breaker"

        # 2. 手动恢复
        await review_fork.resume(agent_id)
        assert await review_fork.is_suspended(agent_id) is False


class TestReviewForkRESTAPI:
    @pytest.mark.asyncio
    async def test_review_fork_endpoints(self):
        """测试 review fork 的 FastAPI 暴露端点。"""
        from agent_platform.api.app import app

        audit_repo = app.state.review_fork_audit_repo
        review_fork = app.state.review_fork

        # 写入一条审计数据以供测试 GET API
        audit1 = ReviewForkAudit(
            source_event_id="evt_101",
            source_event_type="agent_run_completed",
            agent_id="api_agent",
            status="success",
        )
        await audit_repo.create(audit1)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. GET /api/v1/evolution/review-fork/audits
            resp = await client.get("/api/v1/evolution/review-fork/audits?agent_id=api_agent")
            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 1
            assert data[0]["source_event_id"] == "evt_101"
            assert data[0]["agent_id"] == "api_agent"

            # 2. GET /api/v1/evolution/review-fork/status/{agent_id}
            resp = await client.get("/api/v1/evolution/review-fork/status/api_agent")
            assert resp.status_code == 200
            data = resp.json()
            assert data["agent_id"] == "api_agent"
            assert data["suspended"] is False
            assert data["rejection_rate"] == 0.0

            # 3. POST /api/v1/evolution/review-fork/suspend/{agent_id}
            resp = await client.post("/api/v1/evolution/review-fork/suspend/api_agent")
            assert resp.status_code == 200
            assert resp.json() == {"status": "suspended", "agent_id": "api_agent"}

            # 再次查状态，应该是挂起
            resp = await client.get("/api/v1/evolution/review-fork/status/api_agent")
            assert resp.json()["suspended"] is True

            # 4. POST /api/v1/evolution/review-fork/resume/{agent_id}
            resp = await client.post("/api/v1/evolution/review-fork/resume/api_agent")
            assert resp.status_code == 200
            assert resp.json() == {"status": "resumed", "agent_id": "api_agent"}

            # 再次查状态，已恢复
            resp = await client.get("/api/v1/evolution/review-fork/status/api_agent")
            assert resp.json()["suspended"] is False
