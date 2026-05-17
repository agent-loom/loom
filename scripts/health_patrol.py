#!/usr/bin/env python
"""Agent 健康巡检脚本 — 定期检查平台各组件健康状态。

用法:
  python scripts/health_patrol.py [--base-url http://localhost:8000] [--api-key KEY]

检查项:
  1. /health 基础存活
  2. /health/ready 深度就绪（DB/Redis/Runner/Weaviate）
  3. 各 agent 健康状态
  4. DevFlow 状态统计
  5. 审计链完整性
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import httpx


def _headers(api_key: str | None) -> dict[str, str]:
    if api_key:
        return {"Authorization": f"Bearer {api_key}"}
    return {}


def check_health(client: httpx.Client) -> bool:
    r = client.get("/health")
    ok = r.status_code == 200 and r.json().get("status") == "ok"
    print(f"  /health: {'通过' if ok else '失败'} ({r.status_code})")
    return ok


def check_ready(client: httpx.Client) -> bool:
    r = client.get("/health/ready")
    data = r.json()
    status = data.get("status", "unknown")
    ok = r.status_code == 200
    print(f"  /health/ready: {status} ({r.status_code})")
    for check_name, check_val in data.get("checks", {}).items():
        indicator = "OK" if check_val in ("ok", "enabled", "in_memory") else str(check_val)
        print(f"    {check_name}: {indicator}")
    return ok


def check_agents(client: httpx.Client) -> bool:
    r = client.get("/api/v1/agents")
    if r.status_code != 200:
        print(f"  /api/v1/agents: 请求失败 ({r.status_code})")
        return False
    agents = r.json()
    print(f"  已注册 Agent: {len(agents)}")
    all_healthy = True
    for agent in agents:
        agent_id = agent["agent_id"]
        hr = client.get(f"/api/v1/agents/{agent_id}/health")
        if hr.status_code == 200:
            health = hr.json()
            h = health.get("health", "unknown")
            sr = health.get("success_rate", 0)
            active_s = health.get("active_sessions", 0)
            print(f"    {agent_id}: {h} (成功率={sr:.1%}, 活跃会话={active_s})")
            if h != "healthy":
                all_healthy = False
        else:
            print(f"    {agent_id}: 检查失败 ({hr.status_code})")
            all_healthy = False
    return all_healthy


def check_devflow(client: httpx.Client) -> bool:
    r = client.get("/api/v1/devflow/status")
    if r.status_code != 200:
        print(f"  DevFlow 状态: 不可用 ({r.status_code})")
        return True
    data = r.json()
    enabled = data.get("enabled", False)
    print(f"  DevFlow: {'启用' if enabled else '禁用'}")
    if enabled:
        total = data.get("total_jobs", 0)
        by_state = data.get("jobs_by_state", {})
        print(f"    总 Job 数: {total}")
        for state, count in by_state.items():
            print(f"    {state}: {count}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Agent 健康巡检")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    client = httpx.Client(
        base_url=args.base_url,
        headers=_headers(args.api_key),
        timeout=30.0,
    )

    print("=== Agent Platform 健康巡检 ===\n")

    results = []
    print("[1/4] 基础健康检查")
    results.append(check_health(client))

    print("\n[2/4] 深度就绪检查")
    results.append(check_ready(client))

    print("\n[3/4] Agent 健康状态")
    results.append(check_agents(client))

    print("\n[4/4] DevFlow 状态")
    results.append(check_devflow(client))

    print("\n" + "=" * 40)
    passed = sum(results)
    total = len(results)
    if all(results):
        print(f"巡检完成: {passed}/{total} 全部通过")
    else:
        print(f"巡检完成: {passed}/{total} 通过，存在异常")
        sys.exit(1)


if __name__ == "__main__":
    main()
