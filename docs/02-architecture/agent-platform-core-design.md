# Agent Platform 核心功能设计

> 本文档定位：平台**内部重构设计**——代码怎么组织、业务如何剥离、管线如何串联。平台总体设计见 [agent-platform-design.md](agent-platform-design.md)。

本文档聚焦 agent-platform 自身作为平台的功能设计。不讨论任何具体业务 Agent（如 MYJ、促销推荐）的功能。目标是让平台成为一个通用的、业务无关的 Agent 运行与管理基础设施。

## 1. 设计原则

1. **平台代码零业务逻辑** — 平台 `src/agent_platform/` 内不应包含任何具体 Agent 的工具 handler、中文关键词、商品逻辑或行业术语。
2. **Agent Package 自包含** — 每个 Agent 的工具、prompt、策略、评测、适配器全部放在 `agents/{id}/` 内。平台只负责发现、加载、校验和执行。
3. **声明式契约** — Agent 通过 manifest.yaml 声明自己需要什么（模型、工具、知识、权限），平台负责满足或拒绝。
4. **可插拔扩展** — runtime backend、model provider、knowledge backend、session store、tool handler 均通过 protocol/接口注入。
5. **渐进式复杂度** — 一个简单 Agent 只需要 manifest + 一个 prompt 就能跑。复杂功能（多 worker、灰度、策略）按需启用。

## 2. 平台分层架构

```
┌──────────────────────────────────────────────────────────┐
│ API Layer                                                │
│  HTTP (REST/SSE) · WebSocket · Auth · RateLimit · CORS   │
├──────────────────────────────────────────────────────────┤
│ Control Plane                                            │
│  AgentRegistry · DeploymentManager · EvalGate · AuditLog │
├──────────────────────────────────────────────────────────┤
│ Routing Layer                                            │
│  AgentRouter · SemanticRouter · CanaryBucketing           │
├──────────────────────────────────────────────────────────┤
│ Runtime Data Plane                                       │
│  RuntimeManager · RequestParser · ContextBuilder          │
│  ConversationEngine · ResponseBuilder · HookPipeline      │
├──────────────────────────────────────────────────────────┤
│ Capability Layer                                         │
│  ToolRegistry · ToolExecutor · ModelGateway               │
│  KnowledgeService · PolicyEngine · SessionStore           │
├──────────────────────────────────────────────────────────┤
│ Agent Packages (外部资产，不属于平台代码)                    │
│  agents/myj/ · agents/promo/ · agents/faq/ · ...          │
└──────────────────────────────────────────────────────────┘
```

## 3. 核心功能清单

### 3.1 Agent Package 发现与加载

平台启动时自动扫描 `registry_root`（默认 `agents/`）下所有 `manifest.yaml`，加载为 `AgentSpec`，注册到 `AgentRegistry`。

**动态工具加载**是解耦的关键。当前问题：平台代码 `tools/registry.py` 硬编码了6个业务 handler。重构后：

```
启动流程:
  1. 扫描 agents/*/manifest.yaml
  2. 对每个 manifest:
     a. Pydantic 校验 manifest 结构
     b. 校验文件引用 (prompts, policies, evals)
     c. 加载 tools: 读取 manifest.tools.allow, 在 package tools/ 目录发现对应模块
     d. 通过 handler_ref 动态 import handler 函数, 注册到 ToolRegistry
     e. 加载 adapter (如果 manifest 声明了 entrypoint)
     f. 注册 AgentSpec 到 AgentRegistry
     g. 自动部署到 dev channel
```

**工具加载机制**：

```python
# manifest.yaml 中声明工具
tools:
  allow:
    - myj.goods_search
    - myj.goods_location

# agents/myj/tools/goods_search.py 中定义 handler
def goods_search(payload: dict) -> dict:
    ...

# 平台通过 handler_ref 加载
# handler_ref: "agents.myj.tools.goods_search:goods_search"
```

平台的 `ToolRegistry` 本身是空的。工具注册只在以下场景发生：

1. Agent Package 加载时，按 manifest 和 handler_ref 动态注册
2. API 调用 `POST /api/v1/agent-packages/register` 时
3. 插件通过 Hook 注册（`pre_run` hook 可以动态添加工具）

### 3.2 Agent Package 生命周期

