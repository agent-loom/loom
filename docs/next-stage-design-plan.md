# 下一阶段技术设计计划

本文档用于承接 `implementation-gap.md` 的结论，把“接下来要补哪些技术设计”拆成可执行的设计文档和决策项。

当前判断：现有文档已经覆盖 MVP 和总体方向，但还不足以直接进入生产级重构。下一阶段不应继续扩大功能面，而应先冻结持久化、发布制品、Hermes 真接入、DevFlow Runner、安全治理和观测这几条主干设计。

## 1. 当前文档体系评估

### 1.1 已经比较完整的部分

| 领域 | 当前文档 | 评价 |
| --- | --- | --- |
| 平台总架构 | `agent-platform-design.md` | 目标、分层、路由、包结构清晰 |
| AI + 人研发流程 | `ai-human-vibecoding-rd-platform.md` | 生产和研发一体化方向清晰 |
| 核心协议 | `contracts/agent-request-response.md` | 已覆盖请求、响应、trace、header 语义 |
| Manifest | `contracts/agent-manifest-v1.md` | 已对齐当前 loader 校验和发布 gate |
| DevFlow Task Pack | `devflow-task-pack.md` | 适合作为 coding agent 输入契约 |
| Plane / GitLab 边界 | `plane.md`、`gitlab.md` | 三方职责划分基本清楚 |
| Hermes 定位 | `hermes-runtime.md` | 明确不深 fork，作为 RuntimeBackend 能力来源 |
| 实现差距 | `implementation-gap.md` | 已能指导下一批 P0/P1 工作 |

### 1.2 当前文档的主要不足

| 问题 | 影响 | 建议 |
| --- | --- | --- |
| 生产级持久化没有专门设计 | 继续写内存 store 会阻碍多实例、审计和回滚 | 新增 `persistence-storage-design.md` |
| Agent package artifact 只有概念，没有产物格式 | 无法保证发布、回滚、跨环境版本可复现 | 新增 `package-artifact-release-design.md` |
| Hermes 文档偏战略，缺少 spike 接口和测试计划 | 容易继续停留在 stub backend | 新增 `hermes-backend-spike.md` |
| DevFlow 只设计到 task pack，没有 runner/workspace/job | 无法真正启动 Codex/Claude Code 自动开发闭环 | 新增 `devflow-runner-workspace-design.md` |
| 安全、租户、secret、tool permission 分散在多个文档 | 生产前难以形成统一 enforcement | 新增 `security-tenant-policy-design.md` |
| 观测、eval、反馈闭环缺少统一数据模型 | 线上质量、灰度、回归无法闭环 | 新增 `observability-eval-feedback-design.md` |
| SemanticRouter 已接主链路，但规则加载没有契约 | 后续 agent 需要手工注册路由规则 | 新增 `semantic-routing-policy-design.md` |
| Plane/GitLab 状态同步只有流程建议 | 缺少状态机、幂等、重试、DLQ 设计 | 新增 `devflow-state-sync-design.md` |

## 2. 下一阶段必须先设计的文档

### P0-1. 持久化与 Repository 设计

建议文档：`docs/persistence-storage-design.md`

必须回答：

1. 本地开发用 SQLite 还是直接 Postgres。
2. 生产 DB schema 如何设计。
3. 哪些对象必须持久化。
4. repository interface 怎么切换 memory / SQL backend。
5. migration 用 Alembic 还是 SQLModel metadata。
6. request id、actor、created_at、updated_at、tenant_id 如何进入所有写表。

首批持久化对象：

| 对象 | 目的 |
| --- | --- |
| `AgentDefinition` | manifest snapshot 和 agent 元信息 |
| `AgentDeployment` | 当前环境/租户/灰度发布状态 |
| `DeploymentAuditEvent` | 可审计和可回滚 |
| `AgentRun` | 调用记录和排障 |
| `AgentSession` | 多轮会话和跨实例共享 |
| `WebhookDelivery` | Plane/GitLab webhook 幂等 |
| `EvalRun` | 发布 gate 和回归记录 |

验收标准：

