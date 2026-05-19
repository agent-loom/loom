"""ProposalToTaskPackConverter 单元测试。"""
import pytest

from agent_platform.devflow.task_pack import DevelopmentTask
from agent_platform.evolution.models import (
    Evidence,
    EvidenceType,
    ImprovementProposal,
    ProposedChange,
    RiskAssessment,
    RiskLevel,
    RootCause,
    RootCauseCategory,
    ValidationSpec,
)
from agent_platform.evolution.task_pack_converter import ProposalToTaskPackConverter


def _make_proposal(**overrides) -> ImprovementProposal:
    defaults = {
        "title": "[echo] eval 准确率下降",
        "summary": "echo agent 的 eval 准确率从 95% 降至 80%",
        "agent_id": "echo",
        "risk": RiskAssessment(level=RiskLevel.LOW, reason="仅修改 prompt/eval"),
        "root_cause": RootCause(
            category=RootCauseCategory.PROMPT_GAP,
            confidence=0.8,
            explanation="prompt 未覆盖新场景",
        ),
        "evidence": [
            Evidence(
                type=EvidenceType.EVAL_FAILURE,
                id="eval_001",
                summary="golden case #5 失败",
            ),
        ],
        "proposed_changes": [
            ProposedChange(
                type="prompt_update",
                path="agents/echo/prompts/orchestrator.md",
                description="优化 prompt 覆盖新场景",
            ),
            ProposedChange(
                type="eval_case_add",
                path="agents/echo/evals/golden.yaml",
                description="新增回归用例",
            ),
        ],
        "allowed_paths": [
            "agents/echo/prompts/**",
            "agents/echo/evals/**",
            "tests/contract/**",
            "docs/**",
        ],
        "blocked_paths": [
            "src/agent_platform/**",
            "deploy/**",
            ".env",
            "secrets/**",
        ],
        "validation": ValidationSpec(
            commands=["pytest tests/unit -x -q"],
        ),
    }
    defaults.update(overrides)
    return ImprovementProposal(**defaults)


@pytest.fixture
def converter() -> ProposalToTaskPackConverter:
    return ProposalToTaskPackConverter(gitlab_project_id="12345")


class TestConvert:
    def test_returns_development_task(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert isinstance(task, DevelopmentTask)
        assert task.api_version == "devflow.agent-platform/v1"
        assert task.kind == "DevelopmentTask"

    def test_metadata_mapping(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert task.metadata.task_id == proposal.proposal_id
        assert task.metadata.title == proposal.title
        assert task.metadata.type == proposal.task_type
        assert task.metadata.source["system"] == "evolution_engine"
        assert task.metadata.source["proposal_id"] == proposal.proposal_id

    def test_repository_target(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert task.repository.project_id == "12345"
        assert task.repository.work_branch.startswith("evo/")
        assert "echo" in task.repository.work_branch
        assert task.repository.default_branch == "main"

    def test_mr_labels_include_evolution_and_risk(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        labels = task.repository.merge_request.labels
        assert "evolution" in labels
        assert "risk:low" in labels
        assert "ai-generated" in labels

    def test_mr_title_prefixed(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert task.repository.merge_request.title.startswith("[Evolution]")

    def test_scope_uses_proposal_paths(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert task.scope["write_allowed"] == proposal.allowed_paths
        assert task.scope["write_denied"] == proposal.blocked_paths

    def test_scope_defaults_when_no_allowed_paths(self, converter):
        proposal = _make_proposal(allowed_paths=[])
        task = converter.convert(proposal)
        assert "agents/echo/**" in task.scope["write_allowed"]
        assert "tests/contract/**" in task.scope["write_allowed"]

    def test_validation_includes_eval_command(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        commands = task.validation["commands"]
        assert any("eval" in cmd for cmd in commands)

    def test_validation_no_duplicate_eval(self, converter):
        proposal = _make_proposal(
            validation=ValidationSpec(
                commands=["pytest tests/unit -x -q", "python scripts/run_agent_eval.py --agent echo"],
            ),
        )
        task = converter.convert(proposal)
        eval_cmds = [c for c in task.validation["commands"] if "eval" in c]
        assert len(eval_cmds) == 1

    def test_background_contains_evidence(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert "golden case #5 失败" in task.requirement.background
        assert proposal.proposal_id in task.requirement.background

    def test_implementation_outputs(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        outputs = task.implementation["required_outputs"]
        assert "agents/echo/prompts/orchestrator.md" in outputs
        assert "agents/echo/evals/golden.yaml" in outputs

    def test_priority_based_on_risk(self, converter):
        low = _make_proposal(risk=RiskAssessment(level=RiskLevel.LOW, reason="low"))
        medium = _make_proposal(risk=RiskAssessment(level=RiskLevel.MEDIUM, reason="med"))
        assert converter.convert(low).metadata.priority == "P2"
        assert converter.convert(medium).metadata.priority == "P1"

    def test_agent_info(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert task.agent["agent_id"] == "echo"
        assert task.agent["package_path"] == "agents/echo"

    def test_review_checklist_mentions_root_cause(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        checklist = task.review["checklist"]
        assert any("prompt_gap" in item for item in checklist)

    def test_mr_description_generated(self, converter):
        proposal = _make_proposal()
        task = converter.convert(proposal)
        desc = task.repository.merge_request.description
        assert "## Source Task" in desc
        assert "## Requirement Summary" in desc

    def test_custom_default_branch(self):
        converter = ProposalToTaskPackConverter(
            gitlab_project_id="99",
            default_branch="develop",
        )
        proposal = _make_proposal()
        task = converter.convert(proposal)
        assert task.repository.default_branch == "develop"
