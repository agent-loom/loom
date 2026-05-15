# 安全、租户、Policy 与 Secret 设计

> Status: Draft
> Stage: S2
> Owner: platform
> Last verified against code: 2026-05-15

## 1. 背景

当前 `PolicyEngine`（185 行）已实现 `check_input`、`check_output`、`check_tool_allowed` 等方法，但完全未接入 runtime 执行链路。`HookRegistry` 同样已实现但未被调用。Domain models 中包含零售特定字段（`retailer_id`、`StoreContext`、`zh-CN` 默认值）。

## 2. 认证模型

### 2.1 当前：API Key

```
Authorization: Bearer <api_key>
X-Tenant-ID: <tenant_id>
```

适用于：服务间调用、CI pipeline、内部工具。

### 2.2 目标：JWT

```
Authorization: Bearer <jwt>
```

JWT claims：

```json
{
  "sub": "user_001",
  "tenant_id": "tenant_myj",
  "roles": ["agent_operator"],
  "permissions": ["agent:chat", "agent:deploy:staging"],
  "exp": 1716825600
}
```

迁移路径：

| 阶段 | 认证方式 | 适用场景 |
|---|---|---|
| MVP | API Key + `X-Tenant-ID` header | 全部 |
| S2 | API Key（服务间）+ JWT（用户） | 人机分离 |
| S3+ | JWT 统一 + API Key 兼容 | 全部 |

### 2.3 Middleware 实现

```python
async def auth_middleware(request: Request, call_next):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if not token:
        raise HTTPException(401, "Missing authentication")

    if is_jwt(token):
        claims = verify_jwt(token, settings.jwt_secret)
        request.state.auth = AuthContext(
            subject=claims["sub"],
            tenant_id=claims["tenant_id"],
            roles=claims["roles"],
        )
    elif is_api_key(token):
        key_record = await api_key_store.lookup(token)
        request.state.auth = AuthContext(
            subject=f"apikey:{key_record.name}",
            tenant_id=key_record.tenant_id,
            roles=key_record.roles,
        )
    else:
        raise HTTPException(401, "Invalid token")

    response = await call_next(request)
    return response
```

## 3. 授权模型（RBAC）

### 3.1 角色定义

| 角色 | 说明 | 权限 |
|---|---|---|
| `platform_admin` | 平台管理员 | 全部操作 |
| `agent_developer` | Agent 开发者 | 注册、部署到 dev/staging、运行 eval |
| `agent_operator` | Agent 运维 | 部署到 prod、回滚、查看 audit |
| `chat_user` | 终端用户 | 仅 `/api/v1/agent/chat` |
| `readonly` | 只读 | 查看 agent 列表、run 记录、eval 报告 |

### 3.2 权限矩阵

| 操作 | admin | developer | operator | chat_user | readonly |
|---|---|---|---|---|---|
| agent:chat | ✓ | ✓ | ✓ | ✓ | ✗ |
| agent:register | ✓ | ✓ | ✗ | ✗ | ✗ |
| agent:deploy:dev | ✓ | ✓ | ✗ | ✗ | ✗ |
| agent:deploy:staging | ✓ | ✓ | ✓ | ✗ | ✗ |
| agent:deploy:prod | ✓ | ✗ | ✓ | ✗ | ✗ |
| agent:rollback | ✓ | ✗ | ✓ | ✗ | ✗ |
| eval:run | ✓ | ✓ | ✓ | ✗ | ✗ |
| audit:view | ✓ | ✓ | ✓ | ✗ | ✓ |
| policy:manage | ✓ | ✗ | ✗ | ✗ | ✗ |

## 4. 租户隔离

### 4.1 租户边界

```
tenant_id:    组织级隔离（必填）
org_id:       组织内子单元（原 retailer_id，可选）
location_id:  位置/站点（原 store_id，可选）
```

### 4.2 隔离规则

1. 所有 DB 查询必须携带 `tenant_id` 条件。
2. 跨 tenant 查询仅 `platform_admin` 可执行。
3. Agent manifest、deployment、session、run 都属于特定 tenant。
4. Webhook delivery 按 tenant 隔离处理。

### 4.3 Domain Model 泛化

> 此变更需同步更新 [`01-contracts/agent-request-response.md`](../01-contracts/agent-request-response.md)，建议通过 ADR 记录。

```python
# 当前 → 目标
TenantContext.retailer_id   → TenantContext.org_id
StoreContext                → LocationContext
StoreContext.store_id       → LocationContext.location_id
StoreContext.store_name     → LocationContext.location_name
UserContext.member_id       → UserContext.external_id
RequestContext.locale       → 默认值从 "zh-CN" 改为 "en"
RequestContext.timezone     → 默认值从 "Asia/Shanghai" 改为 "UTC"
AgentSession.store_id       → AgentSession.location_id
build_scoped_session_id()   → "tenant_store_user" 改为 "tenant_location_user"
```

向后兼容：保留旧字段名作为 alias，加 deprecation warning。

## 5. PolicyEngine 接入方案

当前 PolicyEngine 存储在 `app.state.policy_engine` 但从未被调用。

### 5.1 接入点

```python
# RuntimeManager.run() 中的执行顺序
async def run(self, request: RuntimeRequest) -> RuntimeResponse:
    # 1. Hook: pre_run
    await self.hook_registry.emit("pre_run", context)

    # 2. Policy: check_input
    input_result = await self.policy_engine.check_input(request)
    if input_result.denied:
        return build_denied_response(input_result)

    # 3. Route
    agent_spec = self.router.route(request)

    # 4. Hook: on_route
    await self.hook_registry.emit("on_route", context)

    # 5. Runtime backend execute
    response = await backend.run(runtime_request)

    # 6. Policy: check_output
    output_result = await self.policy_engine.check_output(response)
    if output_result.denied:
        response = sanitize_response(response, output_result)

    # 7. Hook: post_run
    await self.hook_registry.emit("post_run", context)

    return response
```

