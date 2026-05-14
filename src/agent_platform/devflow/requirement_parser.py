from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ParsedRequirement(BaseModel):
    title: str
    goal: str
    users: list[str] = Field(default_factory=list)
    inputs_required: list[str] = Field(default_factory=list)
    inputs_optional: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    suggested_type: str = "agent:new"
    suggested_agent_id: str | None = None


class RequirementParser:
    KEYWORDS_NEW_AGENT = {"新增", "新建", "创建", "新的", "new"}
    KEYWORDS_CHANGE = {"修改", "更新", "改", "change", "update"}
    KEYWORDS_TOOL = {"工具", "tool", "接口", "API"}
    KEYWORDS_KNOWLEDGE = {"知识", "knowledge", "数据", "同步"}

    def parse(self, raw_text: str, context: dict[str, Any] | None = None) -> ParsedRequirement:
        context = context or {}
        lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]
        title = lines[0] if lines else "Untitled"
        goal = " ".join(lines[:2]) if len(lines) >= 2 else title

        task_type = self._infer_type(raw_text)
        agent_id = self._infer_agent_id(raw_text, context)

        acceptance = []
        for line in lines:
            if any(kw in line for kw in ["验收", "标准", "criteria", "accept"]):
                acceptance.append(line)

        open_questions = []
        for line in lines:
            if "?" in line or "？" in line:
                open_questions.append(line)

        return ParsedRequirement(
            title=title,
            goal=goal,
            users=context.get("users", []),
            inputs_required=context.get("inputs_required", []),
            inputs_optional=context.get("inputs_optional", []),
            outputs=context.get("outputs", []),
            acceptance=acceptance,
            open_questions=open_questions,
            suggested_type=task_type,
            suggested_agent_id=agent_id,
        )

    def _infer_type(self, text: str) -> str:
        lower = text.lower()
        if any(kw in lower for kw in self.KEYWORDS_NEW_AGENT):
            if any(kw in lower for kw in ["agent", "Agent", "助手"]):
                return "agent:new"
            if any(kw in lower for kw in self.KEYWORDS_TOOL):
                return "tool:new"
        if any(kw in lower for kw in self.KEYWORDS_CHANGE):
            return "agent:change"
        if any(kw in lower for kw in self.KEYWORDS_KNOWLEDGE):
            return "knowledge:sync"
        return "platform:change"

    @staticmethod
    def _infer_agent_id(text: str, context: dict[str, Any]) -> str | None:
        if "agent_id" in context:
            return context["agent_id"]
        import re
        match = re.search(r"agent[_\s]*id[:\s=]+(\w+)", text, re.IGNORECASE)
        if match:
            return match.group(1)
        return None