1. 服务重启后 deployment/session/run/webhook delivery 不丢。
2. memory store 仍可用于单测。
3. repository contract tests 覆盖 memory 和 SQL 实现。

### P0-2. Agent Package Artifact 与发布设计

建议文档：`docs/package-artifact-release-design.md`

必须回答：

1. package artifact 是目录、tar.gz 还是 wheel。
2. artifact 内包含什么。
3. checksum 和 manifest snapshot 如何计算。
4. deployment 如何绑定 artifact id / manifest hash。
5. staging/prod 发布 gate 需要哪些输入。
6. rollback 如何保证回到可复现版本。

建议 artifact 元数据：

```yaml
artifact_id: myj-0.1.0-<sha256-prefix>
agent_id: myj
version: 0.1.0
manifest_sha256: ...
package_sha256: ...
created_by: gitlab-ci
git_commit: ...
mr_iid: ...
eval_report_id: ...
```

验收标准：

1. `scripts/package_agent.py` 生成可校验 artifact。
2. deploy 记录 artifact id 和 manifest hash。
3. rollback 使用历史 deployment 的 artifact id。

### P0-3. HermesBackend Spike 设计

建议文档：`docs/hermes-backend-spike.md`

必须回答：

1. Hermes 官方版本如何引入和 pin。
2. `HermesRuntimeBackend` 调用哪个官方 API。
3. 平台 tool 如何转成 Hermes tool callback。
4. Hermes session/memory 如何映射平台 session。
5. Hermes stream event 如何映射平台 SSE/WebSocket。
6. 如果 Hermes 不可用，fallback 策略是什么。

最小 spike 范围：

1. 只跑 `echo` agent 或新建 `hermes_echo` agent。
2. 使用一个平台工具做 tool call。
3. 返回标准 `AgentResponse`。
4. tool call 进入 `ResponseTrace.tool_calls`。
5. 有一条 integration test 可证明不是 stub。

### P0-4. DevFlow Runner / Workspace 设计

建议文档：`docs/devflow-runner-workspace-design.md`

必须回答：

1. `CodingAgentRunner` interface。
2. Codex / Claude Code / OpenHands adapter 统一输入输出。
3. workspace 创建、复用、清理策略。
4. path guard 如何强制执行。
5. runner 日志、超时、中断、重试、失败归档。
6. runner 如何提交 commit、更新 MR、回写 Plane。

建议核心对象：

```text
CodingJob
Workspace
RunnerInvocation
RunnerResult
PathGuard
ValidationResult
```

验收标准：

1. mock runner 可从 task pack 生成一次完整 job result。
2. runner 不能修改 task pack 允许路径之外的文件。
3. 测试结果和 changed files 可回写 GitLab MR comment。

### P0-5. Plane/GitLab 状态同步设计

建议文档：`docs/devflow-state-sync-design.md`

必须回答：

1. Plane Work Item 状态机。
2. GitLab MR / pipeline / approval 状态如何映射回 Plane。
3. webhook 幂等键和重试策略。
4. 失败事件是否进入 DB-backed dead letter queue。
5. 状态冲突时谁是事实源。

建议状态机：

```text
Intake
Ready for AI Dev
AI Developing
AI Review
Testing / Eval
Human Review
Ready for Merge
Done
Blocked
```

验收标准：

1. Plane webhook delivery 重复不会重复创建 MR。
2. GitLab pipeline fail 会回写 Plane comment 和状态。
3. Eval report 链接可回写 Plane custom property。

### P0-6. 安全、租户、Policy、Secret 设计

建议文档：`docs/security-tenant-policy-design.md`

必须回答：

1. API key 之外的 authn/authz 路线。
2. tenant_id、retailer_id、store_id 的边界。
3. tool permission 如何按 agent / tenant / environment 计算。
4. secret 引用格式和注入方式。
5. trace/log 如何脱敏。
6. 高风险工具如何 human-in-the-loop。

验收标准：

1. tool 执行前必须经过 policy decision。
2. secret 不进入 manifest 明文、trace、日志。
3. prod 高风险工具默认拒绝或需要审批。

## 3. P1 设计文档

### 3.1 Semantic Routing Policy

建议文档：`docs/semantic-routing-policy-design.md`

