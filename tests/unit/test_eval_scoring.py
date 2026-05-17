"""EvalRunner 多维评分与数据集加载测试。"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from agent_platform.domain.models import (
    AgentManifest,
    AgentSpec,
    ManifestEvals,
    ManifestMetadata,
    ManifestOutput,
    ManifestVersion,
)
from agent_platform.evals.runner import (
    EvalCase,
    EvalCaseResult,
    EvalCaseScores,
    EvalReport,
    EvalRunner,
    EvalSummaryStats,
    ScoreDimension,
    load_dataset,
)

# ── 辅助 ─────────────────────────────────────────────────


def _make_response(
    text: str = "hello world",
    *,
    latency_ms: int = 100,
    cost: float | None = None,
    tool_calls=None,
):
    trace = MagicMock()
    trace.route_reason = "eval"
    trace.tool_calls = tool_calls or []
    trace.latency_ms = latency_ms
    trace.estimated_cost_usd = cost
    trace.model_dump.return_value = {"route_reason": "eval"}

    output_text = MagicMock()
    output_text.display = text
    output = MagicMock()
    output.text = output_text
    resp = MagicMock()
    resp.output = output
    resp.trace = trace
    result = MagicMock()
    result.response = resp
    return result


def _make_spec(
    cases: list[dict],
    required_pass_rate: float = 0.8,
    package_path: Path | None = None,
):
    manifest = AgentManifest(
        api_version="agent.platform/v1",
        kind="AgentPackage",
        metadata=ManifestMetadata(id="test-agent", name="Test Agent"),
        version=ManifestVersion(package_version="1.0.0"),
        output=ManifestOutput(),
        evals=ManifestEvals(required_pass_rate=required_pass_rate),
    )
    return AgentSpec(manifest=manifest, package_path=package_path or Path("/fake"))


# ── EvalCase 模型 ─────────────────────────────────────────


def test_eval_case_with_tags():
    case = EvalCase(id="c1", input={"query": "hi"}, tags=["smoke", "basic"], weight=2.0)
    assert case.tags == ["smoke", "basic"]
    assert case.weight == 2.0


def test_eval_case_scores_model():
    scores = EvalCaseScores(accuracy=0.9, latency_ms=150, cost_usd=0.001, tool_accuracy=1.0)
    assert scores.accuracy == 0.9
    assert scores.latency_ms == 150


def test_score_dimension_model():
    dim = ScoreDimension(
        name="relevance", score=0.8, max_score=1.0,
        weight=1.5, details="good match",
    )
    assert dim.name == "relevance"
    assert dim.weight == 1.5


# ── load_dataset ──────────────────────────────────────────


def _input(query: str) -> dict:
    return {"input": {"input": {"query": query}}}


def test_load_dataset_yaml(tmp_path):
    data = [
        {"id": "c1", **_input("hello"), "expected": {"output_contains": ["hi"]}},
        {"id": "c2", **_input("bye"), "tags": ["farewell"]},
    ]
    path = tmp_path / "dataset.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")

    cases = load_dataset(path)
    assert len(cases) == 2
    assert cases[0].id == "c1"
    assert cases[1].tags == ["farewell"]


def test_load_dataset_json(tmp_path):
    data = [{"id": "j1", **_input("test")}]
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    cases = load_dataset(path)
    assert len(cases) == 1
    assert cases[0].id == "j1"


def test_load_dataset_with_cases_key(tmp_path):
    data = {"cases": [{"id": "w1", **_input("wrapped")}]}
    path = tmp_path / "wrapped.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")

    cases = load_dataset(path)
    assert len(cases) == 1
    assert cases[0].id == "w1"


def test_load_dataset_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_dataset("/nonexistent/path.yaml")


def test_load_dataset_unsupported_format(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("id,input\n1,hello", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        load_dataset(path)


# ── EvalRunner._evaluate_case 多维评分 ────────────────────


def test_evaluate_case_accuracy_pass():
    runner = EvalRunner()
    case = EvalCase(id="t1", input={"query": "hi"}, expected={"output_contains": ["hello"]})
    response = _make_response("hello world", latency_ms=50, cost=0.001)
    result = runner._evaluate_case(case, response, 50)
    assert result.passed is True
    assert result.scores.accuracy == 1.0
    assert result.scores.latency_ms == 50
    assert result.scores.cost_usd == 0.001


def test_evaluate_case_accuracy_fail():
    runner = EvalRunner()
    case = EvalCase(id="t2", input={"query": "hi"}, expected={"output_contains": ["missing"]})
    response = _make_response("hello world")
    result = runner._evaluate_case(case, response, 100)
    assert result.passed is False
    assert result.scores.accuracy == 0.0


def test_evaluate_case_latency_check():
    runner = EvalRunner()
    case = EvalCase(id="t3", input={"query": "hi"}, expected={"max_latency_ms": 50})
    response = _make_response("ok")
    result = runner._evaluate_case(case, response, 200)
    assert result.passed is False
    assert "latency" in (result.reason or "")


def test_evaluate_case_cost_check():
    runner = EvalRunner()
    case = EvalCase(id="t4", input={"query": "hi"}, expected={"max_cost_usd": 0.001})
    response = _make_response("ok", cost=0.01)
    result = runner._evaluate_case(case, response, 100)
    assert result.passed is False
    assert "cost" in (result.reason or "")


def test_evaluate_case_tool_accuracy():
    runner = EvalRunner()
    tc = MagicMock()
    tc.tool_name = "search_web"
    case = EvalCase(
        id="t5",
        input={"query": "hi"},
        expected={"must_call_tools": ["search_web", "missing_tool"]},
    )
    response = _make_response("ok", tool_calls=[tc])
    result = runner._evaluate_case(case, response, 100)
    assert result.scores.tool_accuracy == 0.5


def test_evaluate_case_tags_preserved():
    runner = EvalRunner()
    case = EvalCase(id="t6", input={"query": "hi"}, tags=["smoke", "core"])
    response = _make_response("ok")
    result = runner._evaluate_case(case, response, 100)
    assert result.tags == ["smoke", "core"]


# ── EvalRunner._compute_summary ──────────────────────────


def test_compute_summary_empty():
    runner = EvalRunner()
    summary = runner._compute_summary([])
    assert summary.avg_accuracy == 0.0
    assert summary.avg_latency_ms is None


def test_compute_summary_basic():
    runner = EvalRunner()
    s1 = EvalCaseScores(accuracy=1.0, latency_ms=100, cost_usd=0.001)
    s2 = EvalCaseScores(accuracy=0.0, latency_ms=200, cost_usd=0.002)
    results = [
        EvalCaseResult(id="r1", passed=True, scores=s1),
        EvalCaseResult(id="r2", passed=False, scores=s2),
    ]
    summary = runner._compute_summary(results)
    assert summary.avg_accuracy == 0.5
    assert summary.avg_latency_ms == 150.0
    assert summary.total_cost_usd == 0.003
    assert summary.p50_latency_ms is not None


def test_compute_summary_by_tag():
    runner = EvalRunner()
    results = [
        EvalCaseResult(
            id="r1", passed=True, tags=["smoke"],
            scores=EvalCaseScores(accuracy=1.0, latency_ms=100),
        ),
        EvalCaseResult(
            id="r2", passed=False, tags=["smoke", "edge"],
            scores=EvalCaseScores(accuracy=0.0, latency_ms=300),
        ),
        EvalCaseResult(
            id="r3", passed=True, tags=["edge"],
            scores=EvalCaseScores(accuracy=1.0, latency_ms=50),
        ),
    ]
    summary = runner._compute_summary(results)
    assert "smoke" in summary.by_tag
    assert "edge" in summary.by_tag
    assert summary.by_tag["smoke"]["total"] == 2
    assert summary.by_tag["smoke"]["passed"] == 1
    assert summary.by_tag["edge"]["total"] == 2


def test_compute_summary_percentiles():
    runner = EvalRunner()
    results = [
        EvalCaseResult(
            id=f"r{i}", passed=True,
            scores=EvalCaseScores(accuracy=1.0, latency_ms=i * 10),
        )
        for i in range(1, 21)
    ]
    summary = runner._compute_summary(results)
    assert summary.p50_latency_ms is not None
    assert summary.p95_latency_ms is not None
    assert summary.p99_latency_ms is not None
    assert summary.p50_latency_ms < summary.p95_latency_ms


def test_compute_summary_tool_accuracy():
    runner = EvalRunner()
    results = [
        EvalCaseResult(
            id="r1", passed=True,
            scores=EvalCaseScores(accuracy=1.0, tool_accuracy=1.0),
        ),
        EvalCaseResult(
            id="r2", passed=False,
            scores=EvalCaseScores(accuracy=0.0, tool_accuracy=0.5),
        ),
    ]
    summary = runner._compute_summary(results)
    assert summary.avg_tool_accuracy == 0.75


# ── EvalRunner.run_agent 集成 ────────────────────────────


@pytest.mark.asyncio
async def test_run_agent_with_extra_datasets(tmp_path):
    extra_data = [{"id": "ext1", **_input("extra")}]
    ds_path = tmp_path / "extra.yaml"
    ds_path.write_text(yaml.dump(extra_data), encoding="utf-8")

    spec = _make_spec([])
    runner = EvalRunner()
    runner.runtime_manager.run = AsyncMock(return_value=_make_response("extra response"))

    report = await runner.run_agent(spec, extra_datasets=[ds_path])
    assert report.total == 1
    assert str(ds_path) in report.dataset_sources


@pytest.mark.asyncio
async def test_run_agent_with_eval_repo():
    spec = _make_spec([])
    repo = MagicMock()
    repo.record = AsyncMock()
    runner = EvalRunner(eval_repo=repo)
    runner.runtime_manager.run = AsyncMock(return_value=_make_response("ok"))

    report = await runner.run_agent(spec)
    assert report.total == 0
    # 无用例时仍会持久化空报告
    repo.record.assert_called_once()


@pytest.mark.asyncio
async def test_run_dataset_standalone(tmp_path):
    data = [
        {"id": "d1", **_input("test1"), "expected": {"output_contains": ["ok"]}},
        {"id": "d2", **_input("test2"), "expected": {"output_contains": ["missing"]}},
    ]
    ds_path = tmp_path / "standalone.json"
    ds_path.write_text(json.dumps(data), encoding="utf-8")

    spec = _make_spec([])
    runner = EvalRunner()
    runner.runtime_manager.run = AsyncMock(return_value=_make_response("ok result"))

    report = await runner.run_dataset(spec, ds_path)
    assert report.total == 2
    assert report.passed == 1
    assert report.summary.avg_accuracy == 0.5
    assert str(ds_path) in report.dataset_sources


# ── EvalReport 模型 ──────────────────────────────────────


def test_eval_report_serialization():
    report = EvalReport(
        agent_id="test",
        agent_version="1.0",
        total=2,
        passed=1,
        pass_rate=0.5,
        required_pass_rate=0.8,
        gate_passed=False,
        results=[
            EvalCaseResult(
                id="c1", passed=True,
                scores=EvalCaseScores(accuracy=1.0, latency_ms=100),
            ),
            EvalCaseResult(
                id="c2", passed=False,
                scores=EvalCaseScores(accuracy=0.0, latency_ms=200),
            ),
        ],
        summary=EvalSummaryStats(avg_accuracy=0.5, avg_latency_ms=150.0),
    )
    data = report.model_dump(mode="json")
    assert data["summary"]["avg_accuracy"] == 0.5
    assert len(data["results"]) == 2
    assert data["results"][0]["scores"]["latency_ms"] == 100


def test_percentile_edge_cases():
    assert EvalRunner._percentile([], 50) == 0.0
    assert EvalRunner._percentile([100], 50) == 100.0
    assert EvalRunner._percentile([10, 20], 50) == 15.0
