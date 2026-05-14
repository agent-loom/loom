import httpx
import pytest

from agent_platform.evals.feedback import EvalFeedback
from agent_platform.evals.runner import EvalCaseResult, EvalReport
from agent_platform.integrations.gitlab.adapter import GitLabAdapter
from agent_platform.integrations.plane.adapter import PlaneAdapter


def _make_report(passed: bool = True) -> EvalReport:
    return EvalReport(
        agent_id="myj",
        total=2,
        passed=2 if passed else 1,
        pass_rate=1.0 if passed else 0.5,
        required_pass_rate=0.9,
        gate_passed=passed,
        results=[
            EvalCaseResult(id="case_001", passed=True),
            EvalCaseResult(id="case_002", passed=passed, reason=None if passed else "missing text"),
        ],
    )


@pytest.mark.asyncio
async def test_eval_feedback_posts_to_gitlab():
    posted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted.append(request)
        return httpx.Response(200, json={"id": 1})

    gitlab = GitLabAdapter(
        base_url="https://gitlab.test",
        token="token",
        transport=httpx.MockTransport(handler),
    )
    feedback = EvalFeedback(gitlab=gitlab)
    report = _make_report(passed=True)
    await feedback.post_to_gitlab(report, "proj-1", 42)

    assert len(posted) == 1
    assert "/merge_requests/42/notes" in posted[0].url.path


@pytest.mark.asyncio
async def test_eval_feedback_updates_plane_state_on_pass():
    requests_made: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(request)
        return httpx.Response(200, json={"id": "wi-1"})

    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="key",
        workspace_slug="ws",
        transport=httpx.MockTransport(handler),
    )
    feedback = EvalFeedback(plane=plane)
    report = _make_report(passed=True)
    await feedback.update_plane_state(report, "proj-1", "wi-1", review_state_id="state-review")

    paths = [r.url.path for r in requests_made]
    assert any("comments" in p for p in paths)
    assert any(
        "work-items" in p and r.method == "PATCH"
        for r, p in zip(requests_made, paths, strict=False)
    )


@pytest.mark.asyncio
async def test_eval_feedback_does_not_advance_state_on_fail():
    requests_made: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_made.append(request)
        return httpx.Response(200, json={"id": "wi-1"})

    plane = PlaneAdapter(
        base_url="https://plane.test",
        api_key="key",
        workspace_slug="ws",
        transport=httpx.MockTransport(handler),
    )
    feedback = EvalFeedback(plane=plane)
    report = _make_report(passed=False)
    await feedback.update_plane_state(report, "proj-1", "wi-1", review_state_id="state-review")

    patch_requests = [r for r in requests_made if r.method == "PATCH"]
    assert len(patch_requests) == 0


def test_format_report_includes_failed_cases():
    report = _make_report(passed=False)
    md = EvalFeedback.format_report_markdown(report)
    assert "FAILED" in md
    assert "case_002" in md
    assert "50.0%" in md
