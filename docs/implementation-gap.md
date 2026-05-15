# 实现与设计差距分析

> Last verified against code: 2026-05-15 (Updated: Phase 1-3 全部完成 — pipeline wiring ✅, Domain Model ✅, 持久化层全量完成 ✅, 动态工具加载 ✅, Hermes 真接入 ✅, ArtifactStore ✅)

本文档对齐以下两份设计文档和当前代码实现：

- `docs/02-architecture/agent-platform-design.md`
- `docs/02-architecture/ai-human-vibecoding-rd-platform.md`

结论：当前实现已经覆盖了平台 MVP 的骨架，具备“多 Agent Package + 统一请求响应契约 + 路由 + RuntimeBackend 抽象 + DevFlow API + Plane/GitLab Adapter + Eval + 基础测试”的能力。但距离设计里的生产级 Agent Platform 还有明显差距，主要集中在持久化、真实 Hermes/LLM 集成、真实外部系统闭环、Coding Agent 执行器、权限治理、发布治理和线上观测。

## 1. 当前实现总览

### 1.1 已经实现的核心模块

| 设计能力 | 当前实现 | 状态 |
| --- | --- | --- |
| 统一 Agent 请求响应协议 | `AgentRequest`、`AgentResponse`、`AgentOutput`、`ResponseCard`、`ResponseCommand` | 基本完成 |
| Agent Manifest | `AgentManifest`、`ManifestLoader`、manifest 校验脚本 | 基本完成 |
| 多 Agent Package | `agents/myj`、`agents/promo_recommendation`、`agents/echo` | 基本完成 |
| 入口路由 | `AgentRouter` 支持 `agent_id`、`app_id`、`retailer_id`、`channel_id`、默认 Agent | 基本完成 |
| 灰度路由 | 基于稳定 hash bucket 的 canary 选择 | 部分完成 |
| Runtime 抽象 | `NativeRuntimeBackend`、`HermesRuntimeBackend`、`LangGraphRuntimeBackend` | 部分完成 |
| 工具注册和执行 | `ToolRegistry`（✅ 已去业务化 + 动态加载）、`ToolExecutor`（✅ hook/metrics 已接入）、agent-scoped tools | 基本完成 |
| 会话 | `InMemorySessionStore`、`AgentSession`、✅ `AgentSessionRepository` Protocol + InMemory/SQL 实现 | 部分完成 |
| Trace / metrics | `InMemoryRunStore`、`MetricsCollector`（✅ 已串联 runtime/tool）、✅ `AgentRunRepository` Protocol + InMemory/SQL、`/metrics` 待实现 | 部分完成 |
| Eval | `EvalRunner`、golden case、CI callback API | 部分完成 |
| DevFlow | 需求解析、issue 生成、task pack、agent 脚手架、设计分析、测试计划 API | 部分完成 |
| Plane 集成 | `PlaneAdapter`、webhook 校验、幂等处理、DevFlow 触发 | 部分完成 |
| GitLab 集成 | `GitLabAdapter`、创建分支/MR、eval 反馈 | 部分完成 |
| Streaming / WebSocket | SSE 和 WebSocket chat endpoint | 部分完成 |
| 自动化测试 | contract、integration、unit tests | 覆盖较好 |

### 1.2 当前测试状态

最近一次验证：

```text
.venv/bin/python -m pytest
437 passed

.venv/bin/ruff check src tests scripts alembic
All checks passed

.venv/bin/python scripts/validate_manifest.py
3 个内置 agent manifest 全部通过
```

已消除 pytest collection warning，当前存在 DeprecationWarning（旧字段名 `store_id`/`retailer_id`/`store` 的向后兼容告警，符合预期）。Repository contract tests 使用 `@pytest.fixture(params=["memory", "sql"])` 参数化，62 个测试覆盖 7 个 Repository 的 InMemory 和 SQL 实现。

## 2. 与 `agent-platform-design.md` 的差距

### 2.1 总体架构

