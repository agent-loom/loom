from __future__ import annotations

import pytest

from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.scm.protocol import MergeRequestResult, ScmAdapter


class TestMergeRequestResult:
    def test_frozen(self):
        result = MergeRequestResult(
            mr_id=1, url="https://gitlab.test/mr/1",
            source_branch="feat/x", target_branch="main",
        )
        with pytest.raises(AttributeError):
            result.mr_id = 2  # type: ignore[misc]

    def test_fields(self):
        result = MergeRequestResult(
            mr_id=42, url="https://gitlab.test/mr/42",
            source_branch="feat/task", target_branch="develop",
            raw={"iid": 42},
        )
        assert result.mr_id == 42
        assert result.url == "https://gitlab.test/mr/42"
        assert result.source_branch == "feat/task"
        assert result.target_branch == "develop"
        assert result.raw == {"iid": 42}

    def test_default_raw(self):
        result = MergeRequestResult(
            mr_id=1, url="u", source_branch="a", target_branch="b",
        )
        assert result.raw == {}


class TestScmAdapterProtocol:
    def test_gitlab_adapter_is_scm_adapter(self):
        assert issubclass(GitLabAdapter, ScmAdapter)

    def test_gitlab_instance_is_scm_adapter(self):
        adapter = GitLabAdapter("https://gitlab.test", "tok")
        assert isinstance(adapter, ScmAdapter)
