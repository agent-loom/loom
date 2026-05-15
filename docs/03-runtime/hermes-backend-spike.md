# HermesBackend Spike 设计0

> Status: Draft  
> Stage: S3  
> Owner: platform  
> Last verified against code: 2026-05-15

> 前置文档：战略设计见 `[hermes-runtime.md](./hermes-runtime.md)`；差距分析见 `[../implementation-gap.md](../implementation-gap.md)` §2.4；设计计划见 `[../next-stage-design-plan.md](../next-stage-design-plan.md)` §P0-3。

---

## 1. 现状分析

### 1.1 已有代码

`src/agent_platform/runtime/hermes.py`（340 行）包含完整的 agentic loop 骨架：

| 组件 | 类名 | 职责 | 当前状态 |
| --- | --- | --- | --- |
| 配置映射 | `ManifestMapper` | manifest -> hermes config dict | 已实现，从 `AgentSpec` 提取 system_prompt / tools / model / hermes extension |
| 工具桥接 | `ToolBridge` | 平台 `ToolDefinition` -> hermes tool dict | 已实现，遍历 `ToolRegistry` 生成 `{name, description, input_schema}` |
| 会话桥接 | `SessionBridge` | 平台 session_id -> hermes session config | 已实现，返回 `{session_id, memory_provider}` |
| 响应映射 | `ResponseMapper` | hermes result dict -> `RuntimeResponse` | 已实现，构建完整的 `AgentResponse` 含 `ToolCallTrace` |
| 追踪桥接 | `TraceBridge` | 提取 hermes run_id / iterations / model_calls | 已实现 |
| 策略检查 | `PolicyEnforcer` | tools allow/deny 冲突校验 | 已实现 |
| 对话引擎 | `ConversationEngine` | 多轮 model call + tool use loop | 已实现完整 loop，含 budget 控制 |
| 后端入口 | `HermesRuntimeBackend` | `RuntimeBackend.run()` 实现 | 已实现，串联上述组件 |

### 1.2 核心问题：model_gateway 始终为 None

```python
# src/agent_platform/runtime/manager.py 第 35 行
HermesRuntimeBackend.name: HermesRuntimeBackend(),
```

`RuntimeManager` 实例化 `HermesRuntimeBackend()` 时没有传入任何参数。`model_gateway` 和 `tool_executor` 都是 `None`。

当 `model_gateway is None` 时，`ConversationEngine.converse()` 直接走 stub 路径：

```python
# hermes.py 第 179 行
if self.model_gateway is None:
    return self._stub_response(system_prompt, user_query, model_config)
```

返回固定字符串 `"[Hermes-stub] Received: {user_query}"`，tool_calls 为空，iterations 为 0。

### 1.3 不存在的部分

| 缺失项 | 说明 |
| --- | --- |
| Hermes SDK import | 整个 hermes.py 没有 `import hermes` 或任何 Hermes 官方包引用 |
| HTTP client | 没有 httpx/aiohttp 调用外部 Hermes 服务 |
| ModelGateway 接口对齐 | hermes.py 内 `ConversationEngine` 的 `model_gateway.chat()` 签名与 `runtime/model_gateway.py` 中 `ModelGateway.chat()` 不同（前者 positional `messages=`，后者第一个参数是 `provider_name`） |
| tool_executor 注入 | `HermesRuntimeBackend.__init__` 的 `tool_executor` 参数从未在 `RuntimeManager` 中被传入 |
| pyproject.toml 依赖 | 没有 Hermes 相关依赖，也没有 openai SDK 依赖 |

### 1.4 hermes.py 内 ConversationEngine vs runtime/conversation.py ConversationEngine

代码库中存在两套 `ConversationEngine`：

| 属性 | `runtime/hermes.py` 内的 `ConversationEngine` | `runtime/conversation.py` 的 `ConversationEngine` |
| --- | --- | --- |
| `model_gateway` 类型 | `Any | None` | `ModelGateway`（具体类） |
| 调用签名 | `converse(system_prompt, user_query, *, model_config, tools, ...)` | `run(context, spec, request)` |
| 返回值 | `dict`（含 text / tool_calls / run_id） | `ConversationResult` dataclass |
| 消息格式 | `list[dict[str, str]]` | `list[ModelMessage]`（Pydantic model） |
| model call 方式 | `self.model_gateway.chat(messages=..., model=..., tools=...)` | `self.model_gateway.chat(provider_name, messages, *, model=..., tools=...)` |
| tool 执行 | `self.tool_executor.execute(tool_name, tool_input, ...)` | `self.tool_executor.execute(tc.name, tc.arguments, ...)` |
| 有测试覆盖 | 无专门测试（`test_hermes_backend.py` 只验证 stub 路径） | `test_conversation_engine.py` 有完整测试 |

`runtime/conversation.py` 已经可以工作（有 3 个测试场景：无 tool call 直接返回、budget 控制、knowledge 注入）。hermes.py 内的 `ConversationEngine` 是一个接口不同的独立副本。

---

## 2. 必须回答的六个问题

### 2.1 Hermes 官方版本如何引入和 pin

**结论：分两步。**

- **Spike A**（本文档重点）不引入 Hermes SDK。让 hermes.py 内的 `ConversationEngine` 使用平台已有的 `ModelGateway` + `ToolExecutor`。不需要任何新依赖。
- **Spike B** 引入 Hermes Python 包。pin 策略：

```toml
# pyproject.toml
[project.optional-dependencies]
hermes = [
    "hermes-agent>=0.13.0,<0.14",   # pin 到 minor 级别，patch 允许浮动
]
```

引入原则：

1. 使用 optional dependency group `hermes`，不强制所有环境安装。
2. CI 中 `uv pip install -e ".[hermes]"` 且锁定到 `uv.lock`。
3. Hermes 版本升级必须通过 adapter contract test 才允许合并。

### 2.2 HermesRuntimeBackend 调用哪个官方 API

**Spike A：不调 Hermes API。** 复用平台的 `ModelGateway.chat()` 和 `ToolExecutor.execute()`。

**Spike B：** 调用 Hermes 的 `AIAgent` 核心接口（修正后）：

```python
from run_agent import AIAgent

agent = AIAgent(
    base_url=...,
    api_key=...,
    provider=...,
    model=...,
    max_iterations=10,
    enabled_toolsets=["agent-platform"],
    session_id=...,
    skip_context_files=True,
    skip_memory=True,
    quiet_mode=True,
)
result = agent.run_conversation(user_message, system_message, conversation_history)
# result 是 dict，result["final_response"] 是最终文本
```

