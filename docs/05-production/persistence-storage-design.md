# 持久化与 Repository 设计

> Status: Draft
> Stage: S2
> Owner: platform
> Last verified against code: 2026-05-15

## 1. 现状与问题

当前代码库中所有状态存储均为内存实现，进程重启后数据全部丢失：

| 存储位置 | 当前实现 | 代码位置 | 问题 |
|---|---|---|---|
| Agent 注册表 | `dict[str, AgentSpec]` | `registry/registry.py` `AgentRegistry._cache` | 内存 dict，重启丢失 |
| 部署状态 | `dict[str, AgentDeployment]` | `registry/registry.py` `AgentRegistry._deployments` | 内存 dict，重启丢失 |
| 部署审计 | `list[DeploymentEvent]` | `registry/deployment.py` `DeploymentAuditLog._events` | 内存 list，重启丢失 |
| 运行记录 | `list[AgentRun]` | `observability/trace.py` `InMemoryRunStore._runs` | 内存 list，重启丢失 |
| 会话存储 | `dict[str, AgentSession]` | `session/store.py` `InMemorySessionStore._store` | 内存 dict，重启丢失 |
| Webhook 幂等 | `set[str]` | `api/app.py` `webhook_deliveries` | 内存 set，重启后幂等性失效 |
| 评测记录 | 无存储 | `evals/runner.py` | report 只写文件，无历史查询 |

唯一的半持久化选项是 `FileSessionStore`（`session/store.py`），通过 JSON 文件存储 session。它是同步阻塞的、无事务支持、无索引查询能力，不适合生产环境。

代码库中没有 SQLAlchemy、Alembic、SQLite 或 Postgres 的任何代码。本文档从零设计持久化层。

### 1.1 已有的 Protocol 模式

代码库已使用 Protocol 模式定义接口，本设计保持一致：

| Protocol | 位置 | 现有实现 |
|---|---|---|
| `RuntimeBackend` | `runtime/base.py` | `NativeRuntimeBackend`, `HermesRuntimeBackend`, `LangGraphRuntimeBackend` |
| `SessionStore` | `session/store.py` | `InMemorySessionStore`, `FileSessionStore` |
| `RunStore` | `observability/trace.py` | `InMemoryRunStore` |

新的 Repository Protocol 遵循相同风格：`Protocol` + `@runtime_checkable` + 多实现。

## 2. 技术决策

### 2.1 本地开发用 SQLite，生产用 Postgres

| 环境 | 数据库 | 驱动 | 理由 |
|---|---|---|---|
| 本地开发 / CI | SQLite | aiosqlite | 零外部依赖，`DATABASE_URL=sqlite+aiosqlite:///./dev.db` 即可启动 |
| 生产 | PostgreSQL 15+ | asyncpg | 并发写入、JSONB 索引、行级锁、连接池 |
| 单元测试 | 无数据库 | 无 | 使用 `InMemory*` 实现，零 I/O |
| 集成测试 | SQLite (内存) | aiosqlite | `sqlite+aiosqlite://`（内存模式），每个测试函数独立 |

通过 `DATABASE_URL` 环境变量切换，无需改代码。不设置该变量时自动回退到内存实现：

```bash
# .env (本地开发)
DATABASE_URL=sqlite+aiosqlite:///./dev.db

# .env (生产)
DATABASE_URL=postgresql+asyncpg://user:pass@db-host:5432/agent_platform
```

### 2.2 ORM 与迁移

| 组件 | 选型 | 理由 |
|---|---|---|
| ORM | SQLAlchemy 2.0 async (`Mapped` + `mapped_column`) | 类型安全、async 原生支持、Pydantic 2 兼容 |
| 迁移 | Alembic | 版本化迁移脚本、团队协作友好、支持 `--autogenerate`、可回滚 |
| 不选 SQLModel | -- | SQLModel 在复杂场景（多表关联、mixin、async session）支持不如原生 SA 成熟 |

### 2.3 依赖添加

```toml
# pyproject.toml 变更

[project]
dependencies = [
    # ... 现有依赖保持不变 ...
    "sqlalchemy[asyncio]>=2.0.30",
    "alembic>=1.13",
]

[project.optional-dependencies]
postgres = ["asyncpg>=0.29"]
dev = [
    # ... 现有 dev 依赖保持不变 ...
    "aiosqlite>=0.20",
]
```

## 3. 必须持久化的对象

### 3.1 持久化对象清单

| 对象 | 当前 domain model | 用途 | 写频率 |
|---|---|---|---|
| AgentDefinition | `domain.models.AgentDefinition` | manifest snapshot + agent 元信息 | 低（注册/更新时） |
| AgentDeployment | `domain.models.AgentDeployment` | 环境/租户/灰度发布状态 | 低（部署时） |
| DeploymentAuditEvent | `registry.deployment.DeploymentEvent` | 审计与回滚 | 低（部署/回滚时） |
| AgentRun | `domain.models.AgentRun` | 调用记录与排障 | 高（每次请求） |
| AgentSession | `domain.models.AgentSession` | 多轮会话与跨实例共享 | 高（每次对话） |
| WebhookDelivery | 新增 domain model | Plane/GitLab webhook 幂等去重 | 中（webhook 触发时） |
| EvalRun | 新增（基于 `evals.runner.EvalReport`） | 发布 gate 与回归记录 | 低（评测时） |

### 3.2 不需要持久化的对象

| 对象 | 理由 |
|---|---|
| `AgentSpec` | 运行时从 manifest.yaml 文件加载的计算对象，`AgentDefinition` 已保存 manifest snapshot |
| `AgentManifest` | 作为 `AgentDefinition.manifest_json` JSONB 列持久化，不需要独立表 |
| `PolicySet` / `ToolRegistry` | 运行时从文件构建的缓存，无需持久化 |
| `MetricsCollector` | Prometheus pull 模型，不走 DB |
| `SemanticRouter` 规则 | 运行时从配置加载 |

## 4. 数据库 Schema 设计

### 4.1 公共基类 Mixin

所有表继承以下 mixin，确保 `request_id`、`created_by`（即 actor）、`created_at`、`updated_at`、`tenant_id` 自动进入每条写入记录：

```python
# src/agent_platform/persistence/base.py

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM model 的基类。"""
    pass


class AuditMixin:
    """审计字段 mixin。所有业务表继承此 mixin，写入时自动携带审计信息。

    字段说明:
    - id:         内部主键，UUID hex，由应用层生成
    - tenant_id:  租户隔离，从 RequestContext 或 HTTP header 获取
    - created_by: 操作人（对应 next-stage-design-plan 中的 actor 概念）
    - request_id: 关联请求 ID，用于链路追踪
    - created_at: 记录创建时间
    - updated_at: 最后更新时间，ORM 层 onupdate 自动刷新
    """

    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid4().hex,
    )
    tenant_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True,
    )
    created_by: Mapped[str] = mapped_column(
        String(128), default="system",
    )
    request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(UTC),
        server_default=func.now(),
        onupdate=lambda: datetime.now(UTC),
    )
```

### 4.2 表定义

所有 ORM Row 类定义在 `src/agent_platform/persistence/tables.py`。

#### 4.2.1 agent_definitions

存储 agent manifest 快照。每次注册或更新 agent 时写入一行。

```python
from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from agent_platform.persistence.base import AuditMixin, Base


class AgentDefinitionRow(AuditMixin, Base):
    __tablename__ = "agent_definitions"

    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active",
    )  # draft | active | deprecated | archived
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_agentdef_agent_version", "agent_id", "version", unique=True),
        Index("ix_agentdef_status", "status"),
    )
```

