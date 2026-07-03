# Agent Platform

Agent Platform 是一个面向生产和研发闭环的多业务 Agent 平台。它把线上 Agent 运行、版本发布、评测门禁、Plane/GitLab DevFlow、AI coding runner、自进化候选资产治理放在同一个受控系统里。

项目的核心目标不是只跑通一个 demo agent，而是让业务 Agent 可以像软件资产一样被注册、评测、发布、灰度、回滚和持续改进。

## 核心能力

| 领域 | 能力 |
| --- | --- |
| Agent 运行时 | Agent Manifest v1、Registry、Router、NativeRuntime、HermesRuntime、LangGraphRuntime |
| 统一协议 | `/api/v1/agent/chat`、SSE/WebSocket、session、trace、command allowlist |
| 生产治理 | RBAC/API key、多租户隔离、PolicyEngine、SecretResolver、HITL 审批、日志/trace 脱敏 |
| 发布交付 | artifact packaging、eval gate、deployment audit、canary metrics、rollback |
| DevFlow | Plane webhook、GitLab branch/MR、Codex/Claude runner、PathGuard、CommandGuard、checkpoint、状态同步 |
| 观测与存储 | SQLAlchemy/Alembic、Prometheus metrics、OpenTelemetry、Langfuse、ToolAudit、AgentRun/Session 持久化 |
| Knowledge/RAG | KnowledgeService、Weaviate backend、知识同步调度 |
| 自进化 | RuntimeMemory、EvolutionMemory、SkillRegistry、Candidate Store、Promotion Workflow、ImprovementProposal |

当前实现状态以 [docs/implementation-gap.md](docs/implementation-gap.md) 和 [docs/document-stage-map.md](docs/document-stage-map.md) 为准。README 只作为项目入口，不作为完成度事实源。

## 架构概览

```text
Client / Frontend
  -> Agent Platform API
  -> AgentRouter
  -> RuntimeManager
     -> NativeRuntimeBackend
     -> HermesRuntimeBackend
     -> LangGraphRuntimeBackend
  -> ToolExecutor / ModelGateway / KnowledgeService / PolicyEngine
  -> AgentResponse + Trace + Metrics

Plane Work Item
  -> DevFlow Orchestrator
  -> GitLab branch / MR
  -> Codex or Claude runner
  -> validation / eval
  -> human review
  -> deployment gate

Runtime feedback / eval failure
  -> Candidate Store
  -> validation / approval / promotion
  -> ImprovementProposal
  -> Plane / DevFlow / MR
```

## 本地开发

要求 Python 3.12，并使用 `uv` 管理依赖。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

启动 API：

```bash
uv run uvicorn agent_platform.api.app:create_app --factory \
  --host 0.0.0.0 \
  --port 8000 \
  --log-level info
```