如果 `run_conversation` 是同步的，adapter 用 `anyio.to_thread.run_sync()` 包装。

### 2.3 平台 tool 如何转成 Hermes tool callback

**Spike A：** 直接使用平台 `ToolExecutor.execute()`。hermes.py 内 `ConversationEngine` 的 tool loop（第 211-232 行）已经调用 `self.tool_executor.execute(tool_name, tool_input, ...)`，接口与 `ToolExecutor` 兼容。

**Spike B：** 需要一个适配函数，把 async 的 `ToolExecutor.execute()` 包装成 Hermes 期望的 tool handler 签名：

```python
def make_hermes_tool_handler(
    tool_name: str,
    tool_executor: ToolExecutor,
    allowed_tools: list[str],
    timeout_ms: int,
) -> Callable[[dict], str]:
    """将平台 ToolExecutor 包装为 Hermes tool handler。"""
    def handler(args: dict, **kwargs) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(
            tool_executor.execute(
                tool_name,
                args,
                allowed_tools=allowed_tools,
                timeout_ms=timeout_ms,
            )
        )
        return str(result.output)
    return handler
```

Spike B 需先验证 Hermes 的 tool callback 是 sync 还是 async 调用，再决定 bridge 方式。

### 2.4 Hermes session/memory 如何映射平台 session

当前 `SessionBridge.map_session()` 已实现基本映射：

```python
{"session_id": session_id, "memory_provider": hermes_config.get("memory_provider", "session")}
```

**Spike A 不需要改动。** 平台 `RuntimeManager` 已负责 session 生命周期（加载、保存、`add_message`），hermes.py 不需要再管 session 持久化。

**Spike B 映射规则：**

```
hermes_session_id = f"{tenant_id}:{agent_id}:{platform_session_id}"
```

- 平台是 session owner，Hermes 只是 consumer。
- Spike B 阶段默认关闭 Hermes 长期 memory（`skip_memory=True`），仅用平台 session。
- 后续再评估是否复用 Hermes 的 SessionDB / memory provider。

### 2.5 Hermes stream event 如何映射平台 SSE/WebSocket

**Spike A/B 均暂不实现 streaming。** 理由：

1. 当前 `RuntimeBackend.run()` 协议返回完整的 `RuntimeResponse`，不是流式。
2. 平台已有 `api/streaming.py` 和 `api/websocket.py`，但 runtime 层还没有 streaming 协议。
3. Spike 目标是证明非 stub，streaming 是后续优化。

**后续设计方向（不在 spike 范围内）：**

```python
class RuntimeBackend(Protocol):
    async def run(self, request: RuntimeRequest) -> RuntimeResponse: ...
    async def run_stream(self, request: RuntimeRequest) -> AsyncIterator[StreamEvent]: ...
```

| Hermes stream event | 平台 StreamEvent type |
| --- | --- |
| conversation started | `run.started` |
| token generated | `message.delta` |
| tool call initiated | `tool.started` |
| tool call completed | `tool.completed` |
| response completed | `message.completed` |
| error | `run.failed` |

### 2.6 如果 Hermes 不可用，fallback 策略是什么

三级 fallback：

| 级别 | 触发条件 | 行为 |
| --- | --- | --- |
| L0 正常 | Hermes SDK 可用 + model provider 健康 | 使用 `AIAgent.run_conversation()`（Spike B）或平台 engine（Spike A） |
| L1 SDK 缺失 | `import hermes` 失败 | 使用平台 `ConversationEngine`（Spike A 路径），日志 warning |
| L2 provider 失败 | `ModelGateway.chat()` 抛异常 | `ConversationEngine` catch 异常，返回 `AgentError(code="MODEL_PROVIDER_ERROR", retryable=True)` |
| L3 全部失败 | `model_gateway` 也是 `None` | stub 响应 `"[Hermes-stub] Received: ..."`，iterations=0 |

当前 hermes.py 的 `ConversationEngine.converse()` 在 `model_gateway is not None` 路径中没有 try/except，provider 异常会直接冒泡到 `RuntimeManager`。Spike A 需要补异常处理。

---

## 3. Spike A：接入真实 ModelGateway

### 3.1 目标

让 `HermesRuntimeBackend` 使用平台已有的 `ModelGateway` + `ToolExecutor` 跑通一次真实 LLM 调用 + tool call。不引入 Hermes SDK。

这不是"接入 Hermes"，但它证明 hermes.py 内的 agentic loop 确实可以工作，并暴露接口不匹配问题。

### 3.2 方案

#### 步骤 1：修改 RuntimeManager，注入 ModelGateway 和 ToolExecutor

```python
# src/agent_platform/runtime/manager.py
from agent_platform.runtime.model_gateway import ModelGateway
from agent_platform.tools.executor import ToolExecutor

class RuntimeManager:
    def __init__(
        self,
        run_store: RunStore | None = None,
        session_store: SessionStore | None = None,
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
    ):
        self._backends = {
            NativeRuntimeBackend.name: NativeRuntimeBackend(),
            HermesRuntimeBackend.name: HermesRuntimeBackend(
                model_gateway=model_gateway,
                tool_executor=tool_executor,
            ),
            LangGraphRuntimeBackend.name: LangGraphRuntimeBackend(),
        }
        self.run_store = run_store or InMemoryRunStore()
        self.session_store = session_store or InMemorySessionStore()
```

#### 步骤 2：修改 hermes.py ConversationEngine，对齐 ModelGateway 接口

当前 hermes.py 调用 `self.model_gateway.chat(messages=..., model=..., tools=...)`。  
平台 `ModelGateway.chat()` 的签名是 `chat(self, provider_name, messages, *, model, temperature, max_tokens, tools)`。  
需要在 `converse()` 中传入 `provider_name`，并使用 `ModelMessage` 对象代替 dict。

修改前（hermes.py 第 191 行）：

```python
model_response = await self.model_gateway.chat(
    messages=messages,
    model=model_config.get("model", "native-demo"),
    temperature=model_config.get("temperature", 0.2),
    max_tokens=model_config.get("max_tokens", 1024),
    tools=tools,
)
```

修改后：

```python
from agent_platform.runtime.model_gateway import ModelGateway, ModelMessage, ModelResponse

# 在 converse() 开头构建参数
provider = model_config.get("provider", "stub")
model = model_config.get("model", "stub")
temperature = model_config.get("temperature", 0.2)
max_tokens = model_config.get("max_tokens", 1024)

messages: list[ModelMessage] = [
    ModelMessage(role="system", content=system_prompt),
    ModelMessage(role="user", content=user_query),
]

# 在 loop 中调用
model_response: ModelResponse = await self.model_gateway.chat(
    provider,
    messages,
    model=model,
    temperature=temperature,
    max_tokens=max_tokens,
    tools=tools,
)
```

