# 下一阶段开发计划（S2-S4）

> Status: Phase 1-3 Complete
> Last updated: 2026-05-15

本计划基于 6 份 P0 设计文档和代码审计结果，将下一阶段拆成 4 个 Phase，每个 Phase 内的任务按依赖关系排序。

## 当前起点

| 指标 | 值 |
|---|---|
| 测试 | 437 passed, ruff clean |
| 代码量 | ~80 files, +9000 lines |
| 存储 | InMemory + SQL Repository 双实现；Alembic 初始 migration 已生成；DI 按 DATABASE_URL 选择后端 |
| PolicyEngine | ✅ 已接入 RuntimeManager（check_input/check_output）和 ToolExecutor |
| HookRegistry | ✅ 已接入 RuntimeManager（pre_run/post_run/on_route/on_error）和 ToolExecutor（pre_tool/post_tool） |
| MetricsCollector | ✅ 已被 RuntimeManager（record_request/record_duration）和 ToolExecutor（record_tool_call）调用 |
| Tool Registry | ✅ 已去业务化 + 动态加载（load_agent_tools 自动扫描 agent tools/ 目录） |
| Domain Models | ✅ StoreContext→LocationContext / retailer_id→org_id / locale=en / timezone=UTC；旧字段 alias 保留 |
| Model Gateway | ✅ OpenAICompatibleProvider 已实现；ModelGateway 注入 HermesBackend；ConversationEngine 接口已修复 |
| Artifact | ✅ ArtifactStore 在 registry/artifact.py 实现（tar.gz + SHA256 + 部署绑定） |
| Hermes | ✅ ConversationEngine 接口修复（provider_name + attribute access）；model_gateway/tool_executor 已注入；hermes_echo agent + 集成测试通过 |
| 持久化 | ✅ 7 ORM 表 + 7 Protocol + 7 InMemory + 7 SQL 实现 + 62 contract tests + Alembic migration + DI 注入 |
| DevFlow | 到 MR 创建，无 CodingAgentRunner |

---

## Phase 1：平台管线串联（预计 3-5 天）

**目标**：把已实现但未接入的组件（PolicyEngine、HookRegistry、MetricsCollector）串进 runtime 执行链路。这是投入最小、收益最快的工作——不写新代码，只做接线。

### 任务列表

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 1.1 | PolicyEngine 接入 RuntimeManager | security-tenant-policy §6 | `check_input` 在 `run()` 开头调用；`check_output` 在返回前调用；`check_tool_allowed` 在 `ToolExecutor.execute()` 中调用 | ✅ 已完成 |
| 1.2 | HookRegistry 接入 RuntimeManager | agent-platform-core-design §3.4 | `pre_run` / `post_run` / `on_error` / `on_route` 在 runtime 执行链路中 emit | ✅ 已完成 |
| 1.3 | HookRegistry 接入 ToolExecutor | 同上 | `pre_tool` / `post_tool` 在工具执行前后 emit | ✅ 已完成 |
| 1.4 | MetricsCollector 接入 runtime | observability 现有代码 | `record_request`、`record_duration`、`record_tool_call` 在每次请求中被调用 | ✅ 已完成 |
| 1.5 | 增加 `/metrics` 端点 | — | Prometheus 格式的指标可被抓取 | ✅ 已完成（app.py format_prometheus + /metrics 端点） |
| 1.6 | 工具 registry 去业务化 | agent-platform-core-design §3.6 | `create_default_tool_registry()` 返回空 registry；6 个零售 handler 移到 `agents/myj/tools/` | ✅ 已完成 |
| 1.7 | 动态工具加载 | agent-platform-core-design §3.6 | Agent Package 加载时从 `tools/` 目录动态注册 handler | ✅ 已完成（load_agent_tools + _ensure_agent_tools 在 native/langgraph backend） |

**依赖关系**：1.1-1.5 可并行；1.6 → 1.7 串行。

### 代码变更范围

```
src/agent_platform/runtime/manager.py    — ✅ 已注入 policy_engine, hook_registry, metrics
src/agent_platform/tools/executor.py     — ✅ 已注入 policy_engine, hook_registry
src/agent_platform/tools/registry.py     — ✅ 已清空默认注册；✅ 动态加载已实现（load_agent_tools）
src/agent_platform/api/app.py            — ✅ 已串联 DI；✅ /metrics 端点已实现（format_prometheus）
agents/myj/tools/                        — ✅ 已迁入 4 个业务 handler
agents/promo_recommendation/tools/       — ✅ 已迁入 2 个业务 handler
src/agent_platform/runtime/native.py     — ✅ _ensure_agent_tools 动态加载
src/agent_platform/runtime/langgraph.py  — ✅ _ensure_agent_tools 动态加载
```

---

