from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

ToolHandler = Callable[
    [dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]
]


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
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    keywords: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._tools[definition.name] = definition

    def unregister(self, name: str) -> None:
        """Remove a tool by name. No-op if the tool is not found."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError(f"tool not found: {name}") from exc

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def list_by_owner(self, owner: str) -> list[ToolDefinition]:
        """Return tools whose *owner* field matches *owner*."""
        return [t for t in self._tools.values() if t.owner == owner]

    def list_by_agent(
        self, agent_id: str
    ) -> list[ToolDefinition]:
        """Return tools whose *owner* equals *agent_id*."""
        return self.list_by_owner(agent_id)


def create_default_tool_registry() -> ToolRegistry:
    """Return an empty tool registry.

    Agent-specific tools should be loaded dynamically via
    ``load_agent_tools`` instead of being hardcoded here.
    """
    return ToolRegistry()


def load_agent_tools(
    registry: ToolRegistry,
    package_path: Path,
    agent_id: str,
) -> None:
    """Dynamically load tools from an agent package.

    Scans ``package_path / "tools"`` for Python modules and looks
    for a ``register_tools(registry)`` or
    ``register_{name}_tools(registry)`` callable in each module.
    Falls back to a module-level ``TOOL_DEFINITIONS`` list of
    ``ToolDefinition`` instances.

    After registration, every newly registered tool gets its
    ``owner`` field set to *agent_id*.
    """
    tools_dir = package_path / "tools"
    if not tools_dir.is_dir():
        logger.debug(
            "No tools/ directory for agent %s at %s",
            agent_id,
            tools_dir,
        )
        return

    known_before = set(registry._tools.keys())

    # Try __init__.py first -- it may contain a top-level register
    # function that aggregates sub-module tools.
    init_file = tools_dir / "__init__.py"
    if init_file.exists():
        _try_load_module(
            registry, tools_dir, init_file, agent_id,
        )

    # Then scan individual .py files (skip __init__.py).
    for py_file in sorted(tools_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        _try_load_module(
            registry, tools_dir, py_file, agent_id,
        )

    # Set owner on all newly added tools.
    for name, defn in registry._tools.items():
        if name not in known_before:
            defn.owner = agent_id

    new_tools = set(registry._tools.keys()) - known_before
    if new_tools:
        logger.info(
            "Loaded %d tool(s) for agent %s: %s",
            len(new_tools),
            agent_id,
            sorted(new_tools),
        )


def _try_load_module(
    registry: ToolRegistry,
    tools_dir: Path,
    py_file: Path,
    agent_id: str,
) -> None:
    """Attempt to import *py_file* and call its register function."""
    module_stem = py_file.stem
    # Build a dotted module path relative to the project root.
    # e.g. agents/myj/tools/__init__.py -> agents.myj.tools
    #      agents/myj/tools/goods_search.py -> agents.myj.tools.goods_search
    module_name = py_file.stem
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if spec and spec.loader:
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    try:
        pass
    except Exception:
        logger.warning(
            "Failed to import tool module %s for agent %s",
            module_name,
            agent_id,
            exc_info=True,
        )
        return

    # Look for register functions.
    register_fn = getattr(module, "register_tools", None)
    if register_fn is None:
        register_fn = getattr(
            module, f"register_{module_stem}_tools", None,
        )
    # __init__.py may expose a function named after the agent.
    if register_fn is None and module_stem == "__init__":
        for attr_name in dir(module):
            if attr_name.startswith("register_") and attr_name.endswith(
                "_tools"
            ):
                register_fn = getattr(module, attr_name)
                break

    if callable(register_fn):
        try:
            register_fn(registry)
            return
        except Exception:
            logger.warning(
                "register function in %s failed for agent %s",
                module_name,
                agent_id,
                exc_info=True,
            )
            return

    # Fallback: look for TOOL_DEFINITIONS list.
    tool_defs = getattr(module, "TOOL_DEFINITIONS", None)
    if isinstance(tool_defs, list):
        for defn in tool_defs:
            if isinstance(defn, ToolDefinition):
                registry.register(defn)
        return

    logger.debug(
        "No register function or TOOL_DEFINITIONS in %s",
        module_name,
    )