设计目标是“控制面 + 数据面 + 能力层 + Agent Package + 外部数据系统”的完整平台。当前已经有对应代码骨架，但很多能力仍是内存实现或 demo 实现。

| 设计模块 | 当前实现 | 差距 |
| --- | --- | --- |
| API Gateway / 协议适配 | FastAPI `/api/v1/agent/chat`、SSE、WebSocket、request id header 回写 | 缺少版本协商、前端能力协商、复杂渠道协议适配 |
| Auth / 租户识别 | API key、request context、`x-tenant-id` 注入 | 缺少 RBAC、租户隔离、细粒度权限、服务间鉴权；header tenant 目前只注入 `tenant_id`，不等同于业务 `org_id` |
| Agent Registry | 文件发现 + 内存 cache | 缺少 DB 持久化、版本索引、artifact registry、并发一致性 |
| 版本/灰度/回滚 | deploy API、canary bucket、rollback API、staging/prod 自动 eval gate、**ArtifactStore 产物绑定**、✅ `AgentDeploymentRepository` + `DeploymentAuditRepository` Protocol + 双实现 | 缺少持久化发布历史切换、manifest_sha256 绑定、真实环境控制、审批、保护环境 |
| Policy | ✅ `PolicyEngine` 已深度接入 runtime/tool 链路（check_input/check_output 在 RuntimeManager，pre_tool/post_tool hooks 在 ToolExecutor） | 策略规则仍需从外置配置或 DB 加载；`check_tool_allowed` 未在 ToolExecutor 中调用 |
| Eval | EvalRunner 存在 | 缺少大规模评测集、质量评分、线上反馈闭环、CI artifact |
| Session / Memory | 内存 SessionStore | 缺少 Redis/Postgres、压缩、长期记忆、跨实例共享 |
| Tool Executor | 工具执行、allowlist、timeout、✅ hook emit、✅ metrics recording | 缺少重试、熔断、审计持久化、secret 注入、租户级工具权限 |
| Knowledge Service | 基础服务和 sources 配置 | 缺少真实 vector db、RAG pipeline、同步任务、数据权限 |
| Model Gateway | ✅ 有网关抽象与 `OpenAICompatibleProvider`（httpx.AsyncClient） | 缺少 token/cost 统计、限流、fallback、多模型路由 |
| Observability | ✅ run store、metrics 已串联至 RuntimeManager 和 ToolExecutor、HookRegistry 已串联、logging | 缺少 OpenTelemetry/Langfuse、dashboard、trace 持久化、告警 |
| Domain Model | ✅ 泛化完成：LocationContext、org_id、locale=en、timezone=UTC | 旧字段通过 alias 保持向后兼容 |
| 持久化骨架 | ✅ SQLAlchemy 2.0 + Alembic + persistence/ 包（7 ORM 表 + 7 Protocol + 7 InMemory + 7 SQL + AuditMixin + Alembic migration） | DI 已就绪；RuntimeManager 内部 store 待切换到 Repository |
| Artifact 管理 | ✅ ArtifactStore（tar.gz + SHA256 + 部署绑定） | 仅 in-memory；缺 manifest_sha256 绑定和远程存储 |

### 2.2 多 Agent 路由

设计中的入口路由优先级已经在 `AgentRouter` 中实现：

1. 显式 `agent_id`
2. `metadata.app_id`
3. `context.tenant.org_id`（旧 `retailer_id`，向后兼容）
4. `context.channel.channel_id`
5. 默认 Agent

差距：

- `SemanticRouter` 已作为默认 Agent 前的可选 fallback 接入主路由链路；但规则仍未从 manifest/policy 自动加载。
- Package 内部任务路由主要依赖 native backend 和 demo 规则，尚未形成统一的 worker/router manifest 配置加载。
- `myj` 内部的商品、位置、店务、优惠 worker 只是轻量实现，尚未迁移真实业务系统里的复杂编排。
- 路由结果没有持久化到可检索 trace 系统，只存在 response trace / run store。

建议下一步：

1. 在 manifest 中明确 `routing.rules` 的 schema，并让 `SemanticRouter` 从 package policy 文件加载规则。
2. 将 route decision 作为结构化 trace event 持久化。

