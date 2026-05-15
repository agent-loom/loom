# ADR-0002: Storage Baseline -- Repository Protocol + Dual Implementation

## Status

Accepted

## Date

2026-05-16

## Context

The platform needed persistence that survives process restarts for production use. Session history, agent runs, deployment records, webhook deliveries, and eval results were all stored in module-level singletons (`InMemoryRunStore`, `InMemorySessionStore`, `DeploymentAuditLog`) that lose data on every restart. This is acceptable for local development but unacceptable for production.

We needed a design that:

1. Keeps zero-config local development working out of the box.
2. Allows production deployments to use a real database.
3. Keeps business logic decoupled from storage technology.
4. Makes both backends testable with the same test suite.

## Decision

### 1. Repository Protocol pattern with `@runtime_checkable`

Seven `Protocol` classes in `persistence/repositories.py` define the async interfaces:

- `AgentDefinitionRepository`
- `AgentDeploymentRepository`
- `DeploymentAuditRepository`
- `AgentRunRepository`
- `AgentSessionRepository`
- `WebhookDeliveryRepository`
- `EvalRunRepository`

All protocols use `@runtime_checkable` so implementations can be verified with `isinstance()` at startup.

### 2. Dual implementation: InMemory (default) + SQL (opt-in)

- **InMemory** (`persistence/memory.py`): dict/list-backed, zero dependencies, suitable for tests and local dev. Seven classes, one per protocol.
- **SQL** (planned/opt-in): SQLAlchemy-backed, activated when `DATABASE_URL` is set.

### 3. SQLAlchemy 2.0 async with `DeclarativeBase` + `AuditMixin`

ORM tables are defined in `persistence/tables.py` using SQLAlchemy 2.0 `Mapped`/`mapped_column` style with a shared `AuditMixin` that provides:

- `id` (UUID hex, primary key)
- `tenant_id` (nullable, indexed)
- `created_by`, `request_id`
- `created_at`, `updated_at` (auto-populated, timezone-aware)

Seven table classes map 1:1 with the repository protocols:

| Table                       | Protocol                       |
|-----------------------------|--------------------------------|
| `agent_definitions`         | `AgentDefinitionRepository`    |
| `agent_deployments`         | `AgentDeploymentRepository`    |
| `deployment_audit_events`   | `DeploymentAuditRepository`    |
| `agent_runs`                | `AgentRunRepository`           |
| `agent_sessions`            | `AgentSessionRepository`       |
| `webhook_deliveries`        | `WebhookDeliveryRepository`    |
| `eval_runs`                 | `EvalRunRepository`            |

### 4. Alembic for schema migrations

Alembic is configured at the project root (`alembic.ini`, `alembic/`) for version-controlled schema migrations against the SQL backend.

### 5. DI at app startup

Backend selection is driven by the `DATABASE_URL` environment variable:

- **Set**: create an async SQLAlchemy engine and inject SQL repository instances.
- **Unset** (default): inject `InMemory*` repository instances.

This keeps the application code agnostic to the storage backend.

### 6. Contract tests parametrized across both implementations

The same test suite runs against both InMemory and SQL implementations, ensuring behavioral parity. Tests are parametrized via pytest fixtures that yield each backend.

## Alternatives Considered

### A. Single SQL-only implementation

Simpler to maintain but forces every developer to run a database locally. Rejected because zero-config dev experience is a stated goal.

### B. Abstract base class instead of Protocol

`ABC` would enforce method signatures at class definition time, but `Protocol` is more Pythonic for structural typing and does not require inheritance. Protocols also work better with dependency injection and mocking.

### C. Raw SQL / query builder (no ORM)

Lower abstraction but higher maintenance cost for CRUD-heavy tables. SQLAlchemy 2.0 mapped columns give us type safety without the weight of older-style ORM patterns.

## Consequences

### Positive

1. Clean separation of domain logic from storage -- business code depends only on protocols.
2. Zero-config for local development and CI -- InMemory backend requires no database.
3. Production-ready with SQL backend via a single environment variable.
4. `AuditMixin` guarantees consistent multi-tenant fields and timestamps across all tables.
5. Contract tests catch behavioral drift between implementations.

### Negative

1. Two implementations per repository must be kept in sync when interfaces change.
2. Async-everywhere adds complexity to code that is fundamentally synchronous (dict lookups).

### Neutral

1. Old sync stores (`RunStore`, `SessionStore`, `DeploymentAuditLog`) are deprecated but not yet removed. They will be deleted once all call sites migrate to the new repositories.

## Key Files

- `src/agent_platform/persistence/repositories.py` -- 7 Protocol definitions
- `src/agent_platform/persistence/tables.py` -- 7 ORM table classes + `AuditMixin`
- `src/agent_platform/persistence/memory.py` -- 7 InMemory implementations
- `src/agent_platform/storage/base.py` -- SQLAlchemy `DeclarativeBase`
- `alembic.ini`, `alembic/` -- migration configuration
