# 下一阶段开发计划（S5：平台生产化与规模化）

> Status: In Progress
> Last updated: 2026-05-16

本计划基于 S2-S4 已完成的基础和 2026-05-16 代码审查校准结论，将 S5 阶段拆成 4 个 Phase。每个 Phase 内的任务按依赖关系排序。

> 重要口径：S2-S4 的很多能力已经有代码基础，但不能等同于生产闭环完成。S5 开始前必须先完成主链路可靠性校准，否则 Hermes/RAG/MCP/Admin API 等扩展能力会建立在不可恢复、不可审计的状态之上。

## 当前起点

| 指标 | 值 |
|---|---|
| 测试 | pytest: 670 passed, 1 skipped；ruff: failed；manifest validate: failed |
| 代码量 | ~90 files, +11000 lines |
| 持久化 | Repository Protocol + InMemory/SQL 双实现 + Alembic migration + 部分 DI 已完成；Registry/Deployment/Audit 接线已在工作树中实现但未过质量门禁，Eval 主链路仍需校准 |
| 安全基线 | Scoped API Key、Tool Permission、SecretResolver、LogSanitizer 已有基础；RBAC/scopes 与高风险审批尚未形成统一 enforcement |
| DevFlow | CodingAgentRunner + WorkspaceManager + PathGuard + Webhook/Eval 基础闭环已实现；Webhook BackgroundTasks 变更已在工作树中实现但未过质量门禁 |
| Hermes | Spike A 完成；Spike B 官方 Hermes SDK 接入代码已出现，但 ruff 存在闭包和格式问题 |
| Knowledge | KnowledgeBackend Protocol + runtime 主链路接入代码已出现；真实 vector backend 待实现 |
| MCP | 实验性代码已出现，尚未决定是否纳入当前提交范围 |
| Admin API | 实验性代码已出现，尚未决定是否纳入当前提交范围 |
| 观测 | MetricsCollector + /metrics + LogSanitizer；tracing/OTel 实验性代码已出现，尚未过质量门禁 |

---

## Phase 0：主链路可靠性校准（预计 2-4 天）

**目标**：把已经实现但未完全接入生产主链路的能力校准为可信状态。此阶段不追求新增大能力，优先修正会影响发布、回滚、审计、DevFlow 触发和后续扩展的基础问题。

### 前置条件

- 当前测试基线可复现：`pytest` 通过，`ruff` 通过。
- 对 `implementation-gap.md` 的结论重新校准，区分“repo/模型/接口已实现”和“主链路已生产闭环”。

### 任务列表

| # | 任务 | 设计来源 | 当前状态 | 验收标准 |
|---|---|---|---|---|
| 0.1 | 重新确认测试与 warning 基线 | implementation-gap §1.2 | pytest 通过；ruff/manifest 失败 | `pytest`、`ruff`、manifest validate 全部通过，或 blockers 明确记录 |
| 0.2 | 修复 Plane webhook 后台任务注入 | plane/devflow-state-sync | 代码已出现，测试通过，待 ruff/整体门禁 | webhook 启用 DevFlow 分支时不再依赖未定义变量；新增覆盖该路径的测试 |
| 0.3 | Registry/Deployment 持久化接线 | persistence-storage §5 | 代码已出现，待 ruff/manifest/文档一致性确认 | `AgentDefinitionRepository`、`AgentDeploymentRepository` 接入 register/deploy/list/resolve 主链路，或在代码和文档中明确 dev-only fallback |
| 0.4 | Deployment audit 主链路校准 | persistence-storage §4.4 | 代码已出现，测试通过，待质量门禁 | deploy/rollback/audit endpoint 不再只依赖进程内 `DeploymentAuditLog`，审计记录重启可恢复 |
| 0.5 | ArtifactStore 生产化切入点 | package-artifact-release §4-8 | Protocol/LocalArtifactStore 代码已出现，待质量门禁 | 至少落地 LocalArtifactStore + manifest/package hash 绑定；S3/GitLab Registry 后移到 Phase 3 |
| 0.6 | ContextBuilder/Knowledge 主链路接入点确认 | agent-platform-core-design §3.2/3.8 | 代码已出现，测试通过，待质量门禁 | 明确 RuntimeManager 是直接调用 ContextBuilder，还是先在 Hermes/ConversationEngine 内部接入 |
| 0.7 | 文档事实源同步 | document-stage-map / implementation-gap | 实施中 | `implementation-gap.md`、`document-stage-map.md`、本计划对当前状态描述一致 |