对应 domain model `AgentDefinition` 的字段映射:

| domain 字段 | 列名 | 说明 |
|---|---|---|
| `agent_id` | `agent_id` | 直接映射 |
| `version` | `version` | 直接映射 |
| `status` | `status` | `AgentDefinitionStatus` 枚举的 `.value` |
| `manifest` | `manifest_json` | `AgentManifest.model_dump()` 序列化为 JSON |
| `created_at` | `created_at` | AuditMixin 提供 |
| `updated_at` | `updated_at` | AuditMixin 提供 |

#### 4.2.2 agent_deployments

存储当前的部署状态。每个 `deployment_id` 唯一（由 `AgentRegistry._deployment_id()` 生成）。

```python
class AgentDeploymentRow(AuditMixin, Base):
    __tablename__ = "agent_deployments"

    deployment_id: Mapped[str] = mapped_column(
        String(256), nullable=False, unique=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # dev | staging | prod
    status: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # 对应 AgentDeploymentStatus 枚举值
    traffic_percent: Mapped[int] = mapped_column(default=100)

    __table_args__ = (
        Index("ix_deploy_agent_channel", "agent_id", "channel"),
        Index("ix_deploy_tenant_channel", "tenant_id", "channel"),
    )
```

对应 domain model `AgentDeployment` 的字段映射:

| domain 字段 | 列名 | 说明 |
|---|---|---|
| `deployment_id` | `deployment_id` | 直接映射 |
| `agent_id` | `agent_id` | 直接映射 |
| `version` | `version` | 直接映射 |
| `channel` | `channel` | `Literal["dev", "staging", "prod"]` |
| `status` | `status` | `AgentDeploymentStatus` 枚举的 `.value` |
| `tenant_id` | `tenant_id` | AuditMixin 提供，对应 `AgentDeployment.tenant_id` |
| `traffic_percent` | `traffic_percent` | 直接映射 |

#### 4.2.3 deployment_audit_events

存储所有部署和回滚事件，对应当前 `DeploymentAuditLog._events` 的每个 `DeploymentEvent`。

```python
class DeploymentAuditEventRow(AuditMixin, Base):
    __tablename__ = "deployment_audit_events"

    event_type: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # deploy | rollback
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    traffic_percent: Mapped[int] = mapped_column(default=100)
    previous_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    actor: Mapped[str] = mapped_column(String(128), default="system")
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_audit_agent_channel", "agent_id", "channel"),
        Index("ix_audit_created", "created_at"),
    )
```

对应 domain model `DeploymentEvent`（`registry/deployment.py`）的字段映射:

| domain 字段 | 列名 | 说明 |
|---|---|---|
| `timestamp` | `created_at` | AuditMixin 提供 |
| `event_type` | `event_type` | "deploy" 或 "rollback" |
| `agent_id` | `agent_id` | 直接映射 |
| `version` | `version` | 直接映射 |
| `channel` | `channel` | 直接映射 |
| `traffic_percent` | `traffic_percent` | 直接映射 |
| `status` | `status` | `AgentDeploymentStatus` 枚举的 `.value` |
| `previous_version` | `previous_version` | 直接映射 |
| `actor` | `actor` | 直接映射（同时写入 `created_by`） |
| `metadata` | `metadata_json` | dict 序列化为 JSON |

#### 4.2.4 agent_runs

存储每次 agent 调用的运行记录。这是写入频率最高的表。

```python
class AgentRunRow(AuditMixin, Base):
    __tablename__ = "agent_runs"

    run_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True,
    )
    session_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    route_reason: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )
    runtime_backend: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # succeeded | failed
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    tool_calls_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_run_agent", "agent_id"),
        Index("ix_run_status", "status"),
        Index("ix_run_created", "created_at"),
    )
```

对应 domain model `AgentRun`（`domain/models.py`）的字段映射:

| domain 字段 | 列名 | 说明 |
|---|---|---|
| `run_id` | `run_id` | 直接映射 |
| `request_id` | `request_id` | AuditMixin 提供 |
| `session_id` | `session_id` | 直接映射 |
| `agent_id` | `agent_id` | 直接映射 |
| `agent_version` | `agent_version` | 直接映射 |
| `route_reason` | `route_reason` | 直接映射 |
| `runtime_backend` | `runtime_backend` | 直接映射 |
| `status` | `status` | `AgentRunStatus` 枚举的 `.value` |
| `latency_ms` | `latency_ms` | 直接映射 |
| `tool_calls` | `tool_calls_json` | `[tc.model_dump() for tc in tool_calls]` |
| `error` | `error_json` | `error.model_dump() if error else None` |
| `metadata` | `metadata_json` | 直接映射 |

#### 4.2.5 agent_sessions

存储多轮会话。`history` 和 `state_snapshot` 序列化为 JSON 列。

```python
class AgentSessionRow(AuditMixin, Base):
    __tablename__ = "agent_sessions"

    session_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True,
    )
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    store_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True,
    )
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    history_json: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list,
    )
    state_snapshot_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict,
    )

    __table_args__ = (
        Index("ix_session_agent", "agent_id"),
        Index("ix_session_tenant_user", "tenant_id", "user_id"),
    )
```

对应 domain model `AgentSession`（`domain/models.py`）的字段映射:

| domain 字段 | 列名 | 说明 |
|---|---|---|
| `session_id` | `session_id` | 直接映射 |
| `agent_id` | `agent_id` | 直接映射 |
| `tenant_id` | `tenant_id` | AuditMixin 提供 |
| `store_id` | `store_id` | 直接映射 |
| `user_id` | `user_id` | 直接映射 |
| `channel_id` | `channel_id` | 直接映射 |
| `history` | `history_json` | `[m.model_dump(mode="json") for m in history]` |
| `state_snapshot` | `state_snapshot_json` | 直接映射 |
| `created_at` | `created_at` | AuditMixin 提供 |
| `updated_at` | `updated_at` | AuditMixin 提供 |

#### 4.2.6 webhook_deliveries

存储 Plane/GitLab webhook 接收记录，替代当前 `app.py` 中的 `webhook_deliveries: set[str]`。

```python
from sqlalchemy import Text


class WebhookDeliveryRow(AuditMixin, Base):
    __tablename__ = "webhook_deliveries"

    delivery_id: Mapped[str] = mapped_column(
        String(256), nullable=False, unique=True,
    )
    source: Mapped[str] = mapped_column(
        String(32), nullable=False,
    )  # plane | gitlab
    event_type: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="accepted",
    )  # accepted | duplicate | failed
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_webhook_source_event", "source", "event_type"),
        Index("ix_webhook_created", "created_at"),
    )
```

#### 4.2.7 eval_runs

存储评测运行记录，对应当前 `EvalReport`（`evals/runner.py`）。

```python
class EvalRunRow(AuditMixin, Base):
    __tablename__ = "eval_runs"

    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_version: Mapped[str] = mapped_column(String(64), nullable=False)
    total: Mapped[int] = mapped_column(nullable=False)
    passed: Mapped[int] = mapped_column(nullable=False)
    pass_rate: Mapped[float] = mapped_column(nullable=False)
    required_pass_rate: Mapped[float] = mapped_column(nullable=False)
    gate_passed: Mapped[bool] = mapped_column(nullable=False)
    results_json: Mapped[list] = mapped_column(JSON, nullable=False)
    trigger: Mapped[str] = mapped_column(
        String(32), default="manual",
    )  # manual | ci | deploy_gate

    __table_args__ = (
        Index("ix_eval_agent", "agent_id"),
        Index("ix_eval_agent_version", "agent_id", "agent_version"),
        Index("ix_eval_created", "created_at"),
    )
```

