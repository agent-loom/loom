"""工具注册中心：定义、注册、发现与动态加载 Agent 工具。

设计定位：
  能力层 (Capability Layer) 的核心组件，负责平台级工具与 Agent Package 内置业务工具的动态加载、解耦与生命周期热拔插。
  对应架构图中的 Capability.Tools (Tool Registry) 组件。
  具体设计详见：docs/02-architecture/agent-platform-core-design.md §3.6 ToolRegistry 与 ToolExecutor。
"""

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
    """工具定义，包含名称、Schema、超时、权限及处理函数。"""
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
    """工具注册中心 (Tool Registry)

    负责在内存中管理并索引所有的工具定义。
    提供 register、unregister、get 以及根据所有者过滤的 list_by_owner 能力。
    一致性规范：docs/02-architecture/agent-platform-core-design.md §3.6 目标状态。
    """

    def __init__(self) -> None:
        """初始化空的工具注册中心。"""
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        """注册一个工具定义。"""
        self._tools[definition.name] = definition

    def unregister(self, name: str) -> None:
        """Remove a tool by name. No-op if the tool is not found."""
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDefinition:
        """根据名称获取工具定义，未找到时抛出 LookupError。"""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise LookupError(f"tool not found: {name}") from exc

    def list_tools(self) -> list[ToolDefinition]:
        """列出所有已注册的工具。"""
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
    """创建一个默认的空工具注册中心。

    一致性规范 (core-design.md §3.6)：
      "目标状态：初始为空，在发现和加载 Agent 实例阶段，根据 handler_ref 动态注册和加载工具。"
      平台不应该硬编码包含任何具体业务工具，而是由 load_agent_tools() 动态按需填充。
    """
    return ToolRegistry()


def load_agent_tools(
    registry: ToolRegistry,
    package_path: Path,
    agent_id: str,
) -> None:
    """从指定的 Agent Package 中动态加载其专属的业务工具集。

    实现细节与注册约定 (core-design.md §3.1)：
      1. 动态 import 机制：扫描 package_path/tools/ 下所有的 Python 模块文件并动态 import。
      2. 约定 1 (函数注册模式)：若模块提供 `register_tools` 或 `register_{name}_tools`  callable 实体，
         则优先将 registry 实例传入以完成该模块工具的内嵌初始化。
      3. 约定 2 (静态声明模式)：若无前述注册函数，则降级查找模块内声明的 `TOOL_DEFINITIONS` 静态列表，
         并遍历调用 `registry.register()` 注册。
      4. 所有加载后的工具，其 `owner` 字段都会被自动覆写为该 `agent_id`，以实现多租户下的工具权限隔离。
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
    try:
        parts = list(py_file.relative_to(Path.cwd()).parts)
        module_name = ".".join(
            p[:-3] if p.endswith(".py") else p for p in parts
        )
    except ValueError:
        module_name = f"_agent_tools_{agent_id}_{module_stem}"
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if not spec or not spec.loader:
        logger.debug("Cannot create import spec for %s", py_file)
        return

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        logger.warning(
            "Failed to import tool module %s for agent %s",
            module_name,
            agent_id,
            exc_info=True,
        )
        del sys.modules[module_name]
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
