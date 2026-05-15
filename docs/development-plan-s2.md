# 下一阶段开发计划（S2-S4）

> Status: Draft
> Last updated: 2026-05-15

本计划基于 6 份 P0 设计文档和代码审计结果，将下一阶段拆成 4 个 Phase，每个 Phase 内的任务按依赖关系排序。

## 当前起点

| 指标 | 值 |
|---|---|
| 测试 | 375 passed, ruff clean |
| 代码量 | ~70 files, +7777 lines |
| 存储 | 全部 in-memory |
| PolicyEngine | 已实现，未接入 runtime |
| HookRegistry | 已实现，未接入 runtime |
| MetricsCollector | 已实现，未被 runtime 调用 |
| Tool Registry | 6 个零售 stub 硬编码 |
| Hermes | agentic loop 完整，model_gateway=None |
| DevFlow | 到 MR 创建，无 CodingAgentRunner |
| Domain Models | StoreContext / retailer_id / zh-CN 硬编码 |

---

## Phase 1：平台管线串联（预计 3-5 天）

**目标**：把已实现但未接入的组件（PolicyEngine、HookRegistry、MetricsCollector）串进 runtime 执行链路。这是投入最小、收益最快的工作——不写新代码，只做接线。

### 任务列表

| # | 任务 | 设计来源 | 验收标准 |
|---|---|---|---|
| 1.1 | PolicyEngine 接入 RuntimeManager | security-tenant-policy §6 | `check_input` 在 `run()` 开头调用；`check_output` 在返回前调用；`check_tool_allowed` 在 `ToolExecutor.execute()` 中调用 |
| 1.2 | HookRegistry 接入 RuntimeManager | agent-platform-core-design §3.4 | `pre_run` / `post_run` / `on_error` / `on_route` 在 runtime 执行链路中 emit |
| 1.3 | HookRegistry 接入 ToolExecutor | 同上 | `pre_tool` / `post_tool` 在工具执行前后 emit |
| 1.4 | MetricsCollector 接入 runtime | observability 现有代码 | `record_request`、`record_duration`、`record_tool_call` 在每次请求中被调用 |
| 1.5 | 增加 `/metrics` 端点 | — | Prometheus 格式的指标可被抓取 |
| 1.6 | 工具 registry 去业务化 | agent-platform-core-design §3.6 | `create_default_tool_registry()` 返回空 registry；6 个零售 handler 移到 `agents/myj/tools/` |
| 1.7 | 动态工具加载 | agent-platform-core-design §3.6 | Agent Package 加载时从 `tools/` 目录动态注册 handler |

**依赖关系**：1.1-1.5 可并行；1.6 → 1.7 串行。

### 代码变更范围

```
src/agent_platform/runtime/manager.py    — 注入 policy_engine, hook_registry, metrics
src/agent_platform/tools/executor.py     — 注入 policy_engine, hook_registry
src/agent_platform/tools/registry.py     — 清空默认注册，增加动态加载
src/agent_platform/api/app.py            — 增加 /metrics 端点，串联 DI
agents/myj/tools/                        — 迁入 6 个业务 handler
```

---

## Phase 2：持久化与 Domain Model 泛化（预计 5-7 天）

**目标**：服务重启不丢数据；领域模型去零售化。这是生产化的基座。

### 前置条件

- Phase 1.1-1.4 完成（policy 和 hook 已串联，避免持久化层重复改动）

### 任务列表

| # | 任务 | 设计来源 | 验收标准 |
|---|---|---|---|
| 2.1 | Domain Model 泛化 | security-tenant-policy §5 | `StoreContext` → `LocationContext`；`retailer_id` → `org_id`；默认 locale/timezone 改为 `en`/`UTC`；旧字段保留 alias |
| 2.2 | 引入 SQLAlchemy 2.0 + Alembic | persistence-storage §2 | `pyproject.toml` 增加依赖；`DATABASE_URL` 环境变量驱动 |
| 2.3 | 实现 ORM models + BaseMixin | persistence-storage §4 | 7 张表的 ORM 定义；`AuditMixin` 自动注入 tenant_id / created_at / updated_at |
| 2.4 | 实现 Repository Protocol | persistence-storage §5 | 7 个 Repository Protocol 定义 |
| 2.5 | 实现 InMemory Repository | persistence-storage §5 | 全部单测可跑，不依赖 DB |
| 2.6 | 实现 SQL Repository | persistence-storage §5 | SQLite 集成测试通过 |
| 2.7 | Repository Contract Tests | persistence-storage §10 | 同一测试用例同时跑 InMemory 和 SQL，行为一致 |
| 2.8 | Alembic migration 初始化 | persistence-storage §9 | `alembic upgrade head` 从空库创建全部表 |
| 2.9 | DI 注入 | persistence-storage §8 | `app.py` 根据 `DATABASE_URL` 选择 InMemory 或 SQL |
| 2.10 | 契约文档更新 | — | `agent-request-response.md` 示例同步泛化；新增 ADR-0002 |

**依赖关系**：2.1 可独立先行；2.2 → 2.3 → 2.4 → (2.5 ∥ 2.6) → 2.7 → 2.8 → 2.9。

### 代码变更范围

