"""Runner 执行日志持久化：记录和查询 Runner 执行过程中的 stdout/stderr 输出。"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LogStream(StrEnum):
    """日志流类型。"""

    STDOUT = "stdout"
    STDERR = "stderr"


class ExecutionLogEntry(BaseModel):
    """单条执行日志记录。"""

    job_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    stream: LogStream
    content: str
    adapter_name: str = ""


@runtime_checkable
class ExecutionLogRepository(Protocol):
    """执行日志仓库协议，定义记录和查询接口。"""

    async def record(self, entry: ExecutionLogEntry) -> None:
        """记录一条日志。"""
        ...

    async def get_logs(
        self,
        job_id: str,
        stream: LogStream | None = None,
    ) -> list[ExecutionLogEntry]:
        """获取指定 job 的日志，可按 stream 类型过滤。"""
        ...

    async def list_jobs_with_logs(self, limit: int = 50) -> list[str]:
        """列出存在日志记录的 job_id 列表（按最近活跃排序）。"""
        ...


class InMemoryExecutionLogRepository:
    """基于内存的执行日志仓库实现。

    适用于开发和测试环境，进程退出后数据丢失。
    """

    def __init__(self) -> None:
        # job_id -> 按时间顺序排列的日志列表
        self._logs: dict[str, list[ExecutionLogEntry]] = defaultdict(list)
        # 记录每个 job 最后一次活跃时间，用于排序
        self._last_active: dict[str, datetime] = {}

    async def record(self, entry: ExecutionLogEntry) -> None:
        """记录一条日志到内存。"""
        self._logs[entry.job_id].append(entry)
        self._last_active[entry.job_id] = entry.timestamp

    async def get_logs(
        self,
        job_id: str,
        stream: LogStream | None = None,
    ) -> list[ExecutionLogEntry]:
        """获取指定 job 的日志，可按 stream 类型过滤。"""
        entries = self._logs.get(job_id, [])
        if stream is not None:
            entries = [e for e in entries if e.stream == stream]
        return list(entries)

    async def list_jobs_with_logs(self, limit: int = 50) -> list[str]:
        """列出存在日志记录的 job_id（按最后活跃时间降序排列）。"""
        sorted_jobs = sorted(
            self._last_active.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        return [job_id for job_id, _ in sorted_jobs[:limit]]

    async def clear(self) -> None:
        """清空所有日志（便于测试）。"""
        self._logs.clear()
        self._last_active.clear()


class FileExecutionLogRepository:
    """基于文件系统的执行日志仓库实现。

    日志按 job_id 分目录存储：
        {base_dir}/{job_id}/stdout.log
        {base_dir}/{job_id}/stderr.log
        {base_dir}/{job_id}/entries.jsonl   (完整结构化日志)

    stdout.log / stderr.log 为纯文本追加写入，方便直接查看。
    entries.jsonl 保留完整的结构化信息，用于程序化查询。
    """

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_id: str) -> Path:
        """获取 job 的日志目录。"""
        return self._base_dir / job_id

    async def record(self, entry: ExecutionLogEntry) -> None:
        """追加写入日志到文件。"""
        job_dir = self._job_dir(entry.job_id)
        job_dir.mkdir(parents=True, exist_ok=True)

        # 写入纯文本日志文件（追加模式）
        stream_file = job_dir / f"{entry.stream.value}.log"
        with stream_file.open("a", encoding="utf-8") as f:
            f.write(entry.content)
            # 确保每行以换行结尾
            if not entry.content.endswith("\n"):
                f.write("\n")

        # 写入结构化 JSONL 日志（追加模式）
        jsonl_file = job_dir / "entries.jsonl"
        with jsonl_file.open("a", encoding="utf-8") as f:
            f.write(entry.model_dump_json())
            f.write("\n")

    async def get_logs(
        self,
        job_id: str,
        stream: LogStream | None = None,
    ) -> list[ExecutionLogEntry]:
        """从 JSONL 文件读取日志条目。"""
        job_dir = self._job_dir(job_id)
        jsonl_file = job_dir / "entries.jsonl"

        if not jsonl_file.exists():
            return []

        entries: list[ExecutionLogEntry] = []
        for line in jsonl_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = ExecutionLogEntry.model_validate_json(line)
                if stream is None or entry.stream == stream:
                    entries.append(entry)
            except Exception:
                logger.warning("解析日志行失败: %s", line[:100])
        return entries

    async def list_jobs_with_logs(self, limit: int = 50) -> list[str]:
        """列出存在日志的 job_id（按目录修改时间降序排列）。"""
        if not self._base_dir.exists():
            return []

        job_dirs: list[tuple[str, float]] = []
        for child in self._base_dir.iterdir():
            if child.is_dir():
                jsonl = child / "entries.jsonl"
                if jsonl.exists():
                    job_dirs.append((child.name, jsonl.stat().st_mtime))

        # 按修改时间降序排列
        job_dirs.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in job_dirs[:limit]]

    def get_stream_text(self, job_id: str, stream: LogStream) -> str:
        """直接读取某个 stream 的纯文本日志。"""
        log_file = self._job_dir(job_id) / f"{stream.value}.log"
        if not log_file.exists():
            return ""
        return log_file.read_text(encoding="utf-8")