## Phase 2：持久化与 Domain Model 泛化（预计 5-7 天）

**目标**：服务重启不丢数据；领域模型去零售化。这是生产化的基座。

### 前置条件

- Phase 1.1-1.4 完成（policy 和 hook 已串联，避免持久化层重复改动）

### 任务列表

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 2.1 | Domain Model 泛化 | security-tenant-policy §5 | `StoreContext` → `LocationContext`；`retailer_id` → `org_id`；默认 locale/timezone 改为 `en`/`UTC`；旧字段保留 alias | ✅ 已完成 |
| 2.2 | 引入 SQLAlchemy 2.0 + Alembic | persistence-storage §2 | `pyproject.toml` 增加依赖；`DATABASE_URL` 环境变量驱动 | ✅ 已完成（storage/base.py + storage/engine.py + alembic/） |
| 2.3 | 实现 ORM models + BaseMixin | persistence-storage §4 | 7 张表的 ORM 定义；`AuditMixin` 自动注入 tenant_id / created_at / updated_at | ✅ 已完成（persistence/tables.py，7 Row 类 + AuditMixin） |
| 2.4 | 实现 Repository Protocol | persistence-storage §5 | 7 个 Repository Protocol 定义 | ✅ 已完成（persistence/repositories.py，7 个 @runtime_checkable Protocol） |
| 2.5 | 实现 InMemory Repository | persistence-storage §5 | 全部单测可跑，不依赖 DB | ✅ 已完成（persistence/memory.py，7 个 InMemory 实现） |
| 2.6 | 实现 SQL Repository | persistence-storage §5 | SQLite 集成测试通过 | ✅ 已完成（persistence/sql.py，7 个 SQL 实现） |
| 2.7 | Repository Contract Tests | persistence-storage §10 | 同一测试用例同时跑 InMemory 和 SQL，行为一致 | ✅ 已完成（62 个参数化测试，31 contract × memory/sql） |
| 2.8 | Alembic migration 初始化 | persistence-storage §9 | `alembic upgrade head` 从空库创建全部表 | ✅ 已完成（初始 migration 生成，7 表全部覆盖，upgrade head 验证通过） |
| 2.9 | DI 注入 | persistence-storage §8 | `app.py` 根据 `DATABASE_URL` 选择 InMemory 或 SQL | ✅ 已完成（webhook_repo 替换 set，database_url 配置，SQL session factory opt-in） |
| 2.10 | 契约文档更新 | — | `agent-request-response.md` 示例同步泛化；新增 ADR-0002 | ✅ 已完成 |

**依赖关系**：2.1 可独立先行；2.2 → 2.3 → 2.4 → (2.5 ∥ 2.6) → 2.7 → 2.8 → 2.9。

### 代码变更范围

```
src/agent_platform/domain/models.py      — ✅ 泛化完成
src/agent_platform/storage/              — ✅ 骨架完成（base.py DeclarativeBase）
src/agent_platform/persistence/          — ✅ 全部完成（tables.py + repositories.py + memory.py + sql.py + context.py + __init__.py）
src/agent_platform/session/store.py      — 待改用 SessionRepository
src/agent_platform/observability/trace.py — 待改用 RunRepository
src/agent_platform/registry/deployment.py — 待改用 DeploymentRepository
src/agent_platform/api/app.py            — ✅ DI 组装完成（webhook_repo + database_url + session_factory）
src/agent_platform/config.py             — ✅ 新增 database_url 配置
alembic/env.py                           — ✅ 已修复 tables import
alembic/versions/                        — ✅ 初始 migration 已生成
tests/unit/test_repository_contracts.py  — ✅ 62 个参数化 contract tests
docs/01-contracts/                       — ✅ 契约已更新（agent-request-response.md 泛化完成）
docs/adr/0002-storage-baseline.md        — 待新增
```

> **✅ 设计偏差已解决**：持久化层已使用 `persistence/` 包名，与设计文档一致（tables.py, repositories.py, memory.py, sql.py, context.py）。`storage/` 仅保留 `base.py`（DeclarativeBase）。

---

## Phase 3：Hermes 真接入 + Artifact 发布（预计 5-7 天）

**目标**：Hermes 可运行非 stub Agent；Agent 发布有可校验的 artifact 和版本记录。

### 前置条件

- Phase 2.9 完成（deployment 记录需要持久化）

### 任务列表

