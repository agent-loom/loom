#!/usr/bin/env python3
"""Promote agent to production with traffic percentage (canary)."""
import argparse
import json
import subprocess
import sys

import httpx


def detect_changed_agents() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD~1"],
        capture_output=True,
        text=True,
        check=False,
    )
    agents: set[str] = set()
    for line in result.stdout.strip().splitlines():
        if line.startswith("agents/"):
            parts = line.split("/")
            if len(parts) >= 2:
                agents.add(parts[1])
    return sorted(agents)


def promote_agent(base_url: str, agent_id: str, version: str, traffic: int) -> dict:
    url = f"{base_url}/api/v1/agent-packages/{agent_id}/versions/{version}/deploy"
    payload = {"channel": "prod", "traffic_percent": traffic}
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Promote agent to production")
    parser.add_argument("--agent", required=True, help="Agent ID")
    parser.add_argument("--channel", default="prod", choices=["prod"])
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--traffic", type=int, default=5, help="Traffic percentage (1-100)")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    agents = detect_changed_agents() if args.agent == "changed" else [args.agent]
    if not agents:
        print("No changed agents detected.")
        return

    results = []
    for agent_id in agents:
        print(f"Promoting {agent_id}@{args.version} to prod at {args.traffic}% traffic...")
        try:
            result = promote_agent(args.base_url, agent_id, args.version, args.traffic)
            status = result.get("status", "unknown")
            deployment_id = result.get("deployment_id", "unknown")
            print(f"  ok: {deployment_id} status={status} traffic={args.traffic}%")
            results.append({"agent_id": agent_id, "status": "promoted", "detail": result})
        except httpx.HTTPStatusError as exc:
            print(f"  failed: {exc.response.status_code} {exc.response.text}")
            sys.exit(1)
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
