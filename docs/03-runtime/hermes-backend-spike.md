# HermesBackend Spike 设计

> Status: Draft
> Stage: S3
> Owner: platform
> Last verified against code: 2026-05-15

> 相关文档：Hermes 战略设计见 [`hermes-runtime.md`](hermes-runtime.md)；当前实现差距见 [`../implementation-gap.md`](../implementation-gap.md) §2.4。

## 1. 当前状态

`src/agent_platform/runtime/hermes.py`（340 行）已实现：

| 组件 | 状态 |
|---|---|
| ManifestMapper | ✓ 完整映射 |
| ToolBridge | ✓ 工具格式转换 |
| SessionBridge | ✓ 会话映射 |
| ResponseMapper | ✓ 标准 AgentResponse 输出 |
| TraceBridge | ✓ tool_calls 进入 trace |
| ConversationEngine | ✓ 多轮 tool-use loop |
| model_gateway 注入 | ✗ 始终为 None |

关键问题：`RuntimeManager` 实例化 `HermesRuntimeBackend()` 时无参数，`model_gateway=None`，导致 `ConversationEngine` 始终返回 `[Hermes-stub]` 响应。

## 2. 两条 Spike 路线

### Spike A：接入真实 Model Gateway（快速验证）

**目标**：证明现有 agentic loop 可以用真实 LLM 完成工具调用。

**做法**：
1. 实现 `OpenAICompatibleProvider`，调用 OpenAI-compatible API。
2. 将 `model_gateway` 注入 `HermesRuntimeBackend`。
3. 用 `hermes_echo` agent 跑一次真实对话 + 工具调用。

```python
class OpenAICompatibleProvider:
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1"):
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        self.name = "openai"

    async def chat(
        self,
        *,
        messages: list[dict],
        model: str = "gpt-4o",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict] | None = None,
    ) -> dict:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
        resp = await self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()
```

**注入方式**：

```python
# app.py 启动时
if settings.openai_api_key:
    provider = OpenAICompatibleProvider(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or "https://api.openai.com/v1",
    )
    model_gateway.register_provider(provider)
    hermes_backend = HermesRuntimeBackend(model_gateway=model_gateway)
    runtime_manager.register_backend(hermes_backend)
```

**验证 Agent**：

```yaml
# agents/hermes_echo/manifest.yaml
api_version: agent.platform/v1
id: hermes_echo
name: Hermes Echo Agent
version: 0.1.0
runtime:
  backend: hermes
  model: gpt-4o
prompts:
  system: |
    你是一个 echo agent，用于验证 Hermes runtime。
    收到任何消息时，先调用 echo 工具，然后返回工具结果。
tools:
  allowed:
    - echo
  definitions:
    - name: echo
      description: 回显输入内容
      parameters:
        type: object
        properties:
          message:
            type: string
        required: [message]
      handler_ref: "agent_platform.tools.builtins:echo_handler"
```

### Spike B：接入真实 Hermes SDK（目标方案）

**目标**：使用 Hermes 官方 Python SDK 的 `AIAgent` 替换自建 `ConversationEngine`。

**做法**：
1. 添加 Hermes SDK 依赖（pin 版本）。
2. 实现 `HermesNativeBackend`，包装 `AIAgent`。
3. 将平台 tool 注册为 Hermes tool callback。

```python
from hermes import AIAgent, Tool as HermesTool  # 假设 SDK 接口

class HermesNativeBackend:
    name = "hermes_native"

    def __init__(self, hermes_config: dict):
        self.config = hermes_config

    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        # 1. 创建 Hermes AIAgent
        agent = AIAgent(
            system_prompt=request.agent_spec.system_prompt,
            model=self.config["model"],
            provider=self.config["provider"],
        )

        # 2. 注册平台工具为 Hermes tools
        for tool_def in request.agent_spec.tool_definitions:
            hermes_tool = self._bridge_tool(tool_def)
            agent.register_tool(hermes_tool)

        # 3. 运行对话
        result = await anyio.to_thread.run_sync(
            lambda: agent.chat(request.input_query)
        )

        # 4. 映射回标准 AgentResponse
        return ResponseMapper.map(result, request)

    def _bridge_tool(self, tool_def: ToolDefinition) -> HermesTool:
        """将平台 ToolDefinition 转为 Hermes Tool"""
        async def callback(**kwargs):
            return await self.tool_executor.execute(tool_def.name, kwargs)
        return HermesTool(
            name=tool_def.name,
            description=tool_def.description,
            parameters=tool_def.parameters,
            callback=callback,
        )
```