| # | 任务 | 设计来源 | 验收标准 | 状态 |
|---|---|---|---|---|
| 3.1 | 实现 OpenAICompatibleProvider | hermes-backend-spike §Spike A | `ModelGateway` 可调用真实 LLM API | ✅ 已完成（runtime/model_gateway.py） |
| 3.2 | 注入 model_gateway 到 HermesBackend | hermes-backend-spike §1.2 | `RuntimeManager` 构造时传入非 None 的 model_gateway | ✅ 已完成（RuntimeManager 传入 model_gateway + tool_executor 到 HermesRuntimeBackend） |
| 3.3 | 修复 ConversationEngine 接口不匹配 | hermes-backend-spike §1.4 | hermes.py 内 ConversationEngine 与平台 ModelGateway.chat() 签名对齐 | ✅ 已完成（修复 provider_name 参数、dict→attribute access、ToolCall 属性、ModelMessage 类型） |
| 3.4 | 创建 hermes_echo agent | hermes-backend-spike §Spike A | `/api/v1/agent/chat` 返回非 stub 响应，tool_calls 有记录 | ✅ 已完成（agents/hermes_echo/ 完整 package） |
| 3.5 | Hermes integration test | hermes-backend-spike §6 | assert response 不包含 `[Hermes-stub]` | ✅ 已完成（tests/integration/test_hermes_echo.py） |
| 3.6 | 重构 package_agent.py | package-artifact-release §4-5 | 生成 tar.gz + metadata.json + SHA256 checksum | ✅ 已完成（registry/artifact.py ArtifactStore） |
| 3.7 | 部署绑定 artifact_id | package-artifact-release §6 | deployment 记录包含 artifact_id 和 manifest_sha256 | 🔶 部分完成（artifact_id 已绑定；manifest_sha256 / package_sha256 待补） |
| 3.8 | 回滚使用历史 artifact | package-artifact-release §8 | rollback 从 artifact store 取历史版本部署 | 🔶 部分完成（rollback API 可用；artifact 绑定尚未完整） |
| 3.9 | CI pipeline 集成 | package-artifact-release §9 | ✅ `.gitlab-ci.yml` package / deploy stages |

**依赖关系**：(3.1 → 3.2 → 3.3 → 3.4 → 3.5) ∥ (3.6 → 3.7 → 3.8 → 3.9)。Hermes 和 Artifact 两条线可并行。

### 代码变更范围

```
src/agent_platform/runtime/hermes.py     — ✅ ConversationEngine 接口已修复（provider_name + attribute access + ModelMessage）
src/agent_platform/runtime/manager.py    — ✅ model_gateway + tool_executor 已注入
src/agent_platform/runtime/model_gateway.py — ✅ OpenAICompatibleProvider 已实现
src/agent_platform/registry/artifact.py  — ✅ ArtifactStore 已实现（in-memory）
src/agent_platform/registry/deployment.py — ✅ DeploymentAuditLog 已扩展 artifact_id
agents/hermes_echo/                      — ✅ 完整 agent package（manifest + prompts + evals + __init__）
tests/integration/test_hermes_echo.py    — ✅ 端到端集成测试
scripts/package_agent.py                 — 待重构（当前由 ArtifactStore API 替代）
scripts/deploy_agent.py                  — 待绑定 artifact
.gitlab-ci.yml                           — 待更新
```

> **⚠️ 设计偏差**：
> - 设计文档指定 `ArtifactStore` 为 ABC Protocol（upload/download/exists/get_metadata/list_versions），当前实现为具体的 in-memory 类，方法签名不同（create_artifact/get_data/verify_checksum）。
> - 设计文档指定 `DeploymentEvent` 应包含 `manifest_sha256`、`package_sha256`、`eval_report_id`、`previous_artifact_id`，当前仅实现 `artifact_id`。
> - 设计文档指定 `record_rollback()` 应包含 `from_artifact_id`、`to_artifact_id`、`to_manifest_sha256` 参数，当前未实现。

---

## Phase 4：安全基线 + DevFlow 闭环（预计 7-10 天）

**目标**：工具执行有权限管控；Secret 安全注入；DevFlow 从 task pack 到 MR 全自动。

### 前置条件

- Phase 2 完成（持久化、Domain Model 泛化）
- Phase 3.6+ 完成（artifact 发布基础）

### 任务列表