### 当前质量门禁 Blockers

| Blocker | 影响 | 处理要求 |
|---|---|---|
| `scripts/validate_manifest.py` 失败 | Agent package/tool 动态加载链路不稳定 | 修复 `agents.myj...` 包路径导入问题 |
| `ruff check` 失败 | 不能提交 | 修复 Hermes 闭包变量、长行、尾随空白、测试变量名 |
| MCP/Admin/Approval 已有实验代码 | 提交范围不清 | 决定纳入当前提交，或标记为实验性并隔离默认路径 |

### 输出文档

```
docs/implementation-gap.md        — 更新为 S5 开工事实源
docs/document-stage-map.md        — 更新 S5 状态与入口
docs/development-plan-s5.md       — 本计划作为执行入口
```

---

## Phase 1：Runtime 能力补齐（预计 7-10 天）

**目标**：Hermes SDK 真接入（Spike B）；Knowledge/RAG 真实接入；模型调用增加 token/cost 统计。三条线可并行。

### 前置条件

- Phase 0 完成，且主链路可靠性风险已被关闭或显式接受。
- Hermes SDK 版本和官方 API 已重新验证。`hermes-agent>=0.13.0,<0.14` 只是 Spike 假设，未验证前不得作为生产 pin。

### 任务列表

| # | 任务 | 设计来源 | 验收标准 |
|---|---|---|---|
| **Hermes Spike B** | | | |
| 1.1 | 添加 hermes-agent 可选依赖 | hermes-backend-spike §11 | 确认官方包名和版本后在 `pyproject.toml` 增加 `hermes` optional dependency；无 SDK 时不影响启动 |
| 1.2 | 实现 Hermes 工具桥接 | hermes-backend-spike §11.3 | `register_platform_tools_to_hermes()` 将 ToolExecutor 包装为 Hermes global registry handler；agent_id 前缀防碰撞；run 结束后 deregister |
| 1.3 | 实现 `_run_with_hermes()` | hermes-backend-spike §11.4 | `HermesRuntimeBackend` 在 SDK 可用时调用真实 `AIAgent.run_conversation()`；同步调用通过 `anyio.to_thread.run_sync()` 包装 |
| 1.4 | Hermes 结果规范化 | hermes-backend-spike §11.5 | 提取 `final_response`、`api_calls`、`input_tokens`、`output_tokens`、`estimated_cost_usd`、tool_calls 并映射到 `AgentResponse` |
| 1.5 | Hermes fallback 与测试 | hermes-backend-spike §11.6 | SDK 不可用时自动回退到 Spike A 路径；integration test 验证非 stub 响应（SDK 存在时）；unit test 验证 fallback（SDK 缺失时） |
| **Knowledge/RAG 真实接入** | | | |
| 1.6 | 设计 Knowledge/RAG 架构 | implementation-gap §P1 | 新增 ADR 或设计小节，确定向量库选型（Weaviate vs Qdrant vs pgvector）、embedding 模型、同步策略和租户过滤 |
| 1.7 | 实现首个真实 KnowledgeBackend | knowledge service 现有接口 | 按 1.6 选型实现，不预设必须是 Weaviate；`retrieve()` 真实调用后端，`sync()` 触发数据同步 |
| 1.8 | Knowledge 数据同步 pipeline | — | 支持从本地文件/URL 导入文档；支持增量更新；支持 tenant 隔离 |
| 1.9 | Knowledge 集成测试 | — | manifest 声明 knowledge source → runtime 注入 snippets → agent 可使用检索结果 |
| **模型调用统计** | | | |
| 1.10 | ModelGateway token/cost tracking | implementation-gap §2.1 | `chat()` 返回值包含 `input_tokens`、`output_tokens`、`estimated_cost_usd`；数据写入 MetricsCollector |
| 1.11 | 多模型配置 | — | manifest 中 `models.chat_model` 可指定不同 provider/model；ModelGateway 支持按 provider_name 路由 |

**依赖关系**：(1.1 → 1.2 → 1.3 → 1.4 → 1.5) ∥ (1.6 → 1.7 → 1.8 → 1.9) ∥ (1.10 → 1.11)。三条线独立并行。

### 代码变更范围

