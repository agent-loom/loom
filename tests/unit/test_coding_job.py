from __future__ import annotations

from agent_platform.devflow.runner.models import (
    CodingJob,
    CommandResult,
    JobState,
    ResultStatus,
    RunnerResult,
    ValidationResult,
)
from agent_platform.devflow.runner.runner import CodingAgentRunner


class TestCodingJobDefaults:
    def test_default_state_is_pending(self):
        job = CodingJob(job_id="j1", task_id="t1")
        assert job.state == JobState.PENDING

    def test_default_retry(self):
        job = CodingJob(job_id="j1", task_id="t1")
        assert job.max_retries == 1
        assert job.retry_count == 0
        assert job.timeout_seconds == 600

    def test_timestamps_populated(self):
        job = CodingJob(job_id="j1", task_id="t1")
        assert job.created_at is not None
        assert job.updated_at is not None

    def test_empty_invocations(self):
        job = CodingJob(job_id="j1", task_id="t1")
        assert job.invocations == []
        assert job.result is None


class TestJobStateEnum:
    def test_all_states_exist(self):
        expected = {
            "pending", "workspace_creating", "running", "validating",
            "committing", "succeeded", "failed", "timed_out", "cancelled",
        }
        assert {s.value for s in JobState} == expected


class TestResultStatusEnum:
    def test_all_statuses_exist(self):
        expected = {
            "success", "validation_failed", "runner_error",
            "path_violation", "timeout", "cancelled",
        }
        assert {s.value for s in ResultStatus} == expected


class TestRunnerResult:
    def test_success_result(self):
        result = RunnerResult(
            status=ResultStatus.SUCCESS,
            changed_files=["src/main.py"],
            validation=ValidationResult(
                commands_executed=[
                    CommandResult(command="pytest", exit_code=0, duration_ms=1000),
                ],
                all_passed=True,
            ),
            commit_sha="abc123",
        )
        assert result.status == ResultStatus.SUCCESS
        assert result.commit_sha == "abc123"
        assert result.validation.all_passed is True

    def test_failed_result(self):
        result = RunnerResult(
            status=ResultStatus.RUNNER_ERROR,
            error_message="something broke",
        )
        assert result.error_message == "something broke"
        assert result.changed_files == []


class TestMRComment:
    def test_mr_comment_format(self):
        from agent_platform.devflow.runner.runner import CodingAgentRunner

        job = CodingJob(
            job_id="job-test",
            task_id="task-test",
            state=JobState.SUCCEEDED,
            result=RunnerResult(
                status=ResultStatus.SUCCESS,
                changed_files=["src/main.py", "tests/test_main.py"],
                validation=ValidationResult(
                    commands_executed=[
                        CommandResult(command="pytest", exit_code=0, duration_ms=1000),
                    ],
                    all_passed=True,
                ),
                commit_sha="abc1234567890",
            ),
        )
        runner = CodingAgentRunner.__new__(CodingAgentRunner)
        comment = runner._build_mr_comment(job)
        assert "src/main.py" in comment
        assert "PASS" in comment
        assert "abc12345" in comment
        assert "DevFlow Runner" in comment

    def test_mr_comment_no_result(self):
        job = CodingJob(job_id="job-test", task_id="task-test")
        runner = CodingAgentRunner.__new__(CodingAgentRunner)
        comment = runner._build_mr_comment(job)
        assert "未完成" in comment
