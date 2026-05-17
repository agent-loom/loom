"""Lightweight Prometheus-compatible metrics collector (no external dependencies)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict


class MetricsCollector:
    """Tracks counters, histograms and gauges, and formats them as Prometheus text."""

    def __init__(self) -> None:
        """初始化计数器、仪表盘和观测值存储。"""
        self._lock = threading.Lock()

        # Counters: (metric_name, frozen_label_tuple) -> float
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float),
        )

        # Gauge values: (metric_name, frozen_label_tuple) -> float
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(
            lambda: defaultdict(float),
        )

        # Duration observations for histogram/summary:
        # metric_name -> {label_tuple -> list[float]}
        self._observations: dict[
            str, dict[tuple[tuple[str, str], ...], list[float]]
        ] = defaultdict(lambda: defaultdict(list))

    # ------------------------------------------------------------------
    # Public helpers for the three pre-defined metric families
    # ------------------------------------------------------------------

    def inc_counter(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        """Increment a counter metric."""
        key = _labels_key(labels)
        with self._lock:
            self._counters[name][key] += value

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge metric to an absolute value."""
        key = _labels_key(labels)
        with self._lock:
            self._gauges[name][key] = value

    def inc_gauge(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: float = 1.0,
    ) -> None:
        """Increment (or decrement with negative *value*) a gauge metric."""
        key = _labels_key(labels)
        with self._lock:
            self._gauges[name][key] += value

    def observe(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record an observation for a summary metric."""
        key = _labels_key(labels)
        with self._lock:
            self._observations[name][key].append(value)

    # ------------------------------------------------------------------
    # Convenience wrappers matching the task specification
    # ------------------------------------------------------------------

    def record_request(self, agent_id: str, status: str) -> None:
        """记录一次 Agent 请求（按 agent_id 和状态分类）。"""
        self.inc_counter(
            "agent_requests_total",
            {"agent_id": agent_id, "status": status},
        )

    def record_error(self, agent_id: str) -> None:
        """记录一次 Agent 请求错误。"""
        self.inc_counter(
            "agent_request_errors_total",
            {"agent_id": agent_id},
        )

    def record_duration(self, agent_id: str, duration: float) -> None:
        """记录一次 Agent 请求耗时（秒）。"""
        self.observe(
            "agent_request_duration_seconds",
            duration,
            {"agent_id": agent_id},
        )

    def record_tool_call(self, tool_name: str, status: str) -> None:
        """记录一次工具调用（按工具名和状态分类）。"""
        self.inc_counter(
            "tool_calls_total",
            {"tool_name": tool_name, "status": status},
        )

    def record_tool_duration(
        self, tool_name: str, duration: float,
    ) -> None:
        """记录一次工具调用耗时（秒）。"""
        self.observe(
            "tool_call_duration_seconds",
            duration,
            {"tool_name": tool_name},
        )

    def set_active_sessions(self, count: int) -> None:
        """设置当前活跃会话数。"""
        self.set_gauge("active_sessions", float(count))

    # ------------------------------------------------------------------
    # Structured metrics retrieval for SLO evaluation
    # ------------------------------------------------------------------

    def get_metrics(self, agent_id: str) -> dict[str, float]:
        """获取指定 agent 的聚合指标，用于 SLO 评估。

        返回的指标包括：
        - total_requests: 总请求数
        - error_count: 错误数
        - error_rate: 错误率
        - success_rate: 成功率
        - p99_latency_ms: P99 延迟（毫秒）
        - p90_latency_ms: P90 延迟（毫秒）
        - p50_latency_ms: P50 延迟（毫秒）
        - avg_latency_ms: 平均延迟（毫秒）
        """
        result: dict[str, float] = {}
        agent_label = (("agent_id", agent_id),)

        with self._lock:
            # 请求总数
            total = 0.0
            for label_key, val in self._counters.get("agent_requests_total", {}).items():
                if any(k == "agent_id" and v == agent_id for k, v in label_key):
                    total += val
            result["total_requests"] = total

            # 错误数
            error_count = self._counters.get(
                "agent_request_errors_total", {},
            ).get(agent_label, 0.0)
            result["error_count"] = error_count

            # 错误率与成功率
            if total > 0:
                result["error_rate"] = error_count / total
                result["success_rate"] = 1.0 - (error_count / total)
            else:
                result["error_rate"] = 0.0
                result["success_rate"] = 1.0

            # 延迟分位数（从观测数据计算）
            observations = self._observations.get(
                "agent_request_duration_seconds", {},
            ).get(agent_label, [])
            if observations:
                sorted_obs = sorted(observations)
                count = len(sorted_obs)
                # 转换为毫秒
                result["p50_latency_ms"] = sorted_obs[min(int(0.5 * count), count - 1)] * 1000
                result["p90_latency_ms"] = sorted_obs[min(int(0.9 * count), count - 1)] * 1000
                result["p99_latency_ms"] = sorted_obs[min(int(0.99 * count), count - 1)] * 1000
                result["avg_latency_ms"] = (sum(sorted_obs) / count) * 1000
            else:
                result["p50_latency_ms"] = 0.0
                result["p90_latency_ms"] = 0.0
                result["p99_latency_ms"] = 0.0
                result["avg_latency_ms"] = 0.0

        return result

    # ------------------------------------------------------------------
    # Context manager for timing requests
    # ------------------------------------------------------------------

    class _Timer:
        """Thin context-manager that records elapsed seconds on exit."""

        def __init__(self, collector: MetricsCollector, agent_id: str) -> None:
            self._collector = collector
            self._agent_id = agent_id
            self._start: float = 0.0

        def __enter__(self) -> MetricsCollector._Timer:
            self._start = time.monotonic()
            return self

        def __exit__(self, *_exc) -> None:
            elapsed = time.monotonic() - self._start
            self._collector.record_duration(self._agent_id, elapsed)

    def time_request(self, agent_id: str) -> _Timer:
        """Return a context manager that records request duration."""
        return self._Timer(self, agent_id)

    # ------------------------------------------------------------------
    # Prometheus text exposition
    # ------------------------------------------------------------------

    # 预定义指标的 HELP 描述映射
    _HELP_DESCRIPTIONS: dict[str, str] = {
        "agent_requests_total": (
            "Total number of agent requests."
        ),
        "agent_request_errors_total": (
            "Total number of agent request errors."
        ),
        "tool_calls_total": (
            "Total number of tool calls."
        ),
        "agent_request_duration_seconds": (
            "Duration of agent requests in seconds."
        ),
        "tool_call_duration_seconds": (
            "Duration of tool calls in seconds."
        ),
        "active_sessions": (
            "Number of currently active sessions."
        ),
    }

    def format_prometheus(self) -> str:
        """Return all metrics in Prometheus text exposition format."""
        return self._render_prometheus()

    def to_prometheus(self) -> str:
        """返回 Prometheus text exposition 格式的全部指标。

        与 format_prometheus() 功能一致，提供语义更明确的别名。
        """
        return self._render_prometheus()

    def _render_prometheus(self) -> str:
        """内部方法：将收集到的指标渲染为 Prometheus 文本格式。"""
        lines: list[str] = []

        with self._lock:
            # 计数器
            for name in sorted(self._counters):
                help_text = self._HELP_DESCRIPTIONS.get(
                    name, "Counter",
                )
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} counter")
                for label_key in sorted(self._counters[name]):
                    val = self._counters[name][label_key]
                    lines.append(
                        f"{name}"
                        f"{_format_labels(label_key)} "
                        f"{_format_value(val)}"
                    )

            # 仪表盘
            for name in sorted(self._gauges):
                help_text = self._HELP_DESCRIPTIONS.get(
                    name, "Gauge",
                )
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} gauge")
                for label_key in sorted(self._gauges[name]):
                    val = self._gauges[name][label_key]
                    lines.append(
                        f"{name}"
                        f"{_format_labels(label_key)} "
                        f"{_format_value(val)}"
                    )

            # 摘要（summary）
            for name in sorted(self._observations):
                help_text = self._HELP_DESCRIPTIONS.get(
                    name, "Summary",
                )
                lines.append(f"# HELP {name} {help_text}")
                lines.append(f"# TYPE {name} summary")
                for label_key in sorted(
                    self._observations[name],
                ):
                    observations = (
                        self._observations[name][label_key]
                    )
                    if not observations:
                        continue
                    sorted_obs = sorted(observations)
                    count = len(sorted_obs)
                    total = sum(sorted_obs)
                    labels_str = _format_labels(label_key)
                    # 基于排序后的观测值计算近似分位数（p50/p90/p99）
                    for quantile in (0.5, 0.9, 0.99):
                        idx = min(
                            int(quantile * count),
                            count - 1,
                        )
                        q_labels = _format_labels_with_extra(
                            label_key,
                            "quantile",
                            str(quantile),
                        )
                        lines.append(
                            f"{name}{q_labels} "
                            f"{_format_value(sorted_obs[idx])}"
                        )
                    lines.append(
                        f"{name}_count{labels_str} {count}"
                    )
                    lines.append(
                        f"{name}_sum{labels_str} "
                        f"{_format_value(total)}"
                    )

        lines.append("")  # 尾部换行
        return "\n".join(lines)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    """将标签字典转为排序后的元组，用作字典键以保证唯一性。"""
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def _format_labels(key: tuple[tuple[str, str], ...]) -> str:
    """将标签元组格式化为 Prometheus 标签字符串，如 {k1="v1",k2="v2"}。"""
    if not key:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in key)
    return "{" + inner + "}"


def _format_labels_with_extra(
    key: tuple[tuple[str, str], ...],
    extra_name: str,
    extra_value: str,
) -> str:
    """在已有标签基础上追加额外标签（如 quantile），用于 summary 分位数行。"""
    parts = list(key) + [(extra_name, extra_value)]
    inner = ",".join(f'{k}="{v}"' for k, v in parts)
    return "{" + inner + "}"


def _format_value(val: float) -> str:
    """格式化数值：整数去小数点，浮点数保留 6 位有效数字。"""
    if val == int(val):
        return str(int(val))
    return f"{val:.6g}"