**依赖管理**：

```toml
[project.optional-dependencies]
hermes = ["hermes-ai>=x.y.z"]
```

Pin 策略：使用 `>=x.y.z,<x.(y+1)` 范围锁定，避免大版本升级引入不兼容。

## 3. Session 映射

| 平台概念 | Hermes 概念 | 映射方式 |
|---|---|---|
| `AgentSession.id` | Hermes conversation ID | 透传 session_id |
| `AgentSession.messages` | Hermes conversation history | 初始化时注入 |
| `AgentSession.metadata` | Hermes context | 注入到 system prompt 或 context |
| Session 过期 | Hermes 无内置过期 | 平台侧控制 |

## 4. Stream 事件映射

| Hermes 事件 | 平台 SSE 事件 |
|---|---|
| conversation started | `run.started` |
| token generated | `message.delta` |
| tool call initiated | `tool.started` |
| tool call completed | `tool.completed` |
| response completed | `message.completed` |
| error | `run.failed` |

```python
async def stream_hermes_response(hermes_stream, request_id: str):
    yield sse_event("run.started", {"request_id": request_id})
    async for event in hermes_stream:
        if event.type == "token":
            yield sse_event("message.delta", {"delta": event.text})
        elif event.type == "tool_start":
            yield sse_event("tool.started", {"tool_name": event.tool_name})
        elif event.type == "tool_end":
            yield sse_event("tool.completed", {"tool_name": event.tool_name, "status": "success"})
    yield sse_event("run.completed", {"request_id": request_id})
```

## 5. Fallback 策略

```python
class HermesRuntimeBackend:
    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        try:
            return await asyncio.wait_for(
                self._run_hermes(request),
                timeout=request.timeout_seconds or 30,
            )
        except (HermesUnavailableError, asyncio.TimeoutError) as e:
            if self.fallback_backend:
                return await self.fallback_backend.run(request)
            raise RuntimeError(f"Hermes unavailable and no fallback: {e}")
```

Fallback 配置：

```yaml
# manifest.yaml
runtime:
  backend: hermes
  fallback: native    # Hermes 不可用时降级到 native
```

## 6. 最小 Spike 范围

| 项目 | Spike A | Spike B |
|---|---|---|
| 新建 `hermes_echo` agent | ✓ | ✓ |
| 至少一个平台工具做 tool call | ✓ | ✓ |
| 返回标准 `AgentResponse` | ✓ | ✓ |
| tool call 进入 `ResponseTrace.tool_calls` | ✓ | ✓ |
| 至少一条 integration test 证明非 stub | ✓ | ✓ |
| 使用真实 Hermes SDK | ✗ | ✓ |
| Stream 事件映射 | ✗ | ✓ |
| Fallback | ✗ | ✓ |

## 7. 推荐执行顺序

1. **先做 Spike A**（1-2 天）：实现 `OpenAICompatibleProvider`，注入 `model_gateway`，跑 `hermes_echo`，证明 agentic loop + tool call 端到端通路。
2. **再做 Spike B**（3-5 天）：引入 Hermes SDK，实现 `HermesNativeBackend`，验证 tool bridge、session 映射、stream 事件。
3. 两个 spike 都通过后，决定生产主链路使用哪个方案。

## 8. 验收标准

1. `hermes_echo` agent 可通过 `/api/v1/agent/chat` 调用并返回非 stub 响应。
2. 工具调用在 `trace.tool_calls` 中有记录。
3. `AgentResponse.output.status` 为 `completed`。
4. Integration test 中 assert response 不包含 `[Hermes-stub]`。
5. model_gateway 未配置时，HermesBackend 仍然正常降级到 stub（不 crash）。
