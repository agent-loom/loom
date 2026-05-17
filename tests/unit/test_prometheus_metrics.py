"""Prometheus 指标格式化输出的单元测试。"""

from __future__ import annotations

import pytest

from agent_platform.observability.metrics import MetricsCollector


class TestToPrometheusEmpty:
    """测试空状态（无数据）下的 to_prometheus() 输出。"""

    def test_empty_output_is_single_newline(self) -> None:
        """空 collector 应该只返回一个空字符串（无指标数据）。"""
        mc = MetricsCollector()
        result = mc.to_prometheus()
        # 仅包含尾部空行（join 一个 [""] 产生 ""）
        assert result == ""

    def test_empty_output_has_no_help_lines(self) -> None:
        """空 collector 输出中不应包含 HELP 行。"""
        mc = MetricsCollector()
        result = mc.to_prometheus()
        assert "# HELP" not in result

    def test_empty_output_has_no_type_lines(self) -> None:
        """空 collector 输出中不应包含 TYPE 行。"""
        mc = MetricsCollector()
        result = mc.to_prometheus()
        assert "# TYPE" not in result


class TestToPrometheusWithData:
    """测试有数据时的 to_prometheus() 输出。"""

    @pytest.fixture()
    def collector(self) -> MetricsCollector:
        """创建并填充测试数据的 MetricsCollector。"""
        mc = MetricsCollector()
        # 记录 agent 请求
        mc.record_request("echo", "ok")
        mc.record_request("echo", "ok")
        mc.record_request("echo", "error")
        # 记录 agent 请求错误
        mc.record_error("echo")
        # 记录请求耗时
        mc.record_duration("echo", 0.5)
        mc.record_duration("echo", 1.5)
        # 记录工具调用
        mc.record_tool_call("search", "ok")
        mc.record_tool_call("search", "error")
        # 记录工具调用耗时
        mc.record_tool_duration("search", 0.1)
        mc.record_tool_duration("search", 0.3)
        return mc

    def test_contains_agent_requests_total(
        self, collector: MetricsCollector,
    ) -> None:
        """输出应包含 agent_requests_total 计数器。"""
        output = collector.to_prometheus()
        assert "agent_requests_total" in output
        # ok=2, error=1
        assert (
            'agent_requests_total{agent_id="echo",'
            'status="ok"} 2' in output
        )
        assert (
            'agent_requests_total{agent_id="echo",'
            'status="error"} 1' in output
        )

    def test_contains_agent_request_errors_total(
        self, collector: MetricsCollector,
    ) -> None:
        """输出应包含 agent_request_errors_total 计数器。"""
        output = collector.to_prometheus()
        assert "agent_request_errors_total" in output
        assert (
            'agent_request_errors_total'
            '{agent_id="echo"} 1' in output
        )

    def test_contains_agent_request_duration_seconds(
        self, collector: MetricsCollector,
    ) -> None:
        """输出应包含 agent_request_duration_seconds 摘要。"""
        output = collector.to_prometheus()
        assert "agent_request_duration_seconds" in output
        # 应有 _count 和 _sum
        assert (
            'agent_request_duration_seconds_count'
            '{agent_id="echo"} 2' in output
        )
        assert (
            "agent_request_duration_seconds_sum"
            in output
        )

    def test_contains_tool_calls_total(
        self, collector: MetricsCollector,
    ) -> None:
        """输出应包含 tool_calls_total 计数器。"""
        output = collector.to_prometheus()
        assert "tool_calls_total" in output
        assert (
            'tool_calls_total{status="ok",'
            'tool_name="search"} 1' in output
        )

    def test_contains_tool_call_duration_seconds(
        self, collector: MetricsCollector,
    ) -> None:
        """输出应包含 tool_call_duration_seconds 摘要。"""
        output = collector.to_prometheus()
        assert "tool_call_duration_seconds" in output
        assert (
            "tool_call_duration_seconds_count"
            in output
        )
        assert (
            "tool_call_duration_seconds_sum"
            in output
        )