对应 domain model `EvalReport`（`evals/runner.py`）的字段映射:

| domain 字段 | 列名 | 说明 |
|---|---|---|
| `agent_id` | `agent_id` | 直接映射 |
| -- | `agent_version` | 新增，从 `AgentSpec.version` 获取 |
| `total` | `total` | 直接映射 |
| `passed` | `passed` | 直接映射 |
| `pass_rate` | `pass_rate` | 直接映射 |
| `required_pass_rate` | `required_pass_rate` | 直接映射 |
| `gate_passed` | `gate_passed` | 直接映射 |
| `results` | `results_json` | `[r.model_dump() for r in results]` |
| -- | `trigger` | 新增，标记触发来源 |

### 4.3 ER 关系概览

```
agent_definitions (agent_id, version)
    │
    ├── 1:N ──> agent_deployments (agent_id)
    │               │
    │               └── 1:N ──> deployment_audit_events (agent_id, channel)
    │
    └── 1:N ──> eval_runs (agent_id, agent_version)

agent_sessions (session_id)
    │
    └── 1:N ──> agent_runs (session_id)

webhook_deliveries (delivery_id)   # 独立表，无 FK
```

设计决策：不使用 ORM 级 `relationship()` 和数据库 FK 约束。表间关系通过应用层查询保持松耦合。理由：
1. InMemory 实现不需要模拟 FK 约束。
2. 高频写入表（`agent_runs`）避免 FK 检查开销。
3. 跨表查询需求有限，不需要 eager loading。

## 5. Repository Protocol 与实现

### 5.1 Repository Protocol 定义

每个聚合根一个 Protocol。所有方法为 `async`，与现有 `RuntimeBackend` Protocol 风格一致。

```python
# src/agent_platform/persistence/repositories.py

from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_platform.domain.models import (
    AgentDefinition,
    AgentDeployment,
    AgentRun,
    AgentSession,
)
from agent_platform.registry.deployment import DeploymentEvent


# ── AgentDefinition ──────────────────────────────────────

@runtime_checkable
class AgentDefinitionRepository(Protocol):
    async def save(self, definition: AgentDefinition) -> None:
        """保存或更新 agent 定义。agent_id + version 唯一。"""
        ...

    async def get(self, agent_id: str, version: str) -> AgentDefinition | None:
        """按 agent_id 和 version 查询。"""
        ...

    async def get_latest(self, agent_id: str) -> AgentDefinition | None:
        """获取指定 agent 的最新版本定义。"""
        ...

    async def list_all(
        self, *, status: str | None = None,
    ) -> list[AgentDefinition]:
        """列出所有定义，可选按 status 过滤。"""
        ...

    async def update_status(
        self, agent_id: str, version: str, status: str,
    ) -> None:
        """更新 agent 定义的状态。"""
        ...


# ── AgentDeployment ──────────────────────────────────────

@runtime_checkable
class AgentDeploymentRepository(Protocol):
    async def save(self, deployment: AgentDeployment) -> None:
        """保存或更新部署记录。deployment_id 唯一。"""
        ...

    async def get(self, deployment_id: str) -> AgentDeployment | None:
        """按 deployment_id 查询。"""
        ...

    async def resolve(
        self,
        *,
        agent_id: str,
        channel: str,
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        """解析指定 agent 在指定 channel 和 tenant 下的活跃部署。
        优先匹配 tenant_id，fallback 到 tenant_id=None 的默认部署。"""
        ...

    async def list_all(
        self, *, agent_id: str | None = None,
    ) -> list[AgentDeployment]:
        """列出所有部署，可选按 agent_id 过滤。"""
        ...

    async def delete(self, deployment_id: str) -> None:
        """删除部署记录。"""
        ...


# ── DeploymentAuditEvent ─────────────────────────────────

@runtime_checkable
class DeploymentAuditRepository(Protocol):
    async def record(self, event: DeploymentEvent) -> None:
        """记录一个部署审计事件。"""
        ...

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        """查询审计事件，按 created_at 倒序。"""
        ...

    async def get_rollback_version(
        self, agent_id: str, channel: str,
    ) -> str | None:
        """查询最近一次 deploy 事件的 previous_version，用于回滚。"""
        ...


# ── AgentRun ─────────────────────────────────────────────
# 替代现有 RunStore (observability/trace.py)

@runtime_checkable
class AgentRunRepository(Protocol):
    async def record(self, run: AgentRun) -> None:
        """记录一次 agent 运行。"""
        ...

    async def get(self, run_id: str) -> AgentRun | None:
        """按 run_id 查询。"""
        ...

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        """列出运行记录，支持按 agent_id / session_id 过滤。"""
        ...


# ── AgentSession ─────────────────────────────────────────
# 替代现有 SessionStore (session/store.py)

@runtime_checkable
class AgentSessionRepository(Protocol):
    async def save(self, session: AgentSession) -> None:
        """保存或更新 session。session_id 唯一。"""
        ...

    async def load(self, session_id: str) -> AgentSession | None:
        """按 session_id 加载。"""
        ...

    async def delete(self, session_id: str) -> None:
        """删除 session。"""
        ...

    async def list_sessions(
        self, *, agent_id: str | None = None,
    ) -> list[AgentSession]:
        """列出所有 session，可选按 agent_id 过滤。"""
        ...


# ── WebhookDelivery ──────────────────────────────────────

@runtime_checkable
class WebhookDeliveryRepository(Protocol):
    async def exists(self, delivery_id: str) -> bool:
        """检查是否已接收过该 delivery_id（幂等检查）。"""
        ...

    async def record(
        self,
        *,
        delivery_id: str,
        source: str,
        event_type: str | None = None,
        status: str = "accepted",
        payload: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        """记录一次 webhook 接收。"""
        ...


# ── EvalRun ──────────────────────────────────────────────

@runtime_checkable
class EvalRunRepository(Protocol):
    async def record(
        self,
        *,
        agent_id: str,
        agent_version: str,
        total: int,
        passed: int,
        pass_rate: float,
        required_pass_rate: float,
        gate_passed: bool,
        results: list[dict],
        trigger: str = "manual",
    ) -> None:
        """记录一次评测运行。"""
        ...

    async def get_latest(
        self, agent_id: str,
    ) -> dict | None:
        """获取指定 agent 的最近一次评测记录。"""
        ...

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """列出评测记录。"""
        ...
```

### 5.2 与现有 Protocol 的关系

| 现有 Protocol | 位置 | 新 Protocol | 迁移策略 |
|---|---|---|---|
| `SessionStore` | `session/store.py` | `AgentSessionRepository` | 新 Protocol 方法签名为 async。过渡期保留 `SessionStore`，`RuntimeManager` 的 `session_store` 参数类型改为 `AgentSessionRepository`。现有 `InMemorySessionStore` 和 `FileSessionStore` 不删除，标记 deprecated |
| `RunStore` | `observability/trace.py` | `AgentRunRepository` | 同上。`RuntimeManager` 的 `run_store` 参数类型改为 `AgentRunRepository`。现有 `InMemoryRunStore` 不删除，标记 deprecated |

