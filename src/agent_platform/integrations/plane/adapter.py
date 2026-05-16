"""Plane 项目管理平台 API 适配器。"""

from typing import Any

import httpx


class PlaneAdapter:
    """Plane API 异步适配器，支持工作项的增删改查。"""
    def __init__(
        self,
        base_url: str,
        api_key: str,
        workspace_slug: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        """初始化 Plane 适配器。"""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.workspace_slug = workspace_slug
        self.transport = transport

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key, "Content-Type": "application/json"}

    async def list_projects(self) -> dict[str, Any]:
        """列出工作空间下的所有项目。"""
        return await self._request("GET", f"/api/v1/workspaces/{self.workspace_slug}/projects/")

    async def list_work_items(self, project_id: str) -> dict[str, Any]:
        """列出指定项目下的所有工作项。"""
        path = f"/api/v1/workspaces/{self.workspace_slug}/projects/{project_id}/work-items/"
        return await self._request("GET", path)

    async def get_work_item(self, project_id: str, work_item_id: str) -> dict[str, Any]:
        """获取单个工作项的详情。"""
        path = (
            f"/api/v1/workspaces/{self.workspace_slug}/projects/{project_id}"
            f"/work-items/{work_item_id}/"
        )
        return await self._request("GET", path)

    async def search_work_items(self, project_id: str, query: str) -> dict[str, Any]:
        """按关键词搜索工作项。"""
        path = f"/api/v1/workspaces/{self.workspace_slug}/projects/{project_id}/work-items/"
        return await self._request("GET", path, params={"search": query})

    async def create_work_item(
        self,
        project_id: str,
        *,
        name: str,
        description: str = "",
        state_id: str | None = None,
        priority: str | None = None,
        labels: list[str] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建新的工作项。"""
        path = f"/api/v1/workspaces/{self.workspace_slug}/projects/{project_id}/work-items/"
        payload: dict[str, Any] = {"name": name, "description_html": description}
        if state_id:
            payload["state"] = state_id
        if priority:
            payload["priority"] = priority
        if labels:
            payload["labels"] = labels
        if properties:
            payload["properties"] = properties
        return await self._request("POST", path, json=payload)

    async def add_comment(self, project_id: str, work_item_id: str, body: str) -> dict[str, Any]:
        """为工作项添加评论。"""
        path = (
            f"/api/v1/workspaces/{self.workspace_slug}/projects/{project_id}"
            f"/work-items/{work_item_id}/comments/"
        )
        return await self._request("POST", path, json={"comment_html": body})

    async def update_work_item(
        self,
        project_id: str,
        work_item_id: str,
        **fields,
    ) -> dict[str, Any]:
        """更新工作项的指定字段。"""
        path = (
            f"/api/v1/workspaces/{self.workspace_slug}/projects/{project_id}"
            f"/work-items/{work_item_id}/"
        )
        return await self._request("PATCH", path, json=fields)

    async def update_work_item_state(
        self,
        project_id: str,
        work_item_id: str,
        state_id: str,
    ) -> dict[str, Any]:
        """更新工作项的状态。"""
        return await self.update_work_item(project_id, work_item_id, state=state_id)

    async def update_custom_properties(
        self,
        project_id: str,
        work_item_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        """更新工作项的自定义属性。"""
        return await self.update_work_item(project_id, work_item_id, properties=properties)

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
