#!/usr/bin/env python3
"""DevFlow 异步任务管道与可靠性回滚 E2E/集成测试套件 (run_devflow_reliability_e2e.py)。

包含对以下 5 大核心场景的极致安全与可靠性验证：
1. E2E_DF_01: Plane 失败状态回滚 (Plane API Failure Reverts Transitions)
2. E2E_DF_02: 关键节点 Checkpoint 完整生成 (Full happy path checkpoint audit)
3. E2E_DF_03: Command/Path Guards 安全防御拦截 (Command & Path Guard blocks & moves to Developing)
4. E2E_DF_04: MR 409 冲突自动重用 (GitLab MR creation 409 conflict MR reuse)
5. E2E_DF_05: 并发去重与 UTC 时间审计 (Seen keys webhook deduplication & UTC timezone transition audit)
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import shutil
import sys
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

# 初始化环境路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from agent_platform.devflow.orchestrator import DevFlowOrchestrator, DevFlowResult
from agent_platform.devflow.runner.adapters.mock import MockRunnerAdapter
from agent_platform.devflow.runner.checkpoint import CheckpointManager
from agent_platform.devflow.runner.command_guard import CommandGuard
from agent_platform.devflow.runner.execution_log import InMemoryExecutionLogRepository
from agent_platform.devflow.runner.models import JobState, ResultStatus, RunnerInvocation, RunnerResult, ValidationResult
from agent_platform.devflow.runner.runner import CodingAgentRunner
from agent_platform.devflow.runner.workspace import WorkspaceManager
from agent_platform.devflow.runner.protocol import RunnerAdapter, RunnerAdapterResult
from agent_platform.devflow.state_machine import DevFlowState, DevFlowStateMachine, InvalidTransitionError
from agent_platform.devflow.state_sync import DevFlowStateSync
from agent_platform.devflow.task_pack import (
    DevelopmentTask,
    MergeRequestSpec,
    RepositoryTarget,
    RequirementSpec,
    TaskMetadata,
)
from agent_platform.integrations.errors import ScmError
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter
from agent_platform.integrations.scm.protocol import MergeRequestResult
from agent_platform.persistence.repositories import WebhookDeliveryRepository

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("devflow_reliability_e2e")

# 彩色控制台
class TermColor:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_banner(msg: str) -> None:
    print(f"\n{TermColor.HEADER}{TermColor.BOLD}=== {msg} ==={TermColor.ENDC}")


def print_ok(msg: str) -> None:
    print(f"  {TermColor.OKGREEN}[PASS]{TermColor.ENDC} {msg}")


def print_fail(msg: str) -> None:
    print(f"  {TermColor.FAIL}[FAIL]{TermColor.ENDC} {msg}")


# ---------------------------------------------------------------------------
# 高保真本地 Git 沙箱 WorkspaceManager 仿真件
# ---------------------------------------------------------------------------
class ReliabilityE2EWorkspaceManager(WorkspaceManager):
    """高保真本地 Git 仓库沙箱。
    在系统临时目录中执行真实的 git init/commit，但拦截并模拟 git push 操作，
    确保 CheckpointManager 可以分析真实的 git 历史和变动文件，而不对远程仓库产生物理交互。
    """

    def __init__(self, base_dir: Path) -> None:
        super().__init__(base_dir=base_dir, cleanup_on_success=True, cleanup_on_failure=False)

    async def create(self, *, branch: str, repo_url: str) -> Path:
        workspace_id = f"ws-{uuid.uuid4().hex[:12]}"
        workspace_dir = self.base_dir / workspace_id
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # 本地初始化真实 Git 仓库，确保 git status / rev-parse 真正可用
        await self._exec_git(workspace_dir, ["git", "init"])
        await self._exec_git(workspace_dir, ["git", "config", "user.name", "E2E Tester"])
        await self._exec_git(workspace_dir, ["git", "config", "user.email", "e2e@test.local"])

        # 创建基本骨架文件并做首次 commit，以此作为默认分支 main/master 的基准
        (workspace_dir / "app.py").write_text("def run():\n    print('Hello World')\n")
        (workspace_dir / "denied_dir").mkdir(exist_ok=True)
        (workspace_dir / "denied_dir" / "secret.key").write_text("API_KEY=12345\n")

        await self._exec_git(workspace_dir, ["git", "add", "."])
        await self._exec_git(workspace_dir, ["git", "commit", "-m", "initial commit"])

        # 签出开发分支
        await self._exec_git(workspace_dir, ["git", "checkout", "-b", branch])
        return workspace_dir

    async def _exec_git(self, cwd: Path, cmd: list[str]) -> None:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(cwd)
        )
        await proc.communicate()

    async def _run_git(self, cwd: Path, cmd: list[str]) -> None:
        # 拦截推送操作，模拟成功
        if "push" in cmd:
            logger.info("[Mock SCM Git Push] 拦截 git push 并成功模拟推送: %s", " ".join(cmd))
            return
        await super()._run_git(cwd, cmd)


# ---------------------------------------------------------------------------
# 内存 WebhookDeliveryRepository 实现
# ---------------------------------------------------------------------------
class InMemoryWebhookDeliveryRepository(WebhookDeliveryRepository):
    def __init__(self) -> None:
        self.db: dict[str, dict[str, Any]] = {}

    async def exists(self, delivery_id: str) -> bool:
        return delivery_id in self.db

    async def record(
        self,
        delivery_id: str,
        source: str,
        event_type: str,
        status: str,
        payload: dict[str, Any],
    ) -> None:
        self.db[delivery_id] = {
            "delivery_id": delivery_id,
            "source": source,
            "event_type": event_type,
            "status": status,
            "payload": payload,
        }

    async def update_status(self, delivery_id: str, status: str) -> None:
        if delivery_id in self.db:
            self.db[delivery_id]["status"] = status


# ---------------------------------------------------------------------------
# 仿真环境适配器与网关
# ---------------------------------------------------------------------------
def _make_mock_task(*, task_id: str, write_allowed: list[str] | None = None, write_denied: list[str] | None = None, required_outputs: list[str] | None = None) -> DevelopmentTask:
    return DevelopmentTask(
        metadata=TaskMetadata(
            task_id=task_id, title="Mock DevFlow Task", type="platform:change", source={}
        ),
        repository=RepositoryTarget(
            project_id="proj-e2e",
            work_branch=f"feat/{task_id}",
            base_branch="main",
        ),
        requirement=RequirementSpec(background="bg"),
        scope={
            "write_allowed": write_allowed or ["*"],
            "write_denied": write_denied or [],
        },
        implementation={
            "required_outputs": required_outputs or [],
            "constraints": [],
        },
        validation={
            "commands": [],
            "required_reports": [],
        }
    )


# ---------------------------------------------------------------------------
# 测试套件执行器
# ---------------------------------------------------------------------------
class DevFlowReliabilityE2ETestSuite:
    def __init__(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="devflow-reliability-e2e-"))
        self.ws_manager = ReliabilityE2EWorkspaceManager(base_dir=self.temp_dir)

    def cleanup(self) -> None:
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def run_all(self) -> bool:
        success = True
        print_banner("DevFlow 异步任务管道与可靠性回滚 E2E 测试套件启动")

        # 1. E2E_DF_01: Plane 失败状态回滚
        try:
            await self.test_e2e_df_01_plane_failure_rollback()
            print_ok("E2E_DF_01: Plane 失败状态自动回滚测试通过")
        except Exception as e:
            print_fail(f"E2E_DF_01: 失败: {str(e)}")
            success = False

        # 2. E2E_DF_02: 关键节点 Checkpoint 完整生成
        try:
            await self.test_e2e_df_02_checkpoints_generation()
            print_ok("E2E_DF_02: 关键节点 Checkpoint 完整生成测试通过")
        except Exception as e:
            print_fail(f"E2E_DF_02: 失败: {str(e)}")
            success = False

        # 3. E2E_DF_03: Command/Path Guards 安全防御拦截
        try:
            await self.test_e2e_df_03_guards_interception()
            print_ok("E2E_DF_03: Command/Path Guards 安全防御拦截测试通过")
        except Exception as e:
            print_fail(f"E2E_DF_03: 失败: {str(e)}")
            success = False

        # 4. E2E_DF_04: MR 409 冲突自动重用
        try:
            await self.test_e2e_df_04_gitlab_mr_409_reuse()
            print_ok("E2E_DF_04: MR 409 冲突自动重用测试通过")
        except Exception as e:
            print_fail(f"E2E_DF_04: 失败: {str(e)}")
            success = False

        # 5. E2E_DF_05: 并发去重与 UTC 时间审计
        try:
            await self.test_e2e_df_05_concurrency_and_timezone()
            print_ok("E2E_DF_05: 并发去重与 UTC 时间审计测试通过")
        except Exception as e:
            print_fail(f"E2E_DF_05: 失败: {str(e)}")
            success = False

        print_banner("测试套件运行完毕")
        return success

    # ---------------------------------------------------------------------------
    # E2E_DF_01: Plane 失败状态自动回滚
    # ---------------------------------------------------------------------------
    async def test_e2e_df_01_plane_failure_rollback(self) -> None:
        # 1. 构造 Plane 模拟适配器，让其在更新状态时强制抛出连接异常 (500)
        mock_plane = AsyncMock()
        mock_plane.update_work_item_state = AsyncMock(side_effect=httpx.HTTPStatusError("500 Internal Server Error", request=MagicMock(), response=MagicMock()))

        # 2. 初始化 DevFlowStateSync，并配置被测工作项 wi-df-01 初始状态为 INTAKE
        sync = DevFlowStateSync(plane_adapter=mock_plane)
        sm = sync.get_or_create("wi-df-01", initial_state=DevFlowState.INTAKE)
        assert sm.current_state == DevFlowState.INTAKE
        assert len(sm.history) == 0

        # 3. 试图执行状态转换 INTAKE -> READY_FOR_AI_DEV
        try:
            await sync.sync_to_plane("wi-df-01", "proj-e2e", DevFlowState.READY_FOR_AI_DEV)
            raise AssertionError("应在 Plane API 报错时向上层抛出异常")
        except httpx.HTTPStatusError:
            pass

        # 4. 核心断言：本地状态机必须安全回滚回 INTAKE，且脏历史记录被完全擦除！
        assert sm.current_state == DevFlowState.INTAKE, f"状态应已回滚回 intake，实际状态: {sm.current_state}"
        assert len(sm.history) == 0, f"转换历史应完全被清理，实际包含: {sm.history}"

    # ---------------------------------------------------------------------------
    # E2E_DF_02: 关键节点 Checkpoint 完整生成
    # ---------------------------------------------------------------------------
    async def test_e2e_df_02_checkpoints_generation(self) -> None:
        # 1. 配置 Happy Path 任务包，要求生成 "src/main.py" 并做修改
        task = _make_mock_task(task_id="task-df-02", required_outputs=["src/main.py"])

        # 2. 构造高仿真 Runner：
        # - mock_adapter 会成功修改 src/main.py
        # - ws_manager 是我们定制的 ReliabilityE2EWorkspaceManager
        # - gitlab 模拟提交 MR
        mock_adapter = MockRunnerAdapter(should_fail=False)
        mock_gitlab = AsyncMock()
        mock_gitlab.create_merge_request = AsyncMock(return_value=MergeRequestResult(mr_id=42, url="https://mock/mr/42", source_branch="feat/task-df-02", target_branch="main"))
        mock_gitlab.comment_merge_request = AsyncMock()

        runner = CodingAgentRunner(
            adapter=mock_adapter,
            workspace_manager=self.ws_manager,
            gitlab=mock_gitlab,
            gitlab_project_id="proj-gl",
            repo_url="https://mock.repo/test.git",
        )

        # 3. 执行任务
        job = await runner.run(task)

        # 4. 验证任务是否成功，且 commit sha 已经产生
        assert job.state == JobState.SUCCEEDED, f"任务应成功，实际状态: {job.state}"
        assert job.result is not None
        assert job.result.status == ResultStatus.SUCCESS
        assert job.result.commit_sha is not None, "应成功生成 commit"

        # 5. 核心断言：验证在 workspace 运行生命周期内，全部 4 个关键 Checkpoint 均被创建，并且按时间线正序追加！
        assert len(job.checkpoints) == 4, f"应包含 4 个 checkpoints，实际: {job.checkpoints}"
        types = [cp["type"] for cp in job.checkpoints]
        assert types == ["before_runner", "before_validation", "before_commit", "after_commit"], f"Checkpoint 阶段顺序异常: {types}"

        # 检查首个 Checkpoint，应代表初始干净状态
        assert job.checkpoints[0]["changed_files_count"] == 0
        # 检查 validated 阶段 Checkpoint，应反映新增/修改的文件数量
        assert job.checkpoints[1]["changed_files_count"] >= 1

    # ---------------------------------------------------------------------------
    # E2E_DF_03: Command/Path Guards 安全防御拦截
    # ---------------------------------------------------------------------------
    async def test_e2e_df_03_guards_interception(self) -> None:
        # --- 场景 A: CommandGuard 拦截 ---
        # 1. 构造高危命令任务包（包含 rm -rf / 危险命令）
        task_danger = _make_mock_task(task_id="task-df-03-danger")
        task_danger.validation["commands"] = ["rm -rf /"]

        mock_adapter = MockRunnerAdapter(should_fail=False)
        mock_gitlab = AsyncMock()
        mock_plane = AsyncMock()

        runner = CodingAgentRunner(
            adapter=mock_adapter,
            workspace_manager=self.ws_manager,
            gitlab=mock_gitlab,
            plane=mock_plane,
            gitlab_project_id="proj-gl",
            repo_url="https://mock.repo/test.git",
            ai_developing_state_id="state-ai-dev", # 触发失败回退状态
        )

        job_danger = await runner.run(task_danger, plane_project_id="proj-e2e", plane_work_item_id="task-df-03-danger")

        # 2. 断言验证阶段被 CommandGuard 拒绝，导致 Job 失败
        assert job_danger.state == JobState.FAILED, "高危命令应被拦截并报告 FAILED"
        assert job_danger.result.status == ResultStatus.VALIDATION_FAILED
        assert job_danger.result.commit_sha is None, "高危命令被拒时不应进行提交与推送！"

        # 3. 验证 Plane 状态由于任务失败被自动回滚至 AI Developing
        mock_plane.update_work_item_state.assert_called_with("proj-e2e", "task-df-03-danger", "state-ai-dev")

        # --- 场景 B: PathGuard 拦截 ---
        # 1. 构造限制写权限的 Task，仅允许在 "src/" 写入，但 Adapter 试图修改受保护 of "denied_dir/secret.key"
        task_restricted = _make_mock_task(task_id="task-df-03-path")
        task_restricted.scope["write_allowed"] = ["src/**"]
        task_restricted.scope["write_denied"] = ["denied_dir/**"]

        # 覆写 adapter.execute 以修改 denied_dir/secret.key 逃逸
        class EscapeAdapter(MockRunnerAdapter):
            async def execute(self, *, workspace_dir: str, task: DevelopmentTask, timeout_seconds: int = 600) -> RunnerAdapterResult:
                ws = Path(workspace_dir)
                key_file = ws / "denied_dir" / "secret.key"
                key_file.write_text("EXPLOIT=hack\n")
                return RunnerAdapterResult(exit_code=0, changed_files=["denied_dir/secret.key"])

        runner_restricted = CodingAgentRunner(
            adapter=EscapeAdapter(),
            workspace_manager=self.ws_manager,
            gitlab=mock_gitlab,
            plane=mock_plane,
            gitlab_project_id="proj-gl",
            repo_url="https://mock.repo/test.git",
        )

        job_restricted = await runner_restricted.run(task_restricted, plane_project_id="proj-e2e", plane_work_item_id="task-df-03-path")

        # 2. 核心断言：检测到越权文件写入，PathGuard 正确发出告警并终止工作流
        assert job_restricted.state == JobState.FAILED, "路径违规应被拦截并报告 FAILED"
        assert job_restricted.result.status == ResultStatus.PATH_VIOLATION
        assert "Path guard violation" in job_restricted.result.error_message

    # ---------------------------------------------------------------------------
    # E2E_DF_04: MR 409 冲突自动重用
    # ---------------------------------------------------------------------------
    async def test_e2e_df_04_gitlab_mr_409_reuse(self) -> None:
        # 1. 构造一个正常的任务包
        task = _make_mock_task(task_id="task-df-04", required_outputs=["src/main.py"])

        # 2. 构造 ScmAdapter 模拟：
        # - 在 create_merge_request 时强制抛出 status_code=409 冲突异常（MR已存在）
        # - 在 find_open_merge_request 时返回一个合法的已有 MR_IID (88)
        mock_gitlab = AsyncMock()
        mock_gitlab.create_merge_request = AsyncMock(side_effect=ScmError("Merge request already exists", status_code=409))
        mock_gitlab.find_open_merge_request = AsyncMock(return_value=MergeRequestResult(mr_id=88, url="https://gitlab.test/mr/88", source_branch="feat/task-df-04", target_branch="main"))
        mock_gitlab.comment_merge_request = AsyncMock()

        runner = CodingAgentRunner(
            adapter=MockRunnerAdapter(should_fail=False),
            workspace_manager=self.ws_manager,
            gitlab=mock_gitlab,
            gitlab_project_id="proj-gl",
            repo_url="https://mock.repo/test.git",
        )

        # 3. 执行任务
        job = await runner.run(task)

        # 4. 核心断言：即使发生 409 冲突，任务也绝不能崩溃，应优雅重用已有 MR
        assert job.state == JobState.SUCCEEDED, f"任务应成功运行，实际: {job.state}"
        assert job.mr_iid == 88, f"已有的 MR IID 88 应被无缝重用，实际: {job.mr_iid}"
        assert job.mr_url == "https://gitlab.test/mr/88"
        mock_gitlab.find_open_merge_request.assert_awaited_once_with("proj-gl", "feat/task-df-04")
        mock_gitlab.comment_merge_request.assert_awaited_once()

    # ---------------------------------------------------------------------------
    # E2E_DF_05: 并发去重与 UTC 时间审计
    # ---------------------------------------------------------------------------
    async def test_e2e_df_05_concurrency_and_timezone(self) -> None:
        # --- 场景 A: 并发去重拦截 ---
        # 1. 配置编排器与 Seen Keys Webhook 仓库
        mock_plane = AsyncMock()
        mock_plane.get_work_item = AsyncMock(return_value={
            "id": "wi-df-05",
            "project": "proj-e2e",
            "name": "并发测试工作项",
            "description_stripped": "desc",
            "properties": {"agent_id": "test-agent", "task_type": "platform:change"},
        })
        mock_plane.update_work_item_state = AsyncMock()
        mock_plane.add_comment = AsyncMock()
        mock_plane.update_custom_properties = AsyncMock()

        mock_gitlab = AsyncMock()
        mock_gitlab.create_branch = AsyncMock()

        webhook_repo = InMemoryWebhookDeliveryRepository()

        # 模拟并发队列触发：同一个 Delivery ID
        payload_1 = {
            "delivery_id": "del-00001",
            "data": {
                "id": "wi-df-05",
                "project": "proj-e2e",
                "name": "并发测试工作项",
                "state_detail": {"name": "Ready for AI Dev"},
            }
        }

        orch = DevFlowOrchestrator(
            plane=mock_plane,
            gitlab=mock_gitlab,
            gitlab_project_id="proj-gl",
            webhook_repo=webhook_repo,
            coding_runner=None, # 本测试中仅编排，不启动 runner
        )

        # 2. 并发执行两次 Webhook 处理
        r1, r2 = await asyncio.gather(
            orch.handle_webhook_event("work_item.updated", payload_1),
            orch.handle_webhook_event("work_item.updated", payload_1),
            return_exceptions=True
        )

        # 3. 核心断言：其中一个必定成功编排返回 DevFlowResult，而另一个被幂等锁与 seen key 拦截，返回 None
        results = [r1, r2]
        passed_results = [r for r in results if isinstance(r, DevFlowResult)]
        deduplicated_results = [r for r in results if r is None]

        assert len(passed_results) == 1, "只应有 1 个请求被允许执行"
        assert len(deduplicated_results) == 1, "另 1 个并发请求应由于幂等/seen key 被拦截去重"

        # --- 场景 B: UTC 时区一致性审计 ---
        # 1. 校验状态机的状态转移历史
        sync = DevFlowStateSync()
        sm = sync.get_or_create("wi-df-05", initial_state=DevFlowState.INTAKE)

        # 触发一次转换
        sm.transition(DevFlowState.READY_FOR_AI_DEV, actor="tester", reason="UTC audit")
        history = sm.history
        assert len(history) == 1
        transition_time = history[0].timestamp

        # 2. 核心断言： transition timestamp 必须严格具有 UTC 时区或 tzinfo，避免本地时区偏移造成差错！
        assert transition_time.tzinfo is not None, "过渡时间戳必须包含时区信息"
        assert transition_time.tzinfo == UTC, f"过度时间戳必须为 UTC 时间，实际时区: {transition_time.tzinfo}"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    suite = DevFlowReliabilityE2ETestSuite()
    try:
        success = asyncio.run(suite.run_all())
        suite.cleanup()
        if success:
            sys.exit(0)
        else:
            sys.exit(1)
    except KeyboardInterrupt:
        suite.cleanup()
        sys.exit(130)
