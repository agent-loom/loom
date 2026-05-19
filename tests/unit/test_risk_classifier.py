"""risk_classifier 单元测试。"""
import pytest

from agent_platform.evolution.models import (
    Evidence,
    EvidenceType,
    ImprovementProposal,
    ProposedChange,
    RiskAssessment,
    RiskLevel,
    RootCause,
    RootCauseCategory,
)
from agent_platform.evolution.risk_classifier import classify_risk, populate_risk_and_paths


def _changes(*paths: str) -> list[ProposedChange]:
    return [ProposedChange(type="update", path=p, description="test") for p in paths]


def _make_proposal(paths: list[str], root_cause: RootCauseCategory = RootCauseCategory.PROMPT_GAP) -> ImprovementProposal:
    return ImprovementProposal(
        title="test",
        summary="test",
        agent_id="echo",
        risk=RiskAssessment(level=RiskLevel.LOW, reason="placeholder"),
        root_cause=RootCause(category=root_cause, confidence=0.8, explanation="test"),
        evidence=[Evidence(type=EvidenceType.EVAL_FAILURE, id="e1", summary="test")],
        proposed_changes=_changes(*paths),
        allowed_paths=[],
        blocked_paths=[],
    )


class TestClassifyRisk:
    def test_low_risk_prompt_only(self):
        r = classify_risk(
            _changes("agents/echo/prompts/orchestrator.md"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.LOW
        assert r.requires_human_confirmation_before_devflow is False
        assert r.requires_human_review_before_merge is True

    def test_low_risk_eval_only(self):
        r = classify_risk(
            _changes("agents/echo/evals/golden.yaml"),
            RootCauseCategory.EVAL_GAP,
        )
        assert r.level == RiskLevel.LOW

    def test_low_risk_docs(self):
        r = classify_risk(
            _changes("docs/architecture.md"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.LOW

    def test_low_risk_contract_tests(self):
        r = classify_risk(
            _changes("tests/contract/test_api.py"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.LOW

    def test_medium_risk_tools(self):
        r = classify_risk(
            _changes("agents/echo/tools/search.py"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.MEDIUM

    def test_medium_risk_manifest(self):
        r = classify_risk(
            _changes("agents/echo/manifest.yaml"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.MEDIUM

    def test_medium_risk_adapters(self):
        r = classify_risk(
            _changes("agents/echo/adapters/api.py"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.MEDIUM

    def test_blocked_platform_code(self):
        r = classify_risk(
            _changes("src/agent_platform/runtime/manager.py"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.HIGH
        assert r.requires_human_confirmation_before_devflow is True

    def test_blocked_deploy(self):
        r = classify_risk(
            _changes("deploy/docker-compose.yaml"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.HIGH

    def test_blocked_env(self):
        r = classify_risk(
            _changes(".env"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.HIGH

    def test_blocked_secrets(self):
        r = classify_risk(
            _changes("secrets/api_key.txt"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.HIGH

    def test_blocked_token_pattern(self):
        r = classify_risk(
            _changes("config/gitlab_token.json"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.HIGH

    def test_high_risk_root_cause_platform_bug(self):
        r = classify_risk(
            _changes("agents/echo/prompts/orchestrator.md"),
            RootCauseCategory.PLATFORM_BUG,
        )
        assert r.level == RiskLevel.HIGH

    def test_high_risk_root_cause_product_requirement(self):
        r = classify_risk(
            _changes("agents/echo/prompts/orchestrator.md"),
            RootCauseCategory.PRODUCT_REQUIREMENT,
        )
        assert r.level == RiskLevel.HIGH

    def test_medium_risk_root_cause_tool_schema_gap(self):
        r = classify_risk(
            _changes("agents/echo/prompts/orchestrator.md"),
            RootCauseCategory.TOOL_SCHEMA_GAP,
        )
        assert r.level == RiskLevel.MEDIUM

    def test_medium_risk_root_cause_routing_error(self):
        r = classify_risk(
            _changes("agents/echo/prompts/orchestrator.md"),
            RootCauseCategory.ROUTING_ERROR,
        )
        assert r.level == RiskLevel.MEDIUM

    def test_mixed_paths_blocked_wins(self):
        r = classify_risk(
            _changes("agents/echo/prompts/x.md", "src/agent_platform/config.py"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.HIGH

    def test_mixed_paths_not_all_low(self):
        r = classify_risk(
            _changes("agents/echo/prompts/x.md", "some/unknown/path.py"),
            RootCauseCategory.PROMPT_GAP,
        )
        assert r.level == RiskLevel.MEDIUM

    def test_empty_changes_default_low(self):
        r = classify_risk([], RootCauseCategory.PROMPT_GAP)
        assert r.level == RiskLevel.LOW


class TestPopulateRiskAndPaths:
    def test_low_risk_fills_allowed_paths(self):
        p = _make_proposal(["agents/echo/prompts/orchestrator.md"])
        p = populate_risk_and_paths(p)
        assert p.risk.level == RiskLevel.LOW
        assert "agents/echo/prompts/**" in p.allowed_paths
        assert "agents/echo/evals/**" in p.allowed_paths

    def test_blocked_paths_always_filled(self):
        p = _make_proposal(["agents/echo/prompts/orchestrator.md"])
        p = populate_risk_and_paths(p)
        assert "src/agent_platform/**" in p.blocked_paths

    def test_medium_risk_no_allowed_paths(self):
        p = _make_proposal(["agents/echo/tools/search.py"])
        p = populate_risk_and_paths(p)
        assert p.risk.level == RiskLevel.MEDIUM
        assert p.allowed_paths == []

    def test_existing_allowed_paths_preserved(self):
        p = _make_proposal(["agents/echo/prompts/orchestrator.md"])
        p.allowed_paths = ["custom/path/**"]
        p = populate_risk_and_paths(p)
        assert p.allowed_paths == ["custom/path/**"]