class TestHelpAndTypeAnnotations:
    """测试 HELP 和 TYPE 注释行的存在性和格式。"""

    @pytest.fixture()
    def collector(self) -> MetricsCollector:
        """创建包含各类指标的 MetricsCollector。"""
        mc = MetricsCollector()
        mc.record_request("a1", "ok")
        mc.record_error("a1")
        mc.record_duration("a1", 0.1)
        mc.record_tool_call("t1", "ok")
        mc.record_tool_duration("t1", 0.2)
        mc.set_active_sessions(5)
        return mc

    def test_help_line_for_agent_requests_total(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 agent_requests_total 的 HELP 行。"""
        output = collector.to_prometheus()
        assert (
            "# HELP agent_requests_total"
            in output
        )

    def test_type_line_for_agent_requests_total(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 agent_requests_total 的 TYPE counter 行。"""
        output = collector.to_prometheus()
        assert (
            "# TYPE agent_requests_total counter"
            in output
        )

    def test_help_line_for_errors(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 agent_request_errors_total 的 HELP 行。"""
        output = collector.to_prometheus()
        assert (
            "# HELP agent_request_errors_total"
            in output
        )

    def test_type_line_for_errors(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 agent_request_errors_total 的 TYPE counter 行。"""
        output = collector.to_prometheus()
        assert (
            "# TYPE agent_request_errors_total counter"
            in output
        )

    def test_help_line_for_duration(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 agent_request_duration_seconds 的 HELP 行。"""
        output = collector.to_prometheus()
        assert (
            "# HELP agent_request_duration_seconds"
            in output
        )

    def test_type_line_for_duration(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 agent_request_duration_seconds 的 TYPE summary 行。"""
        output = collector.to_prometheus()
        assert (
            "# TYPE agent_request_duration_seconds summary"
            in output
        )

    def test_help_line_for_tool_calls(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 tool_calls_total 的 HELP 行。"""
        output = collector.to_prometheus()
        assert "# HELP tool_calls_total" in output

    def test_type_line_for_tool_calls(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 tool_calls_total 的 TYPE counter 行。"""
        output = collector.to_prometheus()
        assert (
            "# TYPE tool_calls_total counter"
            in output
        )

    def test_help_line_for_tool_duration(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 tool_call_duration_seconds 的 HELP 行。"""
        output = collector.to_prometheus()
        assert (
            "# HELP tool_call_duration_seconds"
            in output
        )

    def test_type_line_for_tool_duration(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 tool_call_duration_seconds 的 TYPE summary 行。"""
        output = collector.to_prometheus()
        assert (
            "# TYPE tool_call_duration_seconds summary"
            in output
        )

    def test_help_line_for_active_sessions(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 active_sessions 的 HELP 行。"""
        output = collector.to_prometheus()
        assert "# HELP active_sessions" in output

    def test_type_line_for_active_sessions(
        self, collector: MetricsCollector,
    ) -> None:
        """应包含 active_sessions 的 TYPE gauge 行。"""
        output = collector.to_prometheus()
        assert (
            "# TYPE active_sessions gauge" in output
        )

    def test_help_before_type(
        self, collector: MetricsCollector,
    ) -> None:
        """每个指标的 HELP 行应出现在 TYPE 行之前。"""
        output = collector.to_prometheus()
        lines = output.splitlines()
        # 找出所有 HELP/TYPE 行并验证顺序
        for i, line in enumerate(lines):
            if line.startswith("# TYPE"):
                name = line.split()[2]
                # 前一行应该是同名的 HELP
                assert i > 0
                prev = lines[i - 1]
                assert prev.startswith(f"# HELP {name}")


class TestFormatPrometheusConsistency:
    """验证 to_prometheus() 和 format_prometheus() 输出一致。"""

    def test_same_output(self) -> None:
        """两个方法应返回完全相同的字符串。"""
        mc = MetricsCollector()
        mc.record_request("x", "ok")
        mc.record_duration("x", 0.42)
        mc.record_tool_call("y", "ok")
        assert mc.to_prometheus() == mc.format_prometheus()