#### 步骤 3：适配 ModelResponse 对象访问方式

因为 `ModelGateway.chat()` 返回 `ModelResponse`（Pydantic model）而非 dict，hermes.py 中对 model response 的 dict 风格访问需要改为属性访问：

```python
# 旧代码（dict 风格）
requested_tools = model_response.get("tool_calls", [])
content = model_response.get("content", "")

# 新代码（ModelResponse 对象）
requested_tools = model_response.tool_calls    # list[ToolCall]
content = model_response.content               # str
```

同时 tool loop 中的字段访问也要改：

```python
# 旧代码
tool_name = tc.get("name", "")
tool_input = tc.get("input", {})

# 新代码（ToolCall 是 Pydantic model）
tool_name = tc.name
tool_input = tc.arguments  # 注意：ToolCall 用的是 arguments 不是 input
```

消息列表中追加 tool 结果时，也要用 `ModelMessage`：

```python
# 旧代码
messages.append({"role": "assistant", "content": "", "tool_calls": [tc]})
messages.append({"role": "tool", "content": str(tool_output), "tool_call_id": tc.get("id", "")})

# 新代码
messages.append(ModelMessage(role="assistant", content=f"[tool_call: {tool_name}]"))
messages.append(ModelMessage(role="tool", content=str(tool_output)))
```

#### 步骤 4：补异常处理

```python
# converse() 中 model call 需要 try/except
try:
    model_response = await self.model_gateway.chat(...)
except LookupError as e:
    # provider 未注册
    return {"text": f"Model provider error: {e}", "tool_calls": [], ...}
except Exception as e:
    return {"text": f"Model call failed: {e}", "tool_calls": [], ...}
```

#### 步骤 5：新建 hermes_echo agent

```yaml
# agents/hermes_echo/manifest.yaml
api_version: agent.platform/v1
kind: AgentPackage

metadata:
  id: hermes_echo
  name: Hermes Echo Agent
  description: Echo agent using HermesRuntimeBackend with real model gateway
  owner: platform-team
  domain: demo
  tags: [demo, hermes, spike]

version:
  package_version: 0.1.0
  release_channel: dev

runtime:
  backend: hermes
  max_iterations: 2
  timeout_ms: 10000

models:
  default:
    provider: stub
    model: stub
    temperature: 0.0
    max_tokens: 256

tools:
  allow:
    - myj.goods_search
  deny:
    - terminal
    - code_execution
  timeout_ms: 3000

output:
  protocol: agent-chat/v1
  supports: [text]

evals:
  suites: []
  required_pass_rate: 0.0
```

### 3.3 测试计划

#### 测试 1：StubModelProvider 走非 stub 路径

```python
# tests/unit/test_hermes_real_engine.py
import pytest
from pathlib import Path

from agent_platform.domain.models import (
    AgentInput, AgentManifest, AgentRequest, AgentSpec,
    ManifestMetadata, ManifestModelConfig, ManifestOutput,
    ManifestRuntime, ManifestTools, ManifestVersion,
    RuntimeRequest,
)
from agent_platform.runtime.hermes import HermesRuntimeBackend
from agent_platform.runtime.model_gateway import ModelGateway
from agent_platform.tools.executor import ToolExecutor
from agent_platform.tools.registry import create_default_tool_registry


def _make_hermes_spec() -> AgentSpec:
    return AgentSpec(
        manifest=AgentManifest(
            api_version="agent.platform/v1",
            kind="AgentPackage",
            metadata=ManifestMetadata(id="hermes_echo", name="Hermes Echo"),
            version=ManifestVersion(package_version="0.1.0"),
            runtime=ManifestRuntime(backend="hermes", max_iterations=2),
            models={"default": ManifestModelConfig(provider="stub", model="stub")},
            tools=ManifestTools(allow=["myj.goods_search"]),
            output=ManifestOutput(),
        ),
        package_path=Path("/tmp/hermes_echo"),
    )


@pytest.mark.asyncio
async def test_hermes_engine_with_stub_provider_not_stub_response():
    """model_gateway 不为 None 时，不应返回 [Hermes-stub] 前缀。"""
    gw = ModelGateway()  # 内含 StubModelProvider
    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)

    backend = HermesRuntimeBackend(model_gateway=gw, tool_executor=executor)

    spec = _make_hermes_spec()
    request = RuntimeRequest(
        request=AgentRequest(
            request_id="req-spike-1",
            session_id="sess-spike-1",
            agent_id="hermes_echo",
            input=AgentInput(query="推荐低糖饮料"),
        ),
        agent_spec=spec,
    )

    result = await backend.run(request)

    assert not result.response.output.text.display.startswith("[Hermes-stub]")
    assert "[Stub LLM]" in result.response.output.text.display
    assert result.response.debug["runtime_backend"] == "hermes"
```

#### 测试 2：tool call 进入 ResponseTrace

```python
# tests/integration/test_hermes_tool_call.py
from unittest.mock import MagicMock, AsyncMock

from agent_platform.runtime.model_gateway import (
    ModelGateway, ModelResponse, ToolCall,
)
from agent_platform.tools.executor import ToolExecutor, ToolExecutionResult
from agent_platform.domain.models import ToolCallTrace


@pytest.mark.asyncio
async def test_hermes_tool_call_appears_in_trace():
    """tool call 必须出现在 ResponseTrace.tool_calls 中。"""
    # mock provider：第一次返回 tool call，第二次返回文本
    call_count = 0

    async def mock_chat(messages, *, model, temperature, max_tokens, tools=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return ModelResponse(
                content="",
                tool_calls=[
                    ToolCall(id="tc-1", name="myj.goods_search", arguments={"query": "低糖饮料"})
                ],
                finish_reason="tool_use",
                model="mock",
            )
        return ModelResponse(
            content="推荐低糖茶饮和无糖气泡水。",
            finish_reason="stop",
            model="mock",
        )

    mock_provider = MagicMock()
    mock_provider.name = "stub"
    mock_provider.chat = mock_chat

    gw = ModelGateway()
    gw.register(mock_provider)  # 覆盖默认 stub

    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)

    backend = HermesRuntimeBackend(model_gateway=gw, tool_executor=executor)

    spec = _make_hermes_spec()
    request = RuntimeRequest(
        request=AgentRequest(
            request_id="req-tc-1",
            session_id="sess-tc-1",
            agent_id="hermes_echo",
            input=AgentInput(query="推荐低糖饮料"),
        ),
        agent_spec=spec,
    )

    result = await backend.run(request)

    assert result.response.output.text.display == "推荐低糖茶饮和无糖气泡水。"
    assert len(result.response.trace.tool_calls) == 1
    tc_trace = result.response.trace.tool_calls[0]
    assert tc_trace.tool_name == "myj.goods_search"
    assert tc_trace.status == "success"
```

