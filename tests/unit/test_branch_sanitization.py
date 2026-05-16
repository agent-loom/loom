from agent_platform.devflow.task_pack import _sanitize_branch_name, TaskPackGenerator


class TestSanitizeBranchName:
    def test_lowercase(self):
        assert _sanitize_branch_name("ABC-123") == "abc-123"

    def test_special_chars_replaced(self):
        assert _sanitize_branch_name("feat: add!new@thing") == "feat-add-new-thing"

    def test_double_dashes_collapsed(self):
        assert _sanitize_branch_name("a--b---c") == "a-b-c"

    def test_leading_trailing_dashes_stripped(self):
        assert _sanitize_branch_name("-hello-") == "hello"

    def test_slashes_preserved(self):
        assert _sanitize_branch_name("feat/task-1") == "feat/task-1"

    def test_underscores_preserved(self):
        assert _sanitize_branch_name("my_task_123") == "my_task_123"

    def test_unicode_replaced(self):
        result = _sanitize_branch_name("任务-001")
        assert "/" not in result or result == result
        for c in result:
            assert c in "abcdefghijklmnopqrstuvwxyz0123456789/_-"

    def test_empty_string(self):
        assert _sanitize_branch_name("") == ""

    def test_already_clean(self):
        assert _sanitize_branch_name("feat/wi-001") == "feat/wi-001"


class TestTaskPackBranchGeneration:
    def test_branch_uses_sanitized_task_id(self):
        task = TaskPackGenerator().from_requirement(
            task_id="AGENT_123",
            title="Test",
            task_type="platform:change",
            project_id="proj",
            background="bg",
        )
        assert task.repository.work_branch == "feat/agent_123"

    def test_branch_special_chars(self):
        task = TaskPackGenerator().from_requirement(
            task_id="BUG: Fix #42!",
            title="Fix Bug",
            task_type="platform:change",
            project_id="proj",
            background="bg",
        )
        branch = task.repository.work_branch
        assert branch.startswith("feat/")
        for c in branch[5:]:
            assert c in "abcdefghijklmnopqrstuvwxyz0123456789/_-"
