"""Agent 平台核心领域模型，定义请求/响应协议、Agent 清单及会话结构。"""

from __future__ import annotations

import warnings
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OutputStatus(StrEnum):
    """Agent 输出状态枚举。"""

    COMPLETED = "completed"
    CLARIFICATION_REQUIRED = "clarification_required"
    HANDOFF_REQUIRED = "handoff_required"
    REJECTED = "rejected"
    FAILED = "failed"


class AgentError(BaseModel):
    """Agent 错误信息。"""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class TenantContext(BaseModel):
    """租户上下文，包含租户和组织标识。"""

    model_config = {"populate_by_name": True}

    tenant_id: str | None = None
    org_id: str | None = Field(default=None, alias="retailer_id")

    @model_validator(mode="before")
    @classmethod
    def _warn_deprecated_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and "retailer_id" in data:
            warnings.warn(
                "Field 'retailer_id' is deprecated, use 'org_id' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return data

    @property
    def retailer_id(self) -> str | None:
        return self.org_id

    @retailer_id.setter
    def retailer_id(self, value: str | None) -> None:
        self.org_id = value


class LocationContext(BaseModel):
    """门店/位置上下文信息。"""

    model_config = {"populate_by_name": True}

    location_id: str | None = Field(default=None, alias="store_id")
    location_name: str | None = Field(default=None, alias="store_name")

    @model_validator(mode="before")
    @classmethod
    def _warn_deprecated_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "store_id" in data:
                warnings.warn(
                    "Field 'store_id' is deprecated, use 'location_id' instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            if "store_name" in data:
                warnings.warn(
                    "Field 'store_name' is deprecated, use 'location_name' instead.",
                    DeprecationWarning,
                    stacklevel=2,
                )
        return data

    @property
    def store_id(self) -> str | None:
        return self.location_id

    @store_id.setter
    def store_id(self, value: str | None) -> None:
        self.location_id = value

    @property
    def store_name(self) -> str | None:
        return self.location_name

    @store_name.setter
    def store_name(self, value: str | None) -> None:
        self.location_name = value


# Backward-compatible alias so existing code referencing StoreContext still works.
StoreContext = LocationContext


class ChannelContext(BaseModel):
    """渠道上下文信息。"""

    channel_id: str | None = None
    channel_type: str | None = None


class DeviceContext(BaseModel):
    """设备上下文信息。"""

    device_id: str | None = None
    device_type: str | None = None


class UserContext(BaseModel):
    """用户上下文信息。"""

    user_id: str | None = None
    member_id: str | None = None


class RequestContext(BaseModel):
    """请求上下文，聚合租户、门店、渠道、设备和用户信息。"""

    model_config = {"populate_by_name": True}

    tenant: TenantContext = Field(default_factory=TenantContext)
    location: LocationContext = Field(default_factory=LocationContext, alias="store")
    channel: ChannelContext = Field(default_factory=ChannelContext)
    device: DeviceContext = Field(default_factory=DeviceContext)
    user: UserContext = Field(default_factory=UserContext)
    locale: str = "en"
    timezone: str = "UTC"

    @model_validator(mode="before")
    @classmethod
    def _warn_deprecated_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and "store" in data:
            warnings.warn(
                "Field 'store' is deprecated, use 'location' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return data

    @property
    def store(self) -> LocationContext:
        return self.location

    @store.setter
    def store(self, value: LocationContext) -> None:
        self.location = value


class InputMessage(BaseModel):
    """对话历史中的单条消息。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class AgentInput(BaseModel):
    """Agent 请求的输入体，包含查询文本、消息历史及附件。"""

    type: str = "text"
    query: str
    messages: list[InputMessage] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class RequestOptions(BaseModel):
    """请求级选项，如流式开关和调试模式。"""

    stream: bool = False
    debug: bool = False
    max_latency_ms: int | None = None
    runtime_profile: str = "dev"


class AgentRequest(BaseModel):
    """Agent 请求协议模型，承载完整的入站请求。"""

    protocol_version: str = "agent-chat/v1"
    request_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    context: RequestContext = Field(default_factory=RequestContext)
    input: AgentInput
    options: RequestOptions = Field(default_factory=RequestOptions)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResponseText(BaseModel):
    """响应文本，包含显示文本和可选的 TTS 文本。"""

    display: str
    tts: str | None = None


class ResponseCard(BaseModel):
    """响应卡片，用于富文本展示。"""

    type: str
    id: str | None = None
    title: str
    subtitle: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ResponseCommand(BaseModel):
    """响应指令，用于触发客户端动作。"""

    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    """Agent 输出体，包含文本、卡片和指令。"""

    status: OutputStatus = OutputStatus.COMPLETED
    text: ResponseText
    cards: list[ResponseCard] = Field(default_factory=list)
    commands: list[ResponseCommand] = Field(default_factory=list)


class AgentIdentity(BaseModel):
    """Agent 身份标识。"""

    agent_id: str
    agent_version: str
    deployment_id: str | None = None


class ToolCallTrace(BaseModel):
    """单次工具调用的追踪记录。"""

    tool_name: str
    runtime_tool_name: str | None = None
    latency_ms: int | None = None
    status: str = "success"
    error: str | None = None


class TraceEventType(StrEnum):
    """结构化 trace 事件类型，用于在运行管线各阶段记录可观测事件。"""

    ROUTE_DECISION = "route_decision"  # 路由决策（选择后端）
    CONTEXT_BUILD = "context_build"  # 上下文构建（会话 + 知识注入）
    POLICY_CHECK = "policy_check"  # 策略检查（输入/输出合规）
    MODEL_CALL = "model_call"  # 模型调用（LLM 推理）
    TOOL_CALL = "tool_call"  # 工具调用
    RESPONSE_BUILD = "response_build"  # 响应组装
    ERROR = "error"  # 运行时错误
    CUSTOM = "custom"  # 自定义扩展事件


class TraceEvent(BaseModel):
    """运行管线中的结构化追踪事件，记录各阶段的耗时和上下文数据。"""

    type: TraceEventType
    timestamp: datetime = Field(default_factory=_utc_now)
    duration_ms: int | None = None  # 该阶段累计耗时（毫秒），None 表示尚未结束
    data: dict[str, Any] = Field(default_factory=dict)  # 阶段相关的附加数据


class ResponseTrace(BaseModel):
    """响应级追踪信息，用于可观测性。"""

    run_id: str | None = None
    route_reason: str | None = None
    traffic_bucket: int | None = None
    model: str | None = None
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float | None = None


class AgentResponse(BaseModel):
    """Agent 响应协议模型，承载完整的出站响应。"""

    protocol_version: str = "agent-chat/v1"
    request_id: str | None = None
    session_id: str | None = None
    agent: AgentIdentity
    output: AgentOutput
    debug: dict[str, Any] | None = None
    trace: ResponseTrace | None = None
    error: AgentError | None = None


class ManifestMetadata(BaseModel):
    """Agent 清单元数据。"""

    id: str
    name: str
    description: str | None = None
    owner: str | None = None
    domain: str | None = None
    tags: list[str] = Field(default_factory=list)


class ManifestVersion(BaseModel):
    """Agent 版本与发布渠道信息。"""

    package_version: str
    runtime_compat: str | None = None
    release_channel: str = "dev"


class ManifestEntry(BaseModel):
    """Agent 入口配置，指定运行模式和默认 Worker。"""

    mode: str = "single_worker"
    orchestrator: str | None = None
    default_worker: str = "direct_reply"


class ManifestRuntime(BaseModel):
    """运行时配置，包括后端类型和超时设置。"""

    backend: str = "native"
    entrypoint: str | None = None
    max_iterations: int = 4
    timeout_ms: int = 5000


class ManifestModelConfig(BaseModel):
    """模型提供者及参数配置。"""

    provider: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024


class ManifestTools(BaseModel):
    """工具白名单/黑名单及并发配置。"""

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    timeout_ms: int = 3000
    max_parallel: int = 1


class ManifestKnowledgeSource(BaseModel):
    """知识库数据源配置。"""

    id: str
    type: str
    backend: str
    collection: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class ManifestKnowledge(BaseModel):
    """知识库配置。"""

    sources: list[ManifestKnowledgeSource] = Field(default_factory=list)


class ManifestRoutingRule(BaseModel):
    """单条语义路由规则，声明关键词和正则模式。"""

    keywords: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    description: str = ""


class ManifestRouting(BaseModel):
    """路由策略配置。"""

    strategy: str = "single"
    rules: str | None = None
    routing_rules: list[ManifestRoutingRule] = Field(default_factory=list)
    fallback_worker: str = "direct_reply"
    human_handoff_intents: list[str] = Field(default_factory=list)


class SessionCompression(BaseModel):
    """会话历史压缩配置。"""

    enabled: bool = False
    threshold_tokens: int = 12000


class ManifestSession(BaseModel):
    """会话管理配置。"""

    scope: str = "session"
    history_window: int = 20
    memory_enabled: bool = False
    compression: SessionCompression = Field(default_factory=SessionCompression)


class ManifestContext(BaseModel):
    """请求上下文字段的必选/可选声明。"""

    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class ManifestOutput(BaseModel):
    """输出协议及支持的响应类型配置。"""

    protocol: str = "agent-chat/v1"
    supports: list[str] = Field(default_factory=lambda: ["text"])
    command_allowlist: list[str] = Field(default_factory=list)


class ManifestSafety(BaseModel):
    """安全策略与内容审核配置。"""

    policy: str | None = None
    moderation: dict[str, bool] = Field(
        default_factory=lambda: {"input": False, "output": False}
    )


class ManifestEvals(BaseModel):
    """评测套件配置。"""

    suites: list[str] = Field(default_factory=list)
    required_pass_rate: float = 0.0


class HermesExtension(BaseModel):
    """Hermes 扩展配置。"""

    enabled_toolsets: list[str] = Field(default_factory=list)
    disabled_toolsets: list[str] = Field(default_factory=list)
    max_iterations: int = 8
    memory_provider: str = "session"


class AgentManifest(BaseModel):
    """Agent 包清单，描述 Agent 的完整配置。"""

    api_version: Literal["agent.platform/v1"]
    kind: Literal["AgentPackage"]
    metadata: ManifestMetadata
    version: ManifestVersion
    entry: ManifestEntry = Field(default_factory=ManifestEntry)
    runtime: ManifestRuntime = Field(default_factory=ManifestRuntime)
    models: dict[str, ManifestModelConfig] = Field(default_factory=dict)
    prompts: dict[str, str] = Field(default_factory=dict)
    tools: ManifestTools = Field(default_factory=ManifestTools)
    knowledge: ManifestKnowledge = Field(default_factory=ManifestKnowledge)
    routing: ManifestRouting = Field(default_factory=ManifestRouting)
    session: ManifestSession = Field(default_factory=ManifestSession)
    context: ManifestContext = Field(default_factory=ManifestContext)
    output: ManifestOutput
    safety: ManifestSafety = Field(default_factory=ManifestSafety)
    evals: ManifestEvals = Field(default_factory=ManifestEvals)
    extensions: dict[str, Any] = Field(default_factory=dict)


class AgentSpec(BaseModel):
    """Agent 规格，关联清单与包路径。"""

    manifest: AgentManifest
    package_path: Path

    @property
    def agent_id(self) -> str:
        return self.manifest.metadata.id

    @property
    def version(self) -> str:
        return self.manifest.version.package_version


class RuntimeRequest(BaseModel):
    """运行时请求，在 AgentRequest 基础上附加路由和部署信息。"""

    request: AgentRequest
    agent_spec: AgentSpec
    route_reason: str | None = None
    deployment_id: str | None = None
    traffic_bucket: int | None = None
    knowledge_context: list[str] = Field(default_factory=list)
    runtime_context: Any | None = None

    model_config = {"arbitrary_types_allowed": True}


class RuntimeResponse(BaseModel):
    """运行时响应包装。"""

    response: AgentResponse


class AgentRunStatus(StrEnum):
    """Agent 运行结果状态。"""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentRun(BaseModel):
    """单次 Agent 运行记录。"""

    run_id: str
    request_id: str | None = None
    session_id: str | None = None
    agent_id: str
    agent_version: str
    route_reason: str | None = None
    runtime_backend: str
    status: AgentRunStatus
    latency_ms: int
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    trace_events: list[TraceEvent] = Field(default_factory=list)
    error: AgentError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentDefinitionStatus(StrEnum):
    """Agent 定义的生命周期状态。"""

    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class AgentDeploymentStatus(StrEnum):
    """Agent 部署的生命周期状态。"""

    DRAFT = "draft"
    REGISTERED = "registered"
    EVAL_PASSED = "eval_passed"
    STAGING = "staging"
    PROD_CANARY = "prod_canary"
    PROD = "prod"
    ROLLED_BACK = "rolled_back"
    DEPRECATED = "deprecated"


class AgentDefinition(BaseModel):
    """Agent 定义，包含版本化的清单和状态。"""

    agent_id: str
    version: str
    status: AgentDefinitionStatus = AgentDefinitionStatus.ACTIVE
    manifest: AgentManifest
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class AgentDeployment(BaseModel):
    """Agent 部署记录，包含渠道和流量百分比。"""

    deployment_id: str
    agent_id: str
    version: str
    channel: Literal["dev", "staging", "prod"] = "dev"
    status: AgentDeploymentStatus = AgentDeploymentStatus.REGISTERED
    tenant_id: str | None = None
    traffic_percent: int = Field(default=100, ge=0, le=100)


class SessionMessage(BaseModel):
    """会话中的单条消息记录。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentSession(BaseModel):
    """Agent 会话，管理对话历史与状态快照。"""

    model_config = {"populate_by_name": True}

    session_id: str
    agent_id: str
    tenant_id: str | None = None
    location_id: str | None = Field(default=None, alias="store_id")
    user_id: str | None = None
    channel_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _warn_deprecated_fields(cls, data: Any) -> Any:
        if isinstance(data, dict) and "store_id" in data:
            warnings.warn(
                "Field 'store_id' is deprecated, use 'location_id' instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        return data

    @property
    def store_id(self) -> str | None:
        return self.location_id

    @store_id.setter
    def store_id(self, value: str | None) -> None:
        self.location_id = value
    history: list[SessionMessage] = Field(default_factory=list)
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def add_message(self, role: str, content: str, **meta: Any) -> None:
        """向会话历史追加一条消息。"""
        self.history.append(SessionMessage(role=role, content=content, metadata=meta))
        self.updated_at = _utc_now()

    def recent_messages(self, window: int) -> list[SessionMessage]:
        """返回最近 window 条消息。"""
        return self.history[-window:] if window > 0 else []
