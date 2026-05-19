"""执行日志持久化单元测试：InMemoryExecutionLogRepository 和 FileExecutionLogRepository。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_platform.devflow.runner.execution_log import (
    ExecutionLogEntry,
    ExecutionLogRepository,
    FileExecutionLogRepository,
    InMemoryExecutionLogRepository,
    LogStream,
)

# ---------------------------------------------------------------------------
# 公用 fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def mem_repo() -> InMemoryExecutionLogRepository:
    """创建一个空的内存日志仓库。"""
    return InMemoryExecutionLogRepository()


@pytest.fixture()
def file_repo(tmp_path: Path) -> FileExecutionLogRepository:
    """创建一个基于临时目录的文件日志仓库。"""
    return FileExecutionLogRepository(tmp_path / ".logs")


def _make_entry(
    job_id: str = "job-001",
    stream: LogStream = LogStream.STDOUT,
    content: str = "hello world",
    adapter_name: str = "mock-adapter",
) -> ExecutionLogEntry:
    """创建测试用的日志条目。"""
    return ExecutionLogEntry(
        job_id=job_id,
        stream=stream,
        content=content,
        adapter_name=adapter_name,
    )


# ---------------------------------------------------------------------------
# 协议一致性测试
# ---------------------------------------------------------------------------


class TestProtocolConformance:
    def test_in_memory_implements_protocol(self):
        """验证 InMemoryExecutionLogRepository 满足 Protocol。"""
        assert isinstance(InMemoryExecutionLogRepository(), ExecutionLogRepository)

    def test_file_implements_protocol(self, tmp_path):
        """验证 FileExecutionLogRepository 满足 Protocol。"""
        assert isinstance(
            FileExecutionLogRepository(tmp_path), ExecutionLogRepository
        )


# ---------------------------------------------------------------------------
# InMemoryExecutionLogRepository 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInMemoryExecutionLogRepository:
    async def test_record_and_get_logs(self, mem_repo):
        """验证记录日志后可以查询到。"""
        entry = _make_entry()
        await mem_repo.record(entry)

        logs = await mem_repo.get_logs("job-001")
        assert len(logs) == 1
        assert logs[0].content == "hello world"
        assert logs[0].stream == LogStream.STDOUT

    async def test_get_logs_empty(self, mem_repo):
        """验证查询不存在的 job_id 返回空列表。"""
        logs = await mem_repo.get_logs("nonexistent")
        assert logs == []

    async def test_filter_by_stream(self, mem_repo):
        """验证按 stream 过滤日志。"""
        await mem_repo.record(_make_entry(stream=LogStream.STDOUT, content="out1"))
        await mem_repo.record(_make_entry(stream=LogStream.STDERR, content="err1"))
        await mem_repo.record(_make_entry(stream=LogStream.STDOUT, content="out2"))

        stdout_logs = await mem_repo.get_logs("job-001", stream=LogStream.STDOUT)
        assert len(stdout_logs) == 2
        assert all(e.stream == LogStream.STDOUT for e in stdout_logs)

        stderr_logs = await mem_repo.get_logs("job-001", stream=LogStream.STDERR)
        assert len(stderr_logs) == 1
        assert stderr_logs[0].content == "err1"

    async def test_multiple_jobs(self, mem_repo):
        """验证多个 job 的日志互不干扰。"""
        await mem_repo.record(_make_entry(job_id="job-001", content="log1"))
        await mem_repo.record(_make_entry(job_id="job-002", content="log2"))

        logs1 = await mem_repo.get_logs("job-001")
        logs2 = await mem_repo.get_logs("job-002")
        assert len(logs1) == 1
        assert len(logs2) == 1
        assert logs1[0].content == "log1"
        assert logs2[0].content == "log2"

    async def test_list_jobs_with_logs(self, mem_repo):
        """验证列出有日志的 job_id（按时间倒序）。"""
        await mem_repo.record(
            ExecutionLogEntry(
                job_id="job-old",
                timestamp=datetime(2024, 1, 1, tzinfo=UTC),
                stream=LogStream.STDOUT,
                content="old",
            )
        )
        await mem_repo.record(
            ExecutionLogEntry(
                job_id="job-new",
                timestamp=datetime(2024, 6, 1, tzinfo=UTC),
                stream=LogStream.STDOUT,
                content="new",
            )
        )

        jobs = await mem_repo.list_jobs_with_logs()
        assert jobs == ["job-new", "job-old"]

    async def test_list_jobs_with_limit(self, mem_repo):
        """验证 limit 参数限制返回数量。"""
        for i in range(10):
            await mem_repo.record(
                ExecutionLogEntry(
                    job_id=f"job-{i:03d}",
                    timestamp=datetime(2024, 1, 1 + i, tzinfo=UTC),
                    stream=LogStream.STDOUT,
                    content=f"log-{i}",
                )
            )

        jobs = await mem_repo.list_jobs_with_logs(limit=3)
        assert len(jobs) == 3

    async def test_clear(self, mem_repo):
        """验证清空功能。"""
        await mem_repo.record(_make_entry())
        logs = await mem_repo.get_logs("job-001")
        assert len(logs) == 1

        await mem_repo.clear()
        logs = await mem_repo.get_logs("job-001")
        assert len(logs) == 0
        jobs = await mem_repo.list_jobs_with_logs()
        assert jobs == []

    async def test_adapter_name_preserved(self, mem_repo):
        """验证 adapter_name 字段被正确保存。"""
        entry = _make_entry(adapter_name="claude-code")
        await mem_repo.record(entry)

        logs = await mem_repo.get_logs("job-001")
        assert logs[0].adapter_name == "claude-code"


# ---------------------------------------------------------------------------
# FileExecutionLogRepository 测试
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestFileExecutionLogRepository:
    async def test_record_creates_files(self, file_repo, tmp_path):
        """验证记录日志后在磁盘创建了正确的文件。"""
        entry = _make_entry()
        await file_repo.record(entry)

        job_dir = tmp_path / ".logs" / "job-001"
        assert job_dir.exists()
        assert (job_dir / "stdout.log").exists()
        assert (job_dir / "entries.jsonl").exists()

    async def test_record_and_get_logs(self, file_repo):
        """验证文件仓库的记录和查询。"""
        entry = _make_entry(content="line1")
        await file_repo.record(entry)

        logs = await file_repo.get_logs("job-001")
        assert len(logs) == 1
        assert logs[0].content == "line1"

    async def test_get_logs_empty(self, file_repo):
        """验证查询不存在的 job_id 返回空列表。"""
        assert await file_repo.get_logs("nonexistent") == []

    async def test_filter_by_stream(self, file_repo):
        """验证文件仓库的 stream 过滤。"""
        await file_repo.record(_make_entry(stream=LogStream.STDOUT, content="stdout-line"))
        await file_repo.record(_make_entry(stream=LogStream.STDERR, content="stderr-line"))

        stdout = await file_repo.get_logs("job-001", stream=LogStream.STDOUT)
        assert len(stdout) == 1
        assert stdout[0].content == "stdout-line"

        stderr = await file_repo.get_logs("job-001", stream=LogStream.STDERR)
        assert len(stderr) == 1
        assert stderr[0].content == "stderr-line"

    async def test_stream_text_files(self, file_repo, tmp_path):
        """验证纯文本日志文件的内容。"""
        await file_repo.record(_make_entry(stream=LogStream.STDOUT, content="hello"))
        await file_repo.record(_make_entry(stream=LogStream.STDOUT, content="world"))

        text = file_repo.get_stream_text("job-001", LogStream.STDOUT)
        assert "hello" in text
        assert "world" in text

    async def test_stderr_file_created(self, file_repo, tmp_path):
        """验证 stderr 日志文件的创建。"""
        await file_repo.record(_make_entry(stream=LogStream.STDERR, content="error msg"))

        job_dir = tmp_path / ".logs" / "job-001"
        assert (job_dir / "stderr.log").exists()
        assert "error msg" in (job_dir / "stderr.log").read_text(encoding="utf-8")

    async def test_list_jobs_with_logs(self, file_repo):
        """验证列出有日志的 job_id。"""
        await file_repo.record(_make_entry(job_id="job-aaa", content="a"))
        await file_repo.record(_make_entry(job_id="job-bbb", content="b"))

        jobs = await file_repo.list_jobs_with_logs()
        assert set(jobs) == {"job-aaa", "job-bbb"}

    async def test_list_jobs_with_limit(self, file_repo):
        """验证 limit 参数限制返回数量。"""
        for i in range(5):
            await file_repo.record(_make_entry(job_id=f"job-{i:03d}", content=f"log-{i}"))

        jobs = await file_repo.list_jobs_with_logs(limit=2)
        assert len(jobs) == 2

    async def test_multiple_entries_append(self, file_repo):
        """验证多次写入同一 job 会追加而非覆盖。"""
        await file_repo.record(_make_entry(content="line1"))
        await file_repo.record(_make_entry(content="line2"))
        await file_repo.record(_make_entry(content="line3"))

        logs = await file_repo.get_logs("job-001")
        assert len(logs) == 3
        contents = [e.content for e in logs]
        assert contents == ["line1", "line2", "line3"]

    async def test_get_stream_text_empty(self, file_repo):
        """验证不存在的 stream 返回空字符串。"""
        text = file_repo.get_stream_text("nonexistent", LogStream.STDOUT)
        assert text == ""

    async def test_jsonl_format(self, file_repo, tmp_path):
        """验证 entries.jsonl 文件格式正确。"""
        await file_repo.record(_make_entry(content="test-content"))

        jsonl_path = tmp_path / ".logs" / "job-001" / "entries.jsonl"
        lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        parsed = json.loads(lines[0])
        assert parsed["content"] == "test-content"
        assert parsed["stream"] == "stdout"
        assert parsed["job_id"] == "job-001"


# ---------------------------------------------------------------------------
# ExecutionLogEntry 模型测试
# ---------------------------------------------------------------------------


class TestExecutionLogEntry:
    def test_default_timestamp(self):
        """验证默认时间戳自动生成。"""
        entry = ExecutionLogEntry(
            job_id="job-001",
            stream=LogStream.STDOUT,
            content="test",
        )
        assert entry.timestamp is not None

    def test_serialization_roundtrip(self):
        """验证 JSON 序列化和反序列化的一致性。"""
        entry = _make_entry()
        json_str = entry.model_dump_json()
        restored = ExecutionLogEntry.model_validate_json(json_str)
        assert restored.job_id == entry.job_id
        assert restored.content == entry.content
        assert restored.stream == entry.stream
        assert restored.adapter_name == entry.adapter_name