### 2.3 Agent Package 与 Manifest

当前 `agents/myj`、`agents/promo_recommendation`、`agents/echo` 已按设计目录组织，manifest 也覆盖了 metadata、version、entry、runtime、models、prompts、tools、knowledge、routing、session、context、output、safety、evals、extensions。

已实现或已补强：

- `version.package_version` 使用 SemVer 校验。
- `version.runtime_compat` 会按当前平台版本做范围校验。
- prompt、routing、safety、eval 文件引用会做包内路径和存在性校验。
- tools allow/deny 会检查冲突，allow tool 会校验已注册或 package-local。
- runtime backend、entrypoint 格式、output protocol、output supports、command allowlist、context path 已增加校验。
- `output.command_allowlist` 已在 `ResponseBuilder` 返回阶段过滤。

剩余差距：

- ArtifactStore 已引入并生成 `.tar.gz` 绑定部署，但缺少 `manifest_sha256`、`package_sha256` 字段绑定。
- ArtifactStore 当前为 in-memory 实现，设计要求 ABC Protocol（upload/download/exists）+ 多后端（Local/S3/GitLab Registry）。
- package registry 仍是本地目录扫描，尚未完全切换到 artifact registry 驱动。
- DB 持久化的 Deployment 记录还未完全替代内存 Audit Log。

建议下一步：

1. 增加 package build 产物，例如 `.tar.gz` + checksum + manifest snapshot。
2. AgentRegistry 改为 DB + artifact storage 双层：DB 管元数据和部署，artifact storage 管 package 文件。
3. deploy 时记录 manifest snapshot hash，确保回滚目标可复现。

### 2.4 Runtime 与 Hermes

当前 RuntimeManager 已支持三个 backend：

- `native`
- `hermes`
- `langgraph`

其中 `hermes` backend 目前更像 Hermes 适配层原型：

- ManifestMapper 可以把平台 manifest 映射成 Hermes config。
- ToolBridge 可以把平台工具描述转成 Hermes 可理解的工具定义。
- SessionBridge 可以映射 session。
- ResponseMapper 可以把 Hermes result 转成平台 `AgentResponse`。
- ConversationEngine 在没有 `model_gateway` 时返回 stub。
- **✅ `model_gateway` 和 `tool_executor` 已注入到 `HermesRuntimeBackend`**，通过 `RuntimeManager` 传递。
- **✅ `ConversationEngine.converse()` 接口已修复**：添加 `provider_name` 参数、dict→attribute access 修复、ToolCall 属性访问修复、`list[ModelMessage]` 类型对齐。
- **✅ `hermes_echo` agent 已创建**：完整 agent package（manifest + prompts + evals），集成测试验证非 stub 响应。
- **`OpenAICompatibleProvider` 已实现**（`runtime/model_gateway.py`），使用 httpx.AsyncClient 调用 OpenAI-compatible API。
- **`ModelGateway` 已重构**：`__init__()` 不再自动注册 stub，`create_default()` 工厂方法提供含 stub 的实例。

主要差距：

- 没有调用真实 Hermes 官方 runtime 或 Hermes `AIAgent`。
- 没有将 Hermes 的 planner、memory、tool loop、事件流和 trace 原生能力接入平台。
- 没有把平台 ToolExecutor 注册为 Hermes 的实际可调用 tool。
- Hermes 的 stream event 没有映射成平台 SSE/WebSocket event。
- Hermes memory provider 只是配置字段，没有真实持久化后端。
- Hermes 错误、重试、中断、human-in-the-loop 事件没有规范映射。

最新进展：已完成 Hermes 源码真实对比，修正了 Spike B 设计中对 `AIAgent` 初始化参数和全局 Registry 的不合理假设（见 `docs/03-runtime/hermes-backend-spike.md` 第 10-11 节）。

建议下一步：

