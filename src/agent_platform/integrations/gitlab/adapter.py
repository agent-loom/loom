from typing import Any

import httpx


class GitLabAdapter:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
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

    async def comment_merge_request(
        self,
        project_id: str,
        mr_iid: int,
        body: str,
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes",
            json={"body": body},
        )

    async def get_latest_pipeline(self, project_id: str, ref: str) -> dict[str, Any] | None:
        pipelines = await self._request(
            "GET",
            f"/api/v4/projects/{project_id}/pipelines",
            params={"ref": ref, "per_page": 1},
        )
        if not pipelines:
            return None
        return pipelines[0]

    async def get_pipeline_status(self, project_id: str, ref: str) -> str | None:
        pipeline = await self.get_latest_pipeline(project_id, ref)
        if not pipeline:
            return None
        return pipeline.get("status")

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
