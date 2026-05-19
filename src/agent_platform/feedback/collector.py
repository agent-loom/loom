"""从 agent_runs 表收集运行时反馈信号。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from agent_platform.persistence.tables import AgentRunRow


@dataclass
class FeedbackSignal:
    """单条反馈信号，承载一次运行的关键元数据。"""

    signal_type: str  # "error" | "fallback" | "run"
    agent_id: str
    tenant_id: str
    run_id: str
    tool_name: str | None
    error_message: str | None
    confidence: float | None  # 0-1，当前版本暂不填充
    session_id: str | None
    occurred_at: datetime


class FeedbackCollector:
    """从 AgentRunRow 记录中提取反馈信号。"""

    def __init__(self, session_factory) -> None:
        # session_factory 是 SQLAlchemy async_sessionmaker 或同等可调用
        self._session_factory = session_factory

    async def collect_recent(self, hours: int = 24) -> list[FeedbackSignal]:
        """采集最近 N 小时的运行记录，转换为 FeedbackSignal 列表。

        分类规则：
        - status == "error" → signal_type = "error"
        - error_json 序列化后包含 "fallback" → signal_type = "fallback"
        - 其余 → signal_type = "run"
        """
        cutoff = datetime.now(UTC) - timedelta(hours=hours)

        async with self._session_factory() as session:
            stmt = (
                select(AgentRunRow)
                .where(AgentRunRow.created_at >= cutoff)
                .order_by(AgentRunRow.created_at.desc())
            )
            result = await session.execute(stmt)
            rows: list[AgentRunRow] = list(result.scalars().all())

        signals: list[FeedbackSignal] = []
        for row in rows:
            signal_type, error_message = self._classify(row)
            signals.append(
                FeedbackSignal(
                    signal_type=signal_type,
                    agent_id=row.agent_id,
                    tenant_id=row.tenant_id or "",
                    run_id=row.run_id,
                    tool_name=None,  # 工具级别信号由 ToolAuditRow 提供，此处不采集
                    error_message=error_message,
                    confidence=None,  # 当前版本无置信度字段
                    session_id=row.session_id,
                    occurred_at=row.created_at,
                )
            )
        return signals

    @staticmethod
    def _classify(row: AgentRunRow) -> tuple[str, str | None]:
        """根据 status 和 error_json 判断信号类型，同时提取可读错误摘要。"""
        if row.status == "error":
            # 从 error_json 提取可读描述
            error_msg = _extract_error_text(row.error_json)
            return "error", error_msg

        if row.error_json:
            error_text = _extract_error_text(row.error_json)
            if error_text and "fallback" in error_text.lower():
                return "fallback", error_text

        return "run", None


def _extract_error_text(error_json: dict | None) -> str | None:
    """从 error_json 提取人类可读的错误文本，优先取 message 键。"""
    if not error_json:
        return None
    if isinstance(error_json, dict):
        # 常见键名：message、error、detail、reason
        for key in ("message", "error", "detail", "reason"):
            val = error_json.get(key)
            if val and isinstance(val, str):
                return val
        # 兜底：把整个 dict 转字符串
        return str(error_json)
    # error_json 被错误地存为字符串时
    return str(error_json)
