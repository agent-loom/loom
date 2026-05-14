import json
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agent_platform.domain.models import AgentRequest, AgentSpec, RuntimeRequest
from agent_platform.runtime.manager import RuntimeManager


class EvalCase(BaseModel):
    id: str
    input: dict[str, Any]
    expected: dict[str, Any] = Field(default_factory=dict)


class EvalCaseResult(BaseModel):
    id: str
    passed: bool
    reason: str | None = None


class EvalReport(BaseModel):
    agent_id: str
    total: int
    passed: int
    pass_rate: float
    required_pass_rate: float
    gate_passed: bool
    results: list[EvalCaseResult]


class EvalRunner:
    def __init__(self, runtime_manager: RuntimeManager | None = None):
        self.runtime_manager = runtime_manager or RuntimeManager()

    async def run_agent(self, spec: AgentSpec) -> EvalReport:
        cases = self._load_cases(spec)
        results: list[EvalCaseResult] = []

        for case in cases:
            request = AgentRequest.model_validate(case.input)
            response = await self.runtime_manager.run(
                RuntimeRequest(request=request, agent_spec=spec, route_reason="eval")
            )
            result = self._evaluate_case(case, response)
            results.append(result)

        passed_count = sum(1 for result in results if result.passed)
        total = len(results)
        pass_rate = passed_count / total if total else 0.0
        required_pass_rate = spec.manifest.evals.required_pass_rate
        return EvalReport(
            agent_id=spec.agent_id,
            total=total,
            passed=passed_count,
            pass_rate=pass_rate,
            required_pass_rate=required_pass_rate,
            gate_passed=pass_rate >= required_pass_rate,
            results=results,
        )

    def _evaluate_case(self, case: EvalCase, response) -> EvalCaseResult:
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
        if must_call_tools and trace:
            actual_tools = [tc.tool_name for tc in (trace.tool_calls or [])]
            for required_tool in must_call_tools:
                if not any(required_tool in t for t in actual_tools):
                    failures.append(f"required tool not called: {required_tool}")

        passed = len(failures) == 0
        return EvalCaseResult(
            id=case.id,
            passed=passed,
            reason="; ".join(failures) if failures else None,
        )

    async def run_agent_to_file(self, spec: AgentSpec, report_path: str) -> EvalReport:
        report = await self.run_agent(spec)
        with open(report_path, "w", encoding="utf-8") as file:
            json.dump(report.model_dump(mode="json"), file, ensure_ascii=False, indent=2)
            file.write("\n")
        return report

    def _load_cases(self, spec: AgentSpec) -> list[EvalCase]:
        cases: list[EvalCase] = []
        for suite in spec.manifest.evals.suites:
            suite_path = (spec.package_path / suite).resolve()
            raw = yaml.safe_load(suite_path.read_text()) or []
            if not isinstance(raw, list):
                raise ValueError(f"eval suite must be a list: {suite_path}")
            cases.extend(EvalCase.model_validate(item) for item in raw)
        return cases
