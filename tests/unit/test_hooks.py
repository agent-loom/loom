"""Tests for HookRegistry – register, emit, cancel, unregister, async handlers."""

from __future__ import annotations

import pytest

from agent_platform.hooks import HookContext, HookEvent, HookRegistry

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegister:
    def test_register_valid_event(self) -> None:
        reg = HookRegistry()

        def handler(ctx: HookContext) -> None:
            pass

        reg.register(HookEvent.PRE_RUN, handler)
        assert reg.list_hooks() == {HookEvent.PRE_RUN: 1}

    def test_register_unknown_event_raises(self) -> None:
        reg = HookRegistry()
        with pytest.raises(ValueError, match="unknown hook event"):
            reg.register("not_a_real_event", lambda ctx: None)

    def test_register_multiple_handlers_same_event(self) -> None:
        reg = HookRegistry()
        reg.register(HookEvent.PRE_RUN, lambda ctx: None)
        reg.register(HookEvent.PRE_RUN, lambda ctx: None)
        assert reg.list_hooks()[HookEvent.PRE_RUN] == 2


# ---------------------------------------------------------------------------
# Emit – sync handlers
# ---------------------------------------------------------------------------

class TestEmitSync:
    @pytest.mark.asyncio
    async def test_emit_calls_handler(self) -> None:
        reg = HookRegistry()
        calls: list[str] = []

        def handler(ctx: HookContext) -> None:
            calls.append(ctx.event)

        reg.register(HookEvent.POST_RUN, handler)
        ctx = await reg.emit(HookEvent.POST_RUN, {"key": "value"})

        assert calls == [HookEvent.POST_RUN]
        assert ctx.data == {"key": "value"}
        assert ctx.cancelled is False

    @pytest.mark.asyncio
    async def test_emit_multiple_handlers_called_in_order(self) -> None:
        reg = HookRegistry()
        order: list[int] = []

        reg.register(HookEvent.PRE_TOOL, lambda ctx: order.append(1))
        reg.register(HookEvent.PRE_TOOL, lambda ctx: order.append(2))
        reg.register(HookEvent.PRE_TOOL, lambda ctx: order.append(3))

        await reg.emit(HookEvent.PRE_TOOL)
        assert order == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_emit_no_handlers(self) -> None:
        reg = HookRegistry()
        ctx = await reg.emit(HookEvent.ON_ERROR)
        assert ctx.cancelled is False


# ---------------------------------------------------------------------------
# Emit – async handlers
# ---------------------------------------------------------------------------

class TestEmitAsync:
    @pytest.mark.asyncio
    async def test_async_handler(self) -> None:
        reg = HookRegistry()
        calls: list[str] = []

        async def async_handler(ctx: HookContext) -> None:
            calls.append("async")

        reg.register(HookEvent.POST_TOOL, async_handler)
        await reg.emit(HookEvent.POST_TOOL)
        assert calls == ["async"]

    @pytest.mark.asyncio
    async def test_mixed_sync_and_async_handlers(self) -> None:
        reg = HookRegistry()
        order: list[str] = []

        def sync_h(ctx: HookContext) -> None:
            order.append("sync")

        async def async_h(ctx: HookContext) -> None:
            order.append("async")

        reg.register(HookEvent.PRE_RUN, sync_h)
        reg.register(HookEvent.PRE_RUN, async_h)
        await reg.emit(HookEvent.PRE_RUN)

        assert order == ["sync", "async"]


# ---------------------------------------------------------------------------
# Cancel semantics
# ---------------------------------------------------------------------------

