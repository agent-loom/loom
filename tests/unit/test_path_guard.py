from __future__ import annotations

from agent_platform.devflow.runner.path_guard import PathGuard


class TestPathGuardCheck:
    def test_allowed_file_passes(self):
        guard = PathGuard(
            write_allowed=["src/**", "tests/**"],
            write_denied=[".env", "secrets/**"],
        )
        violations = guard.check(["src/main.py", "tests/test_main.py"])
        assert violations == []

    def test_denied_file_blocked(self):
        guard = PathGuard(
            write_allowed=["src/**", "tests/**"],
            write_denied=[".env", "secrets/**"],
        )
        violations = guard.check([".env", "src/main.py", "secrets/key.txt"])
        assert len(violations) == 2
        assert violations[0].path == ".env"
        assert violations[1].path == "secrets/key.txt"

    def test_denied_takes_priority_over_allowed(self):
        guard = PathGuard(
            write_allowed=["config/**"],
            write_denied=["config/prod/**"],
        )
        violations = guard.check(["config/prod/secrets.yaml"])
        assert len(violations) == 1
        assert "write_denied" in violations[0].reason

    def test_file_not_in_any_pattern_denied(self):
        guard = PathGuard(
            write_allowed=["src/**"],
            write_denied=[],
        )
        violations = guard.check(["README.md"])
        assert len(violations) == 1
        assert "not in any write_allowed" in violations[0].reason

    def test_empty_allowed_means_all_denied(self):
        guard = PathGuard(write_allowed=[], write_denied=[])
        violations = guard.check(["anything.py"])
        assert len(violations) == 1

    def test_empty_files_returns_empty(self):
        guard = PathGuard(write_allowed=["src/**"], write_denied=[])
        assert guard.check([]) == []


class TestPathGuardIsAllowed:
    def test_allowed(self):
        guard = PathGuard(write_allowed=["src/**"], write_denied=[])
        assert guard.is_allowed("src/app.py") is True

    def test_not_allowed(self):
        guard = PathGuard(write_allowed=["src/**"], write_denied=[".env"])
        assert guard.is_allowed(".env") is False


class TestPathGuardFromTask:
    def test_extracts_scope(self):
        from agent_platform.devflow.task_pack import (
            DevelopmentTask,
            RepositoryTarget,
            RequirementSpec,
            TaskMetadata,
        )

        task = DevelopmentTask(
            metadata=TaskMetadata(
                task_id="t1", title="test", type="platform:change", source={},
            ),
            repository=RepositoryTarget(
                project_id="p1",
                work_branch="feat/t1",
                base_branch="main",
            ),
            requirement=RequirementSpec(background="bg"),
            scope={
                "write_allowed": ["src/**"],
                "write_denied": [".env"],
            },
        )
        guard = PathGuard.from_task(task)
        assert guard.write_allowed == ["src/**"]
        assert guard.write_denied == [".env"]
