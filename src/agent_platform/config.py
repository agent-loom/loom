"""平台全局配置，通过环境变量加载。"""

import os
from functools import lru_cache

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """平台配置项，通过对应环境变量初始化。

    环境变量 → 配置项映射:
      AGENT_PLATFORM_ENV          → env（运行环境: dev/staging/production）
      AGENT_PLATFORM_REGISTRY_ROOT → registry_root（agent 清单根目录）
      AGENT_PLATFORM_DEFAULT_AGENT_ID → default_agent_id
      AGENT_PLATFORM_API_KEY      → api_key（全局 API 认证密钥）
      DATABASE_URL                → database_url（异步数据库连接串）
      PLANE_BASE_URL / PLANE_WORKSPACE_SLUG / PLANE_API_KEY → Plane 集成
      PLANE_WEBHOOK_SECRET        → Plane Webhook 签名密钥
      PLANE_AI_DEVELOPING_STATE_ID / PLANE_TESTING_STATE_ID / ... → Plane 状态机 ID
      GITLAB_BASE_URL / GITLAB_TOKEN / GITLAB_PROJECT_ID → GitLab 集成
      GITLAB_WEBHOOK_SECRET       → GitLab Webhook 签名密钥
      CORS_ALLOWED_ORIGINS        → CORS 允许的源（逗号分隔，生产环境必须显式设置）
      DEVFLOW_RUNNER_ADAPTER      → DevFlow 编码适配器（mock/claude_code/codex）
      DEVFLOW_SANDBOX_MODE        → Codex 沙箱模式（bypass/docker）
      DEVFLOW_DOCKER_IMAGE        → Docker 模式下使用的镜像名
      DEVFLOW_AGENT_OWNERSHIP_CONFIG → Plane 项目/标签/关键词到 agent_id 的映射文件
      DEVFLOW_REPO_URL / DEVFLOW_DEFAULT_BRANCH / DEVFLOW_WORKSPACE_BASE_DIR → DevFlow 仓库
      DEVFLOW_JOB_QUEUE_BACKEND   → 任务队列后端（memory/redis）
      REDIS_URL                   → Redis 连接串
      LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST → Langfuse 可观测性
      WEAVIATE_URL / WEAVIATE_API_KEY → Weaviate 向量检索
      SERVICE_JWT_SECRET          → 服务间 JWT 签名密钥
      SERVICE_SHARED_SECRETS      → 服务间共享密钥（逗号分隔）
      MAX_REQUEST_BODY_BYTES      → 请求体大小上限（默认 10MB）
      OPENAI_API_KEY / OPENAI_API_BASE → OpenAI 提供商（ModelGateway 自动注册）
      ANTHROPIC_API_KEY           → Anthropic 提供商（ModelGateway 自动注册）
      HITL_ENABLED                → 启用 Human-in-the-loop 审批门（true/false）
    """

    env: str = "dev"
    registry_root: Path = Field(default=Path("agents"))
    default_agent_id: str | None = None
    api_key: str | None = None
    database_url: str = "sqlite+aiosqlite:///./agent_platform.db"

    plane_base_url: str | None = None
    plane_workspace_slug: str | None = None
    plane_api_key: str | None = None
    plane_webhook_secret: str | None = None
    plane_ai_developing_state_id: str | None = None
    plane_testing_state_id: str | None = None
    plane_human_review_state_id: str | None = None
    plane_staging_state_id: str | None = None
    plane_done_state_id: str | None = None
    plane_project_id: str | None = None

    gitlab_base_url: str | None = None
    gitlab_token: str | None = None
    gitlab_project_id: str | None = None
    gitlab_webhook_secret: str | None = None

    cors_allowed_origins: str = "*"

    devflow_runner_adapter: str = "mock"
    devflow_codex_profile: str | None = None
    devflow_sandbox_mode: str = "bypass"
    devflow_docker_image: str = "codex-runner"
    devflow_agent_ownership_config: str | None = None
    devflow_repo_url: str | None = None
    devflow_default_branch: str = "main"
    devflow_workspace_base_dir: str | None = None

    redis_url: str | None = None
    devflow_job_queue_backend: str = "memory"

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str | None = None

    weaviate_url: str | None = None
    weaviate_api_key: str | None = None

    service_jwt_secret: str | None = None
    service_shared_secrets: str | None = None
    max_request_body_bytes: int = 10 * 1024 * 1024  # 10MB


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例，从环境变量初始化。"""
    return Settings(
        env=os.getenv("AGENT_PLATFORM_ENV", "dev"),
        registry_root=Path(os.getenv("AGENT_PLATFORM_REGISTRY_ROOT", "agents")),
        default_agent_id=os.getenv("AGENT_PLATFORM_DEFAULT_AGENT_ID"),
        api_key=os.getenv("AGENT_PLATFORM_API_KEY"),
        database_url=os.getenv(
            "DATABASE_URL", "sqlite+aiosqlite:///./agent_platform.db"
        ),
        plane_base_url=os.getenv("PLANE_BASE_URL"),
        plane_workspace_slug=os.getenv("PLANE_WORKSPACE_SLUG"),
        plane_api_key=os.getenv("PLANE_API_KEY"),
        plane_webhook_secret=os.getenv("PLANE_WEBHOOK_SECRET"),
        plane_ai_developing_state_id=os.getenv("PLANE_AI_DEVELOPING_STATE_ID"),
        plane_testing_state_id=os.getenv("PLANE_TESTING_STATE_ID"),
        plane_human_review_state_id=os.getenv("PLANE_HUMAN_REVIEW_STATE_ID"),
        plane_staging_state_id=os.getenv("PLANE_STAGING_STATE_ID"),
        plane_done_state_id=os.getenv("PLANE_DONE_STATE_ID"),
        plane_project_id=os.getenv("PLANE_PROJECT_ID"),
        gitlab_base_url=os.getenv("GITLAB_BASE_URL"),
        gitlab_token=os.getenv("GITLAB_TOKEN"),
        gitlab_project_id=os.getenv("GITLAB_PROJECT_ID"),
        gitlab_webhook_secret=os.getenv("GITLAB_WEBHOOK_SECRET"),
        cors_allowed_origins=os.getenv("CORS_ALLOWED_ORIGINS", "*"),
        devflow_runner_adapter=os.getenv("DEVFLOW_RUNNER_ADAPTER", "mock"),
        devflow_codex_profile=os.getenv("DEVFLOW_CODEX_PROFILE"),
        devflow_sandbox_mode=os.getenv("DEVFLOW_SANDBOX_MODE", "bypass"),
        devflow_docker_image=os.getenv("DEVFLOW_DOCKER_IMAGE", "codex-runner"),
        devflow_agent_ownership_config=os.getenv("DEVFLOW_AGENT_OWNERSHIP_CONFIG"),
        devflow_repo_url=os.getenv("DEVFLOW_REPO_URL"),
        devflow_default_branch=os.getenv("DEVFLOW_DEFAULT_BRANCH", "main"),
        devflow_workspace_base_dir=os.getenv("DEVFLOW_WORKSPACE_BASE_DIR"),
        redis_url=os.getenv("REDIS_URL"),
        devflow_job_queue_backend=os.getenv("DEVFLOW_JOB_QUEUE_BACKEND", "memory"),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        langfuse_host=os.getenv("LANGFUSE_HOST"),
        weaviate_url=os.getenv("WEAVIATE_URL"),
        weaviate_api_key=os.getenv("WEAVIATE_API_KEY"),
        service_jwt_secret=os.getenv("SERVICE_JWT_SECRET"),
        service_shared_secrets=os.getenv("SERVICE_SHARED_SECRETS"),
        max_request_body_bytes=int(os.getenv("MAX_REQUEST_BODY_BYTES", str(10 * 1024 * 1024))),
    )