```
    register          activate         deprecate         archive
 ┌───────────┐    ┌───────────┐    ┌──────────────┐   ┌──────────┐
 │   DRAFT   │───>│  ACTIVE   │───>│  DEPRECATED  │──>│ ARCHIVED │
 └───────────┘    └───────────┘    └──────────────┘   └──────────┘
                       │                                    
                       │ deploy to staging/prod             
                       ▼                                    
                  ┌───────────┐                             
                  │ DEPLOYED  │ (per channel)               
                  └───────────┘                             
```

API：

```
POST   /api/v1/agent-packages/register          # 注册新 package (draft)
PATCH  /api/v1/agent-packages/{id}/activate      # draft → active
PATCH  /api/v1/agent-packages/{id}/deprecate     # active → deprecated
PATCH  /api/v1/agent-packages/{id}/archive       # deprecated → archived
POST   /api/v1/agent-packages/{id}/versions/{v}/deploy   # 部署到 channel
POST   /api/v1/agent-packages/{id}/reload        # 热重载 (重新读取 manifest + tools)
```

**热加载**：不需要重启服务就能加载新 Agent 或更新已有 Agent。通过 `/reload` 端点或文件系统 watcher 触发重新扫描。

### 3.3 Manifest 深度校验

当前 `ManifestLoader` 已有 `_validate_contract()` 和 `_validate_file_refs()`。需要增强：

| 校验项 | 当前状态 | 目标 |
| --- | --- | --- |
| manifest schema | ✓ Pydantic | 保持 |
| prompt 文件存在 | ✓ | 保持 |
| eval suite 文件存在 | ✓ | 保持 |
| policy 文件存在 | ✓ | 保持 |
| tool handler_ref 可 import | ✗ | 新增：尝试 import，失败则拒绝注册 |
| tool allow vs registry | ✗ 仅检查硬编码列表 | 新增：动态注册后检查 |
| knowledge source backend 可用 | ✗ | 新增：检查 backend 类型是否已注册 |
| adapter entrypoint 可 import | ✗ | 新增：尝试 import |
| runtime_compat 版本满足 | 部分 | 增强：与平台版本比较 |
| context.required 路径合法 | ✓ | 保持 |

### 3.4 Runtime 执行管线

请求执行的完整管线：

```
Request
  │
  ▼
RequestParser          # 协议版本检测 (v1/v2/legacy), 归一化
  │
  ▼
AgentRouter            # agent_id → tenant → semantic → default
  │
  ▼
ManifestLoader         # 读取 AgentSpec
  │
  ▼
PolicyEngine           # check_input: PII/deny_pattern 检查
  │
  ▼
HookPipeline           # emit(pre_run)
  │
  ▼
ContextBuilder         # 拼装 system prompt + session history + knowledge
  │
  ▼
RuntimeManager         # 选择 backend (native/hermes/langgraph)
  │
  ├── NativeBackend    # orchestrator_workers 或 adapter entrypoint
  ├── HermesBackend    # ConversationEngine (tool loop)
  └── LangGraphBackend # StateGraph executor
  │
  ▼
ToolExecutor           # 权限校验 → schema校验 → 执行 → 超时/重试
  │
  ▼
HookPipeline           # emit(post_tool) / emit(post_run)
  │
  ▼
PolicyEngine           # check_output: PII/deny_output 检查
  │
  ▼
ResponseBuilder        # 构造 AgentResponse, 过滤 command allowlist
  │
  ▼
TraceCollector         # 记录 AgentRun (run_id, tools, latency, model)
  │
  ▼
SessionStore           # 保存对话历史
  │
  ▼
Response
```

**当前缺失的管线环节**：

1. **HookPipeline 集成** — HookRegistry 已实现但未在 RuntimeManager 中调用。需要在 `pre_run`, `post_run`, `pre_tool`, `post_tool` 节点插入 hook emit。
2. **PolicyEngine 前置检查** — 当前 PolicyEngine 存在但未在 chat 主流程调用。应在 RequestParser 之后、RuntimeManager 之前做 input 检查；在 ResponseBuilder 之前做 output 检查。
3. **ContextBuilder 知识注入** — ContextBuilder 存在但 KnowledgeService 未在主流程中被调用。

### 3.5 Hook 管线集成

HookRegistry 支持8个事件点，但当前未接入 runtime。需要在以下位置调用：