```
src/agent_platform/domain/models.py      — 泛化字段
src/agent_platform/storage/              — 新建整个 package
src/agent_platform/session/store.py      — 改用 SessionRepository
src/agent_platform/observability/trace.py — 改用 RunRepository
src/agent_platform/registry/deployment.py — 改用 DeploymentRepository
src/agent_platform/api/app.py            — DI 组装
docs/01-contracts/                       — 契约更新
docs/adr/0002-storage-baseline.md        — 新增
```

---

## Phase 3：Hermes 真接入 + Artifact 发布（预计 5-7 天）

**目标**：Hermes 可运行非 stub Agent；Agent 发布有可校验的 artifact 和版本记录。

### 前置条件

- Phase 2.9 完成（deployment 记录需要持久化）

### 任务列表

| # | 任务 | 设计来源 | 验收标准 |
|---|---|---|---|
| 3.1 | 实现 OpenAICompatibleProvider | hermes-backend-spike §Spike A | `ModelGateway` 可调用真实 LLM API |
| 3.2 | 注入 model_gateway 到 HermesBackend | hermes-backend-spike §1.2 | `RuntimeManager` 构造时传入非 None 的 model_gateway |
| 3.3 | 修复 ConversationEngine 接口不匹配 | hermes-backend-spike §1.4 | hermes.py 内 ConversationEngine 与平台 ModelGateway.chat() 签名对齐 |
| 3.4 | 创建 hermes_echo agent | hermes-backend-spike §Spike A | `/api/v1/agent/chat` 返回非 stub 响应，tool_calls 有记录 |
| 3.5 | Hermes integration test | hermes-backend-spike §6 | assert response 不包含 `[Hermes-stub]` |
| 3.6 | 重构 package_agent.py | package-artifact-release §4-5 | 生成 tar.gz + metadata.json + SHA256 checksum |
| 3.7 | 部署绑定 artifact_id | package-artifact-release §6 | deployment 记录包含 artifact_id 和 manifest_sha256 |
| 3.8 | 回滚使用历史 artifact | package-artifact-release §8 | rollback 从 artifact store 取历史版本部署 |
| 3.9 | CI pipeline 集成 | package-artifact-release §9 | `.gitlab-ci.yml` 增加 package / deploy 阶段 |

**依赖关系**：(3.1 → 3.2 → 3.3 → 3.4 → 3.5) ∥ (3.6 → 3.7 → 3.8 → 3.9)。Hermes 和 Artifact 两条线可并行。

### 代码变更范围

```
src/agent_platform/runtime/hermes.py     — 修复接口对齐
src/agent_platform/runtime/manager.py    — model_gateway 注入
src/agent_platform/models/               — OpenAICompatibleProvider
agents/hermes_echo/                      — 新 agent
scripts/package_agent.py                 — 重构
scripts/deploy_agent.py                  — 绑定 artifact
.gitlab-ci.yml                           — 更新
```

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
| 4.1 | Scoped API Key | security-tenant-policy §2.2 | `ApiKeyRecord` 持久化，按 tenant_id + role 校验 |
| 4.2 | 租户隔离查询 | security-tenant-policy §4 | 所有 Repository 查询携带 tenant_id 条件 |
| 4.3 | Tool Permission 矩阵 | security-tenant-policy §7 | `manifest_allow ∩ tenant_policy ∩ env_policy` 三层计算 |
| 4.4 | Secret 引用与注入 | security-tenant-policy §8 | `$secret:KEY` 格式在 manifest 中使用；runtime 注入；不进入 trace/log |
| 4.5 | 日志和 Trace 脱敏 | security-tenant-policy §9 | PII 正则替换；secret 值自动 `[REDACTED]` |
| **DevFlow 闭环** | | | |
| 4.6 | Webhook 幂等持久化 | devflow-state-sync §4 | `webhook_deliveries` 表去重，重复 webhook 不重复创建 MR |
| 4.7 | GitLab → Plane 状态回写 | devflow-state-sync §3.2 | pipeline fail 回写 Plane comment + 状态 |
| 4.8 | Eval report 回写 Plane | devflow-state-sync §3.3 | eval 结果写入 Plane custom property |
| 4.9 | WorkspaceManager | devflow-runner-workspace §5 | 创建隔离 workspace，clone + checkout |
| 4.10 | PathGuard | devflow-runner-workspace §6 | 校验变更文件在 whitelist 内 |
| 4.11 | CodingAgentRunner Protocol | devflow-runner-workspace §3 | 定义统一接口 |
| 4.12 | MockCodingRunner | devflow-runner-workspace §4.3 | mock runner 可从 task pack 生成完整 result |
| 4.13 | ClaudeCodeRunner | devflow-runner-workspace §4.1 | 真实调用 claude CLI 执行 task pack |
| 4.14 | MR comment 回写 | devflow-runner-workspace §8 | 变更文件列表 + 校验结果写入 MR comment |

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

| 里程碑 | 时间 | 标志 |
|---|---|---|
| M1：平台管线可观测 | Week 1 末 | PolicyEngine、HookRegistry、Metrics 全部接入 runtime；工具动态加载 |
| M2：状态可持久化 | Week 2 末 | 重启不丢 session/run/deployment；Domain Model 泛化完成 |
| M3：非 stub runtime | Week 3 末 | Hermes 可跑 hermes_echo agent；artifact 有 checksum 和版本 |
| M4：生产可审计 | Week 4 末 | 工具权限管控、Secret 安全、日志脱敏、DevFlow 自动执行 |

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
