import pytest

from agent_platform.domain.models import AgentInput, AgentRequest, RuntimeRequest
from agent_platform.registry.loader import ManifestLoader
from agent_platform.runtime.hermes import HermesRuntimeBackend


@pytest.mark.asyncio
async def test_hermes_backend_placeholder_returns_standard_error(tmp_path):
    manifest = tmp_path / "manifest.yaml"
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    (prompt_dir / "orchestrator.md").write_text("demo")
    (prompt_dir / "reply_style.md").write_text("demo")
    eval_dir = tmp_path / "evals"
    eval_dir.mkdir()
    (eval_dir / "golden.yaml").write_text("[]")
    manifest.write_text(
        """
api_version: agent.platform/v1
kind: AgentPackage
metadata:
  id: hermes_demo
  name: Hermes Demo
version:
  package_version: 0.1.0
runtime:
  backend: hermes
prompts:
  orchestrator: prompts/orchestrator.md
  reply_style: prompts/reply_style.md
output:
  protocol: agent-chat/v1
evals:
  suites:
    - evals/golden.yaml
"""
    )
    spec = ManifestLoader().load_file(manifest)

    result = await HermesRuntimeBackend().run(
        RuntimeRequest(
            request=AgentRequest(agent_id="hermes_demo", input=AgentInput(query="hello")),
            agent_spec=spec,
        )
    )

    assert result.response.output.status == "completed"
    assert result.response.output.text.display.startswith("[Hermes]")
    assert result.response.debug["runtime_backend"] == "hermes"

