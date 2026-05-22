# DevFlow 异步任务管道与可靠性回滚端到端测试规范与方案

> Status: Draft  
> Owner: platform  
> Last updated: 2026-05-21

本文档规范了 Agent Platform 平台中 **DevFlow 异步任务管道与可靠性回滚机制** 的端到端测试与集成验证要求。为了在不污染本地物理 Git 仓库和外部系统的前提下，高保真地重现状态机回滚、Git 工作区检查点生成、Command/Path 校验防御、GitLab 409 冲突重用及并发去重等极限场景，测试体系采用了**全链路内存网关注入**与**可重编程 Stub 执行器/模拟网关**方案。

---

## 1. 测试范围与用例拓扑

测试以黑盒/集成形式，覆盖 DevFlow 的 5 个细分高可靠与容灾控制模块：

```text
       [ Webhook / 外部事件 ]
                 │
                 ▼
      ┌─────────────────────┐
      │  DevFlowOrchestrator │ ──(去重/幂等)──> [ 相同 Delivery ID ] (拦截返回)
      └──────────┬──────────┘
                 │ (触发)
                 ▼
      ┌─────────────────────┐
      │  DevFlowStateMachine│ <──(同步失败)──┐
      └──────────┬──────────┘                │ [ Plane API 500 / 网络错误 ]
                 │ (更新 Plane)               │
                 ▼                           │
      ┌─────────────────────┐                │
      │  DevFlowStateSync   │ ──(失败回滚)───┘
      └──────────┬──────────┘
                 │ (启动开发)
                 ▼
      ┌─────────────────────┐
      │  CodingAgentRunner  │ ──(校验阶段)──> Checkpoints (before_runner, before_validation...)
      └──────────┬──────────┘ ──(路径/命令)─> CommandGuard / PathGuard 拦截并退回 AI Developing
                 │ (推送代码)
                 ▼
      ┌─────────────────────┐
      │  GitLab MR 409 冲突 │ ──(自动兜底)──> [ find_open_merge_request ] ──> 复用 MR
      └─────────────────────┘
```

### 核心验证用例

| 用例 ID | 模块名称 | 描述与验证目标 | 验证方法 |
|---|---|---|---|
| **E2E_DF_01** | Plane 失败状态回滚 | 验证当向 Plane 同步新状态发生网络错误/500 异常时，本地状态机能够撤销当前事务，自动执行 `sm.rollback(old_state)` 返回上一次的合法状态并清空脏历史。 | 注入 `sync_to_plane` 操作，拦截 Plane 请求并抛出异常，断言本地状态回到初始态且 history count 为 0。 |
| **E2E_DF_02** | 关键节点 Checkpoint 完整生成 | 验证 CodingAgentRunner 在正常开发周期中，完整走完 Workspace 构造、Adapter 代码生成、测试校验与提交推送全流程，并在 workspace 成功生成 `before_runner`, `before_validation`, `before_commit`, `after_commit` 检查点。 | 执行 Happy Path 任务包，验证 job.checkpoints 包含 4 类指定快照，记录 head_sha 与变动文件数。 |
| **E2E_DF_03** | Command/Path Guards 安全防御拦截 | 确保当任务包中包含高危 Shell 命令（如 `rm -rf /`, `sudo` 等）或试图修改 Denied 路径时，Runner 中的安全防护罩能够实时拦截，并触发 Plane 状态回退至 `AI Developing`。 | 注入带有 `rm -rf /` 或越界文件的 Task，断言 runner.run 返回 JobState.FAILED 且 Plane 端状态回退到 `AI Developing`。 |
| **E2E_DF_04** | MR 409 冲突自动重用 | 验证当 Runner 推送代码并调用 ScmAdapter 创建 MR 时，如果因分支已经存在 MR 导致外部平台返回 409 冲突，Runner 会自动转为通过 `find_open_merge_request` 寻找已有 MR 并无缝衔接。 | 模拟 create_merge_request 产生 ScmError(status_code=409)，断言 runner 成功捕获并安全获取已有 MR_IID，写入 comment。 |
| **E2E_DF_05** | 并发去重与 UTC 时间审计 | 验证在极短时间内并发投递相同 Delivery ID Webhook 时，Orchestrator 能够通过 seen keys / 幂等库机制进行拦截去重；同时审计所有状态过渡时间戳必须均使用 UTC。 | 并发调用 `handle_webhook_event`，断言后续请求返回 None；验证状态机 transition 时间戳包含 `'Z'`。 |

---

## 2. 环境与高保真 Stub 工具设计

为了免除外部 Plane/GitLab 服务以及物理 Git 操作对本地环境的影响，测试脚本使用以下高保真仿真件：
* **`StubPlaneAdapter`**：支持设置响应注入（如异常抛出、延时模拟），用以检验 `DevFlowStateSync` 在 Plane 不可用时的状态撤销逻辑。
* **`StubGitLabAdapter`**：模拟合并请求创建冲突（409），测试 `find_open_merge_request` 的复用流。
* **`StubWorkspaceManager`**：在系统临时目录中构建真实的轻量 Git 仓库沙箱（使用 `subprocess.run(["git", "init"])`），从而在没有外部代码库推送权限时，高保真地对 `CheckpointManager` 及 git 命令输出格式进行真机模拟与断言。
* **`CommandGuard` / `PathGuard`**：使用系统原生白名单及黑名单策略执行校验。

---

## 3. 端到端测试执行指南

测试脚本位于 `scripts/run_devflow_reliability_e2e.py`。

### 运行测试

```bash
uv run python scripts/run_devflow_reliability_e2e.py
```

### 关键控制台输出说明

* **`PASS`**：表示状态机回滚、检查点生成、高危命令拦截、MR 冲突复用以及并发去重等断言全部符合系统健壮性设计。
* **`FAIL`**：触发了非预期的 Bug，状态未回退或安全验证逃逸，需要检查相关的 core 模块。