### 3.4 需要修改的文件清单

| 文件 | 改动类型 | 说明 |
| --- | --- | --- |
| `src/agent_platform/runtime/hermes.py` | 修改 | `ConversationEngine` 适配 `ModelGateway`/`ModelResponse`/`ModelMessage`/`ToolCall` 类型；补异常处理 |
| `src/agent_platform/runtime/manager.py` | 修改 | `RuntimeManager.__init__` 接受 `model_gateway` 和 `tool_executor` 参数，传入 `HermesRuntimeBackend` |
| `agents/hermes_echo/manifest.yaml` | 新建 | spike 验证用 agent |
| `tests/unit/test_hermes_real_engine.py` | 新建 | 验证非 stub 路径 |
| `tests/integration/test_hermes_tool_call.py` | 新建 | 验证 tool call trace |

### 3.5 不需要改动的部分

- `ManifestMapper`、`ToolBridge`、`SessionBridge`、`ResponseMapper`、`TraceBridge`、`PolicyEnforcer`：已实现，Spike A 不需要改。
- `runtime/conversation.py`：Spike A 不影响它。两套 ConversationEngine 暂时共存。
- `pyproject.toml`：Spike A 不引入新依赖。
- `domain/models.py`：不需要修改。

---

## 4. Spike B：真实 Hermes SDK 集成

> **重要：** 本节内容基于初始假设编写。§10-§11 基于实际源码分析提供了修正方案和 B-Q 问题的完整回答。实施时以 §10-§11 为准。

### 4.1 目标

引入 Hermes Python 包，用 `AIAgent` 替代 hermes.py 内自建的 `ConversationEngine`，证明平台可以驱动真实 Hermes runtime。

### 4.2 前置条件

1. Spike A 完成，`HermesRuntimeBackend` 已是非 stub。
2. ~~确认 Hermes Python 包的 PyPI 名称和版本。~~ → 已确认：`hermes-agent>=0.13.0`（§10.1）
3. ~~确认 `AIAgent` 的公开 API 签名（`run_conversation` / `chat` / 其他）。~~ → 已确认（§10.2, §10.3）
4. ~~确认 Hermes tool handler 是 sync 还是 async callback。~~ → 已确认：sync，`(args: dict, **kw) -> str`（§10.4）

### 4.3 依赖引入

```toml
# pyproject.toml — 修正后（见 §10.1）
[project.optional-dependencies]
hermes = [
    "hermes-agent>=0.13.0,<0.14",
]
```

安装方式：

```bash
uv pip install -e ".[hermes]"
```

### 4.4 方案

#### 条件导入 + fallback

```python
# src/agent_platform/runtime/hermes.py 文件顶部 — 修正后（见 §10.1）

try:
    from run_agent import AIAgent
    HERMES_AVAILABLE = True
except ImportError:
    HERMES_AVAILABLE = False
    logger.warning(
        "Hermes SDK not installed; HermesRuntimeBackend will use platform ConversationEngine"
    )
```

#### HermesRuntimeBackend 增加双路径

```python
class HermesRuntimeBackend:
    name = "hermes"

    def __init__(
        self,
        model_gateway: ModelGateway | None = None,
        tool_executor: ToolExecutor | None = None,
    ):
        # ... 现有组件初始化 ...
        self._fallback_engine = ConversationEngine(
            model_gateway=model_gateway,
            tool_executor=tool_executor,
        )

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        violations = self.policy_enforcer.check_pre_run(request.agent_spec)
        if violations:
            return self._policy_error(request, violations)

        hermes_config = self.manifest_mapper.to_hermes_config(request.agent_spec)

        if HERMES_AVAILABLE:
            try:
                return await self._run_with_hermes(request, hermes_config)
            except Exception as e:
                logger.error("Hermes SDK run failed, falling back: %s", e)

        # Fallback 到 Spike A 路径
        return await self._run_with_engine(request, hermes_config)
```

#### \_run_with_hermes 实现

```python
async def _run_with_hermes(
    self, request: RuntimeRequest, hermes_config: dict
) -> RuntimeResponse:
    import anyio

    # 构建 Hermes tool handlers
    tool_handlers = {}
    if self.tool_executor:
        for tool_name in hermes_config.get("tools", []):
            tool_handlers[tool_name] = make_hermes_tool_handler(
                tool_name,
                self.tool_executor,
                allowed_tools=hermes_config.get("tools", []),
                timeout_ms=3000,
            )

    model_cfg = hermes_config.get("model", {})
    agent = AIAgent(
        provider=model_cfg.get("provider", "openai"),
        model=model_cfg.get("model", "gpt-4.1-mini"),
        tools=tool_handlers,
    )

    session_config = self.session_bridge.map_session(
        request.request.session_id, hermes_config
    )

    # AIAgent.run_conversation 可能是同步的
    hermes_result = await anyio.to_thread.run_sync(
        lambda: agent.run_conversation(
            user_message=request.request.input.query,
            system_message=hermes_config.get("system_prompt", ""),
        )
    )

    result_dict = self._normalize_hermes_result(hermes_result)
    return self.response_mapper.to_platform_response(result_dict, request)

@staticmethod
def _normalize_hermes_result(hermes_result) -> dict:
    """将 Hermes AIAgent 的返回值规范化为 ResponseMapper 需要的 dict。"""
    # 具体结构取决于 Hermes SDK 的返回类型
    return {
        "text": str(hermes_result),
        "tool_calls": [],       # 需根据实际结构填充
        "run_id": None,
        "iterations": 0,
        "model_calls": 0,
    }
```

### 4.5 Spike B 必须验证的问题

| 编号 | 问题 | 验证方式 |
| --- | --- | --- |
| B-Q1 | `AIAgent.__init__` 的必需参数列表 | 阅读 Hermes 源码或 `help(AIAgent)` |
| B-Q2 | `run_conversation` 返回值类型（str / dict / dataclass） | 打印返回值 |
| B-Q3 | tool handler 签名 `(args: dict, **kwargs) -> str` 是否正确 | 注册一个测试 handler 验证 |
| B-Q4 | tool handler 被 sync 还是 async 调用 | 如果 sync 调用而 `ToolExecutor.execute()` 是 async，需要 event loop bridge |
| B-Q5 | `AIAgent` 或 `run_conversation` 是否接受 session_id | 决定 SessionBridge 映射方式 |
| B-Q6 | 是否有 `stream_conversation` 或类似流式 API | 决定后续 streaming 设计 |
| B-Q7 | `run_conversation` 是否返回 tool call trace 信息 | 决定 `_normalize_hermes_result` 实现 |

