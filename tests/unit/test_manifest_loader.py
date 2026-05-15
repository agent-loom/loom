from pathlib import Path

import pytest

from agent_platform.registry.loader import ManifestError, ManifestLoader


def test_load_myj_manifest():
    spec = ManifestLoader().load_file(Path("agents/myj/manifest.yaml"))

    assert spec.agent_id == "myj"
    assert spec.version == "0.1.0"
    assert spec.manifest.runtime.backend == "native"


def test_manifest_rejects_invalid_agent_id(tmp_path):
    manifest = _write_manifest(tmp_path, metadata_id="Bad.Agent")

    with pytest.raises(ManifestError, match="metadata.id"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_invalid_semver(tmp_path):
    manifest = _write_manifest(tmp_path, package_version="v1")

    with pytest.raises(ManifestError, match="SemVer"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_unsatisfied_runtime_compat(tmp_path):
    manifest = _write_manifest(tmp_path, runtime_compat=">=9.0.0 <10.0.0")

    with pytest.raises(ManifestError, match="runtime_compat"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_unknown_runtime_backend(tmp_path):
    manifest = _write_manifest(tmp_path, runtime_backend="unknown")

    with pytest.raises(ManifestError, match="unsupported runtime backend"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_invalid_entrypoint(tmp_path):
    manifest = _write_manifest(tmp_path, runtime_entrypoint="not-a-python-entrypoint")

    with pytest.raises(ManifestError, match="runtime.entrypoint"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_invalid_output_protocol(tmp_path):
    manifest = _write_manifest(tmp_path, output_protocol="agent-chat/v9")

    with pytest.raises(ManifestError, match="unsupported output protocol"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_unsupported_output_capability(tmp_path):
    manifest = _write_manifest(tmp_path, output_supports=["text", "video"])

    with pytest.raises(ManifestError, match="unsupported output capabilities"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_invalid_command_name(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        output_supports=["text", "commands"],
        command_allowlist=["Product.Recommend"],
    )

    with pytest.raises(ManifestError, match="invalid command name"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_commands_without_command_output_support(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        output_supports=["text"],
        command_allowlist=["product.recommend"],
    )

    with pytest.raises(ManifestError, match="requires output.supports"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_invalid_context_path(tmp_path):
    manifest = _write_manifest(tmp_path, required_context=["tenant.retailer_id"])

    with pytest.raises(ManifestError, match="context paths"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_allow_and_deny_overlap(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        tools_allow=["myj.goods_search"],
        tools_deny=["myj.goods_search"],
    )

    with pytest.raises(ManifestError, match="tools.deny"):
        ManifestLoader().load_file(manifest)


def test_manifest_rejects_unknown_tool(tmp_path):
    manifest = _write_manifest(tmp_path, tools_allow=["unknown.tool"])

    with pytest.raises(ManifestError, match="tool is not registered"):
        ManifestLoader().load_file(manifest)


def test_manifest_allows_package_local_tool(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "custom_tool.py").write_text("def handle():\n    return {}\n")
    manifest = _write_manifest(tmp_path, tools_allow=["custom_tool"])

    spec = ManifestLoader().load_file(manifest)

    assert spec.manifest.tools.allow == ["custom_tool"]


def test_manifest_rejects_invalid_knowledge_source(tmp_path):
    manifest = _write_manifest(
        tmp_path,
        knowledge_sources=[
            """
    - id: bad
      type: vector_collection
      backend: unknown
      collection: Goods
"""
        ],
    )

    with pytest.raises(ManifestError, match="unsupported knowledge backend"):
        ManifestLoader().load_file(manifest)


def _write_manifest(
    tmp_path,
    *,
    metadata_id: str = "demo_agent",
    package_version: str = "0.1.0",
    runtime_compat: str = ">=0.1.0 <0.2.0",
    runtime_backend: str = "native",
    runtime_entrypoint: str | None = None,
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
    knowledge_sources: list[str] | None = None,
    output_protocol: str = "agent-chat/v1",
    output_supports: list[str] | None = None,
    command_allowlist: list[str] | None = None,
    required_context: list[str] | None = None,
):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir(exist_ok=True)
    (prompt_dir / "orchestrator.md").write_text("demo")
    eval_dir = tmp_path / "evals"
    eval_dir.mkdir(exist_ok=True)
    (eval_dir / "golden.yaml").write_text("[]")
    allow = tools_allow if tools_allow is not None else []
    deny = tools_deny if tools_deny is not None else []
    sources = "\n".join(knowledge_sources or [])
    entrypoint_line = f"  entrypoint: {runtime_entrypoint}\n" if runtime_entrypoint else ""
    supports = output_supports if output_supports is not None else ["text"]
    commands = command_allowlist if command_allowlist is not None else []
    required = required_context if required_context is not None else []
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"""
api_version: agent.platform/v1
kind: AgentPackage
metadata:
  id: {metadata_id}
  name: Demo Agent
version:
  package_version: {package_version}
  runtime_compat: "{runtime_compat}"
runtime:
  backend: {runtime_backend}
{entrypoint_line}context:
  required:
{_yaml_list(required)}
prompts:
  orchestrator: prompts/orchestrator.md
tools:
  allow:
{_yaml_list(allow)}
  deny:
{_yaml_list(deny)}
knowledge:
  sources:
{sources or "    []"}
output:
  protocol: {output_protocol}
  supports:
{_yaml_list(supports)}
  command_allowlist:
{_yaml_list(commands)}
evals:
  suites:
    - evals/golden.yaml
"""
    )
    return manifest


def _yaml_list(values: list[str]) -> str:
    if not values:
        return "    []"
    return "\n".join(f"    - {value}" for value in values)
