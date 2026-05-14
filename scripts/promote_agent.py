#!/usr/bin/env python3
"""Promote agent to production with traffic percentage (canary)."""
import argparse
import json
import sys

import httpx


def promote_agent(base_url: str, agent_id: str, version: str, traffic: int) -> dict:
    url = f"{base_url}/api/v1/agent-packages/{agent_id}/versions/{version}/deploy"
    payload = {"channel": "prod", "traffic_percent": traffic, "eval_passed": True}
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def main():
    parser = argparse.ArgumentParser(description="Promote agent to production")
    parser.add_argument("--agent", required=True, help="Agent ID")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--traffic", type=int, default=5, help="Traffic percentage (1-100)")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    print(f"Promoting {args.agent}@{args.version} to prod at {args.traffic}% traffic...")
    try:
        result = promote_agent(args.base_url, args.agent, args.version, args.traffic)
        status = result.get("status", "unknown")
        deployment_id = result.get("deployment_id", "unknown")
        print(f"  ok: {deployment_id} status={status} traffic={args.traffic}%")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except httpx.HTTPStatusError as exc:
        print(f"  failed: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()
