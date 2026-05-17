"""Agent 评测运行器，加载用例并生成多维评测报告。"""

import json
import logging
import time
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentRequest, AgentSpec, RuntimeRequest
from agent_platform.runtime.manager import RuntimeManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 评分维度
# ---------------------------------------------------------------------------


class ScoreDimension(BaseModel):
    """单个评分维度的结果，支持加权计算。"""

    name: str  # 维度名称，如 accuracy、latency
    score: float  # 当前得分
    max_score: float = 1.0  # 该维度满分
    weight: float = 1.0  # 汇总时的权重
    details: str = ""  # 可选的评分说明


class EvalCaseScores(BaseModel):
    """单个用例的多维评分结果，聚合准确率、延迟、成本和工具准确率。"""

    accuracy: float = 0.0  # 用例准确率（1.0=通过，0.0=失败）
    latency_ms: int | None = None  # 运行耗时（毫秒）
    cost_usd: float | None = None  # 预估成本（美元）
    tool_accuracy: float | None = None  # 工具调用准确率
    dimensions: list[ScoreDimension] = Field(default_factory=list)  # 扩展评分维度


# ---------------------------------------------------------------------------
# 用例与结果
# ---------------------------------------------------------------------------


class EvalCase(BaseModel):
    """单个评测用例，包含输入和预期输出。"""

    id: str
    input: dict[str, Any]
    expected: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    weight: float = 1.0


class EvalCaseResult(BaseModel):
    """单个评测用例的执行结果。"""

    id: str
    passed: bool
    reason: str | None = None
    scores: EvalCaseScores = Field(default_factory=EvalCaseScores)
    tags: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 汇总报告
# ---------------------------------------------------------------------------


class EvalSummaryStats(BaseModel):
    """评测报告的汇总统计，包含准确率、延迟分位数、成本汇总和按标签分组统计。"""

    avg_accuracy: float = 0.0
    avg_latency_ms: float | None = None
    avg_cost_usd: float | None = None
    p50_latency_ms: float | None = None  # 延迟 P50 分位数
    p95_latency_ms: float | None = None  # 延迟 P95 分位数
    p99_latency_ms: float | None = None  # 延迟 P99 分位数
    total_cost_usd: float | None = None  # 全部用例的累计成本
    avg_tool_accuracy: float | None = None
    by_tag: dict[str, dict[str, Any]] = Field(default_factory=dict)  # 按标签聚合的子报告


class EvalReport(BaseModel):
    """评测报告，汇总通过率、多维评分和质量门禁结果。"""

    agent_id: str
    agent_version: str = ""
    total: int
    passed: int
    pass_rate: float
    required_pass_rate: float
    gate_passed: bool
    results: list[EvalCaseResult]
    summary: EvalSummaryStats = Field(default_factory=EvalSummaryStats)
    dataset_sources: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 数据集加载
# ---------------------------------------------------------------------------