1. 保持平台 `RuntimeBackend` 为边界，不让业务代码直接依赖 Hermes。
2. 新增 `HermesClient` 或 `HermesAgentFactory`，封装真实 Hermes 官方 API。
3. 把平台 tool schema 转成 Hermes tool，并把执行回调代理到 `ToolExecutor`。
4. 将 Hermes event stream 映射为平台统一 `AgentStreamEvent`。
5. 加集成测试：manifest -> Hermes config -> tool call -> AgentResponse -> trace。

### 2.5 发布、灰度和回滚

当前有：

- `/api/v1/agent-packages/{agent_id}/versions/{version}/deploy`
- `/api/v1/agent-deployments`
- `/api/v1/deployments/rollback`
- `/api/v1/deployments/audit`
- canary traffic bucket
- staging/prod 发布强制执行 eval gate
- deploy 事件写入内存 audit log

已实现或已补强：

- staging/prod 发布不再信任客户端传入的 `eval_passed=true`，会由服务端执行 `EvalRunner`。
- `eval_passed=false` 会直接阻断 staging/prod 发布。
- deploy API 已移除失效的 `auto_eval` 客户端开关，避免契约和实际行为不一致。
- canary deployment 使用独立 deployment slot，避免覆盖 stable prod deployment。
- router 会按稳定 hash bucket 在 prod stable/canary deployment 间选择。
- deploy API 会返回 eval report，便于 CI/CD 读取。

差距：

- deployment 和 audit log 是内存态，服务重启后丢失。
- rollback target 依赖内存 audit，不能作为生产回滚依据。
- 没有 GitLab protected environment / manual approval 绑定，prod 发布仍缺少人工审批和 MR approval 校验。
- 没有蓝绿/灰度发布的真实流量层控制。
- 没有部署前后的健康检查、自动回滚、SLO 门禁。
- 没有 per-tenant / per-channel 的发布策略 UI 或配置中心。
- canary 命中结果已写入 `AgentResponse.trace.traffic_bucket`，但还没有落到持久化 trace / dashboard。

建议下一步：

1. 优先持久化 `AgentDeployment` 和 `DeploymentAuditEvent`。
2. 发布 API 要写入不可变审计记录。
3. prod 发布必须绑定 eval report id、MR id、审批人和回滚目标。
4. canary 的命中结果写入 trace，方便线上排障。

## 3. 与 `ai-human-vibecoding-rd-platform.md` 的差距

### 3.1 生产侧统一入口

设计中的 `POST /api/v1/agent/chat` 已实现，并支持：

- 统一请求响应模型
- 基于 agent/tenant/channel 的路由
- streaming SSE
- WebSocket
- session_id
- trace/run store

差距：

- 请求协议缺少严格的前端 capabilities negotiation。
- response commands 没有按 channel/device capability 做过滤。
- 还没有生产级 SLA 控制：全链路 timeout budget、降级策略、限流策略持久化。
- WebSocket 鉴权、取消、背压、断线恢复较弱。
- header request id / tenant id 已可注入请求；但还缺少 channel/device/user 等更完整的协议适配。

### 3.2 研发侧 DevFlow

当前已有：

- `/api/v1/devflow/parse-requirement`
- `/api/v1/devflow/generate-issues`
- `/api/v1/devflow/task-packs`
- `/api/v1/devflow/scaffold-agent`
- `/api/v1/devflow/design-analysis`
- `/api/v1/devflow/test-plan`
- Plane webhook 触发 DevFlow
- GitLab 分支/MR adapter

差距：

- 需求理解主要是启发式解析，不是真正的 LLM/Agent 工作流。
- 架构设计、测试计划是轻量模板化 agent，不具备代码库深度分析能力。
- DevFlow 只创建 branch/MR，不会真正启动 Codex/Claude Code/OpenHands 执行代码修改。
- 缺少 coding agent runner 的隔离工作区、权限控制、日志采集、超时、中断和重试。
- 缺少从 Plane Work Item 到 GitLab MR 再到 eval report 的完整状态回写。
- 缺少“人类验收点”的强制状态机。

建议下一步：

