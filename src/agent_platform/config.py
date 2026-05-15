import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    env: str = "dev"
    registry_root: Path = Field(default=Path("agents"))
    default_agent_id: str = "myj"
    api_key: str | None = None

    plane_base_url: str | None = None
    plane_workspace_slug: str | None = None
    plane_api_key: str | None = None
    plane_webhook_secret: str | None = None

    gitlab_base_url: str | None = None
    gitlab_token: str | None = None
    gitlab_project_id: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings(
        env=os.getenv("AGENT_PLATFORM_ENV", "dev"),
        registry_root=Path(os.getenv("AGENT_PLATFORM_REGISTRY_ROOT", "agents")),
        default_agent_id=os.getenv("AGENT_PLATFORM_DEFAULT_AGENT_ID", "myj"),
        api_key=os.getenv("AGENT_PLATFORM_API_KEY"),
        plane_base_url=os.getenv("PLANE_BASE_URL"),
        plane_workspace_slug=os.getenv("PLANE_WORKSPACE_SLUG"),
        plane_api_key=os.getenv("PLANE_API_KEY"),
        plane_webhook_secret=os.getenv("PLANE_WEBHOOK_SECRET"),
        gitlab_base_url=os.getenv("GITLAB_BASE_URL"),
        gitlab_token=os.getenv("GITLAB_TOKEN"),
        gitlab_project_id=os.getenv("GITLAB_PROJECT_ID"),
    )