### 4.6 Spike B 测试计划

```python
# tests/integration/test_hermes_sdk_integration.py

import pytest

try:
    from run_agent import AIAgent
    HERMES_AVAILABLE = True
except ImportError:
    HERMES_AVAILABLE = False


@pytest.mark.skipif(not HERMES_AVAILABLE, reason="Hermes SDK not installed")
@pytest.mark.asyncio
async def test_hermes_sdk_real_agent_run():
    """验证 Hermes AIAgent 可被平台驱动，返回非 stub 响应。"""
    gw = ModelGateway()
    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)

    backend = HermesRuntimeBackend(model_gateway=gw, tool_executor=executor)
    result = await backend.run(runtime_request)

    assert result.response.output.status == "completed"
    assert not result.response.output.text.display.startswith("[Hermes-stub]")
    assert result.response.debug["runtime_backend"] == "hermes"


@pytest.mark.skipif(HERMES_AVAILABLE, reason="Test fallback when SDK absent")
@pytest.mark.asyncio
async def test_hermes_fallback_when_sdk_missing():
    """Hermes SDK 不可用时，自动 fallback 到平台 engine。"""
    gw = ModelGateway()
    registry = create_default_tool_registry()
    executor = ToolExecutor(registry)

    backend = HermesRuntimeBackend(model_gateway=gw, tool_executor=executor)
    result = await backend.run(runtime_request)

    # 应该走 Spike A 路径，不 crash
    assert result.response.output.status == "completed"
    assert "[Stub LLM]" in result.response.output.text.display
```

---

## 5. Fallback 策略详细设计

### 5.1 决策流程

```
HermesRuntimeBackend.run(request)
    |
    +-- PolicyEnforcer.check_pre_run() 失败 --> 返回 POLICY_VIOLATION
    |
    +-- HERMES_AVAILABLE == True
    |       |
    |       +-- _run_with_hermes() 成功 --> 返回正常 RuntimeResponse
    |       |
    |       +-- _run_with_hermes() 异常 --> 日志 error，fallthrough
    |
    +-- model_gateway is not None
    |       |
    |       +-- _run_with_engine() 成功 --> 返回正常 RuntimeResponse（走平台 engine）
    |       |
    |       +-- _run_with_engine() 异常 --> 冒泡到 RuntimeManager（返回 RUNTIME_ERROR）
    |
    +-- model_gateway is None
            |
            +-- 返回 [Hermes-stub] 响应
```

### 5.2 Manifest 控制 fallback 行为

```yaml
extensions:
  hermes:
    require_sdk: false        # false = 允许 fallback 到平台 engine；true = SDK 不可用时直接报错
    fallback_on_error: true   # SDK 运行失败时是否 fallback，false 时直接报错
```

默认值均为宽松模式（允许 fallback），保证平台可用性。

---

## 6. 验收标准

### 6.1 Spike A 验收标准

| 编号 | 标准 | 验证方式 |
| --- | --- | --- |
| A-1 | `HermesRuntimeBackend` 使用 `StubModelProvider` 时，响应不包含 `[Hermes-stub]` 前缀 | 单元测试 |
| A-2 | 使用 MockProvider 可触发 tool call，`myj.goods_search` 被真实执行 | 集成测试 |
| A-3 | tool call 出现在 `ResponseTrace.tool_calls` 中，`status` 为 `success` | 集成测试 |
| A-4 | 返回的 `AgentResponse` 包含 request_id / session_id / agent / output / trace | 单元测试 |
| A-5 | `hermes_echo` agent manifest 可被 `ManifestLoader` 加载 | 单元测试 |
| A-6 | 所有测试不依赖外部网络，CI 可跑 | CI |

### 6.2 Spike B 验收标准

| 编号 | 标准 | 验证方式 |
| --- | --- | --- |
| B-1 | `import hermes` 成功，版本 pin 在 `pyproject.toml` | 安装检查 |
| B-2 | `AIAgent` 可被构造并执行 `run_conversation` | 集成测试（需 API key） |
| B-3 | 平台 tool handler 被 Hermes AIAgent 正确调用 | 集成测试 |
| B-4 | Hermes SDK import 失败时自动 fallback 到 Spike A 路径，不 crash | 单元测试（mock import） |
| B-5 | `ResponseTrace` 包含 Hermes 执行信息（iterations / model_calls） | 集成测试 |

---

## 7. 时间估算

| 阶段 | 工作项 | 估时 | 前置 |
| --- | --- | --- | --- |
| Spike A | 修改 `manager.py` 注入 `model_gateway` 和 `tool_executor` | 0.5d | 无 |
| Spike A | 适配 hermes.py `ConversationEngine`：`ModelGateway`/`ModelResponse`/`ModelMessage` 类型对齐 | 1d | 上一步 |
| Spike A | 补 `converse()` 异常处理 | 0.5d | 上一步 |
| Spike A | 新建 `agents/hermes_echo/manifest.yaml` | 0.5d | 无（可并行） |
| Spike A | 编写测试并通过 | 1d | 代码改动完成 |
| **Spike A 小计** |  | **3d** |  |
| Spike B | 调研 Hermes SDK API（B-Q1 \~ B-Q7） | 1d | Spike A 完成 |
| Spike B | 实现条件导入 + `_run_with_hermes` + `_normalize_hermes_result` | 2d | API 调研完成 |
| Spike B | 实现 `make_hermes_tool_handler` + 解决 sync/async bridge | 0.5d | adapter 完成 |
| Spike B | fallback 逻辑和 manifest extension 支持 | 0.5d | adapter 完成 |
| Spike B | 集成测试 | 1d | adapter 完成 |
| **Spike B 小计** |  | **5d** |  |

**建议：Spike A 优先执行。Spike B 在 Hermes SDK 版本和 API 确认后启动。**

---

## 8. 技术决策记录

