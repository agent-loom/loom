from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = 3000
    permissions: list[str] = Field(default_factory=list)
    handler: ToolHandler
    handler_ref: str | None = None
    owner: str | None = None
    max_retries: int = 0
    keywords: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError(f"tool not found: {name}") from exc

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())


def create_default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    try:
        from agents.myj.tools import register_myj_tools
        register_myj_tools(registry)
    except ImportError:
        pass
    try:
        from agents.promo_recommendation.tools import register_promo_tools
        register_promo_tools(registry)
    except ImportError:
        pass
    return registry