常用检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/ready
curl http://127.0.0.1:8000/metrics
```

## 存储模式

默认使用本地 SQLite：

```text
DATABASE_URL=sqlite+aiosqlite:///./agent_platform.db
```

生产建议使用 PostgreSQL：

```text
DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/agent_platform
```

执行迁移：

```bash
uv run alembic upgrade head
```

## 常用验证脚本

离线或本地验证：

```bash
uv run python scripts/smoke_test.py
uv run python scripts/devflow_e2e_test.py
uv run --extra dev python scripts/evolution_smoke_test.py
```

真实外部系统验证：

```bash
uv run python scripts/devflow_real_e2e.py
uv run python scripts/devflow_webhook_real_e2e.py
uv run python scripts/hermes_echo_e2e.py
```

生产依赖检查：

```bash
uv run python scripts/validate_production.py
```

## 关键配置

基础配置：

| 变量 | 说明 |
| --- | --- |
| `AGENT_PLATFORM_API_KEY` | 平台 API 认证密钥；生产环境必须配置 |
| `DATABASE_URL` | SQLite/PostgreSQL 连接串 |
| `REDIS_URL` | Redis job queue 连接串 |
| `WEAVIATE_URL` / `WEAVIATE_API_KEY` | Weaviate 知识检索后端 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | Langfuse 观测 |

Plane / GitLab / DevFlow：

| 变量 | 说明 |
| --- | --- |
| `PLANE_BASE_URL` | Plane API 地址 |
| `PLANE_WORKSPACE_SLUG` | Plane workspace |
| `PLANE_PROJECT_ID` | Plane project ID |
| `PLANE_API_KEY` | Plane API key |
| `PLANE_WEBHOOK_SECRET` | Plane webhook HMAC 密钥 |
| `GITLAB_BASE_URL` | GitLab 地址 |
| `GITLAB_TOKEN` | GitLab token |
| `GITLAB_PROJECT_ID` | GitLab project ID |
| `GITLAB_WEBHOOK_SECRET` | GitLab webhook secret |
| `DEVFLOW_REPO_URL` | Runner clone/push 的仓库地址 |
| `DEVFLOW_DEFAULT_BRANCH` | MR 目标分支，默认 `main` |
| `DEVFLOW_RUNNER_ADAPTER` | `mock` / `codex` / `claude_code` |
| `DEVFLOW_WORKSPACE_BASE_DIR` | Coding runner workspace 根目录 |
| `DEVFLOW_JOB_QUEUE_BACKEND` | `memory` / `redis` |

Runner 安全配置：

| 变量 | 说明 |
| --- | --- |
| `DEVFLOW_SANDBOX_MODE` | Codex runner 沙箱模式：`bypass` / `docker` |
| `DEVFLOW_DOCKER_IMAGE` | Docker sandbox 镜像名 |
| `DEVFLOW_CODEX_PROFILE` | Codex CLI profile |

生产环境不要使用 `DEVFLOW_RUNNER_ADAPTER=mock`。`DEVFLOW_SANDBOX_MODE=bypass` 只适合本地验证，生产 runner 应使用隔离执行环境。

## 主要 API

| Endpoint | 说明 |
| --- | --- |
| `POST /api/v1/agent/chat` | 统一 Agent 对话入口 |
| `WS /ws/agent/chat` | WebSocket 对话 |
| `GET /api/v1/agents` | Agent 列表 |
| `POST /api/v1/agent-packages/register` | 注册 Agent package |
| `POST /api/v1/agent-packages/{agent_id}/versions/{version}/deploy` | 部署 Agent 版本 |
| `POST /api/v1/deployments/rollback` | 回滚部署 |
| `POST /api/v1/evals/run` | 运行评测 |
| `GET /api/v1/devflow/jobs` | DevFlow job 查询 |
| `POST /api/v1/integrations/plane/webhook` | Plane webhook |
| `POST /api/v1/integrations/gitlab/webhook` | GitLab webhook |
| `GET /api/v1/evolution/candidates` | Candidate 查询 |
| `POST /api/v1/evolution/candidates/{candidate_id}/validate` | Candidate 校验 |
| `POST /api/v1/evolution/candidates/{candidate_id}/promote` | Candidate 晋升 |

更多接口以 FastAPI OpenAPI 为准：

```text
http://127.0.0.1:8000/docs
```

## 文档入口

- [docs/README.md](docs/README.md)：文档索引
- [docs/document-stage-map.md](docs/document-stage-map.md)：阶段和文档状态地图
- [docs/implementation-gap.md](docs/implementation-gap.md)：当前实现与设计差距
- [docs/01-contracts/agent-request-response.md](docs/01-contracts/agent-request-response.md)：统一请求/响应契约
- [docs/01-contracts/agent-manifest-v1.md](docs/01-contracts/agent-manifest-v1.md)：Agent Manifest 契约
- [docs/01-contracts/devflow-task-pack.md](docs/01-contracts/devflow-task-pack.md)：DevFlow TaskPack 契约
- [docs/07-evolution/README.md](docs/07-evolution/README.md)：自进化系统文档入口

## 设计原则

1. Agent Package 自包含，平台核心不写业务逻辑。
2. Runtime 可插拔，Hermes 是能力提供者，不是平台事实源。
3. 生产变更必须经过 eval、audit、approval 和 release gate。
4. 自进化只能先写 Candidate，不能直接改 runtime、代码或生产资产。
5. Coding runner 必须受 TaskPack、PathGuard、CommandGuard 和 validation 约束。
