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
        self.inc_counter("agent_requests_total", {"agent_id": agent_id, "status": status})

    def record_duration(self, agent_id: str, duration: float) -> None:
        """记录一次 Agent 请求耗时（秒）。"""
        self.observe("agent_request_duration_seconds", duration, {"agent_id": agent_id})

    def record_tool_call(self, tool_name: str, status: str) -> None:
        """记录一次工具调用（按工具名和状态分类）。"""
        self.inc_counter("tool_calls_total", {"tool_name": tool_name, "status": status})

    def set_active_sessions(self, count: int) -> None:
        """设置当前活跃会话数。"""
        self.set_gauge("active_sessions", float(count))

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

    def format_prometheus(self) -> str:
        """Return all metrics in Prometheus text exposition format."""
        lines: list[str] = []

        with self._lock:
            # Counters
            for name in sorted(self._counters):
                lines.append(f"# HELP {name} Counter")
                lines.append(f"# TYPE {name} counter")
                for label_key in sorted(self._counters[name]):
                    val = self._counters[name][label_key]
                    lines.append(f"{name}{_format_labels(label_key)} {_format_value(val)}")

            # Gauges
            for name in sorted(self._gauges):
                lines.append(f"# HELP {name} Gauge")
                lines.append(f"# TYPE {name} gauge")
                for label_key in sorted(self._gauges[name]):
                    val = self._gauges[name][label_key]
                    lines.append(f"{name}{_format_labels(label_key)} {_format_value(val)}")

            # Summaries
            for name in sorted(self._observations):
                lines.append(f"# HELP {name} Summary")
                lines.append(f"# TYPE {name} summary")
                for label_key in sorted(self._observations[name]):
                    observations = self._observations[name][label_key]
                    if not observations:
                        continue
                    sorted_obs = sorted(observations)
                    count = len(sorted_obs)
                    total = sum(sorted_obs)
                    labels_str = _format_labels(label_key)
                    for quantile in (0.5, 0.9, 0.99):
                        idx = min(int(quantile * count), count - 1)
                        q_labels = _format_labels_with_extra(
                            label_key, "quantile", str(quantile),
                        )
                        lines.append(f"{name}{q_labels} {_format_value(sorted_obs[idx])}")
                    lines.append(f"{name}_count{labels_str} {count}")
                    lines.append(f"{name}_sum{labels_str} {_format_value(total)}")

        lines.append("")  # trailing newline
        return "\n".join(lines)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _labels_key(labels: dict[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted(labels.items()))


def _format_labels(key: tuple[tuple[str, str], ...]) -> str:
    if not key:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in key)
    return "{" + inner + "}"


def _format_labels_with_extra(
    key: tuple[tuple[str, str], ...],
    extra_name: str,
    extra_value: str,
) -> str:
    parts = list(key) + [(extra_name, extra_value)]
    inner = ",".join(f'{k}="{v}"' for k, v in parts)
    return "{" + inner + "}"


def _format_value(val: float) -> str:
    if val == int(val):
        return str(int(val))
    return f"{val:.6g}"