| 编号 | 决策 | 理由 |
| --- | --- | --- |
| D-1 | Spike A 优先于 Spike B | 不依赖外部 SDK，快速验证现有代码可工作 |
| D-2 | hermes.py 内 `ConversationEngine` 暂时保留，不合并到 `conversation.py` | 减少改动范围；spike 阶段保持 hermes.py 自包含 |
| D-3 | Hermes SDK 作为 optional dependency | 不强制所有环境安装，fallback 路径保证可用性 |
| D-4 | Streaming 不在 spike 范围内 | runtime 层 streaming 协议未定义，spike 目标是证明非 stub |
| D-5 | Fallback 默认开启 | 保证 Hermes 不可用时平台仍可服务 |
| D-6 | Spike B 中 Hermes memory 默认关闭 | 平台是 session owner，避免 memory 数据跨租户泄露 |
| D-7 | hermes.py `ConversationEngine` 适配 `ModelGateway` 类型（方式 A），不直接复用 `conversation.py` 的 engine（方式 B） | 方式 A 改动更小、更可控；方式 B 需要重构 `HermesRuntimeBackend.run()` 以构建 `RuntimeContext` |

---

## 9. 后续工作（spike 之后）

1. **合并两套 ConversationEngine**：spike 验证后，评估是否把 hermes.py 的 engine 合并到 `runtime/conversation.py`，消除代码重复。
2. **Streaming 协议设计**：定义 `RuntimeBackend.run_stream()` + `StreamEvent` 类型，对接 `api/streaming.py` 和 `api/websocket.py`。
3. **真实 LLM provider 注册**：在 `ModelGateway` 中注册 OpenAI-compatible provider（需要 `openai` SDK 或 `httpx` 直连）。
4. **Hermes 版本升级契约测试**：每次 Hermes 版本升级时，adapter contract test 必须通过。
5. **生产部署方案**：Hermes SDK 在容器镜像中的安装、API key 注入、provider 配置管理。

---

## 10. Hermes 源码分析补充（基于 hermes-agent v0.13.0）

> 源码路径：`/Users/errocks/py-workspace/hermes-agent`
> 分析日期：2026-05-15
> 版本：hermes-agent 0.13.0

本节基于实际 Hermes 源码阅读，补充 Spike B 的 API 细节并回答 §4.5 中的 B-Q1 ~ B-Q7。

### 10.1 包名与导入路径

**§4.3 中的假设需要修正：**

| 假设 | 实际 |
| --- | --- |
| 包名 `hermes-ai` | `hermes-agent`（PyPI / `pyproject.toml`） |
| 导入 `from hermes.ai_agent import AIAgent` | `from run_agent import AIAgent` |
| 最低 Python | `>=3.11` |

Hermes 不是传统包结构（无 `hermes/` 顶级包），`AIAgent` 定义在项目根目录 `run_agent.py:1094`。安装后通过 `hermes_agent.egg-info` 注册到 Python path。

**修正后的 pyproject.toml 依赖：**

```toml
[project.optional-dependencies]
hermes = [
    "hermes-agent>=0.13.0,<0.14",
]
```

**修正后的条件导入：**

```python
try:
    from run_agent import AIAgent
    HERMES_AVAILABLE = True
except ImportError:
    HERMES_AVAILABLE = False
```

### 10.2 AIAgent 构造函数（B-Q1 回答）

完整签名约 60 个参数。平台集成关键参数：

```python
class AIAgent:
    def __init__(
        self,
        base_url: str = None,           # LLM API endpoint（如 https://openrouter.ai/api/v1）
        api_key: str = None,            # 优先级：参数 > 环境变量
        provider: str = None,           # "openai" / "anthropic" / "openrouter" / ...
        api_mode: str = None,           # "chat_completions" | "anthropic_messages" | "codex_responses" | "bedrock_converse"
        model: str = "",                # 如 "anthropic/claude-sonnet-4.6"
        max_iterations: int = 90,       # tool-calling loop 最大迭代次数
        enabled_toolsets: List[str] = None,  # 仅启用指定 toolset
        disabled_toolsets: List[str] = None, # 禁用指定 toolset
        session_id: str = None,         # 会话 ID（自动生成如不提供）
        session_db = None,              # SessionDB 实例（SQLite 会话存储）
        skip_context_files: bool = False,  # 跳过 SOUL.md / AGENTS.md 注入
        skip_memory: bool = False,      # 跳过 memory provider
        quiet_mode: bool = False,       # 抑制进度输出
        save_trajectories: bool = False,# 保存对话轨迹到 JSONL
        platform: str = None,           # "cli" / "telegram" / "discord" / ...
        user_id: str = None,            # 用户标识
        # 回调接口
        tool_progress_callback: callable = None,  # (tool_name, args_preview)
        tool_start_callback: callable = None,
        tool_complete_callback: callable = None,
        stream_delta_callback: callable = None,    # 流式 token 回调
        thinking_callback: callable = None,
        reasoning_callback: callable = None,
        step_callback: callable = None,
        # ... 其余 ~35 个参数（credential_pool, fallback_model, checkpoints 等）
    ): ...
```

**关键发现：AIAgent 构造函数不接受 `tools` 参数。** 工具系统通过全局 registry 管理（见 §10.4）。

### 10.3 run_conversation 接口（B-Q2、B-Q5、B-Q7 回答）

```python
def run_conversation(
    self,
    user_message: str,
    system_message: str = None,
    conversation_history: List[Dict[str, Any]] = None,
    task_id: str = None,
    stream_callback: Optional[callable] = None,
    persist_user_message: Optional[str] = None,
) -> Dict[str, Any]:
```

**返回值类型：`dict`**，关键字段：

```python
{
    "final_response": str,              # 最终文本响应
    "last_reasoning": str | None,       # 最后一次 reasoning（思考链）
    "messages": list[dict],             # 完整消息历史（OpenAI 格式）
    "api_calls": int,                   # 本轮 API 调用次数
    "completed": bool,                  # 是否正常完成
    "turn_exit_reason": str,            # 退出原因
    "interrupted": bool,                # 是否被中断
    "model": str,                       # 实际使用的模型
    "provider": str,                    # 实际使用的 provider
    "input_tokens": int,                # 累计 input tokens
    "output_tokens": int,               # 累计 output tokens
    "estimated_cost_usd": float,        # 预估费用
    # ... 其余 token 统计字段
}
```

**`chat()` 方法：** 简单包装，返回 `result["final_response"]`。

**Agent loop 核心逻辑（`run_agent.py:11680`）：**

```python
while (api_call_count < max_iterations and budget.remaining > 0) or grace_call:
    if interrupt_requested: break
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    # response 经过 NormalizedResponse 规范化
    if response.tool_calls:
        for tc in response.tool_calls:
            result = handle_function_call(tc.name, tc.arguments, task_id)
            messages.append(tool_result_message(result))
    else:
        final_response = response.content
        break
```