class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_stops_remaining_handlers(self) -> None:
        reg = HookRegistry()
        order: list[int] = []

        def h1(ctx: HookContext) -> None:
            order.append(1)
            ctx.cancel("aborted")

        def h2(ctx: HookContext) -> None:
            order.append(2)

        reg.register(HookEvent.PRE_TOOL, h1)
        reg.register(HookEvent.PRE_TOOL, h2)

        ctx = await reg.emit(HookEvent.PRE_TOOL)
        assert ctx.cancelled is True
        assert ctx.modified_data["cancel_reason"] == "aborted"
        assert order == [1]  # h2 should NOT have been called

    @pytest.mark.asyncio
    async def test_cancel_with_empty_reason(self) -> None:
        reg = HookRegistry()

        def h(ctx: HookContext) -> None:
            ctx.cancel()

        reg.register(HookEvent.PRE_RUN, h)
        ctx = await reg.emit(HookEvent.PRE_RUN)
        assert ctx.cancelled is True
        assert ctx.modified_data["cancel_reason"] == ""


# ---------------------------------------------------------------------------
# Modify
# ---------------------------------------------------------------------------

class TestModify:
    @pytest.mark.asyncio
    async def test_modify_adds_to_modified_data(self) -> None:
        reg = HookRegistry()

        def h(ctx: HookContext) -> None:
            ctx.modify("prompt", "new prompt")

        reg.register(HookEvent.PRE_MODEL, h)
        ctx = await reg.emit(HookEvent.PRE_MODEL)
        assert ctx.modified_data["prompt"] == "new prompt"


# ---------------------------------------------------------------------------
# Unregister
# ---------------------------------------------------------------------------

class TestUnregister:
    @pytest.mark.asyncio
    async def test_unregister_removes_handler(self) -> None:
        reg = HookRegistry()
        calls: list[str] = []

        def handler(ctx: HookContext) -> None:
            calls.append("called")

        reg.register(HookEvent.POST_RUN, handler)
        reg.unregister(HookEvent.POST_RUN, handler)

        await reg.emit(HookEvent.POST_RUN)
        assert calls == []

    def test_unregister_nonexistent_handler(self) -> None:
        reg = HookRegistry()
        # Should not raise
        reg.unregister(HookEvent.PRE_RUN, lambda ctx: None)

    def test_unregister_nonexistent_event(self) -> None:
        reg = HookRegistry()
        # Should not raise
        reg.unregister("not_registered", lambda ctx: None)


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_specific_event(self) -> None:
        reg = HookRegistry()
        reg.register(HookEvent.PRE_RUN, lambda ctx: None)
        reg.register(HookEvent.POST_RUN, lambda ctx: None)

        reg.clear(HookEvent.PRE_RUN)
        hooks = reg.list_hooks()
        assert HookEvent.PRE_RUN not in hooks
        assert hooks[HookEvent.POST_RUN] == 1

    def test_clear_all(self) -> None:
        reg = HookRegistry()
        reg.register(HookEvent.PRE_RUN, lambda ctx: None)
        reg.register(HookEvent.POST_RUN, lambda ctx: None)

        reg.clear()
        assert reg.list_hooks() == {}


# ---------------------------------------------------------------------------
# Error handling in handlers
# ---------------------------------------------------------------------------

class TestHandlerErrors:
    @pytest.mark.asyncio
    async def test_failing_handler_does_not_stop_others(self) -> None:
        reg = HookRegistry()
        calls: list[int] = []

        def bad_handler(ctx: HookContext) -> None:
            raise RuntimeError("oops")

        def good_handler(ctx: HookContext) -> None:
            calls.append(1)

        reg.register(HookEvent.ON_ERROR, bad_handler)
        reg.register(HookEvent.ON_ERROR, good_handler)

        ctx = await reg.emit(HookEvent.ON_ERROR)
        assert calls == [1]
        assert ctx.cancelled is False


# ---------------------------------------------------------------------------
# All events covered
# ---------------------------------------------------------------------------

class TestAllEvents:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("event", HookEvent.ALL)
    async def test_can_register_and_emit_all_events(self, event: str) -> None:
        reg = HookRegistry()
        called = False

        def h(ctx: HookContext) -> None:
            nonlocal called
            called = True

        reg.register(event, h)
        await reg.emit(event)
        assert called