`RuntimeManager.__init__` 签名变更:

```python
# 当前
def __init__(
    self,
    run_store: RunStore | None = None,
    session_store: SessionStore | None = None,
):

# 变更后
def __init__(
    self,
    run_store: AgentRunRepository | None = None,
    session_store: AgentSessionRepository | None = None,
):
```

由于新 Protocol 的方法名和签名与旧 Protocol 高度相似（`record`/`get`/`list_runs` 和 `save`/`load`/`delete`/`list_sessions`），调用方只需在 `await` 处做最小改动。

### 5.3 InMemory 实现（单元测试用）

每个 Repository Protocol 对应一个 `InMemory*` 实现，零依赖、纯 Python。

```python
# src/agent_platform/persistence/memory.py

from __future__ import annotations

from agent_platform.domain.models import (
    AgentDefinition,
    AgentDeployment,
    AgentRun,
    AgentSession,
)
from agent_platform.registry.deployment import DeploymentEvent


class InMemoryAgentDefinitionRepository:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], AgentDefinition] = {}

    async def save(self, definition: AgentDefinition) -> None:
        self._store[(definition.agent_id, definition.version)] = definition

    async def get(
        self, agent_id: str, version: str,
    ) -> AgentDefinition | None:
        return self._store.get((agent_id, version))

    async def get_latest(self, agent_id: str) -> AgentDefinition | None:
        matches = [
            d for d in self._store.values() if d.agent_id == agent_id
        ]
        if not matches:
            return None
        return max(matches, key=lambda d: d.created_at)

    async def list_all(
        self, *, status: str | None = None,
    ) -> list[AgentDefinition]:
        defs = list(self._store.values())
        if status:
            defs = [d for d in defs if d.status.value == status]
        return defs

    async def update_status(
        self, agent_id: str, version: str, status: str,
    ) -> None:
        key = (agent_id, version)
        if key in self._store:
            from agent_platform.domain.models import AgentDefinitionStatus
            self._store[key].status = AgentDefinitionStatus(status)


class InMemoryAgentDeploymentRepository:
    def __init__(self) -> None:
        self._store: dict[str, AgentDeployment] = {}

    async def save(self, deployment: AgentDeployment) -> None:
        self._store[deployment.deployment_id] = deployment

    async def get(self, deployment_id: str) -> AgentDeployment | None:
        return self._store.get(deployment_id)

    async def resolve(
        self,
        *,
        agent_id: str,
        channel: str,
        tenant_id: str | None = None,
    ) -> AgentDeployment | None:
        # 优先匹配 tenant_id
        for d in self._store.values():
            if (
                d.agent_id == agent_id
                and d.channel == channel
                and d.tenant_id == tenant_id
            ):
                return d
        # fallback 到 tenant_id=None
        if tenant_id is not None:
            for d in self._store.values():
                if (
                    d.agent_id == agent_id
                    and d.channel == channel
                    and d.tenant_id is None
                ):
                    return d
        return None

    async def list_all(
        self, *, agent_id: str | None = None,
    ) -> list[AgentDeployment]:
        deployments = list(self._store.values())
        if agent_id:
            deployments = [
                d for d in deployments if d.agent_id == agent_id
            ]
        return deployments

    async def delete(self, deployment_id: str) -> None:
        self._store.pop(deployment_id, None)


class InMemoryDeploymentAuditRepository:
    def __init__(self) -> None:
        self._events: list[DeploymentEvent] = []
        self._rollback_targets: dict[str, str] = {}

    async def record(self, event: DeploymentEvent) -> None:
        self._events.append(event)
        if event.event_type == "deploy" and event.previous_version:
            key = f"{event.agent_id}:{event.channel}"
            self._rollback_targets[key] = event.previous_version

    async def list_events(
        self,
        *,
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[DeploymentEvent]:
        events = self._events
        if agent_id:
            events = [e for e in events if e.agent_id == agent_id]
        if channel:
            events = [e for e in events if e.channel == channel]
        return events[-limit:]

    async def get_rollback_version(
        self, agent_id: str, channel: str,
    ) -> str | None:
        key = f"{agent_id}:{channel}"
        return self._rollback_targets.get(key)


class InMemoryAgentRunRepository:
    def __init__(self) -> None:
        self._runs: list[AgentRun] = []

    async def record(self, run: AgentRun) -> None:
        self._runs.append(run)

    async def get(self, run_id: str) -> AgentRun | None:
        return next(
            (r for r in self._runs if r.run_id == run_id), None,
        )

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        runs = self._runs
        if agent_id:
            runs = [r for r in runs if r.agent_id == agent_id]
        if session_id:
            runs = [r for r in runs if r.session_id == session_id]
        return runs[-limit:]


class InMemoryAgentSessionRepository:
    def __init__(self) -> None:
        self._store: dict[str, AgentSession] = {}

    async def save(self, session: AgentSession) -> None:
        self._store[session.session_id] = session

    async def load(self, session_id: str) -> AgentSession | None:
        return self._store.get(session_id)

    async def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    async def list_sessions(
        self, *, agent_id: str | None = None,
    ) -> list[AgentSession]:
        sessions = list(self._store.values())
        if agent_id:
            sessions = [
                s for s in sessions if s.agent_id == agent_id
            ]
        return sessions


class InMemoryWebhookDeliveryRepository:
    def __init__(self) -> None:
        self._deliveries: dict[str, dict] = {}

    async def exists(self, delivery_id: str) -> bool:
        return delivery_id in self._deliveries

    async def record(
        self,
        *,
        delivery_id: str,
        source: str,
        event_type: str | None = None,
        status: str = "accepted",
        payload: dict | None = None,
        error_message: str | None = None,
    ) -> None:
        self._deliveries[delivery_id] = {
            "delivery_id": delivery_id,
            "source": source,
            "event_type": event_type,
            "status": status,
        }


class InMemoryEvalRunRepository:
    def __init__(self) -> None:
        self._runs: list[dict] = []

    async def record(
        self,
        *,
        agent_id: str,
        agent_version: str,
        total: int,
        passed: int,
        pass_rate: float,
        required_pass_rate: float,
        gate_passed: bool,
        results: list[dict],
        trigger: str = "manual",
    ) -> None:
        self._runs.append({
            "agent_id": agent_id,
            "agent_version": agent_version,
            "total": total,
            "passed": passed,
            "pass_rate": pass_rate,
            "required_pass_rate": required_pass_rate,
            "gate_passed": gate_passed,
            "results": results,
            "trigger": trigger,
        })

    async def get_latest(self, agent_id: str) -> dict | None:
        matches = [
            r for r in self._runs if r["agent_id"] == agent_id
        ]
        return matches[-1] if matches else None

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        runs = self._runs
        if agent_id:
            runs = [r for r in runs if r["agent_id"] == agent_id]
        return runs[-limit:]
```

### 5.4 SQL 实现示例

以 `AgentRunRepository` 和 `AgentSessionRepository` 为例展示完整 SQL 实现。其他 Repository 同理。

