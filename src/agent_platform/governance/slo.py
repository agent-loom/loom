"""SLO 门禁：定义服务等级目标规则并在部署前进行检查。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from agent_platform.observability.metrics import MetricsCollector

logger = logging.getLogger(__name__)


class SLORule(BaseModel):
    """单条 SLO 规则定义。"""

    name: str
    metric: str
    threshold: float
    operator: Literal["lt", "gt", "lte", "gte"]
    window_seconds: int = 3600
    description: str = ""


class SLOCheckResult(BaseModel):
    """单条 SLO 检查结果。"""

    rule: SLORule
    actual_value: float | None
    passed: bool
    message: str


# 默认 SLO 规则集
DEFAULT_SLO_RULES: list[SLORule] = [
    SLORule(
        name="p99_latency",
        metric="p99_latency_ms",
        threshold=5000.0,
        operator="lt",
        description="P99 延迟必须小于 5000ms",
    ),
    SLORule(
        name="error_rate",
        metric="error_rate",
        threshold=0.05,
        operator="lt",
        description="错误率必须小于 5%",
    ),
    SLORule(
        name="success_rate",
        metric="success_rate",
        threshold=0.95,
        operator="gte",
        description="成功率必须 >= 95%",
    ),
]


def _evaluate_operator(actual: float, threshold: float, operator: str) -> bool:
    """根据操作符比较实际值与阈值。"""
    if operator == "lt":
        return actual < threshold
    if operator == "gt":
        return actual > threshold
    if operator == "lte":
        return actual <= threshold
    if operator == "gte":
        return actual >= threshold
    return False


class SLOGate:
    """SLO 部署门禁：在部署前检查所有 SLO 规则是否满足。"""

    def __init__(
        self,
        metrics: MetricsCollector,
        rules: list[SLORule] | None = None,
    ) -> None:
        self._metrics = metrics
        self._rules = rules if rules is not None else list(DEFAULT_SLO_RULES)

    @property
    def rules(self) -> list[SLORule]:
        """返回当前配置的 SLO 规则列表。"""
        return self._rules

    def check(self, agent_id: str) -> list[SLOCheckResult]:
        """评估指定 agent 的所有 SLO 规则，返回检查结果列表。"""
        agent_metrics = self._metrics.get_metrics(agent_id)
        results: list[SLOCheckResult] = []

        for rule in self._rules:
            actual_value = agent_metrics.get(rule.metric)

            if actual_value is None:
                results.append(
                    SLOCheckResult(
                        rule=rule,
                        actual_value=None,
                        passed=False,
                        message=f"指标 '{rule.metric}' 不可用",
                    )
                )
                continue

            passed = _evaluate_operator(actual_value, rule.threshold, rule.operator)
            if passed:
                msg = (
                    f"SLO '{rule.name}' 通过: "
                    f"{rule.metric}={actual_value:.4f} {rule.operator} {rule.threshold}"
                )
            else:
                msg = (
                    f"SLO '{rule.name}' 违规: "
                    f"{rule.metric}={actual_value:.4f} 不满足 {rule.operator} {rule.threshold}"
                )

            results.append(
                SLOCheckResult(
                    rule=rule,
                    actual_value=actual_value,
                    passed=passed,
                    message=msg,
                )
            )

        return results

    def check_all(self, agent_id: str) -> tuple[bool, list[SLOCheckResult]]:
        """评估所有 SLO 并返回 (全部通过, 结果列表)。"""
        results = self.check(agent_id)
        all_passed = all(r.passed for r in results)
        return all_passed, results
