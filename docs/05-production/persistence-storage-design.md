# 持久化与 Repository 设计

> Status: Draft
> Stage: S2
> Owner: platform
> Last verified against code: 2026-05-15

## 1. 背景

当前平台全部使用 in-memory 存储。进程重启后 session、run record、deployment audit log 全部丢失。唯一的半持久化选项是 `FileSessionStore`（JSON 文件），但不支持多实例共享和事务。

## 2. 设计决策

| 决策项 | 结论 | 理由 |
|---|---|---|
| 本地开发 DB | SQLite（aiosqlite） | 零依赖、嵌入式、CI 可用 |
| 生产 DB | PostgreSQL（asyncpg） | 多实例、ACID、JSON 列、全文检索 |
| ORM | SQLAlchemy 2.0 async | 成熟生态、Protocol 友好 |
| Migration | Alembic | 版本化、可回滚、团队协作 |
| 连接配置 | `DATABASE_URL` 环境变量 | `sqlite+aiosqlite:///data/platform.db` 或 `postgresql+asyncpg://...` |

## 3. 必须持久化的对象

| 对象 | 用途 | 当前存储 | 目标 |
|---|---|---|---|
| `AgentDefinition` | manifest 快照和 agent 元信息 | 文件系统（manifest.yaml）| DB + 文件系统 |
| `AgentDeployment` | 当前环境/租户/灰度发布状态 | in-memory dict | DB |
| `DeploymentAuditEvent` | 可审计和可回滚 | in-memory list | DB |
| `AgentRun` | 调用记录和排障 | in-memory list（InMemoryRunStore）| DB |
| `AgentSession` | 多轮会话和跨实例共享 | in-memory dict / FileSessionStore | DB |
| `WebhookDelivery` | Plane/GitLab webhook 幂等 | 无 | DB |
| `EvalRun` | 发布 gate 和回归记录 | in-memory | DB |

## 4. DB Schema

### 4.1 公共字段 Mixin

所有表包含以下基础字段：

```python
class BaseMixin:
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(64))
```

### 4.2 表定义

```sql
-- Agent 定义（manifest 快照）
CREATE TABLE agent_definitions (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    agent_id        VARCHAR(64) NOT NULL,
    version         VARCHAR(32) NOT NULL,
    manifest_sha256 VARCHAR(64) NOT NULL,
    manifest_json   JSONB NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'draft',  -- draft/active/deprecated/archived
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(128),
    request_id      VARCHAR(64),
    UNIQUE (agent_id, version)
);

-- Agent 部署
CREATE TABLE agent_deployments (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    agent_id        VARCHAR(64) NOT NULL,
    version         VARCHAR(32) NOT NULL,
    channel         VARCHAR(32) NOT NULL DEFAULT 'default',
    environment     VARCHAR(20) NOT NULL DEFAULT 'dev',  -- dev/staging/prod
    artifact_id     VARCHAR(128),
    manifest_sha256 VARCHAR(64),
    traffic_percent INTEGER NOT NULL DEFAULT 100,
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    deployed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deployed_by     VARCHAR(128),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id      VARCHAR(64)
);
CREATE INDEX idx_deployments_agent ON agent_deployments(agent_id, environment, channel);

-- 部署审计事件
CREATE TABLE deployment_audit_events (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    deployment_id   VARCHAR(64) NOT NULL REFERENCES agent_deployments(id),
    action          VARCHAR(32) NOT NULL,  -- deploy/rollback/promote/deactivate
    actor           VARCHAR(128) NOT NULL,
    previous_state  JSONB,
    new_state       JSONB,
    reason          TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id      VARCHAR(64)
);
CREATE INDEX idx_audit_deployment ON deployment_audit_events(deployment_id, created_at);

-- Agent 运行记录
CREATE TABLE agent_runs (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    agent_id        VARCHAR(64) NOT NULL,
    agent_version   VARCHAR(32),
    session_id      VARCHAR(64),
    request_id      VARCHAR(64),
    status          VARCHAR(20) NOT NULL,  -- completed/failed/timeout
    input_query     TEXT,
    output_status   VARCHAR(32),
    route_reason    VARCHAR(64),
    latency_ms      INTEGER,
    model_id        VARCHAR(64),
    tool_calls      JSONB,
    error_code      VARCHAR(32),
    error_message   TEXT,
    traffic_bucket  INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_runs_agent ON agent_runs(agent_id, created_at);
CREATE INDEX idx_runs_session ON agent_runs(session_id);

-- 会话
CREATE TABLE agent_sessions (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    agent_id        VARCHAR(64),
    user_id         VARCHAR(64),
    location_id     VARCHAR(64),
    messages        JSONB NOT NULL DEFAULT '[]',
    metadata        JSONB NOT NULL DEFAULT '{}',
    status          VARCHAR(20) NOT NULL DEFAULT 'active',  -- active/expired/archived
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_sessions_tenant_user ON agent_sessions(tenant_id, user_id);

-- Webhook 交付（幂等）
CREATE TABLE webhook_deliveries (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    source          VARCHAR(20) NOT NULL,  -- plane/gitlab
    event_type      VARCHAR(64) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    payload_hash    VARCHAR(64),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/processed/failed/dead
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ
);
CREATE INDEX idx_webhook_idempotency ON webhook_deliveries(idempotency_key);

-- Eval 运行记录
CREATE TABLE eval_runs (
    id              VARCHAR(64) PRIMARY KEY,
    tenant_id       VARCHAR(64) NOT NULL,
    agent_id        VARCHAR(64) NOT NULL,
    agent_version   VARCHAR(32) NOT NULL,
    suite_id        VARCHAR(64),
    trigger         VARCHAR(32) NOT NULL,  -- manual/ci/deploy_gate
    total_cases     INTEGER NOT NULL DEFAULT 0,
    passed_cases    INTEGER NOT NULL DEFAULT 0,
    failed_cases    INTEGER NOT NULL DEFAULT 0,
    pass_rate       REAL,
    report_json     JSONB,
    status          VARCHAR(20) NOT NULL,  -- running/passed/failed
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    request_id      VARCHAR(64)
);
CREATE INDEX idx_eval_agent ON eval_runs(agent_id, agent_version);
```