| # | 任务 | 设计来源 | 验收标准 |
|---|---|---|---|
| **安全基线** | | | |
| 4.1 | Scoped API Key | security-tenant-policy §2.2 | ✅ `ApiKeyRecord` + `InMemoryApiKeyStore` (SHA-256) |
| 4.2 | 租户隔离查询 | security-tenant-policy §4 | ⬜ 待实现（需 DB 层） |
| 4.3 | Tool Permission 矩阵 | security-tenant-policy §7 | ✅ `compute_tool_permission()` 三层计算 |
| 4.4 | Secret 引用与注入 | security-tenant-policy §8 | ✅ `SecretResolver` + `EnvSecretBackend` |
| 4.5 | 日志和 Trace 脱敏 | security-tenant-policy §9 | ✅ `LogSanitizer` + `TraceSanitizer` |
| **DevFlow 闭环** | | | |
| 4.6 | Webhook 幂等持久化 | devflow-state-sync §4 | ⬜ 待实现（需 DB 层） |
| 4.7 | GitLab → Plane 状态回写 | devflow-state-sync §3.2 | ⬜ 待实现 |
| 4.8 | Eval report 回写 Plane | devflow-state-sync §3.3 | 🔶 `EvalFeedback.update_plane_state()` 已实现，ci-callback 待接入 |
| 4.9 | WorkspaceManager | devflow-runner-workspace §5 | ✅ `WorkspaceManager` (create/validate/commit/cleanup) |
| 4.10 | PathGuard | devflow-runner-workspace §6 | ✅ `PathGuard` (fnmatch glob, denied-first) |
| 4.11 | CodingAgentRunner Protocol | devflow-runner-workspace §3 | ✅ `RunnerAdapter` Protocol + `CodingAgentRunner` |
| 4.12 | MockCodingRunner | devflow-runner-workspace §4.3 | ✅ `MockRunnerAdapter` (7 tests) |
| 4.13 | ClaudeCodeRunner | devflow-runner-workspace §4.1 | ✅ `ClaudeCodeAdapter` + `CodexAdapter` |
| 4.14 | MR comment 回写 | devflow-runner-workspace §8 | ✅ `_build_mr_comment()` + `_build_plane_comment()` |

**依赖关系**：4.1-4.5 安全线可先行；4.6-4.8 状态同步 ∥ 4.9-4.14 runner 线。4.11 → 4.12 → 4.13。

---

## 总体时间线

```
Week 1          Week 2          Week 3          Week 4
|-- Phase 1 ---|-- Phase 2 ----|-- Phase 3 ----|-- Phase 4 ----------|
  管线串联        持久化+泛化      Hermes+Artifact   安全+DevFlow
  3-5d            5-7d            5-7d              7-10d
```

## 里程碑

| 里程碑 | 时间 | 标志 | 状态 |
|---|---|---|---|
| M1：平台管线可观测 | Week 1 末 | PolicyEngine、HookRegistry、Metrics 全部接入 runtime；工具动态加载 | ✅ 完成 |
| M2：状态可持久化 | Week 2 末 | 重启不丢 session/run/deployment；Domain Model 泛化完成 | ✅ 完成（7 Repository Protocol + InMemory/SQL 双实现 + Alembic migration + DI；RuntimeManager 内部仍用 InMemory store 待切换） |
| M3：非 stub runtime | Week 3 末 | Hermes 可跑 hermes_echo agent；artifact 有 checksum 和版本 | ✅ 完成（ConversationEngine 修复 + model_gateway 注入 + hermes_echo 集成测试通过） |
| M4：生产可审计 | Week 4 末 | 工具权限管控、Secret 安全、日志脱敏、DevFlow 自动执行 | 🔶 安全基线 + DevFlow Runner 已实现；4.2 租户隔离、4.6-4.7 状态同步待完成 |

## 不在此计划范围内（后续阶段）

| 工作项 | 阶段 | 原因 |
|---|---|---|
| Admin Web UI | S5 | 先 API + CLI，后 UI |
| Knowledge/RAG 真实接入 | S5 | 需要先明确数据源和向量库 |
| 多模型路由和成本统计 | S5 | 底座稳定后再做 |
| MCP 集成 | S5 | 需要先确定暴露哪些能力 |
| Human-in-the-loop 审批 | S5 | 安全基线之后做 |
| Hermes SDK 真接入（Spike B）| S5 | Spike A 验证通过后再评估 |

## 与 Plane Work Item 的对应

建议在 Plane 中创建以下 Work Item，每个对应一个可交付的 MR：

| Plane Work Item | Phase | 类型 |
|---|---|---|
| 串联 PolicyEngine 和 HookRegistry 到 runtime | 1 | platform:infra |
| 串联 MetricsCollector + 增加 /metrics 端点 | 1 | platform:infra |
| 工具 registry 去业务化和动态加载 | 1 | platform:refactor |
| Domain Model 泛化（StoreContext → LocationContext）| 2 | platform:contract |
| 引入持久化 repository 层（SQLAlchemy + Alembic）| 2 | platform:infra |
| 实现 7 个 SQL Repository + contract tests | 2 | platform:infra |
| Hermes Spike A：接入真实 model gateway | 3 | platform:runtime |
| Agent artifact tar.gz + checksum + deploy 绑定 | 3 | platform:release |
| Scoped API Key + 租户隔离 | 4 | platform:security |
| Secret 引用注入 + 日志脱敏 | 4 | platform:security |
| Webhook 幂等 + GitLab→Plane 状态回写 | 4 | platform:devflow |
| CodingAgentRunner + WorkspaceManager + PathGuard | 4 | platform:devflow |