def load_dataset(path: str | Path) -> list[EvalCase]:
    """从 YAML 或 JSON 文件加载评测数据集，支持顶层列表或 {cases: [...]} 格式。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"eval dataset not found: {p}")

    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        raw = yaml.safe_load(text) or []
    elif p.suffix == ".json":
        raw = json.loads(text)
    else:
        raise ValueError(f"unsupported eval dataset format: {p.suffix}")

    if isinstance(raw, dict) and "cases" in raw:
        raw = raw["cases"]

    if not isinstance(raw, list):
        raise ValueError(f"eval dataset must be a list (or dict with 'cases' key): {p}")

    return [EvalCase.model_validate(item) for item in raw]


# ---------------------------------------------------------------------------
# 评测运行器
# ---------------------------------------------------------------------------


class EvalRunner:
    """评测运行器，执行评测用例并生成多维报告。"""

    def __init__(
        self,
        runtime_manager: RuntimeManager | None = None,
        eval_repo: Any | None = None,
    ):
        self.runtime_manager = runtime_manager or RuntimeManager()
        self.eval_repo = eval_repo

    # ── 主入口 ──────────────────────────────────────────────

    async def run_agent(
        self,
        spec: AgentSpec,
        *,
        trigger: str = "manual",
        extra_datasets: list[str | Path] | None = None,
    ) -> EvalReport:
        """运行指定 Agent 的全部评测用例并返回多维报告。"""
        cases, sources = self._load_all_cases(spec, extra_datasets)
        results: list[EvalCaseResult] = []

        for case in cases:
            request = AgentRequest.model_validate(case.input)
            started = time.monotonic()
            response = await self.runtime_manager.run(
                RuntimeRequest(request=request, agent_spec=spec, route_reason="eval")
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            result = self._evaluate_case(case, response, elapsed_ms)
            results.append(result)

        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        pass_rate = passed_count / total if total else 0.0
        required_pass_rate = spec.manifest.evals.required_pass_rate
        summary = self._compute_summary(results)

        report = EvalReport(
            agent_id=spec.agent_id,
            agent_version=spec.manifest.version.package_version,
            total=total,
            passed=passed_count,
            pass_rate=pass_rate,
            required_pass_rate=required_pass_rate,
            gate_passed=pass_rate >= required_pass_rate,
            results=results,
            summary=summary,
            dataset_sources=sources,
        )

        await self._persist(report, trigger)
        return report

    async def run_agent_to_file(
        self,
        spec: AgentSpec,
        report_path: str,
        *,
        extra_datasets: list[str | Path] | None = None,
    ) -> EvalReport:
        """运行评测并将报告写入 JSON 文件。"""
        report = await self.run_agent(spec, extra_datasets=extra_datasets)
        with open(report_path, "w", encoding="utf-8") as file:
            json.dump(report.model_dump(mode="json"), file, ensure_ascii=False, indent=2)
            file.write("\n")
        return report

    async def run_dataset(
        self,
        spec: AgentSpec,
        dataset_path: str | Path,
        *,
        trigger: str = "dataset",
    ) -> EvalReport:
        """仅运行外部数据集（不加载 manifest 自带用例），独立生成评测报告。"""
        cases = load_dataset(dataset_path)
        sources = [str(dataset_path)]
        results: list[EvalCaseResult] = []

        for case in cases:
            request = AgentRequest.model_validate(case.input)
            started = time.monotonic()
            response = await self.runtime_manager.run(
                RuntimeRequest(request=request, agent_spec=spec, route_reason="eval")
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            result = self._evaluate_case(case, response, elapsed_ms)
            results.append(result)

        passed_count = sum(1 for r in results if r.passed)
        total = len(results)
        pass_rate = passed_count / total if total else 0.0
        required_pass_rate = spec.manifest.evals.required_pass_rate
        summary = self._compute_summary(results)

        report = EvalReport(
            agent_id=spec.agent_id,
            agent_version=spec.manifest.version.package_version,
            total=total,
            passed=passed_count,
            pass_rate=pass_rate,
            required_pass_rate=required_pass_rate,
            gate_passed=pass_rate >= required_pass_rate,
            results=results,
            summary=summary,
            dataset_sources=sources,
        )

        await self._persist(report, trigger)
        return report

    # ── 用例评估 ────────────────────────────────────────────

    def _evaluate_case(self, case: EvalCase, response, elapsed_ms: int) -> EvalCaseResult:
        failures: list[str] = []
        display = response.response.output.text.display
        trace = response.response.trace

        expected_contains = case.expected.get("output_contains", [])
        missing = [text for text in expected_contains if text not in display]
        if missing:
            failures.append(f"missing expected text: {missing}")

        forbidden = case.expected.get("forbidden", [])
        present = [text for text in forbidden if text in display]
        if present:
            failures.append(f"forbidden text found: {present}")

        expected_intent = case.expected.get("intent")
        if expected_intent and trace:
            route_reason = trace.route_reason or ""
            if expected_intent not in route_reason and expected_intent not in display:
                actual_tools = [tc.tool_name for tc in (trace.tool_calls or [])]
                if not any(expected_intent in t for t in actual_tools):
                    failures.append(f"expected intent '{expected_intent}' not detected")

        must_call_tools = case.expected.get("must_call_tools", [])
        tool_calls_matched = 0
        if must_call_tools and trace:
            actual_tools = [tc.tool_name for tc in (trace.tool_calls or [])]
            for required_tool in must_call_tools:
                if any(required_tool in t for t in actual_tools):
                    tool_calls_matched += 1
                else:
                    failures.append(f"required tool not called: {required_tool}")

        max_latency = case.expected.get("max_latency_ms")
        if max_latency is not None and elapsed_ms > max_latency:
            failures.append(f"latency {elapsed_ms}ms exceeds limit {max_latency}ms")

        max_cost = case.expected.get("max_cost_usd")
        cost_usd = trace.estimated_cost_usd if trace else None
        if max_cost is not None and cost_usd is not None and cost_usd > max_cost:
            failures.append(f"cost ${cost_usd:.4f} exceeds limit ${max_cost:.4f}")

        passed = len(failures) == 0
        accuracy = 1.0 if passed else 0.0

        tool_accuracy: float | None = None
        if must_call_tools:
            tool_accuracy = tool_calls_matched / len(must_call_tools) if must_call_tools else 1.0

        scores = EvalCaseScores(
            accuracy=accuracy,
            latency_ms=elapsed_ms,
            cost_usd=cost_usd,
            tool_accuracy=tool_accuracy,
        )

        return EvalCaseResult(
            id=case.id,
            passed=passed,
            reason="; ".join(failures) if failures else None,
            scores=scores,
            tags=case.tags,
        )

    # ── 汇总统计 ────────────────────────────────────────────

    def _compute_summary(self, results: list[EvalCaseResult]) -> EvalSummaryStats:
        """汇总所有用例结果，计算准确率均值、延迟分位数、成本合计及按标签分组统计。"""
        if not results:
            return EvalSummaryStats()

        accuracies = [r.scores.accuracy for r in results]
        latencies = [r.scores.latency_ms for r in results if r.scores.latency_ms is not None]
        costs = [r.scores.cost_usd for r in results if r.scores.cost_usd is not None]
        tool_accs = [r.scores.tool_accuracy for r in results if r.scores.tool_accuracy is not None]

        avg_accuracy = sum(accuracies) / len(accuracies) if accuracies else 0.0

        avg_latency: float | None = None
        p50_latency: float | None = None
        p95_latency: float | None = None
        p99_latency: float | None = None
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            sorted_lat = sorted(latencies)
            p50_latency = self._percentile(sorted_lat, 50)
            p95_latency = self._percentile(sorted_lat, 95)
            p99_latency = self._percentile(sorted_lat, 99)

        avg_cost: float | None = None
        total_cost: float | None = None
        if costs:
            avg_cost = sum(costs) / len(costs)
            total_cost = sum(costs)

        avg_tool_acc: float | None = None
        if tool_accs:
            avg_tool_acc = sum(tool_accs) / len(tool_accs)

        by_tag = self._compute_by_tag(results)

        return EvalSummaryStats(
            avg_accuracy=round(avg_accuracy, 4),
            avg_latency_ms=round(avg_latency, 1) if avg_latency is not None else None,
            avg_cost_usd=round(avg_cost, 6) if avg_cost is not None else None,
            p50_latency_ms=round(p50_latency, 1) if p50_latency is not None else None,
            p95_latency_ms=round(p95_latency, 1) if p95_latency is not None else None,
            p99_latency_ms=round(p99_latency, 1) if p99_latency is not None else None,
            total_cost_usd=round(total_cost, 6) if total_cost is not None else None,
            avg_tool_accuracy=round(avg_tool_acc, 4) if avg_tool_acc is not None else None,
            by_tag=by_tag,
        )

    def _compute_by_tag(self, results: list[EvalCaseResult]) -> dict[str, dict[str, Any]]:
        """按标签分组聚合评测结果，为每个标签计算通过率和平均指标。"""
        tag_groups: dict[str, list[EvalCaseResult]] = {}
        for r in results:
            for tag in r.tags:
                tag_groups.setdefault(tag, []).append(r)

        by_tag: dict[str, dict[str, Any]] = {}
        for tag, group in tag_groups.items():
            total = len(group)
            passed = sum(1 for r in group if r.passed)
            accs = [r.scores.accuracy for r in group]
            lats = [r.scores.latency_ms for r in group if r.scores.latency_ms is not None]
            by_tag[tag] = {
                "total": total,
                "passed": passed,
                "pass_rate": round(passed / total, 4) if total else 0.0,
                "avg_accuracy": round(sum(accs) / len(accs), 4) if accs else 0.0,
                "avg_latency_ms": round(sum(lats) / len(lats), 1) if lats else None,
            }
        return by_tag

    @staticmethod
    def _percentile(sorted_values: list[int | float], p: int) -> float:
        """计算已排序数组的第 p 百分位数（线性插值）。"""
        if not sorted_values:
            return 0.0
        k = (len(sorted_values) - 1) * p / 100.0
        f = int(k)
        c = f + 1
        if c >= len(sorted_values):
            return float(sorted_values[f])
        d = k - f
        return sorted_values[f] + d * (sorted_values[c] - sorted_values[f])

    # ── 数据集加载 ──────────────────────────────────────────

    def _load_all_cases(
        self,
        spec: AgentSpec,
        extra_datasets: list[str | Path] | None,
    ) -> tuple[list[EvalCase], list[str]]:
        cases: list[EvalCase] = []
        sources: list[str] = []

        for suite in spec.manifest.evals.suites:
            suite_path = (spec.package_path / suite).resolve()
            raw = yaml.safe_load(suite_path.read_text()) or []
            if not isinstance(raw, list):
                raise ValueError(f"eval suite must be a list: {suite_path}")
            cases.extend(EvalCase.model_validate(item) for item in raw)
            sources.append(str(suite_path))

        if extra_datasets:
            for ds_path in extra_datasets:
                loaded = load_dataset(ds_path)
                cases.extend(loaded)
                sources.append(str(ds_path))

        return cases, sources

    # ── 旧接口兼容 ─────────────────────────────────────────

    def _load_cases(self, spec: AgentSpec) -> list[EvalCase]:
        cases, _ = self._load_all_cases(spec, None)
        return cases

    # ── 持久化 ─────────────────────────────────────────────

    async def _persist(self, report: EvalReport, trigger: str) -> None:
        if self.eval_repo is None:
            return
        try:
            await self.eval_repo.record(
                agent_id=report.agent_id,
                agent_version=report.agent_version,
                total=report.total,
                passed=report.passed,
                pass_rate=report.pass_rate,
                required_pass_rate=report.required_pass_rate,
                gate_passed=report.gate_passed,
                results=[r.model_dump(mode="json") for r in report.results],
                trigger=trigger,
            )
        except Exception:
            logger.exception("failed to persist eval run for %s", report.agent_id)
