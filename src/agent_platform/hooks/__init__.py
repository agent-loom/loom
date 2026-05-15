from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

HookHandler = Callable[..., Awaitable[Any] | Any]


class HookEvent:
    PRE_RUN = "pre_run"
    POST_RUN = "post_run"
    PRE_TOOL = "pre_tool"
    POST_TOOL = "post_tool"
    ON_ERROR = "on_error"
    PRE_MODEL = "pre_model"
    POST_MODEL = "post_model"
    ON_ROUTE = "on_route"

    ALL = [PRE_RUN, POST_RUN, PRE_TOOL, POST_TOOL, ON_ERROR, PRE_MODEL, POST_MODEL, ON_ROUTE]


class HookContext:
    def __init__(self, event: str, data: dict[str, Any] | None = None):
        self.event = event
        self.data = data or {}
        self.cancelled = False
        self.modified_data: dict[str, Any] = {}

    def cancel(self, reason: str = "") -> None:
        self.cancelled = True
        self.modified_data["cancel_reason"] = reason

    def modify(self, key: str, value: Any) -> None:
        self.modified_data[key] = value


class HookRegistry:
    """Registry for lifecycle hooks. Plugins register handlers for events like
    pre_run, post_run, pre_tool, post_tool, on_error, etc.

    Handlers are called in registration order. A handler can cancel execution
    by calling ctx.cancel() or modify data by calling ctx.modify().
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookHandler]] = defaultdict(list)

    def register(self, event: str, handler: HookHandler) -> None:
        if event not in HookEvent.ALL:
            raise ValueError(f"unknown hook event: {event}")
        self._hooks[event].append(handler)

    def unregister(self, event: str, handler: HookHandler) -> None:
        if event in self._hooks:
            self._hooks[event] = [h for h in self._hooks[event] if h is not handler]

    async def emit(self, event: str, data: dict[str, Any] | None = None) -> HookContext:
        ctx = HookContext(event, data)
        for handler in self._hooks.get(event, []):
            try:
                import asyncio
                result = handler(ctx)
                if asyncio.iscoroutine(result):
                    await result
                if ctx.cancelled:
                    logger.info(
                        "hook cancelled event %s: %s",
                        event,
                        ctx.modified_data.get("cancel_reason"),
                    )
                    break
            except Exception:
                logger.exception("hook handler failed for event %s", event)
        return ctx

    def list_hooks(self) -> dict[str, int]:
        return {event: len(handlers) for event, handlers in self._hooks.items() if handlers}

    def clear(self, event: str | None = None) -> None:
        if event:
            self._hooks.pop(event, None)
        else:
            self._hooks.clear()
