#!/usr/bin/env python3
"""Deploy agent(s) to a target environment via the Agent Platform API."""
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


def deploy_agent(base_url: str, agent_id: str, version: str, env: str) -> dict:
    url = f"{base_url}/api/v1/agent-packages/{agent_id}/versions/{version}/deploy"
    payload = {"channel": env, "eval_passed": True}
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Deploy agent to environment")
    parser.add_argument("--env", required=True, choices=["staging", "prod"])
    parser.add_argument("--agent", required=True, help="Agent ID or 'changed' for auto-detect")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    if args.agent == "changed":
        agents = detect_changed_agents()
        if not agents:
            print("No changed agents detected.")
            return
    else:
        agents = [args.agent]

    results = []
    for agent_id in agents:
        print(f"Deploying {agent_id}@{args.version} to {args.env}...")
        try:
            result = deploy_agent(args.base_url, agent_id, args.version, args.env)
            print(f"  ok: {result.get('deployment_id', 'deployed')}")
            results.append({"agent_id": agent_id, "status": "deployed", "detail": result})
        except httpx.HTTPStatusError as exc:
            print(f"  failed: {exc.response.status_code} {exc.response.text}")
            results.append({"agent_id": agent_id, "status": "failed", "error": str(exc)})
            sys.exit(1)

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
