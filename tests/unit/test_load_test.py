"""S8 8.6.7 — 压力测试脚本验证。

覆盖:
- 结果分析逻辑 (延迟分位数/RPS/错误率)
- EndpointStats 属性计算
- 空结果边界条件
- 脚本可导入性
"""

from __future__ import annotations

import importlib.util


class TestLoadTestImportable:
    def test_importable(self):
        spec = importlib.util.spec_from_file_location(
            "load_test", "scripts/load_test.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert hasattr(mod, "main")
        assert hasattr(mod, "run_load_test")
        assert hasattr(mod, "_analyze_results")


class TestEndpointStats:
    def test_basic_properties(self):
        from scripts.load_test import EndpointStats

        stats = EndpointStats(name="test")
        stats.total = 100
        stats.success = 95
        stats.errors = 5
        stats.latencies = list(range(1, 101))

        assert stats.error_rate == 0.05
        assert stats.p50 == 51
        assert stats.p95 == 96
        assert stats.p99 == 100
        assert stats.max_latency == 100
        assert stats.avg_latency == 50.5

    def test_empty_latencies(self):
        from scripts.load_test import EndpointStats

        stats = EndpointStats(name="empty")
        assert stats.p50 == 0.0
        assert stats.p95 == 0.0
        assert stats.p99 == 0.0
        assert stats.max_latency == 0.0
        assert stats.avg_latency == 0.0

    def test_error_rate_zero_total(self):
        from scripts.load_test import EndpointStats

        stats = EndpointStats(name="none")
        assert stats.error_rate == 0.0


class TestAnalyzeResults:
    def test_empty_results(self):
        from scripts.load_test import _analyze_results

        report = _analyze_results([], 10.0, 50)
        assert "error" in report

    def test_basic_analysis(self):
        from scripts.load_test import RequestResult, _analyze_results

        results = [
            RequestResult(
                endpoint="health",
                method="GET",
                status=200,
                latency_ms=float(i),
            )
            for i in range(1, 101)
        ]
        report = _analyze_results(results, 10.0, 50)

        assert report["summary"]["total_requests"] == 100
        assert report["summary"]["rps"] == 10.0
        assert report["summary"]["error_rate"] == 0
        assert report["summary"]["total_errors"] == 0
        assert report["latency"]["p50_ms"] > 0
        assert report["latency"]["p95_ms"] > report["latency"]["p50_ms"]
        assert "health" in report["by_endpoint"]

    def test_error_counting(self):
        from scripts.load_test import RequestResult, _analyze_results

        results = [
            RequestResult(
                endpoint="test",
                method="GET",
                status=200,
                latency_ms=10.0,
            )
            for _ in range(90)
        ] + [
            RequestResult(
                endpoint="test",
                method="GET",
                status=500,
                latency_ms=100.0,
                error="HTTP 500",
            )
            for _ in range(10)
        ]
        report = _analyze_results(results, 5.0, 20)

        assert report["summary"]["total_errors"] == 10
        assert report["summary"]["error_rate"] == 10.0
        ep = report["by_endpoint"]["test"]
        assert ep["errors"] == 10
        assert ep["error_rate"] == 10.0

    def test_warmup_excluded(self):
        from scripts.load_test import RequestResult, _analyze_results

        results = [
            RequestResult(endpoint="warmup", method="GET", status=200, latency_ms=5.0),
            RequestResult(endpoint="health", method="GET", status=200, latency_ms=10.0),
        ]
        report = _analyze_results(results, 1.0, 1)
        assert report["summary"]["total_requests"] == 1
        assert "warmup" not in report["by_endpoint"]

    def test_multi_endpoint_breakdown(self):
        from scripts.load_test import RequestResult, _analyze_results

        results = []
        for ep in ["health", "list_agents", "list_sessions"]:
            for i in range(20):
                results.append(
                    RequestResult(
                        endpoint=ep,
                        method="GET",
                        status=200,
                        latency_ms=float(i + 1),
                    ),
                )
        report = _analyze_results(results, 5.0, 10)
        assert len(report["by_endpoint"]) == 3
        for _ep_name, ep_stats in report["by_endpoint"].items():
            assert ep_stats["total"] == 20


class TestPrintReport:
    def test_print_report_no_error(self, capsys):
        from scripts.load_test import print_report

        report = {
            "summary": {
                "total_requests": 1000,
                "total_time_s": 10.0,
                "concurrency": 100,
                "rps": 100.0,
                "error_rate": 0.5,
                "total_errors": 5,
            },
            "latency": {
                "p50_ms": 10.0,
                "p95_ms": 50.0,
                "p99_ms": 100.0,
                "max_ms": 200.0,
                "avg_ms": 25.0,
            },
            "by_endpoint": {
                "health": {
                    "total": 500,
                    "success": 498,
                    "errors": 2,
                    "error_rate": 0.4,
                    "p50_ms": 8.0,
                    "p95_ms": 40.0,
                    "p99_ms": 90.0,
                    "max_ms": 180.0,
                },
            },
        }
        print_report(report)
        captured = capsys.readouterr()
        assert "压力测试报告" in captured.out
        assert "1000" in captured.out
        assert "容量规划建议" in captured.out

    def test_print_report_with_error(self, capsys):
        from scripts.load_test import print_report

        print_report({"error": "无结果"})
        captured = capsys.readouterr()
        assert "无结果" in captured.out

    def test_high_error_rate_warning(self, capsys):
        from scripts.load_test import print_report

        report = {
            "summary": {
                "total_requests": 100,
                "total_time_s": 10.0,
                "concurrency": 10,
                "rps": 10.0,
                "error_rate": 6.0,
                "total_errors": 6,
            },
            "latency": {
                "p50_ms": 10.0,
                "p95_ms": 50.0,
                "p99_ms": 1200.0,
                "max_ms": 2000.0,
                "avg_ms": 50.0,
            },
            "by_endpoint": {},
        }
        print_report(report)
        captured = capsys.readouterr()
        assert "警告" in captured.out
