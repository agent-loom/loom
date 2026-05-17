"""GitLab API 适配器，实现 ScmAdapter Protocol。"""

from __future__ import annotations

from typing import Any

import httpx

from agent_platform.integrations.errors import ScmError
from agent_platform.integrations.http_client import HttpClient
from agent_platform.integrations.scm.protocol import MergeRequestResult


class GitLabAdapter:
    """GitLab API 异步适配器，支持分支、MR、Pipeline 等操作。

    Uses ``HttpClient`` internally for connection pooling and retry.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._http = HttpClient(
            base_url=self.base_url,
            headers={"PRIVATE-TOKEN": token, "Content-Type": "application/json"},
            transport=transport,
            max_retries=max_retries,
        )

    @property
    def headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"}

    async def close(self) -> None:
        await self._http.close()

    async def create_branch(
        self,
        project_id: str,
        branch: str,
        *,
        ref: str = "main",
    ) -> dict[str, Any]:
        return await self._http.request(
            "POST",
            f"/api/v4/projects/{project_id}/repository/branches",
            params={"branch": branch, "ref": ref},
            error_cls=ScmError,
        )

    async def create_merge_request(
        self,
        project_id: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
        reviewer_ids: list[int] | None = None,
    ) -> MergeRequestResult:
        payload: dict[str, Any] = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "description": description,
        }
        if labels:
            payload["labels"] = ",".join(labels)
        if reviewer_ids:
            payload["reviewer_ids"] = reviewer_ids

        raw = await self._http.request(
            "POST",
            f"/api/v4/projects/{project_id}/merge_requests",
            json=payload,
            error_cls=ScmError,
        )
        return MergeRequestResult(
            mr_id=raw.get("iid", 0),
            url=raw.get("web_url", ""),
            source_branch=source_branch,
            target_branch=target_branch,
            raw=raw,
        )

    async def get_merge_request(
        self,
        project_id: str,
        mr_iid: int,
    ) -> dict[str, Any]:
        return await self._http.request(
            "GET",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}",
            error_cls=ScmError,
        )

    async def comment_merge_request(
        self,
        project_id: str,
        mr_iid: int,
        body: str,
    ) -> dict[str, Any]:
        return await self._http.request(
            "POST",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes",
            json={"body": body},
            error_cls=ScmError,
        )

    async def get_latest_pipeline(
        self, project_id: str, ref: str,
    ) -> dict[str, Any] | None:
        pipelines = await self._http.request(
            "GET",
            f"/api/v4/projects/{project_id}/pipelines",
            params={"ref": ref, "per_page": 1},
            error_cls=ScmError,
        )
        if not pipelines:
            return None
        return pipelines[0]

    async def get_pipeline_status(
        self, project_id: str, ref: str,
    ) -> str | None:
        pipeline = await self.get_latest_pipeline(project_id, ref)
        if not pipeline:
            return None
        return pipeline.get("status")

    async def update_commit_status(
        self,
        project_id: str,
        sha: str,
        state: str,
        *,
        name: str = "agent-platform/eval",
        description: str = "",
        target_url: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": state,
            "name": name,
            "description": description,
        }
        if target_url:
            payload["target_url"] = target_url
        return await self._http.request(
            "POST",
            f"/api/v4/projects/{project_id}/statuses/{sha}",
            json=payload,
            error_cls=ScmError,
        )

    async def download_artifacts(
        self,
        project_id: str,
        job_id: int,
    ) -> bytes:
        try:
            client = self._http._get_client()
            response = await client.get(
                f"/api/v4/projects/{project_id}/jobs/{job_id}/artifacts"
            )
            response.raise_for_status()
            return response.content
        except Exception as exc:
            raise ScmError(f"下载 artifacts 失败 (job={job_id}): {exc}") from exc