```python
# RuntimeManager.run() 中
await hook_registry.emit("pre_run", HookContext(data={"request": request}))

# ToolExecutor.execute() 前后
await hook_registry.emit("pre_tool", HookContext(data={"tool_name": name, "payload": payload}))
result = await handler(payload)
await hook_registry.emit("post_tool", HookContext(data={"tool_name": name, "result": result}))

# RuntimeManager.run() 完成后
await hook_registry.emit("post_run", HookContext(data={"response": response}))

# 错误发生时
await hook_registry.emit("on_error", HookContext(data={"error": exc}))
```

Hook 的价值：

- 审计日志（记录每次工具调用的参数和结果）
- 观测（发送 metrics、trace span）
- 安全拦截（hook 可以 `ctx.cancel()` 阻止危险操作）
- A/B 测试（修改请求/响应数据）

### 3.6 ToolRegistry 与 ToolExecutor

**当前问题**：`create_default_tool_registry()` 把6个业务 handler 硬编码在平台代码中。

**目标状态**：

```python
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}  # 初始为空

    def register(self, definition: ToolDefinition) -> None: ...
    def unregister(self, name: str) -> None: ...       # 新增：支持热卸载
    def get(self, name: str) -> ToolDefinition: ...
    def list_tools(self) -> list[ToolDefinition]: ...
    def list_by_owner(self, owner: str) -> list[ToolDefinition]: ...  # 新增
    def list_by_agent(self, agent_id: str) -> list[ToolDefinition]: ...  # 新增


def create_default_tool_registry() -> ToolRegistry:
    return ToolRegistry()  # 空 registry，工具由 Agent Package 加载时注册
```

**ToolExecutor 增强**：

| 能力 | 当前 | 目标 |
| --- | --- | --- |
| Allow-list 校验 | ✓ | 保持 |
| JSON Schema 输入校验 | ✓ 基础 | 增强：支持 required fields |
| 超时控制 | ✓ | 保持 |
| 重试 | ✓ | 保持 |
| 调用审计 | 部分 (trace) | 增强：hook emit |
| 并发限制 | ✗ | 新增：manifest.tools.max_parallel |
| 结果 schema 校验 | ✗ | 可选：校验 output 格式 |

### 3.7 ModelGateway

当前 `ModelGateway` 支持 `ModelProvider` Protocol 和 `StubModelProvider`。作为平台能力：

```python
class ModelProvider(Protocol):
    name: str
    async def chat(self, *, messages, model, temperature, max_tokens, tools) -> dict: ...

class ModelGateway:
    def register_provider(self, provider: ModelProvider) -> None: ...
    def get_provider(self, name: str) -> ModelProvider: ...
    async def chat(self, *, provider, model, ...) -> dict: ...
```

平台侧功能：

- **Provider 注册**：OpenAI-compatible、HuaweiCloud、Anthropic 等 provider 通过配置注入
- **成本统计**：记录 token usage per agent per model
- **限流**：per-agent model call rate limit
- **Fallback**：primary provider 不可用时切换到 backup

### 3.8 KnowledgeService

当前 `KnowledgeService` 有 `KnowledgeBackend` Protocol 和 stub 实现。平台应提供：

```python
class KnowledgeBackend(Protocol):
    name: str
    async def search(self, query, *, collection, filters, top_k) -> list[KnowledgeChunk]: ...

class KnowledgeService:
    def register_backend(self, backend: KnowledgeBackend) -> None: ...
    async def retrieve(self, query, *, sources: list[KnowledgeSourceConfig]) -> list[KnowledgeChunk]: ...
```

Knowledge source 由 Agent manifest 声明，平台按 backend type 分发检索请求。平台不知道也不关心 knowledge 的业务内容。

### 3.9 SessionStore

当前有 `InMemorySessionStore` 和 `FileSessionStore`。平台层面：

- **Session 生命周期**：create → active → expired → archived
- **Scope 隔离**：按 tenant_id + agent_id + session_id 隔离
- **History 压缩**：超过 threshold_tokens 时自动压缩早期消息
- **可插拔 backend**：内存、文件、Redis、数据库

### 3.10 PolicyEngine

当前 PolicyEngine 支持 safety rules (PII/deny_pattern/deny_output/deny_tools) 和 routing rules。

**未接入主流程**是关键缺失。需要：

