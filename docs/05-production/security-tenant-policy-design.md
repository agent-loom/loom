# 安全、租户、Policy 与 Secret 设计

> Status: Draft
> Stage: S2
> Owner: platform
> Last verified against code: 2026-05-15

本文档是 `next-stage-design-plan.md` P0-6 的交付物。设计目标：在 Agent Platform 进入生产前，建立统一的鉴权、租户隔离、工具权限、Secret 管理、日志脱敏和高风险工具审批机制。

读者：AI coding agent 和平台开发者。

## 目录

1. [当前状态与问题](#1-当前状态与问题)
2. [Authentication 模型](#2-authentication-模型)
3. [Authorization 模型](#3-authorization-模型)
4. [租户隔离](#4-租户隔离)
5. [Domain Model 泛化](#5-domain-model-泛化)
6. [PolicyEngine 接入主流程](#6-policyengine-接入主流程)
7. [Tool Permission 矩阵](#7-tool-permission-矩阵)
8. [Secret 管理](#8-secret-管理)
9. [Trace 与日志脱敏](#9-trace-与日志脱敏)
10. [高风险工具 Human-in-the-Loop](#10-高风险工具-human-in-the-loop)
11. [实施计划](#11-实施计划)
12. [验收标准](#12-验收标准)

---

## 1. 当前状态与问题

### 1.1 现有安全能力

| 能力 | 当前实现 | 文件 | 问题 |
|---|---|---|---|
| API 鉴权 | 全局 API key，`AuthMiddleware` 比对 `Bearer` 或 `x-api-key` | `api/app.py:127-153` | 单一 key，无角色、无租户、无用户身份 |
| 租户标识 | `x-tenant-id` header 注入 `request.state.tenant_id` | `api/app.py:117-124` | 只注入不校验，不阻止跨租户访问 |
| PolicyEngine | 加载 YAML safety rules，实现 `check_input`/`check_output`/`check_tool_allowed`/`check_commands`/`route_intent` | `policy/engine.py` (185 行) | **完全未接入 runtime**。在 `create_app()` 中存入 `app.state.policy_engine`，但 chat、tool 执行、response 构建均未调用 |
| 工具权限 | `ToolExecutor.execute()` 使用 `allowed_tools` 参数做 manifest allow-list 校验 | `tools/executor.py:36-47` | 只检查 manifest allow-list，不检查 tenant/environment/risk-level |
| Secret | 平台密钥通过环境变量加载（`PLANE_API_KEY`、`GITLAB_TOKEN` 等） | `config.py` | 无统一 secret 引用格式，无运行时注入，无防泄露机制 |
| 日志 | JSON 结构化日志，`JSONFormatter` 输出到 stderr | `observability/logging_config.py` | 无 PII/secret 脱敏过滤 |
| Trace | `InMemoryRunStore` 记录 `AgentRun`（含 `ToolCallTrace`） | `observability/trace.py` | trace 数据可能包含工具参数中的敏感信息，无脱敏 |

### 1.2 核心问题总结

1. **PolicyEngine 是死代码**：被实例化、存入 `app.state`，但运行链路（`chat` handler -> `RuntimeManager.run` -> `ToolExecutor.execute`）没有任何一处调用它。
2. **无租户隔离**：`tenant_id` 只是一个透传字段，查询 session、run、deployment 时不做 tenant 过滤。
3. **无角色授权**：所有持有 API key 的调用者权限相同。
4. **Secret 散落在环境变量**：没有统一引用格式，agent manifest 如果需要引用 API key 没有安全方式。
5. **日志/trace 无脱敏**：工具参数、用户输入、LLM 输出中的手机号、身份证等直接写入日志和 trace。
6. **高风险工具无管控**：任何 manifest 声明的工具都可以直接执行，无分级审批。

---

## 2. Authentication 模型

### 2.1 当前：全局 API Key

```
Client --[x-api-key: KEY]--> AuthMiddleware --[pass/reject]--> Handler
```

当前 `AuthMiddleware`（`api/app.py:127-153`）接受两种方式：
- `Authorization: Bearer <key>`
- `x-api-key: <key>`

单一 key，不区分调用者身份。

### 2.2 目标：API Key -> JWT 迁移路径

分三步迁移，每步向后兼容。

**Phase 1 -- Scoped API Key（当前阶段立即实施）**

引入 `ApiKeyRecord` 数据结构，替代单一全局 key：

```python
class ApiKeyRecord(BaseModel):
    key_id: str                    # 唯一标识，用于审计
    key_hash: str                  # bcrypt/argon2 hash，不存明文
    tenant_id: str                 # 绑定租户
    role: str                      # platform_admin | agent_developer | agent_operator | readonly
    scopes: list[str]              # ["chat", "deploy", "admin"] 允许的操作范围
    created_by: str                # 创建者
    expires_at: datetime | None    # 过期时间
    active: bool = True
```

验证流程：
1. 从 header 取 key 明文。
2. 在 `api_keys` 表中查找 `key_hash` 匹配的记录。
3. 校验 `active`、`expires_at`。
4. 将 `tenant_id`、`role`、`scopes` 写入 `request.state`。
5. key 明文不进入日志。

存储：持久化到 DB（复用 `persistence-storage-design.md` 的 repository 层）。本地开发可用内存 store + 环境变量 fallback。

**Phase 2 -- JWT Bearer Token（后续实施）**

适用于有 IdP（Keycloak / Auth0 / 自建）的部署场景：

```
Client --[Authorization: Bearer <JWT>]--> AuthMiddleware
  --> 验证签名 (RS256, JWKS endpoint)
  --> 解析 claims: sub, tenant_id, role, scopes, exp
  --> 写入 request.state
```

JWT claims 结构：

```json
{
  "sub": "user-123",
  "tenant_id": "tenant-abc",
  "role": "agent_operator",
  "scopes": ["chat", "deploy"],
  "exp": 1748000000
}
```

配置项：

```python
class AuthSettings(BaseModel):
    mode: Literal["api_key", "jwt", "api_key+jwt"] = "api_key"
    jwt_jwks_url: str | None = None       # JWKS endpoint
    jwt_issuer: str | None = None         # expected issuer
    jwt_audience: str | None = None       # expected audience
```

**Phase 3 -- 服务间鉴权（后续实施）**

平台内部组件（DevFlow worker、CodingAgentRunner）使用短期 JWT + mTLS 互相认证。不在本文档详细设计。

### 2.3 AuthMiddleware 重构

```python
class AuthMiddleware(BaseHTTPMiddleware):
    """统一鉴权中间件，支持 api_key 和 jwt 两种模式。"""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        identity = await self._authenticate(request)
        if identity is None:
            return JSONResponse(status_code=401, content={"error": {"code": "UNAUTHORIZED"}})

        request.state.auth = identity  # AuthIdentity(tenant_id, role, scopes, subject)
        return await call_next(request)
```

`AuthIdentity` 是后续所有授权检查的输入：

```python
class AuthIdentity(BaseModel):
    subject: str           # 调用者标识 (user_id 或 service_id)
    tenant_id: str         # 租户
    role: str              # 角色
    scopes: list[str]      # 操作范围
    key_id: str | None     # API key 模式下的 key_id，用于审计
```

---

## 3. Authorization 模型

### 3.1 RBAC 角色定义

| 角色 | 描述 | 典型操作 |
|---|---|---|
| `platform_admin` | 平台管理员 | 管理所有租户的 agent、deployment、API key、policy；查看全局 trace/audit |
| `agent_developer` | Agent 开发者 | 注册/更新/部署 agent（dev/staging）；运行 eval；查看自己租户的 trace |
| `agent_operator` | Agent 运维 | 部署到 prod（需审批）；回滚；查看 trace/metrics；管理 deployment |
| `readonly` | 只读 | 查看 agent 列表、deployment 状态、session（脱敏）；不能修改任何资源 |

### 3.2 权限矩阵

| 操作 | `platform_admin` | `agent_developer` | `agent_operator` | `readonly` |
|---|---|---|---|---|
| `POST /agent/chat` | Y | Y | Y | N |
| `POST /agent-packages/register` | Y | Y | N | N |
| `POST /.../deploy` (dev) | Y | Y | N | N |
| `POST /.../deploy` (staging) | Y | Y | Y | N |
| `POST /.../deploy` (prod) | Y | N | Y (需审批) | N |
| `POST /deployments/rollback` | Y | N | Y | N |
| `GET /agent-runs` | Y (全局) | Y (本租户) | Y (本租户) | Y (本租户,脱敏) |
| `GET /sessions/*` | Y (全局) | Y (本租户) | Y (本租户) | Y (本租户,脱敏) |
| `POST /evals/run` | Y | Y | Y | N |
| `GET /deployments/audit` | Y (全局) | Y (本租户) | Y (本租户) | Y (本租户) |
| API key 管理 | Y | N | N | N |
| Policy 管理 | Y | Y (本租户) | N | N |

### 3.3 授权实现

在路由 handler 层使用 `require_role` / `require_scope` 依赖注入：

```python
from fastapi import Depends

def require_role(*roles: str):
    """FastAPI dependency：校验当前调用者角色。"""
    async def check(request: Request):
        auth: AuthIdentity = request.state.auth
        if auth.role not in roles:
            raise HTTPException(status_code=403, detail="insufficient role")
        return auth
    return Depends(check)

def require_scope(scope: str):
    """FastAPI dependency：校验当前调用者 scope。"""
    async def check(request: Request):
        auth: AuthIdentity = request.state.auth
        if scope not in auth.scopes:
            raise HTTPException(status_code=403, detail=f"missing scope: {scope}")
        return auth
    return Depends(check)

# 使用示例
@app.post("/api/v1/agent-packages/register")
async def register_agent(
    payload: RegisterAgentRequest,
    auth: AuthIdentity = require_role("platform_admin", "agent_developer"),
): ...
```

### 3.4 租户数据过滤

所有返回列表的 API 端点必须按 `auth.tenant_id` 过滤：

```python
# 非 platform_admin 只能看到自己租户的数据
if auth.role != "platform_admin":
    runs = [r for r in runs if r.tenant_id == auth.tenant_id]
```

这在 repository 层实现为统一的 `tenant_scope` 参数：

```python
class RunRepository(Protocol):
    def list_runs(self, tenant_id: str | None = None) -> list[AgentRun]: ...
```

`tenant_id=None` 表示全局查询（仅 `platform_admin` 允许）。

---

## 4. 租户隔离

### 4.1 tenant_id 的语义

`tenant_id` 是 Agent Platform 的**顶层隔离边界**。一个 tenant 代表一个独立的组织或业务单元。

**隔离保证**：
- 一个 tenant 的 agent run、session、deployment、audit event 对其他 tenant 不可见。
- 一个 tenant 的 policy、secret、tool permission 不影响其他 tenant。
- `platform_admin` 可以跨租户操作。

### 4.2 tenant_id 注入链路

```
Request header: x-tenant-id
       |
       v
AuthMiddleware: 从 ApiKeyRecord 或 JWT claims 获取 tenant_id
       |
       v (校验 header tenant_id 与 auth tenant_id 一致，或 auth 是 platform_admin)
       |
       v
request.state.auth.tenant_id  <-- 后续所有操作使用此值
       |
       v
AgentRequest.context.tenant.tenant_id  <-- 注入请求上下文
       |
       v
RuntimeManager.run()  <-- tenant_id 传入 session、run record
       |
       v
ToolExecutor.execute()  <-- tenant_id 用于 policy 查询和 secret 注入
       |
       v
Repository.save()  <-- tenant_id 写入所有持久化对象
```

### 4.3 tenant_id 与 org_id、location_id 的边界

| 概念 | 语义 | 来源 | 用途 |
|---|---|---|---|
| `tenant_id` | 平台级隔离单元，对应一个组织/公司/业务方 | auth 系统 | 数据隔离、权限边界、计费 |
| `org_id` | 租户内的组织结构 ID（原 `retailer_id`） | 请求上下文 | 业务路由、组织级配置 |
| `location_id` | 租户内的位置 ID（原 `store_id`） | 请求上下文 | 位置级数据过滤（如门店库存） |

层级关系：

```
Platform
  |-- Tenant (tenant_id)         -- 平台级隔离
       |-- Organization (org_id) -- 业务组织（原 retailer_id）
            |-- Location (location_id)  -- 位置（原 store_id）
```

规则：
- `tenant_id` 是平台强制字段（Phase 1 可选，Phase 2 起必填）。
- `org_id` 和 `location_id` 是业务可选字段，由 agent manifest 的 `context.required` 声明是否必填。
- 所有持久化表必须包含 `tenant_id` 列。
- 所有查询接口的 repository 方法必须接受 `tenant_id` 参数。

### 4.4 DB 层隔离策略

使用 **行级隔离**（Row-Level Security 或 WHERE 条件），不使用 schema-per-tenant。

理由：
1. 租户数量预期在百级，不需要 schema 级隔离。
2. 行级隔离实现简单，可在 repository 层统一处理。
3. 后续如需更强隔离，可迁移到 schema-per-tenant 而不改 API 层。

```sql
-- 所有核心表都有 tenant_id 列
CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    -- ...other columns...
);

CREATE INDEX idx_runs_tenant ON agent_runs(tenant_id);

-- repository 层自动附加 WHERE tenant_id = ?
```

---

## 5. Domain Model 泛化

> 此变更需同步更新 [`01-contracts/agent-request-response.md`](../01-contracts/agent-request-response.md)，建议通过 ADR 记录。

### 5.1 变更清单

基于 `agent-platform-core-design.md` 第 5 节的设计，执行以下重命名：

| 当前 | 目标 | 文件 | 影响范围 |
|---|---|---|---|
| `StoreContext` | `LocationContext` | `domain/models.py:35-37` | model, API contract, tests |
| `StoreContext.store_id` | `LocationContext.location_id` | `domain/models.py:36` | model, session, tests |
| `StoreContext.store_name` | `LocationContext.location_name` | `domain/models.py:37` | model, tests |
| `RequestContext.store` | `RequestContext.location` | `domain/models.py:57` | model, context_builder, tests |
| `TenantContext.retailer_id` | `TenantContext.org_id` | `domain/models.py:32` | model, router, tests |
| `AgentSession.store_id` | `AgentSession.location_id` | `domain/models.py:374` | session, manager, tests |
| `UserContext.member_id` | `UserContext.external_id` | `domain/models.py:51` | model, tests |
| `RequestContext.locale` 默认值 | `"en"` | `domain/models.py:61` | model |
| `RequestContext.timezone` 默认值 | `"UTC"` | `domain/models.py:62` | model |

### 5.2 向后兼容

在 Pydantic model 上使用 `Field(alias=...)` 和 `model_config` 提供过渡期兼容：

```python
class TenantContext(BaseModel):
    tenant_id: str | None = None
    org_id: str | None = Field(default=None, alias="retailer_id")  # 过渡期兼容

    model_config = ConfigDict(populate_by_name=True)

class LocationContext(BaseModel):
    location_id: str | None = Field(default=None, alias="store_id")
    location_name: str | None = Field(default=None, alias="store_name")

    model_config = ConfigDict(populate_by_name=True)
```

过渡期结束（下一个大版本）后移除 alias。

### 5.3 RequestContext 增加 extra

```python
class RequestContext(BaseModel):
    tenant: TenantContext = Field(default_factory=TenantContext)
    location: LocationContext = Field(default_factory=LocationContext)
    channel: ChannelContext = Field(default_factory=ChannelContext)
    device: DeviceContext = Field(default_factory=DeviceContext)
    user: UserContext = Field(default_factory=UserContext)
    locale: str = "en"
    timezone: str = "UTC"
    extra: dict[str, Any] = Field(default_factory=dict)
```

业务 Agent（如 MYJ）通过 `extra` 传递行业特定字段（`retailer_id`、`store_code` 等），由 Agent Package adapter 解析。

---

## 6. PolicyEngine 接入主流程

### 6.1 当前问题

`PolicyEngine` 在 `create_app()` 中被实例化并存入 `app.state.policy_engine`（`api/app.py:173, 180`），但：

- `chat` handler（`api/app.py:439-498`）不调用任何 policy check。
- `RuntimeManager.run()`（`runtime/manager.py:43-100`）不调用 policy check。
- `ToolExecutor.execute()`（`tools/executor.py:27-143`）只检查 manifest allow-list，不调用 `PolicyEngine`。

PolicyEngine 的5个 public 方法（`check_input`、`check_output`、`check_commands`、`check_tool_allowed`、`route_intent`）全部是死代码。

### 6.2 接入方案

在 runtime 执行管线的4个关键点插入 policy check。参照 `agent-platform-core-design.md` 3.10 节的设计。

**接入点 1：请求入口 -- input check**

位置：`api/app.py` 的 `chat` handler，在路由之后、`RuntimeManager.run()` 之前。

```python
# api/app.py chat handler 中，route 解析后
policy_engine: PolicyEngine = app.state.policy_engine
policy_set = policy_engine.load_policies(route.agent_spec)

input_violations = policy_engine.check_input(request.input.query, policy_set)
if input_violations:
    return JSONResponse(
        status_code=400,
        content=_error_response(
            request,
            code="POLICY_VIOLATION",
            message=f"input blocked: {input_violations[0].message}",
            status_code=400,
        ).model_dump(mode="json"),
    )
```

**接入点 2：工具执行前 -- tool permission check**

位置：`ToolExecutor.execute()`，在 manifest allow-list 校验之后、实际执行之前。

```python
# tools/executor.py execute() 中，在 allowed_tools 检查之后
if self.policy_engine and policy_set:
    tool_violations = self.policy_engine.check_tool_allowed(tool_name, policy_set)
    if tool_violations:
        return ToolExecutionResult(
            tool_name=tool_name,
            output={"error": f"tool blocked by policy: {tool_violations[0].message}"},
            trace=ToolCallTrace(
                tool_name=tool_name,
                latency_ms=self._latency_ms(started),
                status="denied",
                error="POLICY_DENIED",
            ),
        )
```

`ToolExecutor` 需要增加 `policy_engine` 和 `policy_set` 参数（或通过 context 注入）。

**接入点 3：响应返回前 -- output check**

位置：`RuntimeManager.run()`，在构建 response 之后、返回之前。

```python
# runtime/manager.py run() 中，在 response 构建之后
if self.policy_engine and policy_set:
    output_text = response.response.output.text.display
    output_violations = self.policy_engine.check_output(output_text, policy_set)
    if output_violations:
        # 对 warning 级别：记录但不阻断
        # 对 error 级别：替换响应文本
        for v in output_violations:
            if v.severity == "error":
                response.response.output.text.display = "内容已被安全策略过滤"
                response.response.output.status = OutputStatus.REJECTED
                break
```

**接入点 4：命令输出 -- command check**

位置：`ResponseBuilder` 或 `RuntimeManager.run()` 返回前。

```python
if response.response.output.commands:
    cmd_violations = policy_engine.check_commands(
        [c.model_dump() for c in response.response.output.commands],
        policy_set.output_config.get("command_allowlist", []),
    )
    if cmd_violations:
        response.response.output.commands = [
            c for c in response.response.output.commands
            if c.name in policy_set.output_config.get("command_allowlist", [])
        ]
```

### 6.3 PolicyEngine 依赖传递

当前 `PolicyEngine` 在 `app.state` 上，但 `RuntimeManager` 和 `ToolExecutor` 没有引用。需要通过以下方式传递：

**方案 A -- 构造函数注入（推荐）**：

```python
class RuntimeManager:
    def __init__(
        self,
        run_store: RunStore | None = None,
        session_store: SessionStore | None = None,
        policy_engine: PolicyEngine | None = None,  # 新增
    ):
        self.policy_engine = policy_engine
        # ...existing init...
```

在 `create_app()` 中：

```python
app_policy_engine = PolicyEngine()
runtime_manager = RuntimeManager(policy_engine=app_policy_engine)
```

**方案 B -- RuntimeRequest 携带 policy_set**：

```python
class RuntimeRequest(BaseModel):
    # ...existing fields...
    policy_set: PolicySet | None = None  # 新增
```

在 `chat` handler 中加载 policy_set 并传入 RuntimeRequest。

推荐方案 A + B 结合使用：A 传递 engine 实例，B 传递已加载的 policy_set 避免重复加载。

### 6.4 PolicyEngine 增强

当前 PolicyEngine 只支持基于 YAML 的静态规则。为支持 tenant/environment 级 policy，扩展 `load_policies`：

```python
class PolicyEngine:
    def load_policies(
        self,
        spec: AgentSpec,
        *,
        tenant_id: str | None = None,
        environment: str = "dev",
    ) -> PolicySet:
        # 1. 加载 agent package 级 policy（从 manifest 引用的 YAML）
        agent_policy = self._load_agent_policy(spec)
        # 2. 加载 tenant 级 policy（从 DB 或配置）
        tenant_policy = self._load_tenant_policy(tenant_id)
        # 3. 加载 environment 级 policy（prod 比 dev 更严格）
        env_policy = self._load_env_policy(environment)
        # 4. 合并：env > tenant > agent（更严格的优先）
        return self._merge_policies(agent_policy, tenant_policy, env_policy)
```

合并规则：
- `safety_rules`：取所有层级的并集（更多规则 = 更严格）。
- `deny_tools`：取并集。
- `allow_tools`：取交集（只有所有层级都允许才允许）。
- `routing_rules`：只使用 agent 层。

---

## 7. Tool Permission 矩阵

### 7.1 权限计算模型

一个工具是否允许执行，由三层权限的**交集**决定：

```
最终权限 = Agent manifest allow-list
         n Tenant tool policy
         n Environment tool policy
```

### 7.2 Agent 层：manifest 声明

当前已实现。agent manifest 声明 `tools.allow` 和 `tools.deny`：

```yaml
# agents/myj/manifest.yaml
tools:
  allow:
    - myj.goods_search
    - myj.goods_location
    - myj.promotion_lookup
    - myj.store_consult
  deny: []
```

### 7.3 Tenant 层：租户工具策略

每个 tenant 可以配置允许/禁止的工具，存储在 DB 中：

```python
class TenantToolPolicy(BaseModel):
    tenant_id: str
    allow_tools: list[str] = []      # 空 = 不限制（使用 agent 层）
    deny_tools: list[str] = []       # 优先于 allow
    max_calls_per_minute: int = 60   # 租户级工具调用限流
```

### 7.4 Environment 层：环境工具策略

按 `dev` / `staging` / `prod` 环境配置不同的工具策略：

```yaml
# config/tool_policy.yaml (平台级配置)
environments:
  dev:
    default_action: allow           # dev 环境默认允许
    deny_tools: []
  staging:
    default_action: allow
    deny_tools:
      - "*.write_production_db"     # 通配符匹配
  prod:
    default_action: deny            # prod 默认拒绝，只允许白名单
    allow_tools:
      - myj.goods_search
      - myj.goods_location
      - myj.promotion_lookup
      - myj.store_consult
    high_risk_tools:                # 需要审批的工具
      - "*.delete_*"
      - "*.write_*"
      - "*.execute_*"
```

### 7.5 工具风险分级

| 风险等级 | 描述 | 策略 |
|---|---|---|
| `low` | 只读查询（search, lookup, read） | 所有环境允许 |
| `medium` | 有副作用的写操作（create, update） | staging 允许，prod 需要 operator 角色 |
| `high` | 破坏性操作（delete, execute, send_email, call_api） | prod 默认拒绝，需要 human-in-the-loop 审批 |
| `critical` | 涉及资金、账户、权限变更 | 所有环境需要 human-in-the-loop |

工具风险等级在 `ToolDefinition` 中声明：

```python
class ToolDefinition(BaseModel):
    name: str
    description: str
    risk_level: Literal["low", "medium", "high", "critical"] = "low"  # 新增
    # ...existing fields...
```

### 7.6 权限计算伪代码

```python
class ToolPermissionDecision:
    """权限计算结果基类。"""
    pass

class Allowed(ToolPermissionDecision):
    pass

class Denied(ToolPermissionDecision):
    reason: str

class RequiresApproval(ToolPermissionDecision):
    reason: str


def compute_tool_permission(
    tool_name: str,
    agent_spec: AgentSpec,
    tenant_policy: TenantToolPolicy | None,
    environment: str,
    env_policy: EnvironmentToolPolicy,
    tool_definition: ToolDefinition,
) -> ToolPermissionDecision:
    # Step 1: Agent manifest check
    if tool_name in agent_spec.manifest.tools.deny:
        return Denied(reason="agent manifest deny-list")
    if agent_spec.manifest.tools.allow and tool_name not in agent_spec.manifest.tools.allow:
        return Denied(reason="agent manifest allow-list")

    # Step 2: Tenant policy check
    if tenant_policy:
        if tool_name in tenant_policy.deny_tools:
            return Denied(reason="tenant deny-list")
        if tenant_policy.allow_tools and tool_name not in tenant_policy.allow_tools:
            return Denied(reason="tenant allow-list")

    # Step 3: Environment policy check
    if tool_name in env_policy.deny_tools:
        return Denied(reason="environment deny-list")
    if env_policy.default_action == "deny" and tool_name not in env_policy.allow_tools:
        return Denied(reason="environment default deny")

    # Step 4: Risk-level gating
    if tool_definition.risk_level in ("high", "critical"):
        if environment == "prod":
            return RequiresApproval(reason="high-risk tool in prod")
    if tool_definition.risk_level == "critical":
        if environment == "staging":
            return RequiresApproval(reason="critical tool in staging")

    return Allowed()
```

---

## 8. Secret 管理

### 8.1 设计原则

1. Secret 明文**只存在于** secret backend（环境变量 / Vault / DB encrypted column）。
2. Agent manifest、日志、trace、API response 中**永远不出现** secret 明文。
3. 运行时通过引用格式注入，tool handler 拿到的是解析后的值。

### 8.2 Secret 引用格式

在 agent manifest 和 tool 配置中使用 `$secret:KEY_NAME` 格式引用 secret：

```yaml
# agents/myj/manifest.yaml
models:
  main:
    provider: openai
    model: gpt-4o
    api_key: "$secret:OPENAI_API_KEY"       # secret 引用

tools:
  allow:
    - myj.goods_search
  config:
    myj.goods_search:
      api_endpoint: "https://api.example.com"
      api_key: "$secret:MYJ_GOODS_API_KEY"  # secret 引用
```

引用格式规范：

```
$secret:<KEY_NAME>               # 平台级 secret
$secret:<TENANT_ID>/<KEY_NAME>   # 租户级 secret
```

- `KEY_NAME` 只允许大写字母、数字、下划线：`[A-Z0-9_]+`。
- manifest 校验时检查引用格式合法性，但**不解析实际值**。
- 实际值在运行时注入。

### 8.3 Secret Backend

```python
class SecretBackend(Protocol):
    def get(self, key: str, *, tenant_id: str | None = None) -> str | None:
        """返回 secret 明文值。如果不存在返回 None。"""
        ...

    def exists(self, key: str, *, tenant_id: str | None = None) -> bool:
        ...
```

Phase 1 实现 -- `EnvSecretBackend`（从环境变量读取）：

```python
class EnvSecretBackend:
    def get(self, key: str, *, tenant_id: str | None = None) -> str | None:
        if tenant_id:
            # 租户级 secret：尝试 TENANT_ABC_OPENAI_API_KEY 格式
            tenant_key = f"{tenant_id.upper().replace('-', '_')}_{key}"
            value = os.environ.get(tenant_key)
            if value:
                return value
        return os.environ.get(key)

    def exists(self, key: str, *, tenant_id: str | None = None) -> bool:
        return self.get(key, tenant_id=tenant_id) is not None
```

Phase 2 实现 -- `VaultSecretBackend`（从 HashiCorp Vault 或类似服务读取）。

### 8.4 Secret 注入流程

```
Agent Package 加载时:
  1. ManifestLoader 发现 "$secret:..." 引用
  2. 记录引用列表，但不解析值
  3. 校验引用格式合法

Tool 执行时:
  1. ToolExecutor 从 tool config 中提取 "$secret:..." 引用
  2. 调用 SecretBackend.get() 解析为明文
  3. 将解析后的 config 传给 tool handler
  4. handler 执行完毕后，解析后的 config 不保留
  5. 明文值不进入 ToolCallTrace 或日志
```

```python
class SecretResolver:
    SECRET_PATTERN = re.compile(r'^\$secret:([A-Z0-9_]+(?:/[A-Z0-9_]+)?)$')

    def __init__(self, backend: SecretBackend):
        self._backend = backend

    def resolve_config(
        self,
        config: dict[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """递归解析 config 中的 $secret:... 引用。

        返回 (resolved_config, resolved_secret_values)。
        resolved_secret_values 用于后续输出扫描，请求结束后必须丢弃。
        """
        resolved = {}
        secrets = []
        for key, value in config.items():
            if isinstance(value, str):
                match = self.SECRET_PATTERN.match(value)
                if match:
                    secret_key = match.group(1)
                    if "/" in secret_key:
                        parts = secret_key.split("/", 1)
                        resolved_value = self._backend.get(parts[1], tenant_id=parts[0])
                    else:
                        resolved_value = self._backend.get(secret_key, tenant_id=tenant_id)
                    if resolved_value is None:
                        raise SecretNotFoundError(f"secret not found: {value}")
                    resolved[key] = resolved_value
                    secrets.append(resolved_value)
                else:
                    resolved[key] = value
            elif isinstance(value, dict):
                sub_resolved, sub_secrets = self.resolve_config(value, tenant_id=tenant_id)
                resolved[key] = sub_resolved
                secrets.extend(sub_secrets)
            else:
                resolved[key] = value
        return resolved, secrets
```

### 8.5 Manifest 校验

`ManifestLoader` 在加载时校验 secret 引用，但不解析值：

```python
def _validate_secret_refs(self, manifest: AgentManifest) -> list[str]:
    """找出所有 $secret: 引用并校验格式。不解析实际值。"""
    errors = []
    refs = self._find_secret_refs(manifest.model_dump())
    for ref in refs:
        if not SecretResolver.SECRET_PATTERN.match(ref):
            errors.append(f"invalid secret reference format: {ref}")
    return errors

def _find_secret_refs(self, data: Any) -> list[str]:
    """递归扫描 dict/list 中所有 $secret: 开头的字符串。"""
    refs = []
    if isinstance(data, str) and data.startswith("$secret:"):
        refs.append(data)
    elif isinstance(data, dict):
        for v in data.values():
            refs.extend(self._find_secret_refs(v))
    elif isinstance(data, list):
        for item in data:
            refs.extend(self._find_secret_refs(item))
    return refs
```

### 8.6 Secret 不进入以下位置

| 位置 | 保护措施 |
|---|---|
| Manifest YAML 文件 | 只存 `$secret:KEY_NAME` 引用 |
| AgentSpec / AgentManifest 对象 | 只存引用字符串 |
| ToolCallTrace | 不记录 tool config，只记录 tool_name、latency_ms、status |
| AgentRun | 不记录 tool 的输入参数中的 secret |
| JSON 日志 | LogSanitizer 过滤（见第 9 节） |
| API response | 不返回 tool config |
| DB | manifest snapshot 中 secret 引用不解析 |

---

## 9. Trace 与日志脱敏

### 9.1 脱敏目标

| 敏感信息类型 | 示例 | 处理方式 |
|---|---|---|
| Secret / API Key | `sk-abc123...` | 替换为 `***SECRET***` |
| 手机号 | `13812345678` | 替换为 `138****5678` |
| 身份证号 | `110101199001011234` | 替换为 `110101****1234` |
| 银行卡号 | `6222021234567890` | 替换为 `6222****7890` |
| 邮箱 | `user@example.com` | 替换为 `u***@example.com` |
| `$secret:...` 解析后的值 | 运行时 secret 明文 | 不记录 |

### 9.2 LogSanitizer

在 `JSONFormatter` 中嵌入脱敏过滤：

```python
class LogSanitizer:
    """日志脱敏器。在日志写入前过滤敏感信息。"""

    PII_PATTERNS: list[tuple[re.Pattern, str]] = [
        # 中国手机号
        (re.compile(r'(1[3-9]\d)\d{4}(\d{4})'), r'\1****\2'),
        # 身份证号 (18位)
        (re.compile(r'(\d{6})\d{8}(\d{4})'), r'\1****\2'),
        # 银行卡号 (16-19位)
        (re.compile(r'(\d{4})\d{8,12}(\d{4})'), r'\1****\2'),
        # 邮箱
        (re.compile(r'([a-zA-Z0-9])[a-zA-Z0-9.]*@'), r'\1***@'),
    ]

    SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
        # API key 格式 (sk-..., key-..., token-...)
        (re.compile(r'(sk-|key-|token-)[a-zA-Z0-9]{8,}'), '***SECRET***'),
        # Bearer token (长度 >= 20)
        (re.compile(r'Bearer\s+[a-zA-Z0-9._-]{20,}'), 'Bearer ***SECRET***'),
    ]

    @classmethod
    def sanitize(cls, text: str) -> str:
        for pattern, replacement in cls.PII_PATTERNS + cls.SECRET_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
```

集成到 `JSONFormatter`（`observability/logging_config.py`）：

```python
class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": ...,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # ...existing logic...
        serialized = json.dumps(log_entry, default=str)
        # 脱敏：在序列化后统一过滤
        serialized = LogSanitizer.sanitize(serialized)
        return serialized
```

### 9.3 TraceSanitizer

`AgentRun` 和 `ToolCallTrace` 写入 RunStore 前脱敏：

```python
class TraceSanitizer:
    """Trace 数据脱敏器。"""

    @staticmethod
    def sanitize_tool_trace(trace: ToolCallTrace) -> ToolCallTrace:
        """脱敏 tool trace 中的错误信息。"""
        if trace.error:
            trace.error = LogSanitizer.sanitize(trace.error)
        return trace

    @staticmethod
    def sanitize_run(run: AgentRun) -> AgentRun:
        """脱敏整个 run 记录。"""
        run.tool_calls = [
            TraceSanitizer.sanitize_tool_trace(tc) for tc in run.tool_calls
        ]
        if run.error and run.error.message:
            run.error.message = LogSanitizer.sanitize(run.error.message)
        return run
```

在 `RuntimeManager._record_run()` 中调用：

```python
def _record_run(self, *, request, run_id, backend_name, status, latency_ms, response):
    # ...existing AgentRun construction...
    run = TraceSanitizer.sanitize_run(run)
    self.run_store.record(run)
```

### 9.4 Secret 运行时隔离

为防止 secret 明文通过工具输出进入 trace，在 `ToolExecutor.execute()` 返回结果前做 secret 值扫描：

```python
# ToolExecutor.execute() 中，handler 返回后
result_str = json.dumps(result, default=str)
for known_secret in active_secrets:
    if known_secret in result_str:
        result_str = result_str.replace(known_secret, "***SECRET***")
result = json.loads(result_str)
```

`active_secrets` 是本次请求中通过 `SecretResolver.resolve_config()` 解析过的 secret 明文列表，只在请求生命周期内存活，请求结束后销毁。

### 9.5 生产反馈洞察的安全边界

Runtime Feedback Intelligence 会读取生产运行数据并生成 Plane 候选需求，因此必须比普通日志分析更严格。该链路只能消费脱敏、聚合后的 Insight Context，不允许直接把原始用户输入、工具入参、工具输出或完整 trace 交给 Hermes。

数据进入 Hermes Insight Agent 前必须经过：

1. `TraceSanitizer` 脱敏。
2. 租户过滤，默认只在单租户内分析。
3. 聚合和去重，只保留问题摘要、统计和证据引用。
4. prompt injection 处理，用户原文只能作为 data，不作为指令。
5. 最小化证据，只保留 `run_id`、时间窗口、错误类型、工具名和脱敏摘要。

禁止写入 Plane 的内容：

| 类型 | 原因 |
| --- | --- |
| 原始用户输入全文 | 可能包含 PII、商业信息或 prompt injection |
| 原始工具入参 / 输出 | 可能包含业务数据或 secret |
| 未脱敏 trace payload | 可能泄露租户、门店、用户或供应链数据 |
| access token / API key / secret 引用解析值 | 高风险凭据泄露 |
| 跨租户明细样本 | 违反租户隔离 |

允许写入 Plane 的内容：

| 类型 | 示例 |
| --- | --- |
| 脱敏证据引用 | `run_abc123` |
| 聚合统计 | `affected_sessions=42` |
| 匿名跨租户统计 | `affected_tenants=3` |
| 问题摘要 | `库存查询场景 fallback 增多` |
| 建议验收标准 | `新增库存查询 eval case` |

跨租户策略：

1. 默认禁止跨租户合并明细。
2. 平台管理员可启用匿名跨租户聚合，但输出不得包含 tenant 名称、门店、用户、原始 query。
3. Plane Work Item 默认归属到触发租户或平台运维项目；跨租户问题只能进入平台级项目。
4. 非 `platform_admin` 不能查看其他租户的反馈 proposal。

自动化边界：

1. 生产反馈只能创建 `Backlog` / `Clarifying` 候选需求。
2. 不允许自动把状态推进到 `Ready for AI Dev`。
3. 不允许直接触发 Codex / Claude Code / CodingAgentRunner。
4. 高风险 proposal 必须人工 review 后才能进入 DevFlow。

---

## 10. 高风险工具 Human-in-the-Loop

### 10.1 触发条件

当 `compute_tool_permission()` 返回 `RequiresApproval` 时，进入审批流程。触发条件：

1. 工具 `risk_level` 为 `high` 或 `critical`。
2. 环境为 `prod`（或 `staging` + `critical` 工具）。
3. 平台或租户 policy 明确标记为需要审批。

### 10.2 审批流程

```
ToolExecutor 尝试执行高风险工具
       |
       v
PolicyEngine 返回 RequiresApproval
       |
       v
创建 ApprovalRequest 记录 (DB)
       |
       v
通知审批人 (webhook / 消息 / 邮件)
       |
       v
Agent Response 返回 status=PENDING_APPROVAL
       |
       v (等待审批)
       |
  +----+----+
  |         |
Approved  Rejected / Timeout
  |         |
  v         v
执行工具   返回 REJECTED response
  |
  v
正常完成，记录审计
```

### 10.3 数据模型

```python
class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    approval_id: str
    run_id: str
    request_id: str
    tenant_id: str
    agent_id: str
    tool_name: str
    tool_payload_hash: str          # payload 的 SHA-256 hash，不存明文
    risk_level: str
    environment: str
    requested_by: str               # 触发 agent run 的调用者
    status: ApprovalStatus = ApprovalStatus.PENDING
    approved_by: str | None = None
    decision_reason: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime            # 超时时间
    decided_at: datetime | None = None
```

### 10.4 审批 API

```
POST /api/v1/approvals/{approval_id}/decide
{
  "action": "approve" | "reject",
  "reason": "optional reason"
}
```

权限要求：调用者必须是 `platform_admin` 或 `agent_operator`，且 `tenant_id` 匹配。

查询待审批：

```
GET /api/v1/approvals?status=pending&tenant_id=xxx
```

### 10.5 超时策略

| 环境 | 默认超时 | 超时行为 |
|---|---|---|
| staging | 5 分钟 | 自动标记 EXPIRED，工具调用失败，记录审计 |
| prod | 30 分钟 | 自动标记 EXPIRED，工具调用失败，记录审计 |

超时检测：后台定时任务每分钟扫描 `status=PENDING` 且 `expires_at < now()` 的记录，批量更新为 `EXPIRED`。

### 10.6 异步 vs 同步

**Phase 1 -- 同步阻塞（简单实现）**

`ToolExecutor` 在 `RequiresApproval` 时直接返回 `PENDING_APPROVAL` 状态的 response。客户端需要轮询或等待 webhook 通知后重新提交请求。

```python
# ToolExecutor.execute() 中
if permission.requires_approval:
    approval = await self._create_approval(tool_name, payload, context)
    return ToolExecutionResult(
        tool_name=tool_name,
        output={"status": "pending_approval", "approval_id": approval.approval_id},
        trace=ToolCallTrace(
            tool_name=tool_name,
            status="pending_approval",
            latency_ms=self._latency_ms(started),
        ),
    )
```

对应的 `AgentResponse` 使用新的 `OutputStatus`：

```python
class OutputStatus(StrEnum):
    # ...existing values...
    PENDING_APPROVAL = "pending_approval"  # 新增
```

**Phase 2 -- 异步回调（后续实施）**

使用消息队列。审批完成后，系统自动恢复工具执行并通过 WebSocket / webhook 通知客户端。不在本文档详细设计。

### 10.7 审计记录

所有高风险工具调用（无论是否通过审批）都写入审计日志：

```python
class ToolAuditEvent(BaseModel):
    event_id: str
    run_id: str
    tenant_id: str
    agent_id: str
    tool_name: str
    risk_level: str
    environment: str
    action: str                     # "executed" | "denied" | "pending" | "expired"
    approval_id: str | None = None
    actor: str                      # 执行者 / 审批者
    timestamp: datetime = Field(default_factory=_utc_now)
```

审计日志持久化到 DB（复用 `persistence-storage-design.md` 的 repository 层），不可修改和删除。

---

## 11. 实施计划

### Phase 1：最小安全基线（与持久化同步实施）

| 任务 | 涉及文件 | 依赖 |
|---|---|---|
| 1.1 PolicyEngine 接入 chat handler（input check） | `api/app.py` | 无 |
| 1.2 PolicyEngine 接入 ToolExecutor（tool check） | `tools/executor.py`, `runtime/manager.py` | 1.1 |
| 1.3 PolicyEngine 接入 response 路径（output check） | `runtime/manager.py` | 1.1 |
| 1.4 LogSanitizer 嵌入 JSONFormatter | `observability/logging_config.py` | 无 |
| 1.5 TraceSanitizer 嵌入 RuntimeManager._record_run() | `runtime/manager.py`, 新文件 `observability/sanitizer.py` | 无 |
| 1.6 Domain Model 泛化（StoreContext -> LocationContext 等） | `domain/models.py`, 全部引用文件, 全部测试 | 无 |
| 1.7 SecretResolver + EnvSecretBackend 实现 | 新文件 `policy/secret.py` | 无 |
| 1.8 Manifest 校验增加 secret ref 格式检查 | `registry/loader.py` | 1.7 |

### Phase 2：Scoped API Key + RBAC

| 任务 | 涉及文件 | 依赖 |
|---|---|---|
| 2.1 ApiKeyRecord 数据模型和 repository | `domain/models.py`, 新文件 `auth/keys.py` | 持久化层 |
| 2.2 AuthMiddleware 重构为 Scoped API Key 模式 | `api/app.py` | 2.1 |
| 2.3 AuthIdentity 注入所有 handler | `api/app.py` | 2.2 |
| 2.4 require_role / require_scope 依赖注入 | 新文件 `api/auth.py` | 2.3 |
| 2.5 Repository 层增加 tenant_id 过滤 | 所有 repository | 持久化层 |
| 2.6 API key 管理 API（create/revoke/list） | `api/app.py` | 2.1 |

### Phase 3：工具权限矩阵 + 高风险审批

| 任务 | 涉及文件 | 依赖 |
|---|---|---|
| 3.1 ToolDefinition 增加 risk_level 字段 | `tools/registry.py` | 无 |
| 3.2 TenantToolPolicy 和 EnvironmentToolPolicy 数据模型 | 新文件 `policy/tool_policy.py` | 持久化层 |
| 3.3 compute_tool_permission() 实现 | `policy/engine.py` | 3.1, 3.2 |
| 3.4 ApprovalRequest 数据模型和审批 API | 新文件 `policy/approval.py`, `api/app.py` | 持久化层 |
| 3.5 ToolExecutor 集成审批流程 | `tools/executor.py` | 3.3, 3.4 |
| 3.6 ToolAuditEvent 审计日志 | 新文件 `policy/audit.py` | 持久化层 |

### Phase 4：JWT + 服务间鉴权（后续阶段）

| 任务 | 涉及文件 | 依赖 |
|---|---|---|
| 4.1 JWT 验证（RS256 + JWKS） | `api/auth.py` | JWKS endpoint 部署 |
| 4.2 api_key + jwt 双模式 AuthMiddleware | `api/app.py` | 4.1 |
| 4.3 服务间 mTLS | 部署层配置 | 4.2 |

---

## 12. 验收标准

### AC-1: Tool 执行前必须经过 policy decision

```
对于每次 ToolExecutor.execute() 调用:
  1. 如果 PolicyEngine 已配置且 PolicySet 已加载:
     - check_tool_allowed() 必须被调用
     - 如果返回 violation 且 severity=error: 工具不执行，返回 POLICY_DENIED
  2. ToolCallTrace.status 中可追溯 "denied" (policy 拒绝) 或正常状态
  3. 有对应的集成测试覆盖
```

验证方法：

```python
def test_tool_execution_requires_policy_check():
    """工具执行前必须经过 policy check。"""
    policy_engine = PolicyEngine()
    policy_set = policy_engine.load_policies(spec_with_deny_tool_rule)
    executor = ToolExecutor(registry, policy_engine=policy_engine)
    result = await executor.execute(
        "denied_tool", {}, allowed_tools=["denied_tool"], policy_set=policy_set,
    )
    assert result.trace.status == "denied"
    assert result.trace.error == "POLICY_DENIED"
```

### AC-2: Secret 不进入 manifest 明文、trace、日志

```
1. Agent manifest YAML 文件中只出现 "$secret:KEY_NAME" 引用，不出现明文
2. ToolCallTrace 不包含 tool config 中的 secret 值
3. AgentRun 记录不包含 secret 值
4. JSON 日志输出中不包含 secret 值
5. API response 中不包含 secret 值
```

验证方法：

```python
def test_secret_not_in_trace():
    """secret 不出现在 trace 中。"""
    result = await executor.execute("tool_with_secret", {"query": "test"}, ...)
    trace_json = result.trace.model_dump_json()
    assert "my-actual-secret-value" not in trace_json

def test_secret_not_in_log(caplog):
    """secret 不出现在日志中。"""
    with caplog.at_level(logging.INFO):
        await executor.execute(...)
    for record in caplog.records:
        formatted = JSONFormatter().format(record)
        assert "my-actual-secret-value" not in formatted
```

### AC-3: Prod 高风险工具默认拒绝或需要审批

```
1. risk_level=high 或 critical 的工具在 environment=prod 时:
   - compute_tool_permission() 返回 RequiresApproval 或 Denied
   - 工具不会自动执行
2. ApprovalRequest 记录被创建并持久化
3. 超时后自动标记为 EXPIRED
4. ToolAuditEvent 审计日志记录所有高风险工具调用
```

验证方法：

```python
def test_high_risk_tool_blocked_in_prod():
    """高风险工具在 prod 环境默认需要审批。"""
    decision = compute_tool_permission(
        tool_name="myj.delete_order",
        agent_spec=spec,
        tenant_policy=None,
        environment="prod",
        env_policy=prod_policy,
        tool_definition=ToolDefinition(
            name="myj.delete_order", risk_level="high", handler=noop,
        ),
    )
    assert isinstance(decision, RequiresApproval)

def test_high_risk_tool_allowed_in_dev():
    """高风险工具在 dev 环境允许执行（便于开发调试）。"""
    decision = compute_tool_permission(
        tool_name="myj.delete_order",
        agent_spec=spec,
        tenant_policy=None,
        environment="dev",
        env_policy=dev_policy,
        tool_definition=ToolDefinition(
            name="myj.delete_order", risk_level="high", handler=noop,
        ),
    )
    assert isinstance(decision, Allowed)
```
