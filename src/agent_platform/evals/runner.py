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
            display = response.response.output.text.display
            expected_contains = case.expected.get("output_contains", [])
            missing = [text for text in expected_contains if text not in display]
            passed = not missing
            results.append(
                EvalCaseResult(
                    id=case.id,
                    passed=passed,
                    reason=f"missing expected text: {missing}" if missing else None,
                )
            )

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
