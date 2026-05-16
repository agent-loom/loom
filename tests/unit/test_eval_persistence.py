"""Tests for EvalRunner auto-persistence of eval runs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_platform.evals.runner import EvalRunner
from agent_platform.registry.loader import ManifestLoader


@pytest.mark.asyncio
async def test_eval_runner_persists_to_repo():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    repo = MagicMock()
    repo.record = AsyncMock()

    runner = EvalRunner(eval_repo=repo)
    report = await runner.run_agent(spec, trigger="deploy_gate")

    assert report.gate_passed is True
    repo.record.assert_called_once()
    call_kwargs = repo.record.call_args[1]
    assert call_kwargs["agent_id"] == "myj"
    assert call_kwargs["trigger"] == "deploy_gate"
    assert call_kwargs["total"] == report.total
    assert call_kwargs["passed"] == report.passed
    assert call_kwargs["gate_passed"] is True
    assert len(call_kwargs["results"]) == report.total


@pytest.mark.asyncio
async def test_eval_runner_no_repo_still_works():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    runner = EvalRunner(eval_repo=None)
    report = await runner.run_agent(spec)
    assert report.gate_passed is True


@pytest.mark.asyncio
async def test_eval_runner_repo_failure_does_not_break():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    repo = MagicMock()
    repo.record = AsyncMock(side_effect=RuntimeError("db down"))

    runner = EvalRunner(eval_repo=repo)
    report = await runner.run_agent(spec)
    assert report.gate_passed is True
    repo.record.assert_called_once()


@pytest.mark.asyncio
async def test_eval_runner_default_trigger_is_manual():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))
    repo = MagicMock()
    repo.record = AsyncMock()

    runner = EvalRunner(eval_repo=repo)
    await runner.run_agent(spec)

    call_kwargs = repo.record.call_args[1]
    assert call_kwargs["trigger"] == "manual"
