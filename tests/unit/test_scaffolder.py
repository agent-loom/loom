"""Tests for AgentScaffolder — src/agent_platform/devflow/scaffolder.py"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_platform.devflow.scaffolder import AgentScaffolder

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def scaffolder(tmp_path: Path) -> AgentScaffolder:
    return AgentScaffolder(agents_root=tmp_path)


# ---------------------------------------------------------------------------
# Tests — create() directory structure
# ---------------------------------------------------------------------------

def test_create_generates_proper_directory_structure(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="test_agent",
        name="Test Agent",
        description="A test agent",
    )

    assert agent_dir.exists()
    assert agent_dir == tmp_path / "test_agent"

    # Check all expected directories
    expected_dirs = ["prompts", "policies", "tools", "knowledge", "evals", "tests"]
    for d in expected_dirs:
        assert (agent_dir / d).is_dir(), f"Directory '{d}' should exist"


def test_create_generates_expected_files(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="test_agent",
        name="Test Agent",
        description="A test agent",
    )

    expected_files = [
        "manifest.yaml",
        "adapter.py",
        "prompts/orchestrator.md",
        "evals/golden.yaml",
        "tools/__init__.py",
        "tests/__init__.py",
    ]
    for f in expected_files:
        assert (agent_dir / f).is_file(), f"File '{f}' should exist"


def test_create_raises_if_directory_exists(scaffolder: AgentScaffolder, tmp_path: Path):
    scaffolder.create(agent_id="existing_agent", name="Existing")

    with pytest.raises(FileExistsError, match="agent directory already exists"):
        scaffolder.create(agent_id="existing_agent", name="Existing Again")


# ---------------------------------------------------------------------------
# Tests — manifest.yaml content
# ---------------------------------------------------------------------------

def test_generated_manifest_is_valid_yaml(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="demo_bot",
        name="Demo Bot",
        description="A demo bot for testing",
        owner="test-team",
        domain="retail",
        mode="single_worker",
    )

    manifest_path = agent_dir / "manifest.yaml"
    content = manifest_path.read_text(encoding="utf-8")
    manifest = yaml.safe_load(content)

    assert manifest is not None
    assert manifest["api_version"] == "agent.platform/v1"
    assert manifest["kind"] == "AgentPackage"
    assert manifest["metadata"]["id"] == "demo_bot"
    assert manifest["metadata"]["name"] == "Demo Bot"
    assert manifest["metadata"]["description"] == "A demo bot for testing"
    assert manifest["metadata"]["owner"] == "test-team"
    assert manifest["metadata"]["domain"] == "retail"
    assert manifest["entry"]["mode"] == "single_worker"
    assert manifest["version"]["package_version"] == "0.1.0"


def test_manifest_runtime_entrypoint_uses_adapter_class(
    scaffolder: AgentScaffolder, tmp_path: Path,
):
    agent_dir = scaffolder.create(
        agent_id="my_agent",
        name="My Agent",
    )

    manifest_path = agent_dir / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    # "my_agent" -> adapter_class = "MyAgentAdapter"
    assert manifest["runtime"]["entrypoint"] == "agents.my_agent.adapter:MyAgentAdapter"


# ---------------------------------------------------------------------------
# Tests — adapter.py content
# ---------------------------------------------------------------------------

def test_generated_adapter_contains_correct_class_name(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="order_helper",
        name="Order Helper",
    )

    adapter_content = (agent_dir / "adapter.py").read_text(encoding="utf-8")

    # "order_helper" -> "OrderHelperAdapter"
    assert "class OrderHelperAdapter:" in adapter_content
    assert "async def run(self, request: RuntimeRequest)" in adapter_content


def test_adapter_class_name_for_single_word_id(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="echo",
        name="Echo",
    )

    adapter_content = (agent_dir / "adapter.py").read_text(encoding="utf-8")
    assert "class EchoAdapter:" in adapter_content


# ---------------------------------------------------------------------------
# Tests — prompts directory
# ---------------------------------------------------------------------------

def test_generated_prompts_directory_has_orchestrator(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="test_agent",
        name="Test Agent",
        description="Handles test queries",
    )

    prompt_path = agent_dir / "prompts" / "orchestrator.md"
    assert prompt_path.is_file()

    content = prompt_path.read_text(encoding="utf-8")
    assert "Test Agent" in content
    assert "Handles test queries" in content


# ---------------------------------------------------------------------------
# Tests — evals directory
# ---------------------------------------------------------------------------

def test_generated_evals_directory_has_golden(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="test_agent",
        name="Test Agent",
    )

    eval_path = agent_dir / "evals" / "golden.yaml"
    assert eval_path.is_file()

    content = eval_path.read_text(encoding="utf-8")
    assert "test_agent" in content
    assert "test_agent_demo_001" in content


def test_evals_golden_is_valid_yaml(scaffolder: AgentScaffolder, tmp_path: Path):
    agent_dir = scaffolder.create(
        agent_id="retail_bot",
        name="Retail Bot",
    )

    eval_path = agent_dir / "evals" / "golden.yaml"
    evals = yaml.safe_load(eval_path.read_text(encoding="utf-8"))

    assert isinstance(evals, list)
    assert len(evals) == 1
    assert evals[0]["id"] == "retail_bot_demo_001"
    assert evals[0]["input"]["query"] == "hello"


# ---------------------------------------------------------------------------
# Tests — list_templates
# ---------------------------------------------------------------------------

def test_list_templates_returns_known_modes(scaffolder: AgentScaffolder):
    templates = scaffolder.list_templates()
    assert "single_worker" in templates
    assert "orchestrator_workers" in templates
    assert "graph" in templates
