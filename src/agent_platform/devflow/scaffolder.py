from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MANIFEST_TEMPLATE = """\
api_version: agent.platform/v1
kind: AgentPackage

metadata:
  id: {agent_id}
  name: {name}
  description: {description}
  owner: {owner}
  domain: {domain}
  tags: []

version:
  package_version: 0.1.0
  release_channel: dev

entry:
  mode: {mode}
  default_worker: direct_reply

runtime:
  backend: native
  entrypoint: agents.{agent_id}.adapter:{adapter_class}
  max_iterations: 4
  timeout_ms: 5000

models:
  default:
    provider: stub
    model: stub
    temperature: 0.2
    max_tokens: 1024

prompts:
  orchestrator: prompts/orchestrator.md

tools:
  allow: []
  deny:
    - terminal
    - code_execution
  timeout_ms: 3000
  max_parallel: 2

knowledge:
  sources: []

routing:
  strategy: single
  fallback_worker: direct_reply

session:
  scope: session
  history_window: 20
  memory_enabled: false

context:
  required: []
  optional: []

output:
  protocol: agent-chat/v1
  supports:
    - text
  command_allowlist: []

safety:
  moderation:
    input: false
    output: false

evals:
  suites:
    - evals/golden.yaml
  required_pass_rate: 0.9
"""

ADAPTER_TEMPLATE = '''\
from __future__ import annotations

from agent_platform.domain.models import (
    AgentIdentity,
    AgentOutput,
    AgentResponse,
    ResponseText,
    ResponseTrace,
    RuntimeRequest,
    RuntimeResponse,
)


class {adapter_class}:
    async def run(self, request: RuntimeRequest) -> RuntimeResponse:
        agent = request.agent_spec
        query = request.request.input.query
        display = f"Agent {{agent.agent_id}} received: {{query}}"
        response = AgentResponse(
            request_id=request.request.request_id,
            session_id=request.request.session_id,
            agent=AgentIdentity(
                agent_id=agent.agent_id,
                agent_version=agent.version,
                deployment_id=request.deployment_id,
            ),
            output=AgentOutput(text=ResponseText(display=display, tts=display)),
            trace=ResponseTrace(route_reason=request.route_reason),
        )
        return RuntimeResponse(response=response)
'''

ORCHESTRATOR_PROMPT_TEMPLATE = """\
你是 {name}。

## 职责
{description}

## 约束
- 只能使用允许的工具
- 回复要简洁明了
- 不确定时请求用户澄清
"""

EVAL_TEMPLATE = """\
- id: {agent_id}_demo_001
  input:
    query: "hello"
  expected:
    output_contains:
      - "{agent_id}"
"""


class AgentScaffolder:
    """Creates a new Agent Package directory structure from templates."""

    def __init__(self, agents_root: str | Path = "agents"):
        self.agents_root = Path(agents_root)

    def create(
        self,
        agent_id: str,
        name: str,
        description: str = "",
        owner: str = "platform",
        domain: str = "general",
        mode: str = "single_worker",
    ) -> Path:
        agent_dir = self.agents_root / agent_id
        if agent_dir.exists():
            raise FileExistsError(f"agent directory already exists: {agent_dir}")

        adapter_class = "".join(w.capitalize() for w in agent_id.split("_")) + "Adapter"

        agent_dir.mkdir(parents=True)
        (agent_dir / "prompts").mkdir()
        (agent_dir / "policies").mkdir()
        (agent_dir / "tools").mkdir()
        (agent_dir / "knowledge").mkdir()
        (agent_dir / "evals").mkdir()
        (agent_dir / "tests").mkdir()

        (agent_dir / "manifest.yaml").write_text(
            MANIFEST_TEMPLATE.format(
                agent_id=agent_id,
                name=name,
                description=description,
                owner=owner,
                domain=domain,
                mode=mode,
                adapter_class=adapter_class,
            ),
            encoding="utf-8",
        )

        (agent_dir / "adapter.py").write_text(
            ADAPTER_TEMPLATE.format(adapter_class=adapter_class),
            encoding="utf-8",
        )

        (agent_dir / "prompts" / "orchestrator.md").write_text(
            ORCHESTRATOR_PROMPT_TEMPLATE.format(name=name, description=description),
            encoding="utf-8",
        )

        (agent_dir / "evals" / "golden.yaml").write_text(
            EVAL_TEMPLATE.format(agent_id=agent_id),
            encoding="utf-8",
        )

        (agent_dir / "tools" / "__init__.py").write_text("", encoding="utf-8")
        (agent_dir / "tests" / "__init__.py").write_text("", encoding="utf-8")

        logger.info("created agent package: %s at %s", agent_id, agent_dir)
        return agent_dir

    def list_templates(self) -> list[str]:
        return ["single_worker", "orchestrator_workers", "graph"]