### 5.2 Tool 级 Policy

```python
# ToolExecutor.execute() 中
async def execute(self, tool_name: str, params: dict, context: ToolContext) -> ToolResult:
    # 1. Hook: pre_tool
    await self.hook_registry.emit("pre_tool", context)

    # 2. Policy: check_tool_allowed
    allowed = await self.policy_engine.check_tool_allowed(
        tool_name=tool_name,
        agent_id=context.agent_id,
        tenant_id=context.tenant_id,
        environment=context.environment,
    )
    if not allowed:
        raise ToolForbiddenError(tool_name)

    # 3. Execute
    result = await handler(params)

    # 4. Hook: post_tool
    await self.hook_registry.emit("post_tool", context)

    return result
```

## 6. Tool Permission 矩阵

Permission 按三个维度计算：

```
final_permission = manifest_allow ∩ tenant_policy ∩ environment_policy
```

| 维度 | 配置位置 | 粒度 |
|---|---|---|
| Agent manifest | `agents/<id>/manifest.yaml` → `tools.allowed` | 声明 agent 可用哪些工具 |
| Tenant policy | `policies/tenants/<tenant_id>.yaml` | 限制 tenant 可用哪些工具 |
| Environment policy | `policies/environments/<env>.yaml` | prod 禁用高风险工具 |

高风险工具分类：

| 风险级别 | 工具类型 | prod 默认 |
|---|---|---|
| low | 查询类（search、locate） | 允许 |
| medium | 写入类（create、update） | 需 tenant policy 允许 |
| high | 删除类、外部 API 调用 | 默认拒绝，需人工审批 |

## 7. Secret 管理

### 7.1 引用格式

Manifest 中不存储明文 secret：

```yaml
# manifest.yaml
extensions:
  api_keys:
    openai: "$secret:OPENAI_API_KEY"
    plane: "$secret:PLANE_API_KEY"
```

### 7.2 注入方式

```python
class SecretResolver:
    def __init__(self, providers: list[SecretProvider]):
        self.providers = providers

    async def resolve(self, ref: str) -> str:
        """$secret:KEY_NAME → actual value"""
        if not ref.startswith("$secret:"):
            return ref
        key_name = ref.removeprefix("$secret:")
        for provider in self.providers:
            value = await provider.get(key_name)
            if value is not None:
                return value
        raise SecretNotFoundError(key_name)

class EnvSecretProvider:
    """从环境变量读取"""
    async def get(self, key: str) -> str | None:
        return os.environ.get(key)

class VaultSecretProvider:
    """从 HashiCorp Vault 读取（后续阶段）"""
    ...
```

### 7.3 Secret 保护规则

1. `$secret:*` 引用在 manifest 校验时检查格式，不解析值。
2. Secret 值只在 runtime 执行时注入，用完即丢弃。
3. Secret 不进入 `AgentRun`、`ResponseTrace`、日志。
4. Secret 不写入 `metadata.json` 或 artifact。
5. 日志和 trace 中的 secret 值自动替换为 `[REDACTED]`。

## 8. 日志和 Trace 脱敏

```python
class SanitizingFilter(logging.Filter):
    PATTERNS = [
        (re.compile(r'(api[_-]?key|token|secret|password|authorization)["\s:=]+\S+', re.I),
         r'\1=[REDACTED]'),
        (re.compile(r'\b[A-Za-z0-9]{32,}\b'), '[POSSIBLE_SECRET]'),  # 长随机串
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pattern, replacement in self.PATTERNS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        return True
```

Trace 脱敏：

```python
def sanitize_trace(trace: ResponseTrace) -> ResponseTrace:
    for tc in trace.tool_calls:
        tc.params = redact_secrets(tc.params)
        tc.result = redact_secrets(tc.result)
    return trace
```

## 9. 高风险工具 Human-in-the-Loop

```python
class ApprovalGate:
    async def request_approval(
        self,
        tool_name: str,
        agent_id: str,
        tenant_id: str,
        params: dict,
        timeout_seconds: int = 300,
    ) -> ApprovalResult:
        """
        1. 创建 approval request 记录
        2. 通知审批人（Plane comment / 钉钉 / 邮件）
        3. 等待审批结果或超时
        4. 超时 → 默认拒绝
        """
        ...

class ApprovalResult:
    approved: bool
    approver: str | None
    reason: str | None
    approved_at: datetime | None
```

执行流程：

```
Tool 调用 → check_tool_allowed → risk_level=high
  → ApprovalGate.request_approval()
    → 通知审批人
    → 等待结果（最长 5 分钟）
    → approved → 执行工具
    → denied/timeout → 返回 TOOL_FORBIDDEN
```

## 10. 验收标准

1. Tool 执行前必须经过 `PolicyEngine.check_tool_allowed()` 决策。
2. Secret 不出现在 manifest 明文、trace JSON、任何日志行中。
3. Prod 环境 high-risk tool 默认拒绝或需要审批。
4. 所有 DB 查询携带 `tenant_id` 条件。
5. PolicyEngine 和 HookRegistry 真正接入 RuntimeManager 执行链路。
6. Domain model 泛化后，旧字段名仍可用（alias）。