```python
# src/agent_platform/persistence/sql.py

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_platform.domain.models import (
    AgentError,
    AgentRun,
    AgentRunStatus,
    AgentSession,
    SessionMessage,
    ToolCallTrace,
)
from agent_platform.persistence.context import get_audit_context
from agent_platform.persistence.tables import AgentRunRow, AgentSessionRow


class SqlAgentRunRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def record(self, run: AgentRun) -> None:
        ctx = get_audit_context()
        async with self._session_factory() as session:
            row = AgentRunRow(
                run_id=run.run_id,
                request_id=run.request_id or ctx.request_id,
                session_id=run.session_id,
                agent_id=run.agent_id,
                agent_version=run.agent_version,
                route_reason=run.route_reason,
                runtime_backend=run.runtime_backend,
                status=run.status.value,
                latency_ms=run.latency_ms,
                tool_calls_json=[
                    tc.model_dump() for tc in run.tool_calls
                ],
                error_json=(
                    run.error.model_dump() if run.error else None
                ),
                metadata_json=run.metadata,
                tenant_id=ctx.tenant_id,
                created_by=ctx.actor,
            )
            session.add(row)
            await session.commit()

    async def get(self, run_id: str) -> AgentRun | None:
        async with self._session_factory() as session:
            stmt = select(AgentRunRow).where(
                AgentRunRow.run_id == run_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def list_runs(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[AgentRun]:
        async with self._session_factory() as session:
            stmt = select(AgentRunRow).order_by(
                AgentRunRow.created_at.desc(),
            )
            if agent_id:
                stmt = stmt.where(AgentRunRow.agent_id == agent_id)
            if session_id:
                stmt = stmt.where(AgentRunRow.session_id == session_id)
            stmt = stmt.limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_domain(r) for r in rows]

    @staticmethod
    def _to_domain(row: AgentRunRow) -> AgentRun:
        return AgentRun(
            run_id=row.run_id,
            request_id=row.request_id,
            session_id=row.session_id,
            agent_id=row.agent_id,
            agent_version=row.agent_version,
            route_reason=row.route_reason,
            runtime_backend=row.runtime_backend,
            status=AgentRunStatus(row.status),
            latency_ms=row.latency_ms,
            tool_calls=[
                ToolCallTrace.model_validate(tc)
                for tc in (row.tool_calls_json or [])
            ],
            error=(
                AgentError.model_validate(row.error_json)
                if row.error_json
                else None
            ),
            metadata=row.metadata_json or {},
        )


class SqlAgentSessionRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    async def save(self, agent_session: AgentSession) -> None:
        ctx = get_audit_context()
        async with self._session_factory() as session:
            stmt = select(AgentSessionRow).where(
                AgentSessionRow.session_id == agent_session.session_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                row = AgentSessionRow(
                    session_id=agent_session.session_id,
                    agent_id=agent_session.agent_id,
                    tenant_id=agent_session.tenant_id or ctx.tenant_id,
                    store_id=agent_session.store_id,
                    user_id=agent_session.user_id,
                    channel_id=agent_session.channel_id,
                    history_json=[
                        m.model_dump(mode="json")
                        for m in agent_session.history
                    ],
                    state_snapshot_json=agent_session.state_snapshot,
                    created_by=ctx.actor,
                    request_id=ctx.request_id,
                )
                session.add(row)
            else:
                row.history_json = [
                    m.model_dump(mode="json")
                    for m in agent_session.history
                ]
                row.state_snapshot_json = agent_session.state_snapshot
                row.updated_at = datetime.now(UTC)
            await session.commit()

    async def load(self, session_id: str) -> AgentSession | None:
        async with self._session_factory() as session:
            stmt = select(AgentSessionRow).where(
                AgentSessionRow.session_id == session_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            return self._to_domain(row)

    async def delete(self, session_id: str) -> None:
        async with self._session_factory() as session:
            stmt = select(AgentSessionRow).where(
                AgentSessionRow.session_id == session_id,
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row:
                await session.delete(row)
                await session.commit()

    async def list_sessions(
        self, *, agent_id: str | None = None,
    ) -> list[AgentSession]:
        async with self._session_factory() as session:
            stmt = select(AgentSessionRow)
            if agent_id:
                stmt = stmt.where(
                    AgentSessionRow.agent_id == agent_id,
                )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._to_domain(r) for r in rows]

    @staticmethod
    def _to_domain(row: AgentSessionRow) -> AgentSession:
        return AgentSession(
            session_id=row.session_id,
            agent_id=row.agent_id,
            tenant_id=row.tenant_id,
            store_id=row.store_id,
            user_id=row.user_id,
            channel_id=row.channel_id,
            history=[
                SessionMessage.model_validate(m)
                for m in (row.history_json or [])
            ],
            state_snapshot=row.state_snapshot_json or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

# SqlAgentDefinitionRepository, SqlAgentDeploymentRepository,
# SqlDeploymentAuditRepository, SqlWebhookDeliveryRepository,
# SqlEvalRunRepository 同理实现。每个类:
# 1. 构造函数接收 async_sessionmaker
# 2. 每个方法内 async with self._session_factory() as session
# 3. 写入时从 get_audit_context() 读取 request_id / actor / tenant_id
# 4. 提供 _to_domain 静态方法做 Row -> domain model 转换
```

## 6. 审计字段注入机制

### 6.1 AuditContext 传播

当前 `RequestContextMiddleware`（`api/app.py` 第 116-124 行）已将 `request_id` 和 `tenant_id` 写入 `request.state`。通过 `contextvars` 在异步调用链中传播审计上下文，避免在每个 repository 方法签名中传递：

```python
# src/agent_platform/persistence/context.py

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class AuditContext:
    request_id: str | None = None
    actor: str = "system"
    tenant_id: str | None = None


_audit_ctx: ContextVar[AuditContext] = ContextVar(
    "audit_ctx", default=AuditContext(),
)


def get_audit_context() -> AuditContext:
    return _audit_ctx.get()


def set_audit_context(ctx: AuditContext) -> None:
    _audit_ctx.set(ctx)
```

### 6.2 中间件集成

在 `RequestContextMiddleware.dispatch` 中设置 `AuditContext`：

```python
# api/app.py RequestContextMiddleware.dispatch 变更

from agent_platform.persistence.context import AuditContext, set_audit_context

class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = (
            request.headers.get("x-request-id") or f"req_{uuid4().hex}"
        )
        tenant_id = request.headers.get("x-tenant-id")
        actor = request.headers.get("x-actor", "system")

        request.state.request_id = request_id
        request.state.tenant_id = tenant_id

        # 设置 AuditContext，所有下游 repository 写入自动携带
        set_audit_context(AuditContext(
            request_id=request_id,
            actor=actor,
            tenant_id=tenant_id,
        ))

        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
```

### 6.3 字段填充流程

```
HTTP Request
  │
  ▼
RequestContextMiddleware
  │ set_audit_context(request_id, actor, tenant_id)
  ▼
Router → RuntimeManager → ...
  │
  ▼
Repository.save/record()
  │ ctx = get_audit_context()
  │ row.request_id = ctx.request_id
  │ row.created_by = ctx.actor
  │ row.tenant_id = ctx.tenant_id
  │ row.created_at = 自动 (AuditMixin default)
  │ row.updated_at = 自动 (AuditMixin onupdate)
  ▼
DB
```

非 HTTP 上下文（CLI 脚本、定时任务）需要手动调用 `set_audit_context()` 设置调用方信息。

## 7. 数据库引擎与连接池

### 7.1 Engine 工厂