1. Agent Package 加载时，从 `policies/safety.yaml` 和 `policies/routing.yaml` 加载规则到 `PolicySet`
2. Chat 请求进入时，调用 `check_input(query, policy_set)` 检查 PII 和危险模式
3. 工具调用前，调用 `check_tool_allowed(tool_name, policy_set)` 检查工具权限
4. 响应返回前，调用 `check_output(display, policy_set)` 检查输出合规
5. 违规时返回标准错误或审计告警

### 3.11 Observability

当前有 `JSONFormatter` (结构化日志)、`MetricsCollector` (Prometheus)、`InMemoryRunStore` (trace)。

需要统一观测数据流：

```
每次请求产生:
  - Structured log: request_id, agent_id, route_reason, latency_ms, status
  - Trace: run_id → tool_calls → model_calls → errors
  - Metrics: 
      agent_requests_total{agent_id, status}
      agent_latency_ms{agent_id, backend}
      tool_calls_total{tool_name, status}
      model_calls_total{provider, model}
```

### 3.12 API 端点设计

**核心端点**（平台必须）：

| Method | Path | 用途 |
| --- | --- | --- |
| POST | `/api/v1/agent/chat` | 统一对话入口 |
| GET | `/api/v1/agent/chat/stream` | SSE 流式对话 |
| WS | `/ws/agent/chat` | WebSocket 对话 |
| GET | `/health` | 健康检查 |
| GET | `/metrics` | Prometheus 指标 |

**Agent 管理端点**：

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/api/v1/agents` | 列出所有注册 Agent |
| POST | `/api/v1/agent-packages/register` | 注册 Agent Package |
| PATCH | `/api/v1/agent-packages/{id}/activate` | 激活 |
| PATCH | `/api/v1/agent-packages/{id}/deprecate` | 弃用 |
| POST | `/api/v1/agent-packages/{id}/reload` | 热重载 |
| POST | `/api/v1/agent-packages/{id}/versions/{v}/deploy` | 部署到 channel |
| POST | `/api/v1/deployments/rollback` | 回滚 |
| GET | `/api/v1/deployments/audit` | 部署审计日志 |

**运行时端点**：

| Method | Path | 用途 |
| --- | --- | --- |
| GET | `/api/v1/agent-runs` | 查看运行记录 |
| GET | `/api/v1/sessions` | 列出会话 |
| GET | `/api/v1/sessions/{id}` | 查看会话详情 |

**Eval 端点**：

| Method | Path | 用途 |
| --- | --- | --- |
| POST | `/api/v1/evals/run` | 运行评测 |
| POST | `/api/v1/evals/ci-callback` | CI 回调 |

**DevFlow 端点**（可选，按需启用）：

| Method | Path | 用途 |
| --- | --- | --- |
| POST | `/api/v1/devflow/parse-requirement` | 需求解析 |
| POST | `/api/v1/devflow/generate-issues` | 生成 Issue |
| POST | `/api/v1/devflow/scaffold-agent` | 脚手架 |
| POST | `/api/v1/devflow/task-packs` | 生成任务包 |
| POST | `/api/v1/devflow/design-analysis` | 架构分析 |
| POST | `/api/v1/devflow/test-plan` | 测试计划 |

## 4. 与业务 Agent 的边界

```
平台代码 (src/agent_platform/) 负责:
  ✓ 协议解析和归一化
  ✓ Agent 路由
  ✓ Manifest 加载和校验
  ✓ Runtime 选择和执行
  ✓ 工具注册表 (空壳) 和执行引擎
  ✓ 模型网关
  ✓ 知识检索接口
  ✓ 会话管理
  ✓ 策略引擎
  ✓ Hook 管线
  ✓ 部署/灰度/回滚
  ✓ 评测运行器
  ✓ 观测 (日志/指标/Trace)
  ✓ 研发流水线 (需求解析/脚手架/Issue生成)

平台代码不负责:
  ✗ 任何业务工具的 handler 实现
  ✗ 任何业务领域的关键词、术语、规则
  ✗ 任何特定 Agent 的 prompt 内容
  ✗ 任何特定语言/地区的硬编码默认值
  ✗ 任何特定行业的数据模型字段

业务 Agent Package (agents/{id}/) 负责:
  ✓ manifest.yaml 声明
  ✓ prompts/ 下的 prompt 模板
  ✓ policies/ 下的安全/路由/输出策略
  ✓ tools/ 下的工具 handler 实现
  ✓ knowledge/ 下的知识源配置
  ✓ evals/ 下的评测用例
  ✓ tests/ 下的业务测试
  ✓ adapter.py 适配器 (如需自定义入口)
