"""对话引擎，管理 LLM 调用与工具执行的迭代循环。"""

from __future__ import annotations

import logging

from agent_platform.domain.models import (
    AgentSpec,
    RuntimeRequest,
    ToolCallTrace,
)
from agent_platform.runtime.context_builder import RuntimeContext
from agent_platform.runtime.model_gateway import (
    ModelGateway,
    ModelMessage,
)
from agent_platform.tools.executor import ToolExecutor

logger = logging.getLogger(__name__)


class ConversationResult:
    """对话执行结果，包含展示文本、工具调用追踪和迭代次数。"""

    def __init__(
        self,
        display: str,
        tool_traces: list[ToolCallTrace] | None = None,
        model_used: str | None = None,
        total_iterations: int = 0,
    ):
        """初始化对话结果。"""
        self.display = display
        self.tool_traces = tool_traces or []
        self.model_used = model_used
        self.total_iterations = total_iterations


class ConversationEngine:
    """Manages the LLM call + tool loop with budget control."""

    def __init__(
        self,
        model_gateway: ModelGateway,
        tool_executor: ToolExecutor,
    ):
        """初始化对话引擎，注入模型网关和工具执行器。"""
        self.model_gateway = model_gateway
        self.tool_executor = tool_executor

    async def run(
        self,
        context: RuntimeContext,
        spec: AgentSpec,
        request: RuntimeRequest,
    ) -> ConversationResult:
        """执行对话循环：LLM 推理 -> 工具调用 -> 结果汇总。"""
        max_iterations = spec.manifest.runtime.max_iterations
        model_config = spec.manifest.models.get("default")
        if not model_config:
            return ConversationResult(
                display=f"Agent {spec.agent_id} has no model configured.",
            )

        messages = [ModelMessage(role="system", content=context.system_prompt)]
        if context.knowledge_snippets:
            knowledge_block = "\n\n".join(context.knowledge_snippets)
            messages.append(ModelMessage(
                role="system",
                content=f"Reference knowledge:\n{knowledge_block}",
            ))
        for msg in context.messages:
            messages.append(ModelMessage(role=msg["role"], content=msg["content"]))

        tool_defs = context.tools if context.tools else None
        all_traces: list[ToolCallTrace] = []
        allowed_tools = spec.manifest.tools.allow

        for iteration in range(max_iterations):
            response = await self.model_gateway.chat(
                model_config.provider,
                messages,
                model=model_config.model,
                temperature=model_config.temperature,
                max_tokens=model_config.max_tokens,
                tools=tool_defs,
            )

            if not response.tool_calls:
                return ConversationResult(
                    display=response.content,
                    tool_traces=all_traces,
                    model_used=response.model,
                    total_iterations=iteration + 1,
                )

            for tc in response.tool_calls:
                result = await self.tool_executor.execute(
                    tc.name,
                    tc.arguments,
                    allowed_tools=allowed_tools,
                    timeout_ms=spec.manifest.tools.timeout_ms,
                )
                all_traces.append(result.trace)
                messages.append(ModelMessage(
                    role="assistant",
                    content=f"[tool_call: {tc.name}]",
                ))
                messages.append(ModelMessage(
                    role="tool",
                    content=str(result.output),
                ))

        logger.warning(
            "agent %s hit max iterations (%d)",
            spec.agent_id,
            max_iterations,
        )
        fallback_response = await self.model_gateway.chat(
            model_config.provider,
            messages,
            model=model_config.model,
            temperature=model_config.temperature,
            max_tokens=model_config.max_tokens,
        )
        return ConversationResult(
            display=(
                fallback_response.content
                or "I was unable to complete the request within the allowed iterations."
            ),
            tool_traces=all_traces,
            model_used=fallback_response.model,
            total_iterations=max_iterations,
        )