```
pyproject.toml                              — 添加可选依赖
src/agent_platform/runtime/hermes.py        — Spike B 工具桥接 + _run_with_hermes()
src/agent_platform/knowledge/service.py     — WeaviateKnowledgeBackend 真实实现
src/agent_platform/knowledge/sync.py        — 新增数据同步 pipeline
src/agent_platform/runtime/model_gateway.py — token/cost tracking + 多 provider 路由
tests/integration/test_hermes_sdk.py        — 新增 Spike B 集成测试
tests/unit/test_hermes_fallback.py          — 新增 fallback unit test
tests/integration/test_knowledge_rag.py     — 新增 Knowledge 集成测试
```

---

## Phase 2：平台能力扩展（预计 7-10 天）

**目标**：MCP 集成暴露平台能力；OpenTelemetry 观测接入；SemanticRouter 自动规则加载。

### 前置条件

- Phase 1.10 完成（模型调用统计，为 trace 提供 token 数据）
- scoped API key / RBAC 的 endpoint enforcement 至少覆盖 MCP 暴露的高风险操作。

### 任务列表

| # | 任务 | 设计来源 | 验收标准 |
|---|---|---|---|
| **MCP 集成** | | | |
| 2.1 | MCP Server 设计 | implementation-gap §P2 | 确定暴露哪些能力（agent 注册/部署/回滚、DevFlow task pack、eval 运行、知识查询）以及每个 tool 所需 scope |
| 2.2 | 实现 MCP Server | — | 基于 `mcp` SDK 实现 server；通过 stdio/SSE transport 暴露 tools |
| 2.3 | MCP 工具定义 | — | 每个暴露的平台能力有对应的 MCP tool schema；支持认证（API key 传递） |
| 2.4 | MCP 集成测试 | — | Claude Code / 其他 MCP client 可通过 MCP 协议调用平台 API |
| **观测增强** | | | |
| 2.5 | OpenTelemetry 接入 | implementation-gap §P1 | 引入 `opentelemetry-sdk`；HTTP 请求自动生成 span；tool 调用生成子 span |
| 2.6 | Langfuse 可选集成 | — | 当 `LANGFUSE_PUBLIC_KEY` 设置时，trace 自动发送到 Langfuse；否则仅用 OTLP |
| 2.7 | Trace 事件模型与持久化 | — | 先定义 route/tool/model/runtime event schema，再写入 `AgentRunRepository`；`/api/v1/agent-runs/{run_id}/trace` 可查询 |
| **路由增强** | | | |
| 2.8 | SemanticRouter 规则自动加载 | implementation-gap §P1 | manifest `routing.rules` 在 agent 注册时自动加入 SemanticRouter；trace 记录命中原因 |
| 2.9 | 路由决策持久化 | — | route decision 作为结构化 trace event 写入 run store |

**依赖关系**：(2.1 → 2.2 → 2.3 → 2.4) ∥ (2.5 → 2.6 → 2.7) ∥ (2.8 → 2.9)。三条线独立并行。

### 代码变更范围

```
src/agent_platform/mcp/                     — 新增 MCP server 包
src/agent_platform/observability/tracing.py  — 新增 OpenTelemetry 集成
src/agent_platform/observability/langfuse.py — 新增 Langfuse 适配
src/agent_platform/router_semantic.py       — 自动加载 manifest routing rules
src/agent_platform/api/app.py               — MCP server 启动 + OTLP 配置
pyproject.toml                              — 添加 mcp, opentelemetry 依赖
```

---

## Phase 3：治理与运维（预计 7-10 天）

**目标**：Human-in-the-loop 审批；Admin API 基础；ArtifactStore Protocol 化。

### 前置条件

- Phase 2.5 完成（观测能力，为审批提供 trace 上下文）
- deploy/audit/artifact 的持久化边界已经在 Phase 0/2 中校准，审批记录不能只落内存。

### 任务列表