```python
# src/agent_platform/persistence/engine.py

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.persistence.base import Base


def get_database_url() -> str | None:
    """从环境变量读取 DATABASE_URL。返回 None 表示使用内存模式。"""
    return os.getenv("DATABASE_URL")


def create_engine_from_url(url: str) -> AsyncEngine:
    """根据 DATABASE_URL 创建 async engine。
    自动区分 SQLite 和 Postgres 的配置差异。
    """
    connect_args: dict = {}
    pool_kwargs: dict = {}

    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        pool_kwargs = {"pool_pre_ping": False}
    else:
        # Postgres 连接池配置
        pool_kwargs = {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_pre_ping": True,
            "pool_recycle": 3600,
        }

    return create_async_engine(
        url,
        connect_args=connect_args,
        echo=os.getenv("SQL_ECHO", "").lower() == "true",
        **pool_kwargs,
    )


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """仅用于开发/测试环境。
    生产环境必须使用 Alembic 迁移。
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

### 7.2 连接池参数说明

| 参数 | 值 | 说明 |
|---|---|---|
| `pool_size` | 10 | 单实例常驻连接数 |
| `max_overflow` | 20 | 突发时可临时创建的额外连接数（峰值 30） |
| `pool_pre_ping` | True | 从池中取连接前先 ping，检测断连并自动重连 |
| `pool_recycle` | 3600 | 每小时回收连接，避免长时间空闲后被服务端断开 |
| `expire_on_commit` | False | commit 后不过期 ORM 对象的属性，避免访问时触发额外查询 |

如果使用外部连接池（PgBouncer），将 `pool_size=1, max_overflow=0`，由 PgBouncer 管理池化。

### 7.3 SQLite 注意事项

- `check_same_thread=False` 是必须的，asyncio 的线程模型要求允许跨线程访问。
- SQLite 不支持 `pool_size` / `max_overflow` 参数，不传。
- aiosqlite 下 WAL 模式自动启用，支持并发读 + 串行写。
- JSON 列在 SQLite 中存储为 TEXT，`json()` 函数可用但无 JSONB 索引。生产环境查询 JSON 字段的需求使用 Postgres JSONB。

### 7.4 事务边界

- 读操作：autocommit，不开显式事务。
- 写操作：每个 repository 方法内 `async with session_factory() as session:` 自带隐式事务。方法结束时 `commit()` 或异常时自动 `rollback()`。
- 跨 repository 事务：当前不需要。如果未来需要（例如 deploy 时同时写 deployment + audit），在 service 层创建共享 session 传入两个 repository。预留方案：

```python
# 未来需要时的跨 repo 事务写法
async with session_factory() as shared_session:
    await deployment_repo.save(deployment, session=shared_session)
    await audit_repo.record(event, session=shared_session)
    await shared_session.commit()
```

## 8. DI 组装：RepositoryProvider

```python
# src/agent_platform/persistence/provider.py

from agent_platform.persistence.engine import (
    create_engine_from_url,
    create_session_factory,
    get_database_url,
)
from agent_platform.persistence.memory import (
    InMemoryAgentDefinitionRepository,
    InMemoryAgentDeploymentRepository,
    InMemoryAgentRunRepository,
    InMemoryAgentSessionRepository,
    InMemoryDeploymentAuditRepository,
    InMemoryEvalRunRepository,
    InMemoryWebhookDeliveryRepository,
)
from agent_platform.persistence.sql import (
    SqlAgentDefinitionRepository,
    SqlAgentDeploymentRepository,
    SqlAgentRunRepository,
    SqlAgentSessionRepository,
    SqlDeploymentAuditRepository,
    SqlEvalRunRepository,
    SqlWebhookDeliveryRepository,
)


class RepositoryProvider:
    """根据 DATABASE_URL 环境变量决定使用 memory 还是 SQL 实现。

    DATABASE_URL 存在 → SQL 实现（SQLite 或 Postgres）
    DATABASE_URL 为空 → InMemory 实现（行为与当前代码一致）
    """

    def __init__(self) -> None:
        url = get_database_url()
        if url:
            engine = create_engine_from_url(url)
            factory = create_session_factory(engine)
            self.engine = engine
            self.agent_definitions = SqlAgentDefinitionRepository(factory)
            self.agent_deployments = SqlAgentDeploymentRepository(factory)
            self.deployment_audit = SqlDeploymentAuditRepository(factory)
            self.agent_runs = SqlAgentRunRepository(factory)
            self.agent_sessions = SqlAgentSessionRepository(factory)
            self.webhook_deliveries = SqlWebhookDeliveryRepository(factory)
            self.eval_runs = SqlEvalRunRepository(factory)
        else:
            self.engine = None
            self.agent_definitions = InMemoryAgentDefinitionRepository()
            self.agent_deployments = InMemoryAgentDeploymentRepository()
            self.deployment_audit = InMemoryDeploymentAuditRepository()
            self.agent_runs = InMemoryAgentRunRepository()
            self.agent_sessions = InMemoryAgentSessionRepository()
            self.webhook_deliveries = InMemoryWebhookDeliveryRepository()
            self.eval_runs = InMemoryEvalRunRepository()
```

### 8.1 接入 create_app()

`api/app.py` 中 `create_app()` 的变更：

```python
from agent_platform.persistence.provider import RepositoryProvider

def create_app() -> FastAPI:
    # ... 现有代码 ...

    repos = RepositoryProvider()

    # 替换现有的内存 store
    runtime_manager = RuntimeManager(
        run_store=repos.agent_runs,
        session_store=repos.agent_sessions,
    )

    # 替换 DeploymentAuditLog（不再使用内存 list）
    # audit_log = DeploymentAuditLog()  # 删除
    # 改用 repos.deployment_audit

    # 替换 webhook_deliveries: set[str]
    # webhook_deliveries: set[str] = set()  # 删除
    # 改用 repos.webhook_deliveries

    app.state.repos = repos

    # 生命周期管理
    @app.on_event("startup")
    async def startup():
        if repos.engine:
            from agent_platform.persistence.engine import init_db
            await init_db(repos.engine)

    @app.on_event("shutdown")
    async def shutdown():
        if repos.engine:
            await repos.engine.dispose()

    # webhook 端点变更示例：
    @app.post("/api/v1/integrations/plane/webhook")
    async def plane_webhook(request: Request, ...):
        # ...
        if x_plane_delivery:
            if await repos.webhook_deliveries.exists(x_plane_delivery):
                return {"status": "duplicate", ...}
            await repos.webhook_deliveries.record(
                delivery_id=x_plane_delivery,
                source="plane",
                event_type=x_plane_event,
            )
        # ...

    # deployment audit 端点变更示例：
    @app.get("/api/v1/deployments/audit")
    async def deployment_audit(
        agent_id: str | None = None,
        channel: str | None = None,
        limit: int = 50,
    ):
        events = await repos.deployment_audit.list_events(
            agent_id=agent_id, channel=channel, limit=limit,
        )
        return [e.model_dump(mode="json") for e in events]

    return app
```

### 8.2 Settings 变更

在 `config.py` 的 `Settings` 中添加 `database_url` 字段。注意：`RepositoryProvider` 直接读环境变量，`Settings` 中的字段仅用于文档和 `/health` 接口展示。

```python
# config.py 变更
class Settings(BaseModel):
    # ... 现有字段 ...
    database_url: str | None = None

@lru_cache
def get_settings() -> Settings:
    return Settings(
        # ... 现有字段 ...
        database_url=os.getenv("DATABASE_URL"),
    )
```

## 9. Alembic 迁移策略

### 9.1 初始化

```bash
cd /Users/errocks/py-workspace/llm-agent-projects/agent-platform