1. 定义 `CodingAgentRunner` 抽象，支持 Codex CLI、Claude Code、OpenHands、内部 worker。
2. DevFlow webhook 只负责生成 task pack 和创建 MR；实际编码交给 runner 异步执行。
3. runner 必须在隔离 workspace 中运行，只允许修改 task pack 声明的路径。
4. runner 输出必须包括 changed files、tests、风险、后续建议，并回写 GitLab/Plane。
5. 将 Plane state 设计为强状态机：Intake -> Ready for AI Dev -> AI Developing -> AI Review -> Human Review -> Ready for Merge -> Done。

### 3.3 Issue 看板与 GitLab

当前 Plane 和 GitLab adapter 都存在，且 Plane OpenAPI 文档已归档到 `docs/vendor/plane`。

已实现或已补强：

- `DevFlowOrchestrator` 的 `gitlab_project_id` 已改为来自 `GITLAB_PROJECT_ID`，不再复用 Plane workspace slug。
- DevFlow 启用条件已包含 `GITLAB_TOKEN`，避免创建缺 token 的 GitLab adapter。

差距：

- Plane project、state、label、custom property 初始化还没有自动化。
- 当前只处理部分 webhook event 和字段，真实 Plane payload 兼容性还需要压测。
- 没有 dead-letter queue，webhook 失败后不易恢复。
- 没有把 GitLab pipeline/eval 状态稳定同步回 Plane。
- DevFlow 只有在 Plane base url、Plane key、GitLab base url、GitLab token、GitLab project id 都存在时才启用；缺少启动时配置诊断。

建议下一步：

1. 增加 Plane bootstrap 脚本，创建标准 states、labels、properties。
2. 建立 `DevFlowStateSync`，专门负责 Plane/GitLab 双向状态同步。
3. ✅ Webhook delivery idempotency 已改用 `InMemoryWebhookDeliveryRepository`（可切换 SQL 实现）。
4. 失败事件进入 DB-backed retry queue。

### 3.4 AI + 人治理

设计要求“AI 不直接越权改生产”，人负责关键决策。当前更多停留在文档和 API 元数据层。

差距：

- 没有强制审批模型。
- 没有高风险变更识别和阻断。
- 没有产物签名、审计、责任人绑定。
- 没有 release checklist 与 GitLab MR approval 的强绑定。
- 没有“AI 生成内容必须经过 eval + human review”的系统性 enforcement。

建议下一步：

1. 增加 `ApprovalPolicy`，按环境、agent、工具风险、变更类型计算审批要求。
2. deploy prod 时强制校验 GitLab MR approval、eval report、业务验收字段。
3. 对高风险工具调用加入 human-in-the-loop gate。

## 4. 当前实现的主要架构债

### 4.1 内存态过多

以下对象当前都是内存态或本地文件态：

- Agent registry cache
- Agent deployment
- Deployment audit
- Agent run store
- Session store
- Webhook idempotency set
- Metrics collector runtime state

这会导致：

- 服务重启后状态丢失。
- 多实例部署不一致。
- 不能支撑生产审计。
- 回滚和灰度结果不可追溯。
- Webhook 幂等失效。

最新进展：

- ✅ 已引入 `sqlalchemy[asyncio]`、`aiosqlite` 和 `alembic`，持久化层完整实现。
- ✅ ArtifactStore 已实现本地产物保存（in-memory tar.gz + SHA256）。
- ✅ `persistence/` 包已创建（tables.py 7 ORM Row + AuditMixin、repositories.py 7 Protocol、memory.py 7 InMemory、sql.py 7 SQL、context.py AuditContext）。
- ✅ Alembic 配置和初始 migration 已完成，`alembic upgrade head` 验证通过。
- ✅ DI 注入已就绪：`create_app()` 按 `DATABASE_URL` 选择 InMemory 或 SQL。
- ✅ 62 个 Repository contract tests 验证 InMemory 和 SQL 行为一致。
- 🔶 RuntimeManager 内部仍使用 `InMemoryRunStore` 和 `InMemorySessionStore`，待切换到新 Repository 接口。