目标：

1. 定义 `policies/routing.yaml` schema。
2. 明确入口级 semantic route 和 package 内部 worker route 的区别。
3. 让 `SemanticRouter` 可从 manifest/policy 自动加载 rule。
4. 将 semantic 命中原因进入 trace。

### 3.2 Observability / Eval / Feedback

建议文档：`docs/observability-eval-feedback-design.md`

目标：

1. 定义 trace event schema。
2. 定义 eval report artifact。
3. 定义线上失败样本如何回流到 eval。
4. 定义 Langfuse/OpenTelemetry 的接入边界。
5. 定义 token/cost/latency 指标。

### 3.3 Model Gateway

建议文档：`docs/model-gateway-design.md`

目标：

1. provider 配置和 secret 引用。
2. model profile。
3. fallback / retry / timeout。
4. token usage 和成本统计。
5. Hermes provider 与平台 ModelGateway 的边界。

### 3.4 Knowledge / RAG

建议文档：`docs/knowledge-rag-design.md`

目标：

1. knowledge source 同步模型。
2. Weaviate/Postgres/local source 的统一接口。
3. tenant/store 过滤。
4. 文档更新、索引、回滚。
5. MYJ 商品/货架/促销数据的接入路径。

## 4. 当前文档可优化项

### 4.1 降低重复

`agent-platform-design.md` 和 `ai-human-vibecoding-rd-platform.md` 都有总体架构、流程和阶段规划。建议后续：

1. `agent-platform-design.md` 只保留生产平台架构。
2. `ai-human-vibecoding-rd-platform.md` 只保留研发自动化架构。
3. 具体契约全部下沉到 `docs/01-contracts/` 或独立专题文档。
4. 当前实现状态只放在 `implementation-gap.md`。

### 4.2 增加 ADR

下一阶段至少需要新增以下 ADR：

| ADR | 决策 |
| --- | --- |
| `0002-storage-baseline.md` | SQLite/Postgres、repository interface、migration 方案 |
| `0003-package-artifact-release.md` | artifact 格式、checksum、manifest snapshot、rollback |
| `0004-hermes-integration-boundary.md` | 官方 Hermes 使用方式、pin 策略、adapter 边界 |
| `0005-devflow-runner-security.md` | coding runner 权限、workspace、path guard |

### 4.3 增加“实现状态”标记

建议每份核心设计文档增加简短状态头：

```text
Status: Draft / Implemented / Partially Implemented / Superseded
Last verified against code: YYYY-MM-DD
Owner: platform
```

这样可以减少“设计已经写了但实现不是这样”的歧义。

### 4.4 清理非文档文件

`docs/.DS_Store` 出现在目录里，不应作为文档资产保留。建议确认是否被 Git 跟踪；如果未跟踪，加入全局或项目 `.gitignore` 的 `**/.DS_Store`。

## 5. 推荐设计顺序

按阻塞程度排序：

1. `persistence-storage-design.md`
2. `package-artifact-release-design.md`
3. `hermes-backend-spike.md`
4. `devflow-runner-workspace-design.md`
5. `devflow-state-sync-design.md`
6. `security-tenant-policy-design.md`
7. `semantic-routing-policy-design.md`
8. `observability-eval-feedback-design.md`
9. `model-gateway-design.md`
10. `knowledge-rag-design.md`

理由：

1. 持久化和 artifact 是生产发布、回滚、审计的前置条件。
2. Hermes spike 决定 runtime 方向，越早验证越能避免业务 agent 绑定 stub。
3. DevFlow runner 是“AI + 人 + vibe coding”闭环的核心。
4. 安全和观测必须在接真实业务 API 前完成基线设计。
5. semantic routing、model gateway、knowledge/RAG 属于多 agent 扩展能力，可以在底座明确后推进。

## 6. 下一步建议

下一步不建议直接继续扩大功能。建议先完成前三份 P0 设计：

1. 持久化与 Repository 设计。
2. Package Artifact 与发布设计。
3. HermesBackend Spike 设计。

这三份设计冻结后，再进入实现会更稳：状态不会丢、发布能回滚、runtime 路线可验证。
