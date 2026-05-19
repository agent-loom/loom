"""ScmAdapter Protocol — vendor-neutral interface for source code management operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MergeRequestResult:
    """Normalized merge/pull request creation result."""

    mr_id: int
    url: str
    source_branch: str
    target_branch: str
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ScmAdapter(Protocol):
    """Vendor-neutral interface for SCM operations used by DevFlow.

    Implementations exist for GitLab (and later GitHub).
    Every method is async to support non-blocking HTTP calls.
    """

    async def create_branch(
        self,
        project_id: str,
        branch: str,
        *,
        ref: str = "main",
    ) -> dict[str, Any]:
        """Create a branch from *ref*."""
        ...

    async def create_merge_request(
        self,
        project_id: str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
        labels: list[str] | None = None,
    ) -> MergeRequestResult:
        """Create a merge/pull request and return a normalized result."""
        ...

    async def find_open_merge_request(
        self,
        project_id: str,
        source_branch: str,
    ) -> MergeRequestResult | None:
        """Find an open merge/pull request for *source_branch*, if one exists."""
        ...

    async def get_merge_request(
        self,
        project_id: str,
        mr_id: int,
    ) -> dict[str, Any]:
        """Fetch merge/pull request details."""
        ...

    async def comment_merge_request(
        self,
        project_id: str,
        mr_id: int,
        body: str,
    ) -> dict[str, Any]:
        """Post a comment on a merge/pull request."""
        ...

    async def get_pipeline_status(
        self,
        project_id: str,
        ref: str,
    ) -> str | None:
        """Return the latest CI pipeline status for *ref*, or ``None``."""
        ...

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
        """Set a commit status (build check) on *sha*."""
        ...
