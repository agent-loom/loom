from pathlib import Path

import pytest

from agent_platform.evals.runner import EvalRunner
from agent_platform.registry.loader import ManifestLoader


@pytest.mark.asyncio
async def test_eval_runner_myj():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))

    report = await EvalRunner().run_agent(spec)

    assert report.total == 4
    assert report.passed == 4
    assert report.pass_rate == 1.0
    assert report.required_pass_rate == 0.9
    assert report.gate_passed is True


@pytest.mark.asyncio
async def test_eval_runner_writes_report(tmp_path):
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    report_path = tmp_path / "eval-report.json"

    report = await EvalRunner().run_agent_to_file(spec, str(report_path))

    assert report.gate_passed is True
    assert report_path.exists()
    assert '"agent_id": "myj"' in report_path.read_text()
