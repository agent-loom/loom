"""FeedbackIntelligence → EvolutionEvent 适配器单元测试。"""
import pytest

from agent_platform.evolution.feedback_adapter import (
    batch_convert,
    requirement_to_evolution_event,
)
from agent_platform.evolution.models import EvolutionEvent
from agent_platform.feedback.miner import RequirementProposal


def _make_proposal(**overrides) -> RequirementProposal:
    defaults = {
        "proposal_type": "bug",
        "title": "[Bug] echo: 超时错误",
        "agent_id": "echo",
        "severity": "medium",
        "confidence": 0.75,
        "evidence": [
            {"run_id": "run_001", "summary": "timeout after 30s"},
            {"run_id": "run_002", "summary": "timeout after 30s"},
        ],
        "impact": {
            "affected_tenants": 2,
            "affected_sessions": 5,
            "first_seen": "2026-05-15T10:00:00",
            "last_seen": "2026-05-19T15:00:00",
        },
        "suggested_task_type": "agent:change",
    }
    defaults.update(overrides)
    return RequirementProposal(**defaults)


class TestRequirementToEvolutionEvent:
    def test_returns_evolution_event(self):
        event = requirement_to_evolution_event(_make_proposal())
        assert isinstance(event, EvolutionEvent)

    def test_bug_maps_to_tool_error(self):
        event = requirement_to_evolution_event(_make_proposal(proposal_type="bug"))
        assert event.event_type == "tool_error"

    def test_eval_gap_maps_to_eval_failure(self):
        event = requirement_to_evolution_event(_make_proposal(proposal_type="eval_gap"))
        assert event.event_type == "eval_failure"

    def test_optimization_maps_to_runtime_anomaly(self):
        event = requirement_to_evolution_event(_make_proposal(proposal_type="optimization"))
        assert event.event_type == "runtime_anomaly"

    def test_feature_maps_to_user_feedback(self):
        event = requirement_to_evolution_event(_make_proposal(proposal_type="feature"))
        assert event.event_type == "user_feedback"

    def test_unknown_type_defaults_to_user_feedback(self):
        event = requirement_to_evolution_event(_make_proposal(proposal_type="custom"))
        assert event.event_type == "user_feedback"

    def test_agent_id_preserved(self):
        event = requirement_to_evolution_event(_make_proposal(agent_id="code_review"))
        assert event.agent_id == "code_review"

    def test_summary_is_title(self):
        event = requirement_to_evolution_event(_make_proposal(title="测试标题"))
        assert event.summary == "测试标题"

    def test_details_contain_source(self):
        event = requirement_to_evolution_event(_make_proposal())
        assert event.details["source"] == "feedback_intelligence"
        assert event.details["proposal_type"] == "bug"
        assert event.details["severity"] == "medium"
        assert event.details["confidence"] == 0.75

    def test_evidence_run_ids_extracted(self):
        event = requirement_to_evolution_event(_make_proposal())
        assert "run_001" in event.details["evidence_run_ids"]
        assert "run_002" in event.details["evidence_run_ids"]

    def test_evidence_run_ids_capped_at_3(self):
        evidence = [{"run_id": f"run_{i}", "summary": "test"} for i in range(10)]
        event = requirement_to_evolution_event(_make_proposal(evidence=evidence))
        assert len(event.details["evidence_run_ids"]) == 3


class TestBatchConvert:
    def test_batch_convert_empty(self):
        assert batch_convert([]) == []

    def test_batch_convert_multiple(self):
        proposals = [
            _make_proposal(agent_id="echo"),
            _make_proposal(agent_id="code_review", proposal_type="eval_gap"),
        ]
        events = batch_convert(proposals)
        assert len(events) == 2
        assert events[0].agent_id == "echo"
        assert events[1].agent_id == "code_review"
        assert events[1].event_type == "eval_failure"