重构方向：

- 完成核心 Repository 到 SQLAlchemy 的最终迁移。
- 本地开发可用 SQLite，生产用 Postgres。
- 使用 repository interface 隔离 storage 实现。
- 所有写操作增加 `created_at`、`updated_at`、`actor`、`request_id`。

### 4.2 Runtime 抽象已经有，但真实能力不足

`RuntimeBackend` 是正确边界，但当前 backend 能力分化明显：

- native backend 可以跑 demo agent。
- langgraph backend 是轻量状态图模拟。
- hermes backend 是 mapper + stub conversation。

重构方向：

- 明确 `RuntimeBackend` 最小接口：`run`、`stream`、`validate_manifest`、`healthcheck`。
- Hermes 作为优先补齐的真实 runtime。
- Native 保留为本地开发和简单 agent 后备 runtime。
- LangGraph 作为可选 orchestration runtime，不应和 Hermes 混在同一层语义里。

### 4.3 DevFlow 有流程骨架，但没有执行闭环

当前 DevFlow 可以从需求生成 task pack，也能创建 branch/MR，但不能自动完成“读代码、改代码、跑测试、提交、回写”的闭环。

重构方向：

- 增加异步 job 系统。
- 增加 `CodingAgentRunner`。
- 增加 workspace manager。
- 增加 path guard。
- 增加测试执行和结果解析。
- 增加 GitLab commit/MR update。
- 增加 Plane 状态回写。

### 4.4 安全和租户隔离不足

当前安全能力适合 MVP，不适合生产：

- API key 是全局级别。
- 没有用户、角色、权限。
- 没有 tenant-level secret。
- 工具调用没有细粒度授权。
- trace 里没有 PII 脱敏策略。

重构方向：

- 引入 AuthN/AuthZ 抽象。
- ✅ PolicyEngine 已接入 runtime（check_input/check_output）；check_tool_allowed 待接入 ToolExecutor。
- 所有外部 API token 进入 secret manager。
- request/response/logging 增加脱敏层。

## 5. 优先级建议

### P0：正式进入生产前必须补齐

| 优先级 | 工作项 | 原因 |
| --- | --- | --- |
| P0 | 持久化 AgentDeployment、AgentRun、Session、Audit、WebhookDelivery | 生产可追溯、多实例一致性、回滚基础 |
| P0 | 真实 HermesBackend 集成或明确继续使用 Native runtime | 当前 Hermes 只是 stub，不能宣称用上 Hermes runtime |
| P0 | manifest 强校验 | 已完成基础补强；下一步需要和 package artifact / deployment gate 绑定 |
| P0 | deploy gate 强化 | eval gate 已补强；仍需人工审批、MR approval、release artifact 绑定 |
| P0 | Plane/GitLab 状态闭环 | 研发流程要能追踪任务、MR、测试、验收 |
| P0 | Tool 权限和 secret 管理 | 外部业务 API 调用必须可控可审计 |

### P1：平台扩展多个业务 Agent 前补齐

| 优先级 | 工作项 | 原因 |
| --- | --- | --- |
| P1 | package artifact registry | 多 agent、多版本、跨环境发布需要稳定产物 |
| P1 | SemanticRouter manifest 规则加载 | 新 agent 增多后不能依赖手工注册 semantic rule |
| P1 | CodingAgentRunner | 新增业务 agent 的降本核心能力 |
| P1 | Eval 数据集扩展和自动报告 | 业务质量回归需要量化 |
| P1 | Knowledge/RAG 真实接入 | MYJ 等业务 agent 离不开业务知识 |
| P1 | OpenTelemetry/Langfuse trace | 线上排障和质量分析必需 |

### P2：规模化运营阶段补齐

| 优先级 | 工作项 | 原因 |
| --- | --- | --- |
| P2 | Admin UI | 管理 agent、版本、灰度、eval、trace |
| P2 | MCP 集成 | 给外部研发工具统一暴露 Plane/GitLab/平台能力 |
| P2 | 多模型路由和成本治理 | 控制 token 成本和模型可用性 |
| P2 | Human-in-the-loop runtime event | 高风险工具、人审回复、人工接管 |
| P2 | 多租户计费/配额 | 平台化运营需要 |

