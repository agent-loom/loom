"""SLO 门禁单元测试。"""

from __future__ import annotations

from agent_platform.governance.slo import (
    DEFAULT_SLO_RULES,
    SLOGate,
    SLORule,
    _evaluate_operator,
)
from agent_platform.observability.metrics import MetricsCollector

# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def _make_metrics_with_data(agent_id: str = "test-agent", **overrides) -> MetricsCollector:
    """创建带有预设数据的 MetricsCollector。"""
    m = MetricsCollector()
    # 模拟 10 个请求
    for _ in range(10):
        m.record_request(agent_id, "success")
    # 模拟延迟（秒）
    for duration in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        m.record_duration(agent_id, duration)
    return m


# ---------------------------------------------------------------------------
# SLORule 创建和验证
# ---------------------------------------------------------------------------


class TestSLORule:
    """SLORule 模型测试。"""

    def test_create_basic_rule(self):
        """测试基本 SLO 规则创建。"""
        rule = SLORule(
            name="test_rule",
            metric="error_rate",
            threshold=0.05,
            operator="lt",
        )
        assert rule.name == "test_rule"
        assert rule.metric == "error_rate"
        assert rule.threshold == 0.05
        assert rule.operator == "lt"
        assert rule.window_seconds == 3600
        assert rule.description == ""

    def test_create_rule_with_all_fields(self):
        """测试带全部字段的 SLO 规则创建。"""
        rule = SLORule(
            name="latency_rule",
            metric="p99_latency_ms",
            threshold=3000.0,
            operator="lte",
            window_seconds=1800,
            description="P99 延迟不超过 3 秒",
        )
        assert rule.window_seconds == 1800
        assert rule.description == "P99 延迟不超过 3 秒"

    def test_default_rules_exist(self):
        """测试默认规则集包含预期规则。"""
        assert len(DEFAULT_SLO_RULES) == 3
        names = {r.name for r in DEFAULT_SLO_RULES}
        assert "p99_latency" in names
        assert "error_rate" in names
        assert "success_rate" in names


# ---------------------------------------------------------------------------
# 操作符评估
# ---------------------------------------------------------------------------


class TestEvaluateOperator:
    """操作符评估函数测试。"""

    def test_lt_pass(self):
        assert _evaluate_operator(3.0, 5.0, "lt") is True

    def test_lt_fail(self):
        assert _evaluate_operator(5.0, 5.0, "lt") is False

    def test_gt_pass(self):
        assert _evaluate_operator(6.0, 5.0, "gt") is True

    def test_gt_fail(self):
        assert _evaluate_operator(5.0, 5.0, "gt") is False

    def test_lte_pass_equal(self):
        assert _evaluate_operator(5.0, 5.0, "lte") is True

    def test_lte_pass_less(self):
        assert _evaluate_operator(4.0, 5.0, "lte") is True

    def test_gte_pass_equal(self):
        assert _evaluate_operator(5.0, 5.0, "gte") is True

    def test_gte_fail(self):
        assert _evaluate_operator(4.0, 5.0, "gte") is False

    def test_unknown_operator(self):
        assert _evaluate_operator(5.0, 5.0, "unknown") is False


# ---------------------------------------------------------------------------
# SLOGate.check() 测试
# ---------------------------------------------------------------------------


