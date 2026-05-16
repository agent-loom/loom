"""平台全局配置，通过环境变量加载。"""

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    """平台配置项，包含注册中心、数据库及第三方集成设置。"""

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

    gitlab_base_url: str | None = None
    gitlab_token: str | None = None
    gitlab_project_id: str | None = None
    gitlab_webhook_secret: str | None = None

    cors_allowed_origins: str = "*"

    devflow_runner_adapter: str = "mock"
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
        gitlab_base_url=os.getenv("GITLAB_BASE_URL"),
        gitlab_token=os.getenv("GITLAB_TOKEN"),
        gitlab_project_id=os.getenv("GITLAB_PROJECT_ID"),
        gitlab_webhook_secret=os.getenv("GITLAB_WEBHOOK_SECRET"),
        cors_allowed_origins=os.getenv("CORS_ALLOWED_ORIGINS", "*"),
        devflow_runner_adapter=os.getenv("DEVFLOW_RUNNER_ADAPTER", "mock"),
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
    )
