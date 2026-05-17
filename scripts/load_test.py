#!/usr/bin/env python
"""压力测试与容量规划 — 模拟大量并发请求，测量吞吐量/延迟/错误率。

用法:
  python scripts/load_test.py [--base-url http://localhost:8000] [--concurrency 100]
                              [--duration 30] [--api-key KEY]

输出:
  - 每秒请求数 (RPS)
  - 延迟分布: P50 / P95 / P99 / Max
  - 错误率
  - 按端点统计
  - 容量规划建议
"""

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx


@dataclass
class RequestResult:
    endpoint: str
    method: str
    status: int
    latency_ms: float
    error: str | None = None


@dataclass
class EndpointStats:
    name: str
    total: int = 0
    success: int = 0
    errors: int = 0
    latencies: list[float] = field(default_factory=list)

    @property
    def error_rate(self) -> float:
        return self.errors / self.total if self.total > 0 else 0.0

    @property
    def p50(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.50)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99(self) -> float:
        if not self.latencies:
            return 0.0
        sorted_lat = sorted(self.latencies)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def max_latency(self) -> float:
        return max(self.latencies) if self.latencies else 0.0

    @property
    def avg_latency(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0.0


SCENARIOS = [
    ("GET", "/health", None, "health"),
    ("GET", "/health/ready", None, "health_ready"),
    ("GET", "/api/v1/agents", None, "list_agents"),
    ("GET", "/api/v1/sessions", None, "list_sessions"),
    ("GET", "/api/v1/agent-runs", None, "list_runs"),
    ("GET", "/api/v1/agent-deployments", None, "list_deployments"),
]


async def send_request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    body: dict | None,
    label: str,
) -> RequestResult:
    start = time.monotonic()
    try:
        if method == "GET":
            resp = await client.get(path)
        else:
            resp = await client.post(path, json=body)
        elapsed = (time.monotonic() - start) * 1000
        error = None if resp.status_code < 500 else f"HTTP {resp.status_code}"
        return RequestResult(
            endpoint=label,
            method=method,
            status=resp.status_code,
            latency_ms=elapsed,
            error=error,
        )
    except Exception as e:
        elapsed = (time.monotonic() - start) * 1000
        return RequestResult(
            endpoint=label,
            method=method,
            status=0,
            latency_ms=elapsed,
            error=str(e),
        )


async def worker(
    client: httpx.AsyncClient,
    results: list[RequestResult],
    stop_event: asyncio.Event,
    worker_id: int,
):
    scenario_count = len(SCENARIOS)
    idx = worker_id % scenario_count
    while not stop_event.is_set():
        method, path, body, label = SCENARIOS[idx % scenario_count]
        result = await send_request(client, method, path, body, label)
        results.append(result)
        idx += 1
        await asyncio.sleep(0.01)


async def run_load_test(
    base_url: str,
    concurrency: int,
    duration: int,
    api_key: str | None,
) -> dict:
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    results: list[RequestResult] = []
    stop_event = asyncio.Event()

    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        timeout=30.0,
        limits=httpx.Limits(
            max_connections=concurrency + 10,
            max_keepalive_connections=concurrency,
        ),
    ) as client:
        # 预热
        print(f"预热: 发送 {min(10, concurrency)} 个请求...")
        warmup_tasks = [
            send_request(client, "GET", "/health", None, "warmup")
            for _ in range(min(10, concurrency))
        ]
        await asyncio.gather(*warmup_tasks)

        print(f"开始压力测试: {concurrency} 并发, {duration}s 持续时间")
        print(f"目标: {base_url}")
        print("-" * 60)

        start_time = time.monotonic()
        workers = [
            asyncio.create_task(worker(client, results, stop_event, i))
            for i in range(concurrency)
        ]

        await asyncio.sleep(duration)
        stop_event.set()
        await asyncio.gather(*workers, return_exceptions=True)
        total_time = time.monotonic() - start_time

    return _analyze_results(results, total_time, concurrency)