### 10.4 工具系统（B-Q3、B-Q4 回答）

**架构：全局 registry + 自动发现，非构造函数注入。**

```
tools/registry.py  →  ToolRegistry（全局单例 `registry`）
       ↑
tools/*.py  →  每个文件在 import 时调用 registry.register() 自注册
       ↑
model_tools.py  →  discover_builtin_tools() 触发导入；提供 handle_function_call()
       ↑
run_agent.py  →  AIAgent 在 loop 中调用 handle_function_call()
```

**工具注册 API：**

```python
from tools.registry import registry

registry.register(
    name="web_search",           # 工具名
    toolset="web",               # 所属 toolset（用于启用/禁用分组）
    schema=WEB_SEARCH_SCHEMA,    # OpenAI 格式的 function schema dict
    handler=lambda args, **kw: web_search_tool(args.get("query", ""), limit=args.get("limit", 5)),
    check_fn=check_web_api_key,  # 可用性检查函数
    requires_env=["WEB_API_KEY"],# 必需环境变量
    is_async=False,              # True 时用 _run_async() bridge
    emoji="🔍",
    max_result_size_chars=100_000,
)
```

**工具 handler 签名：** `(args: dict, **kwargs) -> str`

- 同步函数（`is_async=False`）直接调用
- 异步函数（`is_async=True`）通过 `_run_async()` 在持久 event loop 上执行
- 返回值必须是 `str`（JSON string）
- `kwargs` 包含 `task_id`, `session_id`, `tool_call_id`, `user_task` 等上下文

**工具调度：** `handle_function_call(function_name, function_args, task_id, ...)` 查 registry，执行 handler，返回 `str`。

**对 Spike B 的影响：**

平台工具不能通过 `AIAgent(tools=...)` 传入。集成方式有两条路径：

| 路径 | 方式 | 优势 | 风险 |
| --- | --- | --- | --- |
| R1：注册到 Hermes registry | 将平台 `ToolExecutor` 注册为 Hermes tool | 完全复用 Hermes tool loop | 工具注册在全局空间，多 agent 可能冲突 |
| R2：自定义 toolset + check_fn | 为平台工具创建独立 toolset，`check_fn` 控制可用性 | 隔离性好 | 需要为每个平台工具生成 schema + handler wrapper |

**推荐路径 R2，具体实现：**

```python
def register_platform_tools_to_hermes(
    tool_executor: ToolExecutor,
    allowed_tools: list[str],
    toolset_name: str = "agent-platform",
):
    """将平台 ToolExecutor 中的工具注册到 Hermes 全局 registry。"""
    from tools.registry import registry

    for tool_name in allowed_tools:
        tool_def = tool_executor.registry.get(tool_name)
        if not tool_def:
            continue

        schema = {
            "name": tool_name,
            "description": tool_def.description,
            "parameters": tool_def.input_schema or {"type": "object", "properties": {}},
        }

        def make_handler(name: str):
            def handler(args: dict, **kwargs) -> str:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(
                        tool_executor.execute(name, args, allowed_tools=allowed_tools)
                    )
                    return json.dumps({"output": str(result.output)}, ensure_ascii=False)
                finally:
                    loop.close()
            return handler

        registry.register(
            name=tool_name,
            toolset=toolset_name,
            schema=schema,
            handler=make_handler(tool_name),
            is_async=False,
        )
```

### 10.5 Provider 系统

Hermes 的 provider 系统分三层：

```
ProviderProfile (providers/base.py)
    声明式配置：name, api_mode, base_url, env_vars, default_headers, fallback_models
    方法：prepare_messages(), build_extra_body(), fetch_models()
         ↓
ProviderTransport (agent/transports/base.py)
    数据转换：convert_messages() → convert_tools() → build_kwargs() → normalize_response()
    实现：ChatCompletionsTransport, AnthropicTransport, CodexTransport, BedrockTransport
         ↓
AIAgent
    拥有 OpenAI client 实例，执行实际 API 调用
    根据 api_mode 选择 transport
```

**NormalizedResponse（所有 provider 的统一返回格式）：**

```python
@dataclass
class NormalizedResponse:
    content: str | None
    tool_calls: list[ToolCall] | None
    finish_reason: str      # "stop" / "tool_calls" / "length" / "content_filter"
    reasoning: str | None
    usage: Usage | None
    provider_data: dict | None

@dataclass
class ToolCall:
    id: str | None
    name: str
    arguments: str          # JSON string
    provider_data: dict | None
```

**对平台集成的影响：** AIAgent 内部使用 OpenAI SDK client，provider 通过 `base_url` + `api_key` 配置。平台的 `ModelGateway` 与 Hermes 的 provider 系统是并行的两套——Spike B 中 Hermes 直接调 LLM，不经过平台 ModelGateway。

### 10.6 Session 管理（B-Q5 回答）

Hermes 使用 `SessionDB`（`hermes_state.py`）实现会话持久化：

- 底层：SQLite WAL mode + FTS5 全文搜索
- 存储：session metadata、消息历史、模型配置
- 路径：`~/.hermes/state.db`
- 会话来源标记：`cli` / `telegram` / `discord` 等

**`AIAgent` 接受 `session_id` 和 `session_db` 参数。** `run_conversation()` 也接受 `conversation_history` 参数注入历史消息。

**平台集成策略（修正 §2.4）：**

```python
agent = AIAgent(
    session_id=f"{tenant_id}:{agent_id}:{platform_session_id}",
    session_db=None,        # 不使用 Hermes 的 SessionDB
    skip_memory=True,       # 平台管 session，不用 Hermes memory
    skip_context_files=True,# 不注入 Hermes 的 SOUL.md / AGENTS.md
)

# conversation_history 由平台 session_store 提供
result = agent.run_conversation(
    user_message=query,
    system_message=system_prompt,
    conversation_history=platform_session.messages,
)
```

### 10.7 Streaming 支持（B-Q6 回答）

`run_conversation()` 接受 `stream_callback: callable` 参数，用于 token 级流式输出：

```python
result = agent.run_conversation(
    user_message="...",
    stream_callback=lambda delta: print(delta, end="", flush=True),
)
```

`chat()` 也支持：`agent.chat(message, stream_callback=callback)`。

这意味着平台后续的 streaming 设计可以直接对接 Hermes 的 `stream_callback`，不需要额外适配。

### 10.8 子代理 (Subagent) 模式

Hermes 内置 `delegate_task` 工具实现子代理模式：

