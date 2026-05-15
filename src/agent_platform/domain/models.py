from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OutputStatus(StrEnum):
    COMPLETED = "completed"
    CLARIFICATION_REQUIRED = "clarification_required"
    HANDOFF_REQUIRED = "handoff_required"
    REJECTED = "rejected"
    FAILED = "failed"


class AgentError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class TenantContext(BaseModel):
    tenant_id: str | None = None
    retailer_id: str | None = None


class StoreContext(BaseModel):
    store_id: str | None = None
    store_name: str | None = None


class ChannelContext(BaseModel):
    channel_id: str | None = None
    channel_type: str | None = None


class DeviceContext(BaseModel):
    device_id: str | None = None
    device_type: str | None = None


class UserContext(BaseModel):
    user_id: str | None = None
    member_id: str | None = None


class RequestContext(BaseModel):
    tenant: TenantContext = Field(default_factory=TenantContext)
    store: StoreContext = Field(default_factory=StoreContext)
    channel: ChannelContext = Field(default_factory=ChannelContext)
    device: DeviceContext = Field(default_factory=DeviceContext)
    user: UserContext = Field(default_factory=UserContext)
    locale: str = "zh-CN"
    timezone: str = "Asia/Shanghai"


class InputMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class AgentInput(BaseModel):
    type: str = "text"
    query: str
    messages: list[InputMessage] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)


class RequestOptions(BaseModel):
    stream: bool = False
    debug: bool = False
    max_latency_ms: int | None = None
    runtime_profile: str = "dev"


class AgentRequest(BaseModel):
    protocol_version: str = "agent-chat/v1"
    request_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    context: RequestContext = Field(default_factory=RequestContext)
    input: AgentInput
    options: RequestOptions = Field(default_factory=RequestOptions)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResponseText(BaseModel):
    display: str
    tts: str | None = None


class ResponseCard(BaseModel):
    type: str
    id: str | None = None
    title: str
    subtitle: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ResponseCommand(BaseModel):
    name: str
    data: dict[str, Any] = Field(default_factory=dict)


class AgentOutput(BaseModel):
    status: OutputStatus = OutputStatus.COMPLETED
    text: ResponseText
    cards: list[ResponseCard] = Field(default_factory=list)
    commands: list[ResponseCommand] = Field(default_factory=list)


class AgentIdentity(BaseModel):
    agent_id: str
    agent_version: str
    deployment_id: str | None = None


class ToolCallTrace(BaseModel):
    tool_name: str
    runtime_tool_name: str | None = None
    latency_ms: int | None = None
    status: str = "success"
    error: str | None = None


class ResponseTrace(BaseModel):
    run_id: str | None = None
    route_reason: str | None = None
    traffic_bucket: int | None = None
    model: str | None = None
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None


class AgentResponse(BaseModel):
    protocol_version: str = "agent-chat/v1"
    request_id: str | None = None
    session_id: str | None = None
    agent: AgentIdentity
    output: AgentOutput
    debug: dict[str, Any] | None = None
    trace: ResponseTrace | None = None
    error: AgentError | None = None


class ManifestMetadata(BaseModel):
    id: str
    name: str
    description: str | None = None
    owner: str | None = None
    domain: str | None = None
    tags: list[str] = Field(default_factory=list)


class ManifestVersion(BaseModel):
    package_version: str
    runtime_compat: str | None = None
    release_channel: str = "dev"


class ManifestEntry(BaseModel):
    mode: str = "single_worker"
    orchestrator: str | None = None
    default_worker: str = "direct_reply"


class ManifestRuntime(BaseModel):
    backend: str = "native"
    entrypoint: str | None = None
    max_iterations: int = 4
    timeout_ms: int = 5000


class ManifestModelConfig(BaseModel):
    provider: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1024


class ManifestTools(BaseModel):
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    timeout_ms: int = 3000
    max_parallel: int = 1


class ManifestKnowledgeSource(BaseModel):
    id: str
    type: str
    backend: str
    collection: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)


class ManifestKnowledge(BaseModel):
    sources: list[ManifestKnowledgeSource] = Field(default_factory=list)


class ManifestRouting(BaseModel):
    strategy: str = "single"
    rules: str | None = None
    fallback_worker: str = "direct_reply"
    human_handoff_intents: list[str] = Field(default_factory=list)


class SessionCompression(BaseModel):
    enabled: bool = False
    threshold_tokens: int = 12000


class ManifestSession(BaseModel):
    scope: str = "session"
    history_window: int = 20
    memory_enabled: bool = False
    compression: SessionCompression = Field(default_factory=SessionCompression)


class ManifestContext(BaseModel):
    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class ManifestOutput(BaseModel):
    protocol: str = "agent-chat/v1"
    supports: list[str] = Field(default_factory=lambda: ["text"])
    command_allowlist: list[str] = Field(default_factory=list)


class ManifestSafety(BaseModel):
    policy: str | None = None
    moderation: dict[str, bool] = Field(
        default_factory=lambda: {"input": False, "output": False}
    )


class ManifestEvals(BaseModel):
    suites: list[str] = Field(default_factory=list)
    required_pass_rate: float = 0.0


class HermesExtension(BaseModel):
    enabled_toolsets: list[str] = Field(default_factory=list)
    disabled_toolsets: list[str] = Field(default_factory=list)
    max_iterations: int = 8
    memory_provider: str = "session"


class AgentManifest(BaseModel):
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
    manifest: AgentManifest
    package_path: Path

    @property
    def agent_id(self) -> str:
        return self.manifest.metadata.id

    @property
    def version(self) -> str:
        return self.manifest.version.package_version


class RuntimeRequest(BaseModel):
    request: AgentRequest
    agent_spec: AgentSpec
    route_reason: str | None = None
    deployment_id: str | None = None
    traffic_bucket: int | None = None

    model_config = {"arbitrary_types_allowed": True}


class RuntimeResponse(BaseModel):
    response: AgentResponse


class AgentRunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AgentRun(BaseModel):
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
    error: AgentError | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentDefinitionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class AgentDeploymentStatus(StrEnum):
    DRAFT = "draft"
    REGISTERED = "registered"
    EVAL_PASSED = "eval_passed"
    STAGING = "staging"
    PROD_CANARY = "prod_canary"
    PROD = "prod"
    ROLLED_BACK = "rolled_back"
    DEPRECATED = "deprecated"


class AgentDefinition(BaseModel):
    agent_id: str
    version: str
    status: AgentDefinitionStatus = AgentDefinitionStatus.ACTIVE
    manifest: AgentManifest
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class AgentDeployment(BaseModel):
    deployment_id: str
    agent_id: str
    version: str
    channel: Literal["dev", "staging", "prod"] = "dev"
    status: AgentDeploymentStatus = AgentDeploymentStatus.REGISTERED
    tenant_id: str | None = None
    traffic_percent: int = Field(default=100, ge=0, le=100)


class SessionMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    timestamp: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentSession(BaseModel):
    session_id: str
    agent_id: str
    tenant_id: str | None = None
    store_id: str | None = None
    user_id: str | None = None
    channel_id: str | None = None
    history: list[SessionMessage] = Field(default_factory=list)
    state_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    def add_message(self, role: str, content: str, **meta: Any) -> None:
        self.history.append(SessionMessage(role=role, content=content, metadata=meta))
        self.updated_at = _utc_now()

    def recent_messages(self, window: int) -> list[SessionMessage]:
        return self.history[-window:] if window > 0 else []