def _analyze_results(
    results: list[RequestResult],
    total_time: float,
    concurrency: int,
) -> dict:
    if not results:
        return {"error": "无结果"}

    by_endpoint: dict[str, EndpointStats] = {}
    all_latencies: list[float] = []
    total_errors = 0

    for r in results:
        if r.endpoint == "warmup":
            continue
        if r.endpoint not in by_endpoint:
            by_endpoint[r.endpoint] = EndpointStats(name=r.endpoint)
        stats = by_endpoint[r.endpoint]
        stats.total += 1
        stats.latencies.append(r.latency_ms)
        all_latencies.append(r.latency_ms)
        if r.error:
            stats.errors += 1
            total_errors += 1
        else:
            stats.success += 1

    total_requests = len(all_latencies)
    rps = total_requests / total_time if total_time > 0 else 0

    sorted_all = sorted(all_latencies) if all_latencies else [0]
    p50_idx = int(len(sorted_all) * 0.50)
    p95_idx = int(len(sorted_all) * 0.95)
    p99_idx = int(len(sorted_all) * 0.99)

    return {
        "summary": {
            "total_requests": total_requests,
            "total_time_s": round(total_time, 2),
            "concurrency": concurrency,
            "rps": round(rps, 1),
            "error_rate": round(total_errors / total_requests * 100, 2) if total_requests else 0,
            "total_errors": total_errors,
        },
        "latency": {
            "p50_ms": round(sorted_all[min(p50_idx, len(sorted_all) - 1)], 2),
            "p95_ms": round(sorted_all[min(p95_idx, len(sorted_all) - 1)], 2),
            "p99_ms": round(sorted_all[min(p99_idx, len(sorted_all) - 1)], 2),
            "max_ms": round(max(sorted_all), 2),
            "avg_ms": round(statistics.mean(all_latencies), 2) if all_latencies else 0,
        },
        "by_endpoint": {
            name: {
                "total": s.total,
                "success": s.success,
                "errors": s.errors,
                "error_rate": round(s.error_rate * 100, 2),
                "p50_ms": round(s.p50, 2),
                "p95_ms": round(s.p95, 2),
                "p99_ms": round(s.p99, 2),
                "max_ms": round(s.max_latency, 2),
            }
            for name, s in sorted(by_endpoint.items())
        },
    }


def print_report(report: dict) -> None:
    if "error" in report:
        print(f"错误: {report['error']}")
        return

    s = report["summary"]
    lat = report["latency"]

    print("\n" + "=" * 60)
    print("                   压力测试报告")
    print("=" * 60)

    print(f"\n总请求数:      {s['total_requests']}")
    print(f"测试时长:      {s['total_time_s']}s")
    print(f"并发数:        {s['concurrency']}")
    print(f"吞吐量 (RPS):  {s['rps']}")
    print(f"错误率:        {s['error_rate']}% ({s['total_errors']} 个错误)")

    print(f"\n{'延迟分布':=^58}")
    print(f"  P50:  {lat['p50_ms']:.2f} ms")
    print(f"  P95:  {lat['p95_ms']:.2f} ms")
    print(f"  P99:  {lat['p99_ms']:.2f} ms")
    print(f"  Max:  {lat['max_ms']:.2f} ms")
    print(f"  Avg:  {lat['avg_ms']:.2f} ms")

    print(f"\n{'端点明细':=^58}")
    fmt = "{:<20} {:>6} {:>6} {:>6} {:>8} {:>8} {:>8}"
    header = fmt.format("端点", "总数", "成功", "错误", "P50(ms)", "P95(ms)", "P99(ms)")
    print(header)
    print("-" * len(header))
    for name, ep in report["by_endpoint"].items():
        print(
            f"{name:<20} {ep['total']:>6} {ep['success']:>6} "
            f"{ep['errors']:>6} {ep['p50_ms']:>8.2f} "
            f"{ep['p95_ms']:>8.2f} {ep['p99_ms']:>8.2f}"
        )

    print(f"\n{'容量规划建议':=^56}")
    rps = s["rps"]
    p99 = lat["p99_ms"]
    err_rate = s["error_rate"]

    if err_rate > 5:
        print("  [警告] 错误率超过 5%，存在稳定性问题，需排查瓶颈")
    elif err_rate > 1:
        print("  [注意] 错误率超过 1%，建议增加资源或优化热路径")
    else:
        print("  [良好] 错误率低于 1%")

    if p99 > 1000:
        print("  [警告] P99 延迟超过 1s，存在长尾问题")
    elif p99 > 500:
        print("  [注意] P99 延迟超过 500ms，可考虑优化")
    else:
        print("  [良好] P99 延迟在可接受范围内")

    estimated_daily = rps * 86400
    print(f"\n  预估日处理能力: ~{estimated_daily:,.0f} 请求/天")
    print(f"  单实例 RPS:     ~{rps:.0f}")
    print(f"  建议横向扩展:   {max(1, int(1000 / rps))} 实例可达 ~1000 RPS")

    print("\n" + "=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Agent Platform 压力测试")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--concurrency", type=int, default=100, help="并发数")
    parser.add_argument("--duration", type=int, default=30, help="测试持续秒数")
    parser.add_argument("--api-key", default=None, help="API Key")
    args = parser.parse_args()

    print("=== Agent Platform 压力测试与容量规划 ===\n")
    report = asyncio.run(run_load_test(
        base_url=args.base_url,
        concurrency=args.concurrency,
        duration=args.duration,
        api_key=args.api_key,
    ))
    print_report(report)


if __name__ == "__main__":
    main()