- `_build_child_agent()` 在主线程构造子 AIAgent 实例
- `_run_single_child()` 在 ThreadPoolExecutor 中执行
- 子代理继承父代理的 toolset 配置（交集约束）
- 支持嵌套（`role="orchestrator"` 允许子代理继续 delegate）
- 最大深度默认 1（parent → child），可配置到 3

**对平台的参考价值：** 平台的 `RuntimeManager` 概念上类似——平台是 orchestrator，Hermes 是 child。关键差异是平台需要跨进程边界，而 Hermes 的 delegate 是进程内线程池。

---

## 11. Spike B 修正方案（基于源码分析）

基于 §10 的发现，§4 中的方案需要做以下修正：

### 11.1 修正后的 _run_with_hermes 实现

```python
async def _run_with_hermes(
    self, request: RuntimeRequest, hermes_config: dict
) -> RuntimeResponse:
    import anyio
    from run_agent import AIAgent

    model_cfg = hermes_config.get("model", {})
    tools_config = hermes_config.get("tools", [])

    # 1. 将平台工具注册到 Hermes 全局 registry
    toolset_name = f"ap-{request.request.agent_id}"
    if self.tool_executor and tools_config:
        register_platform_tools_to_hermes(
            self.tool_executor,
            allowed_tools=tools_config,
            toolset_name=toolset_name,
        )

    # 2. 构造 AIAgent
    agent = AIAgent(
        base_url=model_cfg.get("base_url", ""),
        api_key=model_cfg.get("api_key"),
        provider=model_cfg.get("provider", ""),
        model=model_cfg.get("model", ""),
        max_iterations=hermes_config.get("max_iterations", 10),
        enabled_toolsets=[toolset_name] if tools_config else [],
        session_id=f"{request.request.agent_id}:{request.request.session_id}",
        session_db=None,
        skip_context_files=True,
        skip_memory=True,
        quiet_mode=True,
    )

    # 3. 在线程中执行（run_conversation 是同步的）
    system_prompt = hermes_config.get("system_prompt", "")
    platform_history = hermes_config.get("conversation_history", [])

    hermes_result = await anyio.to_thread.run_sync(
        lambda: agent.run_conversation(
            user_message=request.request.input.query,
            system_message=system_prompt,
            conversation_history=platform_history,
        )
    )

    # 4. 规范化返回值
    result_dict = self._normalize_hermes_result(hermes_result)
    return self.response_mapper.to_platform_response(result_dict, request)

@staticmethod
def _normalize_hermes_result(hermes_result: dict) -> dict:
    """将 Hermes AIAgent.run_conversation() 返回值转为 ResponseMapper 格式。"""

    # 从 messages 中提取 tool call 信息
    tool_calls = []
    for msg in hermes_result.get("messages", []):
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                fn = tc.get("function", tc)
                tool_calls.append({
                    "name": fn.get("name", ""),
                    "input": json.loads(fn.get("arguments", "{}")) if isinstance(fn.get("arguments"), str) else fn.get("arguments", {}),
                    "output": "",  # tool results 在后续 tool role message 中
                    "status": "success",
                })

    return {
        "text": hermes_result.get("final_response", ""),
        "tool_calls": tool_calls,
        "run_id": None,
        "iterations": hermes_result.get("api_calls", 0),
        "model_calls": hermes_result.get("api_calls", 0),
        "model": hermes_result.get("model", ""),
        "provider": hermes_result.get("provider", ""),
        "completed": hermes_result.get("completed", False),
        "input_tokens": hermes_result.get("input_tokens", 0),
        "output_tokens": hermes_result.get("output_tokens", 0),
        "estimated_cost_usd": hermes_result.get("estimated_cost_usd", 0.0),
    }
```

### 11.2 B-Q 问题完整回答

| 编号 | 问题 | 回答 |
| --- | --- | --- |
| B-Q1 | `AIAgent.__init__` 必需参数 | 无必需参数，全部有默认值。平台集成推荐设置：`base_url`, `api_key`, `provider`, `model`, `max_iterations`, `enabled_toolsets`, `session_id`, `skip_context_files=True`, `skip_memory=True`, `quiet_mode=True` |
| B-Q2 | `run_conversation` 返回值类型 | `dict`，关键字段见 §10.3 |
| B-Q3 | tool handler 签名 | `(args: dict, **kwargs) -> str`（sync），kwargs 包含 `task_id`, `session_id` 等 |
| B-Q4 | tool handler sync/async | 默认 sync 调用；`is_async=True` 时通过 `_run_async()` 在持久 event loop 上执行 |
| B-Q5 | session_id 接受方式 | `AIAgent(session_id=...)` 构造时传入 + `session_db=None` 禁用 Hermes SessionDB |
| B-Q6 | 流式 API | `run_conversation(stream_callback=callable)` + `chat(stream_callback=callable)` |
| B-Q7 | tool call trace | 在 `result["messages"]` 中，`role="assistant"` 的消息含 `tool_calls` 列表，`role="tool"` 的消息含 `content`（工具输出） |

### 11.3 Spike B 风险清单（新增）

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| Hermes 全局 tool registry 冲突 | 多个 agent 注册同名工具会被拒绝 | 用 `{agent_id}.{tool_name}` 前缀 + 请求结束后 `registry.deregister()` |
| Hermes 依赖链庞大（openai SDK、httpx 等） | 增加镜像体积、构建时间 | optional dependency group；Spike A 路径作为 fallback |
| `run_conversation` 是同步阻塞 | 占用 worker 线程直到 LLM 返回 | `anyio.to_thread.run_sync()` 包装；配置 thread pool 大小 |
| Hermes `run_agent.py` 是 ~15k 行单文件 | 升级时 diff 可能很大 | pin minor version；建立 adapter contract test |
| Provider 配置在 AIAgent 中绕过平台 ModelGateway | 平台无法统一管理 API key 和配额 | Spike B 阶段 provider 由平台 config 注入；后续可实现平台 proxy provider |

### 11.4 修正后的时间估算

| 阶段 | 工作项 | 估时 | 前置 |
| --- | --- | --- | --- |
| Spike B | 实现 `register_platform_tools_to_hermes` + tool handler wrapper | 1d | Spike A 完成 |
| Spike B | 实现 `_run_with_hermes` + `_normalize_hermes_result` | 1.5d | tool 注册完成 |
| Spike B | 解决全局 registry 冲突（agent scope prefix + deregister） | 0.5d | _run_with_hermes 完成 |
| Spike B | fallback 逻辑和 manifest extension 支持 | 0.5d | adapter 完成 |
| Spike B | 集成测试 + adapter contract test | 1d | adapter 完成 |
| **Spike B 修正小计** | | **4.5d** | |