## 6. 推荐重构路线

### 阶段 1：把 MVP 从内存态改成可运行服务

目标：本地和测试环境能稳定保存状态，重启不丢数据。

主要任务（**部分完成**：依赖与 migration 骨架已引入）：

1. ~~引入 DB 层和 migrations。~~
2. 实现 `AgentRepository`、`DeploymentRepository`、`RunRepository`、`SessionRepository`、`WebhookDeliveryRepository`。
3. FastAPI app 支持依赖注入 storage backend。
4. 所有当前内存 store 保留为测试实现。
5. 补充 repository contract tests。

### 阶段 2：补齐 Hermes 真实 runtime

目标：平台能通过 manifest 选择 Hermes，并真实运行 Hermes agent。

主要任务：

1. 封装 Hermes 官方 runtime client/factory。
2. 平台工具桥接到 Hermes tool callback。
3. Hermes trace/event 映射到平台 trace/SSE。
4. Hermes memory 映射到平台 session store。
5. 添加 Hermes integration tests。

### 阶段 3：打通 DevFlow 执行闭环

目标：Plane Work Item 进入指定状态后，平台能自动生成 task pack、创建分支、调用 coding agent、提交 MR、跑测试、回写结果。

主要任务：

1. 新增 async job queue。
2. 新增 workspace manager。
3. 新增 `CodingAgentRunner` interface。
4. 支持 Codex/Claude Code runner adapter。
5. 增加 path guard、timeout、日志和 artifact。
6. 回写 Plane comment、custom properties、state。

### 阶段 4：生产治理

目标：支撑 staging/prod 灰度、回滚、审计和多 agent 运维。

主要任务：

1. deploy gate 绑定 eval report、MR approval、人工审批。
2. release audit 不可变。
3. canary trace 和指标。
4. prod rollback 使用持久化 release history。
5. 增加 SLO、告警和 dashboard。

## 7. 最小下一批 Issue 建议

可以直接拆成以下 Plane Work Items：

| 标题 | 类型 | 优先级 | 验收标准 |
| --- | --- | --- | --- |
| 引入持久化 repository 层 | platform:infra | P0 | deployment/run/session/webhook delivery 重启不丢失，单测覆盖 |
| package artifact 与 manifest snapshot | platform:contract | P0 | 发布产物包含 manifest snapshot、checksum，deployment 记录可复现版本 |
| 实现真实 HermesBackend spike | platform:runtime | P0 | 一个 echo agent 能通过 Hermes backend 完成真实 run，并返回平台响应 |
| DevFlow 状态同步设计和实现 | platform:devflow | P0 | Plane Work Item、GitLab MR、eval result 可双向回写 |
| CodingAgentRunner 接口设计 | platform:devflow | P1 | 定义 runner、workspace、path guard、result schema，并完成一个 mock runner |
| SemanticRouter manifest 规则加载 | platform:routing | P1 | manifest routing rule 可自动注册到 semantic router，trace 记录命中原因 |
| Tool 权限和 secret 管理 | platform:security | P1 | 工具执行前校验 tenant/agent policy，secret 不出现在日志和 trace |
| Eval report artifact | platform:quality | P1 | CI callback 生成可追溯 eval report，并可回写 GitLab/Plane |

## 8. 总结

当前项目不是空壳，已经具备 agent platform 的基础骨架，并且测试覆盖较完整。真正的差距不在“有没有 API 或类”，而在生产级平台必须具备的状态持久化、真实 runtime、真实外部系统闭环、权限审计、发布治理和执行自动化。

如果目标是支撑多个类似 `myj` 的业务 agent，下一步不建议继续堆 demo agent，而应优先把平台底座变成可持久化、可发布、可审计、可回滚的服务；同时尽快验证 Hermes 官方 runtime 的真实接入边界，避免后续业务 agent 写死在当前 stub runtime 上。
