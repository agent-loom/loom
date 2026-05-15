# HermesBackend Spike 设计

> Status: Draft
> Stage: S3
> Owner: platform
> Last verified against code: 2026-05-15

> 前置文档：战略设计见 [`hermes-runtime.md`](./hermes-runtime.md)；差距分析见 [`../implementation-gap.md`](../implementation-gap.md) §2.4；设计计划见 [`../next-stage-design-plan.md`](../next-stage-design-plan.md) §P0-3。

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
| `model_gateway` 类型 | `Any \| None` | `ModelGateway`（具体类） |
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
    "hermes-ai>=0.x.y,<0.x+1",   # pin 到 minor 级别，patch 允许浮动
]
```

引入原则：

1. 使用 optional dependency group `hermes`，不强制所有环境安装。
2. CI 中 `uv pip install -e ".[hermes]"` 且锁定到 `uv.lock`。
3. Hermes 版本升级必须通过 adapter contract test 才允许合并。

### 2.2 HermesRuntimeBackend 调用哪个官方 API

**Spike A：不调 Hermes API。** 复用平台的 `ModelGateway.chat()` 和 `ToolExecutor.execute()`。

**Spike B：** 调用 Hermes 的 `AIAgent` 核心接口：

```python
from hermes.ai_agent import AIAgent

agent = AIAgent(
    provider=...,
    model=...,
    tools=[...],
    session_id=...,
)
result = agent.run_conversation(user_message, system_message, conversation_history)
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

```text
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

### 4.1 目标

引入 Hermes Python 包，用 `AIAgent` 替代 hermes.py 内自建的 `ConversationEngine`，证明平台可以驱动真实 Hermes runtime。

### 4.2 前置条件

1. Spike A 完成，`HermesRuntimeBackend` 已是非 stub。
2. 确认 Hermes Python 包的 PyPI 名称和版本。
3. 确认 `AIAgent` 的公开 API 签名（`run_conversation` / `chat` / 其他）。
4. 确认 Hermes tool handler 是 sync 还是 async callback。

### 4.3 依赖引入

```toml
# pyproject.toml
[project.optional-dependencies]
hermes = [
    "hermes-ai>=0.x.y,<0.x+1",
]
```

安装方式：

```bash
uv pip install -e ".[hermes]"
```

### 4.4 方案

#### 条件导入 + fallback

```python
# src/agent_platform/runtime/hermes.py 文件顶部

try:
    from hermes.ai_agent import AIAgent
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

#### _run_with_hermes 实现

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
    from hermes.ai_agent import AIAgent
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

```text
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
| **Spike A 小计** | | **3d** | |
| Spike B | 调研 Hermes SDK API（B-Q1 ~ B-Q7） | 1d | Spike A 完成 |
| Spike B | 实现条件导入 + `_run_with_hermes` + `_normalize_hermes_result` | 2d | API 调研完成 |
| Spike B | 实现 `make_hermes_tool_handler` + 解决 sync/async bridge | 0.5d | adapter 完成 |
| Spike B | fallback 逻辑和 manifest extension 支持 | 0.5d | adapter 完成 |
| Spike B | 集成测试 | 1d | adapter 完成 |
| **Spike B 小计** | | **5d** | |

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
