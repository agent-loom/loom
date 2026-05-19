#!/usr/bin/env python3
"""DevFlow HTTP Webhook 真实 E2E 验证。

这个脚本要求 agent-platform API 服务已经启动。它通过真实 HTTP endpoint
`/api/v1/integrations/plane/webhook` 触发 DevFlow，而不是直接调用
DevFlowOrchestrator，因此可以覆盖：

- API 鉴权
- Plane webhook HMAC 签名校验
- delivery 幂等记录
- FastAPI BackgroundTasks
- DevFlowOrchestrator -> GitLab MR -> CodingAgentRunner
- Plane/GitLab 回写

用法：
  uv run python scripts/devflow_webhook_real_e2e.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from typing import Any

import httpx
from dotenv import load_dotenv

from agent_platform.integrations.plane.adapter import PlaneAdapter

passed = 0
failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS  {label}")
        return
    failed += 1
    msg = f"  FAIL  {label}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DevFlow HTTP webhook 真实 E2E")
    parser.add_argument(
        "--base-url",
        default=os.getenv("AGENT_PLATFORM_BASE_URL", "http://localhost:8000"),
        help="agent-platform API 地址，默认 http://localhost:8000。",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("DEVFLOW_WEBHOOK_E2E_TIMEOUT_SECONDS", "900")),
        help="等待 DevFlow 完成的最长秒数，默认 900。",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.getenv("DEVFLOW_WEBHOOK_E2E_POLL_INTERVAL", "5")),
        help="轮询间隔秒数，默认 5。",
    )
    parser.add_argument(
        "--require-commit",
        action="store_true",
        help="要求 MR head sha 非空，并且 GitLab MR 上有 Runner 报告。",
    )
    return parser.parse_args()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"缺少环境变量: {name}")
    return value


def _signature(secret: str | None, raw_body: bytes) -> str | None:
    if not secret:
        return None
    digest = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _comment_text(comment: dict[str, Any]) -> str:
    for key in ("body", "comment_html", "comment_stripped", "comment"):
        value = comment.get(key)
        if isinstance(value, str):
            return value
    return str(comment)


async def _get_gitlab_mr_by_branch(
    *,
    gitlab_base: str,
    token: str,
    project_id: str,
    branch: str,
) -> dict[str, Any] | None:
    async with httpx.AsyncClient(headers={"PRIVATE-TOKEN": token}, timeout=15) as client:
        response = await client.get(
            f"{gitlab_base}/api/v4/projects/{project_id}/merge_requests",
            params={"source_branch": branch, "state": "opened"},
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list) and data:
            return data[0]
    return None


async def _get_gitlab_mr_notes(
    *,
    gitlab_base: str,
    token: str,
    project_id: str,
    mr_iid: int,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(headers={"PRIVATE-TOKEN": token}, timeout=15) as client:
        response = await client.get(
            f"{gitlab_base}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, list) else []


async def _get_plane_comments(
    *,
    plane_base: str,
    plane_key: str,
    plane_slug: str,
    project_id: str,
    work_item_id: str,
) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(headers={"X-API-Key": plane_key}, timeout=15) as client:
        response = await client.get(
            f"{plane_base}/api/v1/workspaces/{plane_slug}/projects/{project_id}"
            f"/work-items/{work_item_id}/comments/"
        )
        response.raise_for_status()
        data = response.json()
    if isinstance(data, dict):
        results = data.get("results")
        if isinstance(results, list):
            return results
        comments = data.get("comments")
        if isinstance(comments, list):
            return comments
        return []
    return data if isinstance(data, list) else []


async def main() -> None:
    load_dotenv(override=True)
    args = _parse_args()

    plane_base = _required_env("PLANE_BASE_URL")
    plane_key = _required_env("PLANE_API_KEY")
    plane_slug = _required_env("PLANE_WORKSPACE_SLUG")
    plane_project_id = _required_env("PLANE_PROJECT_ID")
    gitlab_base = _required_env("GITLAB_BASE_URL").rstrip("/")
    gitlab_token = _required_env("GITLAB_TOKEN")
    gitlab_project_id = _required_env("GITLAB_PROJECT_ID")
    api_key = os.getenv("AGENT_PLATFORM_API_KEY")
    webhook_secret = os.getenv("PLANE_WEBHOOK_SECRET")

    print("=" * 60)
    print("DevFlow HTTP Webhook 真实 E2E")
    print("=" * 60)
    print(f"Platform: {args.base_url}")
    print(f"Plane:    {plane_base} / {plane_slug} / {plane_project_id}")
    print(f"GitLab:   {gitlab_base} / project {gitlab_project_id}")

    plane = PlaneAdapter(
        base_url=plane_base,
        api_key=plane_key,
        workspace_slug=plane_slug,
    )

    work_item_id: str | None = None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            health = await client.get(f"{args.base_url.rstrip('/')}/health")
            _check(
                "agent-platform /health 可达",
                health.status_code == 200,
                f"status={health.status_code}",
            )

        ts = int(time.time())
        title = f"DevFlow HTTP Webhook E2E {ts}"
        requirement = os.getenv(
            "DEVFLOW_TEST_REQUIREMENT",
            (
                "DevFlow HTTP Webhook E2E。请只做一个最小、可验证的 Echo Agent 变更："
                "在 agents/echo/prompts/orchestrator.md 末尾追加一行 "
                "'DevFlow Webhook E2E marker'，不要修改无关模块。"
            ),
        )

        work_item = await plane.create_work_item(
            plane_project_id,
            name=title,
            description=f"<p>{requirement}</p>",
        )
        work_item_id = work_item.get("id")
        _check("Plane 工作项创建成功", bool(work_item_id), str(work_item))
        if not work_item_id:
            sys.exit(1)

        branch = f"feat/{work_item_id}"
        payload = {
            "data": {
                "id": work_item_id,
                "project": plane_project_id,
                "name": title,
                "description_stripped": requirement,
                "state_detail": {"name": "Ready for AI Dev"},
                "properties": {
                    "agent_id": os.getenv("DEVFLOW_TEST_AGENT_ID", "echo"),
                    "task_type": os.getenv("DEVFLOW_TEST_TASK_TYPE", "agent:change"),
                },
            }
        }
        raw_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        delivery_id = f"devflow-webhook-e2e-{work_item_id}-{ts}"
        headers = {
            "content-type": "application/json",
            "x-plane-delivery": delivery_id,
            "x-plane-event": "work_item.updated",
        }
        if api_key:
            headers["x-api-key"] = api_key
        sig = _signature(webhook_secret, raw_body)
        if sig:
            headers["x-plane-signature"] = sig

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{args.base_url.rstrip('/')}/api/v1/integrations/plane/webhook",
                content=raw_body,
                headers=headers,
            )
        _check(
            "Plane webhook endpoint accepted",
            response.status_code == 200,
            f"status={response.status_code}, body={response.text[:500]}",
        )
        if response.status_code != 200:
            sys.exit(1)
        body = response.json()
        _check("webhook devflow 已入队", body.get("devflow_status") == "queued", str(body))

        print("\n--- 等待 GitLab MR 和回写 ---")
        deadline = time.monotonic() + args.timeout
        mr: dict[str, Any] | None = None
        notes: list[dict[str, Any]] = []
        plane_comments: list[dict[str, Any]] = []

        while time.monotonic() < deadline:
            mr = await _get_gitlab_mr_by_branch(
                gitlab_base=gitlab_base,
                token=gitlab_token,
                project_id=gitlab_project_id,
                branch=branch,
            )
            if mr:
                notes = await _get_gitlab_mr_notes(
                    gitlab_base=gitlab_base,
                    token=gitlab_token,
                    project_id=gitlab_project_id,
                    mr_iid=int(mr["iid"]),
                )
                plane_comments = await _get_plane_comments(
                    plane_base=plane_base,
                    plane_key=plane_key,
                    plane_slug=plane_slug,
                    project_id=plane_project_id,
                    work_item_id=work_item_id,
                )
                note_text = "\n".join(_comment_text(note) for note in notes)
                plane_text = "\n".join(_comment_text(comment) for comment in plane_comments)
                if "DevFlow Runner" in note_text and "DevFlow Runner" in plane_text:
                    break
            await asyncio.sleep(args.poll_interval)

        _check("GitLab MR 已创建", mr is not None)
        if mr:
            _check("MR source branch 匹配", mr.get("source_branch") == branch)
            _check("MR head sha 非空", bool(mr.get("sha")))
            print(f"  MR: {mr.get('web_url')}")
            if args.require_commit:
                _check("MR head sha 满足 require-commit", bool(mr.get("sha")))

        note_text = "\n".join(_comment_text(note) for note in notes)
        plane_text = "\n".join(_comment_text(comment) for comment in plane_comments)
        _check("GitLab MR 有 Runner 报告评论", "DevFlow Runner" in note_text)
        _check("Plane 有 MR 创建评论", "MR created" in plane_text or "MR" in plane_text)
        _check("Plane 有 Runner 报告评论", "DevFlow Runner" in plane_text)

    finally:
        await plane.close()

    print("\n" + "=" * 60)
    print(f"结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
    print("=" * 60)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
