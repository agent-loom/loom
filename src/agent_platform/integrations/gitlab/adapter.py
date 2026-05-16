"""GitLab API 适配器，封装 GitLab REST API 常用操作。"""

from typing import Any

import httpx


class GitLabAdapter:
    """GitLab API 异步适配器，支持分支、MR、Pipeline 等操作。"""
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        """初始化 GitLab 适配器。"""
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport

    @property
    def headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"}

    async def create_branch(
        self,
        project_id: str,
        branch: str,
        ref: str = "main",
    ) -> dict[str, Any]:
        """基于指定 ref 创建新分支。"""
        return await self._request(
            "POST",
            f"/api/v4/projects/{project_id}/repository/branches",
            params={"branch": branch, "ref": ref},
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
    ) -> dict[str, Any]:
        """创建合并请求 (Merge Request)。"""
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

        return await self._request(
            "POST",
            f"/api/v4/projects/{project_id}/merge_requests",
            json=payload,
        )

    async def get_merge_request(
        self,
        project_id: str,
        mr_iid: int,
    ) -> dict[str, Any]:
        """获取指定 MR 的详情。"""
        return await self._request(
            "GET",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}",
        )

    async def comment_merge_request(
        self,
        project_id: str,
        mr_iid: int,
        body: str,
    ) -> dict[str, Any]:
        """在 MR 上添加评论。"""
        return await self._request(
            "POST",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )

    async def get_latest_pipeline(self, project_id: str, ref: str) -> dict[str, Any] | None:
        """获取指定 ref 上最新的 Pipeline 信息。"""
        pipelines = await self._request(
            "GET",
            f"/api/v4/projects/{project_id}/pipelines",
            params={"ref": ref, "per_page": 1},
        )
        if not pipelines:
            return None
        return pipelines[0]

    async def get_pipeline_status(self, project_id: str, ref: str) -> str | None:
        """获取指定 ref 上最新 Pipeline 的状态字符串。"""
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
        """更新指定 commit 的构建状态。"""
        payload: dict[str, Any] = {
            "state": state,
            "name": name,
            "description": description,
        }
        if target_url:
            payload["target_url"] = target_url
        return await self._request(
            "POST",
            f"/api/v4/projects/{project_id}/statuses/{sha}",
            json=payload,
        )

    async def download_artifacts(
        self,
        project_id: str,
        job_id: int,
    ) -> bytes:
        """下载指定 Job 的构建产物。"""
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=60,
            transport=self.transport,
        ) as client:
            response = await client.get(
                f"/api/v4/projects/{project_id}/jobs/{job_id}/artifacts"
            )
            response.raise_for_status()
            return response.content

    async def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=self.headers,
            timeout=20,
            transport=self.transport,
        ) as client:
            response = await client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()