class TestSLOGateCheck:
    """SLOGate 检查测试。"""

    def test_check_all_pass_with_good_metrics(self):
        """全部 SLO 通过时返回 True。"""
        m = _make_metrics_with_data("agent-a")
        gate = SLOGate(metrics=m)
        all_passed, results = gate.check_all("agent-a")
        assert all_passed is True
        assert all(r.passed for r in results)

    def test_check_fail_with_high_error_rate(self):
        """错误率超标时 SLO 检查失败。"""
        m = MetricsCollector()
        # 10 个请求中 2 个错误 -> error_rate = 0.2
        for _ in range(10):
            m.record_request("agent-b", "success")
        for _ in range(2):
            m.record_error("agent-b")
        for d in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
            m.record_duration("agent-b", d)

        gate = SLOGate(metrics=m)
        all_passed, results = gate.check_all("agent-b")
        # error_rate = 0.2 > 0.05 -> 失败
        assert all_passed is False
        error_rule = next(r for r in results if r.rule.name == "error_rate")
        assert error_rule.passed is False

    def test_check_fail_with_high_latency(self):
        """P99 延迟超标时 SLO 检查失败。"""
        m = MetricsCollector()
        for _ in range(10):
            m.record_request("agent-c", "success")
        # 全部高延迟（6秒 = 6000ms > 5000ms）
        for _ in range(10):
            m.record_duration("agent-c", 6.0)

        gate = SLOGate(metrics=m)
        results = gate.check("agent-c")
        latency_rule = next(r for r in results if r.rule.name == "p99_latency")
        assert latency_rule.passed is False
        assert latency_rule.actual_value is not None
        assert latency_rule.actual_value > 5000

    def test_check_with_no_data(self):
        """无数据时检查应通过（因为默认值满足条件）。"""
        m = MetricsCollector()
        gate = SLOGate(metrics=m)
        all_passed, results = gate.check_all("unknown-agent")
        # 无请求数据：error_rate=0.0, success_rate=1.0, p99_latency=0.0
        # 所有默认规则都应通过
        assert all_passed is True

    def test_check_with_custom_rules(self):
        """使用自定义规则进行 SLO 检查。"""
        m = _make_metrics_with_data("agent-d")
        custom_rules = [
            SLORule(
                name="custom_latency",
                metric="p50_latency_ms",
                threshold=100.0,
                operator="lt",
                description="P50 延迟必须低于 100ms",
            ),
        ]
        gate = SLOGate(metrics=m, rules=custom_rules)
        results = gate.check("agent-d")
        assert len(results) == 1
        # P50 of [100, 200, ..., 1000] ms ≈ 500ms -> 不通过
        assert results[0].passed is False

    def test_check_result_message_pass(self):
        """通过时消息包含 '通过'。"""
        m = _make_metrics_with_data("agent-e")
        gate = SLOGate(metrics=m)
        results = gate.check("agent-e")
        passed_results = [r for r in results if r.passed]
        assert len(passed_results) > 0
        assert "通过" in passed_results[0].message

    def test_check_result_message_fail(self):
        """失败时消息包含 '违规'。"""
        m = MetricsCollector()
        for _ in range(10):
            m.record_request("agent-f", "success")
        for _ in range(5):
            m.record_error("agent-f")
        for d in [0.1] * 10:
            m.record_duration("agent-f", d)
        gate = SLOGate(metrics=m)
        results = gate.check("agent-f")
        failed_results = [r for r in results if not r.passed]
        assert len(failed_results) > 0
        assert "违规" in failed_results[0].message

    def test_gate_uses_default_rules(self):
        """默认构造使用 DEFAULT_SLO_RULES。"""
        m = MetricsCollector()
        gate = SLOGate(metrics=m)
        assert len(gate.rules) == len(DEFAULT_SLO_RULES)

    def test_gate_custom_rules_override_defaults(self):
        """自定义规则应替代默认规则。"""
        m = MetricsCollector()
        custom = [
            SLORule(name="single", metric="error_rate", threshold=0.1, operator="lt"),
        ]
        gate = SLOGate(metrics=m, rules=custom)
        assert len(gate.rules) == 1
        assert gate.rules[0].name == "single"

    def test_check_unavailable_metric(self):
        """请求不存在的指标时应报告不可用。"""
        m = MetricsCollector()
        rules = [
            SLORule(
                name="nonexistent",
                metric="some_nonexistent_metric",
                threshold=1.0,
                operator="lt",
            ),
        ]
        gate = SLOGate(metrics=m, rules=rules)
        results = gate.check("any-agent")
        assert len(results) == 1
        assert results[0].passed is False
        assert results[0].actual_value is None
        assert "不可用" in results[0].message