## 5. Repository Protocol

```python
from typing import Protocol, Sequence

class AgentRunRepository(Protocol):
    async def save(self, run: AgentRun) -> None: ...
    async def get(self, run_id: str) -> AgentRun | None: ...
    async def list_by_agent(self, agent_id: str, *, limit: int = 50) -> Sequence[AgentRun]: ...
    async def list_by_session(self, session_id: str) -> Sequence[AgentRun]: ...

class SessionRepository(Protocol):
    async def save(self, session: AgentSession) -> None: ...
    async def get(self, session_id: str) -> AgentSession | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def cleanup_expired(self) -> int: ...

class DeploymentRepository(Protocol):
    async def save(self, deployment: AgentDeployment) -> None: ...
    async def get(self, deployment_id: str) -> AgentDeployment | None: ...
    async def get_active(self, agent_id: str, *, environment: str = "dev", channel: str = "default") -> AgentDeployment | None: ...
    async def list_by_agent(self, agent_id: str) -> Sequence[AgentDeployment]: ...

class DeploymentAuditRepository(Protocol):
    async def save(self, event: DeploymentAuditEvent) -> None: ...
    async def list_by_deployment(self, deployment_id: str) -> Sequence[DeploymentAuditEvent]: ...

class WebhookDeliveryRepository(Protocol):
    async def save(self, delivery: WebhookDelivery) -> None: ...
    async def exists(self, idempotency_key: str) -> bool: ...
    async def mark_processed(self, delivery_id: str) -> None: ...
    async def mark_failed(self, delivery_id: str, error: str) -> None: ...

class EvalRunRepository(Protocol):
    async def save(self, run: EvalRun) -> None: ...
    async def get(self, run_id: str) -> EvalRun | None: ...
    async def get_latest(self, agent_id: str, version: str) -> EvalRun | None: ...
```

## 6. 实现层次

```
src/agent_platform/storage/
    __init__.py
    base.py              # BaseMixin, engine 工厂
    models.py            # SQLAlchemy ORM models
    repositories/
        __init__.py
        memory.py        # InMemory* 实现（用于单测）
        sql.py           # SQL 实现（用于集成测试和生产）
    migrations/
        env.py           # Alembic env
        versions/
            001_initial.py
```

## 7. Engine 工厂

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

def create_engine(database_url: str):
    if database_url.startswith("sqlite"):
        return create_async_engine(database_url, echo=False, connect_args={"check_same_thread": False})
    return create_async_engine(database_url, echo=False, pool_size=5, max_overflow=10)

def create_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)
```

## 8. DI 注入

`RuntimeManager` 和 `app.py` 通过 `DATABASE_URL` 环境变量决定使用哪个实现：

```python
if settings.database_url:
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    run_repo = SqlAgentRunRepository(session_factory)
    session_repo = SqlSessionRepository(session_factory)
    deployment_repo = SqlDeploymentRepository(session_factory)
else:
    run_repo = InMemoryAgentRunRepository()
    session_repo = InMemorySessionRepository()
    deployment_repo = InMemoryDeploymentRepository()
```

## 9. Migration 策略

1. 使用 `alembic init` 创建 migration 环境。
2. `alembic.ini` 的 `sqlalchemy.url` 从 `DATABASE_URL` 环境变量读取。
3. 每次 schema 变更生成新的 migration 版本。
4. CI 中运行 `alembic upgrade head` 验证 migration 可执行。
5. 生产部署前先跑 migration，再启动服务。

## 10. 测试策略

| 层级 | Repository 实现 | DB |
|---|---|---|
| 单元测试 | InMemory* | 无 |
| 集成测试 | Sql* | SQLite（aiosqlite） |
| Contract 测试 | 同时跑 InMemory 和 Sql | 验证两个实现行为一致 |
| 生产回归 | Sql* | PostgreSQL |

Contract 测试示例：

```python
@pytest.fixture(params=["memory", "sqlite"])
async def run_repo(request, tmp_path):
    if request.param == "memory":
        yield InMemoryAgentRunRepository()
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield SqlAgentRunRepository(async_sessionmaker(engine))

async def test_save_and_get(run_repo):
    run = AgentRun(id="run_1", agent_id="myj", ...)
    await run_repo.save(run)
    result = await run_repo.get("run_1")
    assert result.agent_id == "myj"
```

## 11. 验收标准

1. 服务重启后 deployment / session / run / webhook delivery 不丢失。
2. `InMemory*` 实现仍然可用于全部单元测试，无需 DB。
3. Repository contract tests 同时覆盖 memory 和 SQL 实现，行为一致。
4. `DATABASE_URL` 为空时自动降级到 in-memory，无 crash。
5. Alembic migration 可从空库升级到最新 schema。

## 12. 依赖变更

```toml
[project.dependencies]
sqlalchemy = ">=2.0"
alembic = ">=1.13"
aiosqlite = ">=0.20"

[project.optional-dependencies]
postgres = ["asyncpg>=0.29"]
```