```

## 5. Domain Model 解耦

> **注意**：本节提出的变更将影响 `[01-contracts/agent-request-response.md](../01-contracts/agent-request-response.md)` 中的字段定义和示例。实施前需同步更新契约文档，建议通过 ADR 记录此决策。

当前 `RequestContext` 中的零售行业字段需要泛化：

```python
# 当前 (零售耦合)
class TenantContext(BaseModel):
    tenant_id: str | None = None
    retailer_id: str | None = None     # ← 零售

class StoreContext(BaseModel):
    store_id: str | None = None        # ← 零售
    store_name: str | None = None      # ← 零售

# 目标 (通用)
class TenantContext(BaseModel):
    tenant_id: str | None = None
    org_id: str | None = None          # 通用组织 ID

class LocationContext(BaseModel):      # 通用位置, 不局限于 "门店"
    location_id: str | None = None
    location_name: str | None = None

class RequestContext(BaseModel):
    tenant: TenantContext = Field(default_factory=TenantContext)
    location: LocationContext = Field(default_factory=LocationContext)
    user: UserContext = Field(default_factory=UserContext)
    channel: ChannelContext = Field(default_factory=ChannelContext)
    device: DeviceContext = Field(default_factory=DeviceContext)
    locale: str = "en"                 # 不硬编码 zh-CN
    timezone: str = "UTC"              # 不硬编码 Asia/Shanghai
    extra: dict[str, Any] = Field(default_factory=dict)  # 业务扩展字段
```

业务 Agent 可以在 `extra` 中传递行业特定字段（如 `retailer_id`, `store_code`），由 Agent Package 的 adapter 自行解析。

## 6. 配置解耦

```python
class Settings(BaseModel):
    env: str = "dev"
    registry_root: Path = Field(default=Path("agents"))
    default_agent_id: str | None = None   # 不硬编码 "myj", 为空时返回 404
    api_key: str | None = None
    platform_version: str = "0.2.0"
    
    # 可选集成 (全部默认关闭)
    plane_base_url: str | None = None
    gitlab_base_url: str | None = None
```

## 7. 重构实施计划

### Phase 1: 工具解耦 (核心)

1. 把 `tools/registry.py` 中的6个业务 handler 移到各自 Agent Package 的 `tools/` 目录
2. `create_default_tool_registry()` 返回空 registry
3. Agent Package 加载时通过 `handler_ref` 动态 import 和注册工具
4. 更新 manifest 的 tool 配置，添加 `handler_ref` 指向 package 内模块
5. 更新所有测试，使用动态加载而非硬编码 registry

### Phase 2: Domain Model 泛化

1. `StoreContext` → `LocationContext`
2. `TenantContext.retailer_id` → `TenantContext.org_id`
3. 默认 locale/timezone 改为 `en` / `UTC`
4. `RequestContext` 增加 `extra: dict` 扩展字段
5. MYJ Agent 的 adapter 负责从 `extra` 中提取 `retailer_id`

### Phase 3: Hook 管线集成

1. RuntimeManager.run() 中插入 pre_run / post_run hook
2. ToolExecutor.execute() 中插入 pre_tool / post_tool hook
3. 错误路径插入 on_error hook

### Phase 4: Policy 主流程接入

1. Chat 入口调用 PolicyEngine.check_input()
2. 工具调用前调用 PolicyEngine.check_tool_allowed()
3. 响应返回前调用 PolicyEngine.check_output()

### Phase 5: Agent 生命周期

1. AgentRegistry 增加 status 状态机 (draft/active/deprecated/archived)
2. 新增 activate/deprecate/archive API 端点
3. 新增 reload 端点支持热加载
4. 文件系统 watcher 可选

## 8. 验收标准

平台重构完成后：

1. `src/agent_platform/` 中不包含任何 `myj`、`goods`、`store_consult`、`promotion` 等业务关键词
2. 删除 `agents/myj/` 目录后，平台仍能启动，只是没有注册 Agent
3. 新增一个 Agent 只需要在 `agents/` 下创建目录和 manifest，不需要修改任何平台代码
4. 所有 hook 事件在 runtime 主流程中被调用
5. PolicyEngine 在每次 chat 请求的 input/output 路径上生效
6. 所有测试通过，且测试不依赖硬编码的业务工具