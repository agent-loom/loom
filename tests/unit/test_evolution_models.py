"""evolution models 单元测试。"""
from datetime import UTC, datetime

import pytest

from agent_platform.evolution.models import (
    Evidence,
    EvidenceType,
    EvolutionEvent,
    ImprovementProposal,
    ProposalSource,
    ProposalStatus,
    ProposedChange,
    RiskAssessment,
    RiskLevel,
    RootCause,
    RootCauseCategory,
    ValidationSpec,
)


def _make_proposal(**overrides) -> ImprovementProposal:
    defaults = {
        "title": "测试提案",
        "summary": "测试摘要",
        "agent_id": "echo",
        "risk": RiskAssessment(level=RiskLevel.LOW, reason="test"),
        "root_cause": RootCause(
            category=RootCauseCategory.PROMPT_GAP,
            confidence=0.8,
            explanation="test",
        ),
        "evidence": [
            Evidence(type=EvidenceType.EVAL_FAILURE, id="e1", summary="test evidence"),
        ],
    }
    defaults.update(overrides)
    return ImprovementProposal(**defaults)


class TestImprovementProposal:
    def test_default_fields(self):
        p = _make_proposal()
        assert p.proposal_id.startswith("evo_")
        assert p.status == ProposalStatus.DRAFT
        assert p.schema_version == 1
        assert p.tenant_id == "default"
        assert p.source == ProposalSource.EVOLUTION_ENGINE
        assert p.plane_work_item_id is None
        assert p.gitlab_mr_iid is None
        assert p.closed_at is None

    def test_blocked_paths_default(self):
        p = _make_proposal()
        assert "src/agent_platform/**" in p.blocked_paths
        assert "deploy/**" in p.blocked_paths
        assert ".env" in p.blocked_paths

    def test_evidence_min_length(self):
        with pytest.raises(Exception):
            _make_proposal(evidence=[])

    def test_proposal_id_uniqueness(self):
        p1 = _make_proposal()
        p2 = _make_proposal()
        assert p1.proposal_id != p2.proposal_id

    def test_json_serialization(self):
        p = _make_proposal()
        data = p.model_dump(mode="json")
        assert data["status"] == "draft"
        assert data["risk"]["level"] == "low"
        assert isinstance(data["created_at"], str)

    def test_root_cause_confidence_bounds(self):
        with pytest.raises(Exception):
            RootCause(category=RootCauseCategory.PROMPT_GAP, confidence=1.5, explanation="x")
        with pytest.raises(Exception):
            RootCause(category=RootCauseCategory.PROMPT_GAP, confidence=-0.1, explanation="x")


class TestEvolutionEvent:
    def test_defaults(self):
        e = EvolutionEvent(
            event_type="eval_failure",
            agent_id="echo",
            summary="eval failed",
        )
        assert e.tenant_id == "default"
        assert isinstance(e.created_at, datetime)
        assert e.details == {}

    def test_with_details(self):
        e = EvolutionEvent(
            event_type="tool_error",
            agent_id="code_review",
            summary="tool crashed",
            details={"trace_id": "tr_123", "tool_name": "search"},
        )
        assert e.details["trace_id"] == "tr_123"


class TestValidationSpec:
    def test_defaults(self):
        v = ValidationSpec()
        assert v.commands == []
        assert v.existing_eval_regression_allowed is False

    def test_with_commands(self):
        v = ValidationSpec(commands=["pytest tests/ -x"])
        assert len(v.commands) == 1