| # | 任务 | 设计来源 | 验收标准 |
|---|---|---|---|
| **Human-in-the-loop** | | | |
| 3.1 | ApprovalPolicy 设计 | implementation-gap §3.4 | 按环境、agent、工具风险、变更类型计算审批要求 |
| 3.2 | 高风险工具审批 gate | security-tenant-policy §7 | `RequiresApproval` 返回时，tool 执行暂停等待人工审批；支持 WebSocket 推送审批请求 |
| 3.3 | Deploy 审批绑定 | implementation-gap §2.5 | prod deploy 强制校验 GitLab MR approval + eval report；审批记录写入 audit log |
| **Admin API** | | | |
| 3.4 | Agent 管理 API | implementation-gap §P2 | CRUD agent packages；查看版本历史；查看部署状态；所有写操作要求 admin scope |
| 3.5 | Tenant 管理 API | — | 创建/更新 tenant；配置 tool permissions；管理 API keys |
| 3.6 | DevFlow 管理 API | — | 查看 job 列表/详情；手动触发/取消 runner；查看 workspace 日志 |
| **Artifact 增强** | | | |
| 3.7 | ArtifactStore Protocol 化 | package-artifact-release §4 | 抽取 `ArtifactStore` 为 Protocol（upload/download/exists/get_metadata/list_versions） |
| 3.8 | LocalArtifactStore | — | 基于本地文件系统的实现；manifest_sha256 + package_sha256 绑定 |
| 3.9 | S3/GitLab Registry 后端 | — | 可选远程存储后端；部署时从 registry 拉取 |

**依赖关系**：(3.1 → 3.2 → 3.3) ∥ (3.4 ∥ 3.5 ∥ 3.6) ∥ (3.7 → 3.8 → 3.9)。

### 代码变更范围

```
src/agent_platform/policy/approval.py       — 新增 ApprovalPolicy
src/agent_platform/tools/executor.py        — 审批 gate 集成
src/agent_platform/api/admin.py             — 新增 Admin API router
src/agent_platform/registry/artifact.py     — Protocol 化 + 多后端
src/agent_platform/registry/artifact_local.py — 新增本地文件后端
```

---

## 总体时间线

```
Week 0            Week 1-2          Week 3-4          Week 5-6
|-- Phase 0 ------|-- Phase 1 ------|-- Phase 2 ------|-- Phase 3 ------|
  主链路校准         Runtime 补齐       平台扩展          治理与运维
  持久化/审计/文档    Hermes B + RAG     MCP + OTLP       HITL + Admin
  2-4d              7-10d             7-10d            7-10d
```

## 里程碑

| 里程碑 | 时间 | 标志 |
|---|---|---|
| M4.5：主链路事实源可信 | Week 0 末 | 测试基线、实现差距、阶段地图一致；webhook/audit/artifact/registry 风险明确关闭或接受 |
| M5：非 stub runtime | Week 2 末 | Hermes SDK 真实执行 agent；Knowledge 真实检索；模型调用有 token 统计 |
| M6：平台可集成 | Week 4 末 | MCP server 可被外部工具调用；OpenTelemetry trace 可导出；路由规则自动加载 |
| M7：生产治理就绪 | Week 6 末 | 高风险操作有审批；Admin API 可管理 agent/tenant；Artifact 有远程存储 |

## 不在此计划范围内（后续阶段）

| 工作项 | 阶段 | 原因 |
|---|---|---|
| Admin Web UI（前端） | S6 | S5 先提供 Admin API，前端 UI 后续跟进 |
| 多租户计费/配额 | S6 | 需要先完成 token 统计和 tenant 管理 |
| 蓝绿/灰度真实流量控制 | S6 | 需要先完成 deploy 审批和 artifact registry |
| 分布式 Job Queue | S6 | CodingAgentRunner 当前同步执行，规模化后需要异步队列 |
| Hermes memory 持久化 | S6 | Spike B 使用 skip_memory=True；后续需映射到平台 session store |

## 与 Plane Work Item 的对应

| Plane Work Item | Phase | 类型 |
|---|---|---|
| S5 主链路可靠性校准 | 0 | platform:infra |
| Plane webhook DevFlow 触发路径修复 | 0 | platform:devflow |
| Registry/Deployment/Audit 持久化接入 | 0 | platform:release |
| LocalArtifactStore + hash 绑定 | 0 | platform:release |
| ContextBuilder/Knowledge 主链路接入点确认 | 0 | platform:runtime |
| Hermes SDK 真接入（Spike B） | 1 | platform:runtime |
| Knowledge/RAG 真实接入 | 1 | platform:runtime |
| ModelGateway token/cost 统计 + 多模型路由 | 1 | platform:runtime |
| MCP Server 实现 | 2 | platform:integration |
| OpenTelemetry + Langfuse 观测接入 | 2 | platform:observability |
| SemanticRouter 自动规则加载 | 2 | platform:routing |
| Human-in-the-loop 审批机制 | 3 | platform:security |
| Admin API（agent/tenant/devflow 管理） | 3 | platform:api |
| ArtifactStore Protocol 化 + 多后端 | 3 | platform:release |