# 初始化 Alembic
alembic init src/agent_platform/persistence/migrations
```

### 9.2 alembic.ini 配置

```ini
# alembic.ini (关键配置)
[alembic]
script_location = src/agent_platform/persistence/migrations

# sqlalchemy.url 留空，运行时从 DATABASE_URL 环境变量读取
sqlalchemy.url =
```

### 9.3 env.py 配置

```python
# src/agent_platform/persistence/migrations/env.py

import os

from alembic import context
from sqlalchemy import create_engine

from agent_platform.persistence.base import Base

# 确保所有表定义被导入，注册到 Base.metadata
import agent_platform.persistence.tables  # noqa: F401

target_metadata = Base.metadata


def get_sync_url() -> str:
    """从 DATABASE_URL 获取同步连接 URL。
    Alembic 迁移使用同步 engine。"""
    url = os.environ["DATABASE_URL"]
    return (
        url.replace("+asyncpg", "")
        .replace("+aiosqlite", "")
    )


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本但不执行。"""
    context.configure(
        url=get_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    engine = create_engine(get_sync_url())

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=True,  # SQLite 需要 batch mode 处理 ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()

    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

关键配置：`render_as_batch=True` 确保 SQLite 的 ALTER TABLE 限制被自动处理（SQLite 不支持 DROP COLUMN / ALTER COLUMN，Alembic batch mode 通过重建表实现）。

### 9.4 迁移工作流

```bash
# 生成迁移脚本（基于 ORM 定义与数据库当前状态的差异）
DATABASE_URL=sqlite:///./dev.db alembic revision --autogenerate \
  -m "initial schema"

# 执行迁移
DATABASE_URL=sqlite:///./dev.db alembic upgrade head

# 回滚一步
DATABASE_URL=sqlite:///./dev.db alembic downgrade -1

# 查看当前版本
DATABASE_URL=sqlite:///./dev.db alembic current

# 检查是否有未生成的迁移（CI 用）
DATABASE_URL=sqlite:///./dev.db alembic check
```

### 9.5 CI 集成

```yaml
# .gitlab-ci.yml 新增

db-migration-check:
  stage: test
  script:
    - pip install -e ".[dev]"
    - pip install aiosqlite
    - DATABASE_URL=sqlite:///./ci-test.db alembic upgrade head
    - DATABASE_URL=sqlite:///./ci-test.db alembic check
```

### 9.6 生产部署流程

```bash
# 1. 先执行迁移
DATABASE_URL=postgresql+psycopg2://... alembic upgrade head

# 2. 再启动服务
DATABASE_URL=postgresql+asyncpg://... uvicorn agent_platform.api.app:app
```

注意：Alembic 使用同步驱动（`psycopg2`），应用使用异步驱动（`asyncpg`）。两者指向同一个数据库，但驱动不同。

## 10. 测试策略

### 10.1 三层测试

| 层级 | 数据库 | Repository 实现 | 运行条件 | 速度 |
|---|---|---|---|---|
| 单元测试 | 无 | InMemory | 始终运行 | 毫秒级 |
| 集成测试 | SQLite (内存) | SQL | 始终运行 | 百毫秒级 |
| E2E 测试 | Postgres | SQL | CI + Postgres 服务可用时 | 秒级 |

### 10.2 Repository Contract Tests

使用 pytest 参数化，同一套测试用例同时覆盖 memory 和 SQL 两种实现，确保行为一致。

```python
# tests/persistence/conftest.py

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

from agent_platform.persistence.base import Base


@pytest_asyncio.fixture
async def sql_session_factory():
    """每个测试函数一个独立的 SQLite 内存数据库。"""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()
```

```python
# tests/persistence/test_agent_run_repo.py

import pytest
import pytest_asyncio

from agent_platform.domain.models import AgentRun, AgentRunStatus
from agent_platform.persistence.memory import InMemoryAgentRunRepository
from agent_platform.persistence.sql import SqlAgentRunRepository


@pytest.fixture
def memory_repo():
    return InMemoryAgentRunRepository()


@pytest_asyncio.fixture
async def sql_repo(sql_session_factory):
    return SqlAgentRunRepository(sql_session_factory)


@pytest.fixture(params=["memory", "sql"])
def repo(request, memory_repo, sql_repo):
    if request.param == "memory":
        return memory_repo
    return sql_repo


def _make_run(
    run_id: str = "run_001",
    agent_id: str = "test-agent",
    session_id: str | None = None,
) -> AgentRun:
    return AgentRun(
        run_id=run_id,
        agent_id=agent_id,
        agent_version="0.1.0",
        runtime_backend="native",
        status=AgentRunStatus.SUCCEEDED,
        latency_ms=42,
        session_id=session_id,
    )


@pytest.mark.asyncio
async def test_record_and_get(repo):
    run = _make_run()
    await repo.record(run)
    result = await repo.get("run_001")
    assert result is not None
    assert result.run_id == "run_001"
    assert result.agent_id == "test-agent"
    assert result.status == AgentRunStatus.SUCCEEDED
    assert result.latency_ms == 42


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(repo):
    result = await repo.get("does_not_exist")
    assert result is None


@pytest.mark.asyncio
async def test_list_runs_filter_by_agent_id(repo):
    await repo.record(_make_run("r1", "agent-a"))
    await repo.record(_make_run("r2", "agent-b"))
    await repo.record(_make_run("r3", "agent-a"))
    results = await repo.list_runs(agent_id="agent-a")
    assert len(results) == 2
    assert all(r.agent_id == "agent-a" for r in results)


@pytest.mark.asyncio
async def test_list_runs_filter_by_session_id(repo):
    await repo.record(_make_run("r1", session_id="s1"))
    await repo.record(_make_run("r2", session_id="s2"))
    await repo.record(_make_run("r3", session_id="s1"))
    results = await repo.list_runs(session_id="s1")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_list_runs_respects_limit(repo):
    for i in range(10):
        await repo.record(_make_run(f"r{i}"))
    results = await repo.list_runs(limit=3)
    assert len(results) == 3


@pytest.mark.asyncio
async def test_list_runs_empty(repo):
    results = await repo.list_runs()
    assert results == []
```

```python
# tests/persistence/test_agent_session_repo.py

import pytest
import pytest_asyncio

from agent_platform.domain.models import AgentSession
from agent_platform.persistence.memory import InMemoryAgentSessionRepository
from agent_platform.persistence.sql import SqlAgentSessionRepository


@pytest.fixture
def memory_repo():
    return InMemoryAgentSessionRepository()


@pytest_asyncio.fixture
async def sql_repo(sql_session_factory):
    return SqlAgentSessionRepository(sql_session_factory)


@pytest.fixture(params=["memory", "sql"])
def repo(request, memory_repo, sql_repo):
    if request.param == "memory":
        return memory_repo
    return sql_repo


def _make_session(
    session_id: str = "sess_001",
    agent_id: str = "test-agent",
) -> AgentSession:
    return AgentSession(
        session_id=session_id,
        agent_id=agent_id,
    )


@pytest.mark.asyncio
async def test_save_and_load(repo):
    session = _make_session()
    await repo.save(session)
    result = await repo.load("sess_001")
    assert result is not None
    assert result.session_id == "sess_001"
    assert result.agent_id == "test-agent"


@pytest.mark.asyncio
async def test_load_nonexistent_returns_none(repo):
    result = await repo.load("does_not_exist")
    assert result is None


@pytest.mark.asyncio
async def test_delete(repo):
    session = _make_session()
    await repo.save(session)
    await repo.delete("sess_001")
    result = await repo.load("sess_001")
    assert result is None


@pytest.mark.asyncio
async def test_delete_nonexistent_no_error(repo):
    await repo.delete("does_not_exist")  # 不应抛异常


@pytest.mark.asyncio
async def test_save_updates_existing(repo):
    session = _make_session()
    await repo.save(session)
    session.add_message("user", "hello")
    await repo.save(session)
    result = await repo.load("sess_001")
    assert result is not None
    assert len(result.history) == 1
    assert result.history[0].content == "hello"


@pytest.mark.asyncio
async def test_list_sessions_filter_by_agent_id(repo):
    await repo.save(_make_session("s1", "agent-a"))
    await repo.save(_make_session("s2", "agent-b"))
    await repo.save(_make_session("s3", "agent-a"))
    results = await repo.list_sessions(agent_id="agent-a")
    assert len(results) == 2
    assert all(s.agent_id == "agent-a" for s in results)


@pytest.mark.asyncio
async def test_list_sessions_no_filter(repo):
    await repo.save(_make_session("s1", "agent-a"))
    await repo.save(_make_session("s2", "agent-b"))
    results = await repo.list_sessions()
    assert len(results) == 2
```

同理为其他 5 个 Repository 编写 contract test。每个测试文件遵循相同模式：
- `memory_repo` fixture + `sql_repo` fixture + `params=["memory", "sql"]` fixture
- 覆盖所有 Protocol 方法
- 测试正常路径、空结果、边界条件

### 10.3 测试文件清单

```
tests/persistence/
    conftest.py                       # 共享 sql_session_factory fixture
    test_agent_definition_repo.py     # save, get, get_latest, list_all, update_status
    test_agent_deployment_repo.py     # save, get, resolve, list_all, delete
    test_deployment_audit_repo.py     # record, list_events, get_rollback_version
    test_agent_run_repo.py            # record, get, list_runs
    test_agent_session_repo.py        # save, load, delete, list_sessions
    test_webhook_delivery_repo.py     # exists, record
    test_eval_run_repo.py             # record, get_latest, list_runs
```

### 10.4 现有测试的兼容

现有单元测试（`tests/unit/test_session.py`、`tests/unit/test_observability.py` 等）使用 `InMemorySessionStore` 和 `InMemoryRunStore`。这些测试不受影响，不需要修改。旧的 InMemory 实现不删除，标记 deprecated 即可。

## 11. 文件结构总览

```
src/agent_platform/persistence/
    __init__.py
    base.py                  # Base, AuditMixin
    tables.py                # 7 个 ORM Row class
    repositories.py          # 7 个 Protocol 定义
    memory.py                # 7 个 InMemory 实现
    sql.py                   # 7 个 SQL 实现
    engine.py                # create_engine_from_url, create_session_factory, init_db
    provider.py              # RepositoryProvider (DI)
    context.py               # AuditContext (contextvars)
    migrations/
        env.py               # Alembic env 配置
        script.mako           # Alembic 迁移脚本模板
        versions/
            0001_initial_schema.py   # 第一个迁移文件

tests/persistence/
    conftest.py              # 共享 fixture (sql_session_factory)
    test_agent_definition_repo.py
    test_agent_deployment_repo.py
    test_deployment_audit_repo.py
    test_agent_run_repo.py
    test_agent_session_repo.py
    test_webhook_delivery_repo.py
    test_eval_run_repo.py
```

## 12. 实施步骤

### Phase 1: 基础设施 (预计 1-2 天)

1. 在 `pyproject.toml` 添加依赖：`sqlalchemy[asyncio]>=2.0.30`、`alembic>=1.13`。dev 依赖添加 `aiosqlite>=0.20`。
2. 创建 `src/agent_platform/persistence/__init__.py`。
3. 实现 `base.py`：`Base` 和 `AuditMixin`。
4. 实现 `tables.py`：7 张表的 ORM Row class。
5. 实现 `engine.py`：`create_engine_from_url`、`create_session_factory`、`init_db`。
6. 实现 `context.py`：`AuditContext` + `get_audit_context` / `set_audit_context`。
7. 初始化 Alembic（`alembic init`），配置 `alembic.ini` 和 `env.py`。
8. 生成初始迁移：`alembic revision --autogenerate -m "initial schema"`。
9. 验证：`DATABASE_URL=sqlite+aiosqlite:///./dev.db alembic upgrade head` 成功。

### Phase 2: Repository 实现 (预计 2-3 天)

1. 实现 `repositories.py`：7 个 Protocol 定义。
2. 实现 `memory.py`：7 个 InMemory 实现。
3. 实现 `sql.py`：7 个 SQL 实现（每个包含 `_to_domain` 转换方法）。
4. 实现 `provider.py`：`RepositoryProvider`。
5. 编写 `tests/persistence/conftest.py` 和 7 个 contract test 文件。
6. 运行测试，确保 14 个实现（7 memory + 7 SQL）全部通过 contract test。

### Phase 3: 接入主流程 (预计 1-2 天)

1. 修改 `config.py`：`Settings` 添加 `database_url` 字段。
2. 修改 `api/app.py` `create_app()`：
   - 创建 `RepositoryProvider`。
   - `RuntimeManager` 改用 `repos.agent_runs` 和 `repos.agent_sessions`。
   - webhook 端点改用 `repos.webhook_deliveries.exists()` 和 `repos.webhook_deliveries.record()`。
   - 部署审计改用 `repos.deployment_audit`。
   - 添加 startup/shutdown lifecycle hook。
3. 修改 `RequestContextMiddleware`：调用 `set_audit_context()`。
4. 修改 `RuntimeManager`：将 `run_store` 和 `session_store` 参数类型改为新 Protocol，方法调用加 `await`。
5. `EvalRunner.run_agent_to_file()` 结果额外写入 `repos.eval_runs.record()`。

### Phase 4: 验收 (预计 1 天)

执行以下验收场景：

1. 设置 `DATABASE_URL=sqlite+aiosqlite:///./dev.db`，启动服务。
2. 执行 chat、deploy、rollback、webhook 操作。
3. 杀掉进程（`kill -9`），重新启动。
4. 查询 deployment、session、run、webhook delivery 数据仍在。
5. 不设置 `DATABASE_URL`，启动服务，验证 memory 模式功能正常。
6. 运行全部测试 `pytest tests/`，确认无回归。

## 13. 验收标准

| # | 标准 | 验证方式 |
|---|---|---|
| AC-1 | 服务重启后 deployment/session/run/webhook delivery 不丢 | 设置 `DATABASE_URL` 启动服务，写入数据，杀进程，重启，查询数据存在 |
| AC-2 | memory store 仍可用于单测 | 不设置 `DATABASE_URL` 启动服务，功能正常；单元测试使用 InMemory 实现，全部通过 |
| AC-3 | repository contract tests 覆盖 memory 和 SQL 实现 | 每个 Protocol 对应一个 test 文件，`@pytest.fixture(params=["memory", "sql"])` 参数化，所有测试通过 |
| AC-4 | Alembic migration 可正常执行和回滚 | `alembic upgrade head` 和 `alembic downgrade -1` 无报错 |
| AC-5 | 所有写入记录包含 request_id、created_by、tenant_id、created_at、updated_at | 用 SQL 查询数据库，确认审计字段被填充 |
| AC-6 | 现有测试无回归 | `pytest tests/unit/` 全部通过，无修改 |
