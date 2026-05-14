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
    tools_allow: list[str] | None = None,
    tools_deny: list[str] | None = None,
    knowledge_sources: list[str] | None = None,
):
    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir(exist_ok=True)
    (prompt_dir / "orchestrator.md").write_text("demo")
    eval_dir = tmp_path / "evals"
    eval_dir.mkdir(exist_ok=True)
    (eval_dir / "golden.yaml").write_text("[]")
    allow = tools_allow if tools_allow is not None else ["myj.goods_search"]
    deny = tools_deny if tools_deny is not None else []
    sources = "\n".join(knowledge_sources or [])
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
  backend: native
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
  protocol: agent-chat/v1
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
