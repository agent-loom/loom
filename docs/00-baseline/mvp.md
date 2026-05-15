# MVP 范围与验收标准

本文档定义 Agent Platform 第一阶段正式开发的边界。MVP 的目标不是一次性做完整平台，而是跑通“一个业务 Agent 从 manifest 注册到生产调用、评测、研发任务流”的最小闭环。

## 1. MVP 目标

第一阶段需要证明以下事情成立：

1. 平台可以承载多个业务 Agent 的注册模型。
2. 前端或调用方可以通过统一 API 调用 Agent。
3. 平台可以根据 `agent_id`、租户、渠道路由到正确 Agent。
4. Agent 的 prompt、工具、知识源、运行时和评测可以通过 manifest 声明。
5. 一个 Agent 变更可以通过 GitLab MR、CI、manifest 校验和 eval 门禁进入 staging。
6. AI coding agent 可以基于结构化 task pack 执行开发，而不是直接根据口头需求改全仓库。

## 2. MVP 包含内容

### 2.1 生产运行面

| 模块 | MVP 范围 |
| --- | --- |
| Agent API | 提供 `POST /api/v1/agent/chat` |
| Agent Router | 支持按 `agent_id`、`tenant.retailer_id`、默认 Agent 路由 |
| Agent Registry | 支持本地文件或数据库注册 Agent |
| Manifest Loader | 支持加载和校验 `agent.platform/v1` |
| Runtime Backend | 先实现 `NativeRuntimeBackend` |
| Tool Registry | 支持注册工具 schema、handler、timeout、权限元数据 |
| Eval Runner | 支持读取 YAML eval case 并输出 JSON report |
| Trace | 记录 request_id、agent_id、version、route、tools、latency、error |

### 2.2 研发流程面

| 模块 | MVP 范围 |
| --- | --- |
| DevFlow Task Pack | 定义 coding agent 的结构化输入 |
| GitLab Adapter | 支持创建分支、创建 MR、评论 MR、读取 pipeline 状态 |
| CI 契约 | 支持 lint、unit test、manifest validate、agent eval |
| 人审机制 | MR checklist + required approval |
| Plane Adapter | 接入已部署 Plane，支持读取/更新 Work Item、评论、状态和最小 webhook |

### 2.3 第一个 Agent

MVP 以 `myj` 作为第一个业务 Agent Package，但不要求立刻完整迁移现有项目。

第一版 `myj` package 目标：

1. 有 `agents/myj/manifest.yaml`。
2. 能通过 manifest loader 校验。
3. 有最小 prompt 和 eval case。
4. Runtime 可以返回标准 `AgentResponse`。
5. 后续可以逐步接入真实 MYJ 后端、工具和 RAG。

## 3. MVP 不包含内容

第一阶段明确不做：

1. 完整 Web 管理后台。
2. 自动生产全量发布。
3. 多 coding agent 并发调度。
4. 完整 Plane 双向同步。
5. Agent marketplace。
6. 深度 fork Hermes。
7. 完整替换 `myj` 现有业务逻辑。
8. 复杂多租户计费和用量结算。
9. 完整权限后台。
10. 大规模分布式工具执行。

## 4. 技术栈建议

MVP 推荐：

| 类别 | 选择 |
| --- | --- |
| 后端 | Python + FastAPI |
| 数据模型 | Pydantic |
| ORM | SQLAlchemy 或 SQLModel |
| 数据库 | PostgreSQL，开发期可 SQLite |
| 缓存 / 队列 | Redis，开发期可先不用 |
| 测试 | pytest |
| API 文档 | OpenAPI |
| CI | GitLab CI |
| Agent Runtime | NativeRuntimeBackend，后续接 Hermes / LangGraph |
| Trace / Eval | 先本地 JSON report，后续接 Langfuse |

## 5. MVP API

MVP 至少实现：

```http
POST /api/v1/agent/chat
POST /api/v1/agent-packages/register
GET  /api/v1/agents
POST /api/v1/evals/run
POST /api/v1/devflow/task-packs
```

可以先不实现完整鉴权，但接口结构必须预留：

```http
Authorization: Bearer <token>
X-Tenant-ID: <tenant_id>
X-Request-ID: <request_id>
```

## 6. MVP 验收标准

### 6.1 生产链路验收

必须能完成：

1. 注册 `myj` manifest。
2. 调用 `POST /api/v1/agent/chat`。
3. Router 命中 `myj`。
4. Runtime 返回标准 `AgentResponse`。
5. 记录 `AgentRun`。
6. 输出 trace。

### 6.2 评测链路验收

必须能完成：

1. 从 `agents/myj/evals/*.yaml` 读取 eval cases。
2. 执行 eval。
3. 输出 pass / fail / score。
4. 生成 `eval-report.json`。
5. CI 中能使用 eval 结果作为门禁。

### 6.3 研发链路验收

必须能完成：

1. 输入一个需求草案。
2. 生成 DevFlow Task Pack。
3. 根据 task pack 创建 GitLab branch / MR。
4. Coding agent 可以基于 task pack 开发。
5. MR 中包含测试、eval 和文档 checklist。

## 7. 成功标准

MVP 成功不是功能多，而是边界清晰：

1. 新增第二个 demo Agent 不需要修改核心 API。
2. 新增工具不需要修改 Runtime Engine。
3. 修改 Agent prompt 或 eval 不需要改平台代码。
4. Coding agent 的修改范围可由 task pack 控制。
5. 每次 Agent 调用都能看到 agent、version、route、tools、latency。

## 8. 开工后第一批任务

建议按顺序开发：

1. 初始化 Python 项目骨架。
2. 定义 domain models。
3. 实现 manifest loader。
4. 实现 Agent Registry。
5. 实现 Agent Router。
6. 实现 NativeRuntimeBackend。
7. 实现 `/api/v1/agent/chat`。
8. 增加 `agents/myj` demo package。
9. 实现 eval runner。
10. 实现 GitLab MR task pack 草案。
11. 实现最小 PlaneAdapter，打通 `Ready for AI Dev -> GitLab MR`。
