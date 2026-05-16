"""Agent Manifest 加载器：解析、校验 manifest.yaml 并生成 AgentSpec。"""

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_platform.domain.models import AgentManifest, AgentSpec
from agent_platform.policy.secret import validate_secret_refs
from agent_platform.tools import create_default_tool_registry, load_agent_tools


class ManifestError(ValueError):
    """Manifest 格式或内容校验失败时抛出。"""


_AGENT_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_RUNTIME_COMPAT_PATTERN = re.compile(r"^(>=\d+\.\d+\.\d+)( <\d+\.\d+\.\d+)?$")
_ENTRYPOINT_PATTERN = re.compile(
    r"^[a-zA-Z_][\w]*(\.[a-zA-Z_][\w]*)*:[a-zA-Z_][\w]*(\.[a-zA-Z_][\w]*)*$"
)
_COMMAND_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_PLATFORM_VERSION = "0.1.0"
_SUPPORTED_RUNTIME_BACKENDS = {"native", "hermes", "langgraph"}
_SUPPORTED_OUTPUT_PROTOCOLS = {"agent-chat/v1"}
_SUPPORTED_OUTPUT_CAPABILITIES = {"text", "tts", "cards", "commands", "debug"}


class ManifestLoader:
    """加载并校验 Agent manifest.yaml，返回 AgentSpec。"""

    def __init__(self, registered_tools: set[str] | None = None):
        """初始化加载器，可选传入已注册工具集合。"""
        self._explicit_tools = registered_tools
        # When None, tools are discovered dynamically per-package
        # via load_agent_tools in load_file().
        self.registered_tools: set[str] = (
            registered_tools if registered_tools is not None else set()
        )

    def load_file(self, path: Path) -> AgentSpec:
        """从指定路径加载 manifest.yaml 并返回校验后的 AgentSpec。"""
        manifest_path = path.resolve()
        if not manifest_path.exists():
            raise ManifestError(f"manifest not found: {path}")
        if manifest_path.name != "manifest.yaml":
            raise ManifestError(
                "manifest file must be named "
                f"manifest.yaml: {path}"
            )

        raw = yaml.safe_load(manifest_path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ManifestError(
                f"manifest must be a mapping: {path}"
            )

        try:
            manifest = AgentManifest.model_validate(raw)
        except ValidationError as exc:
            raise ManifestError(str(exc)) from exc

        package_path = manifest_path.parent

        # If no explicit tools were provided, dynamically
        # discover tools from the agent package.
        if self._explicit_tools is None:
            registry = create_default_tool_registry()
            load_agent_tools(
                registry, package_path, manifest.metadata.id,
            )

        self._validate_contract(manifest, package_path)
        self._validate_file_refs(package_path, raw)
        return AgentSpec(
            manifest=manifest, package_path=package_path,
        )

    def _validate_contract(self, manifest: AgentManifest, package_path: Path) -> None:
        if not _AGENT_ID_PATTERN.fullmatch(manifest.metadata.id):
            raise ManifestError(
                "metadata.id may only contain lowercase letters, numbers, '-' and '_'"
            )

        if not _SEMVER_PATTERN.fullmatch(manifest.version.package_version):
            raise ManifestError("version.package_version must be SemVer, for example 0.1.0")

        runtime_compat = manifest.version.runtime_compat
        if runtime_compat and not self._runtime_compat_satisfied(runtime_compat):
            raise ManifestError(
                f"version.runtime_compat is not satisfied by platform {_PLATFORM_VERSION}: "
                f"{runtime_compat}"
            )

        if manifest.runtime.backend not in _SUPPORTED_RUNTIME_BACKENDS:
            raise ManifestError(f"unsupported runtime backend: {manifest.runtime.backend}")

        if manifest.runtime.entrypoint and not _ENTRYPOINT_PATTERN.fullmatch(
            manifest.runtime.entrypoint
        ):
            raise ManifestError(
                "runtime.entrypoint must use 'python.module:Symbol' format"
            )

        if manifest.output.protocol not in _SUPPORTED_OUTPUT_PROTOCOLS:
            raise ManifestError(f"unsupported output protocol: {manifest.output.protocol}")

        unsupported_outputs = set(manifest.output.supports) - _SUPPORTED_OUTPUT_CAPABILITIES
        if unsupported_outputs:
            raise ManifestError(f"unsupported output capabilities: {sorted(unsupported_outputs)}")

        for command in manifest.output.command_allowlist:
            if not _COMMAND_PATTERN.fullmatch(command):
                raise ManifestError(
                    f"output.command_allowlist contains invalid command name: {command}"
                )
            if "commands" not in manifest.output.supports:
                raise ManifestError(
                    "output.command_allowlist requires output.supports to include commands"
                )

        for context_path in [*manifest.context.required, *manifest.context.optional]:
            if not context_path.startswith("context."):
                raise ManifestError(
                    f"context paths must start with 'context.': {context_path}"
                )

        duplicate_tools = set(manifest.tools.allow).intersection(manifest.tools.deny)
        if duplicate_tools:
            raise ManifestError(
                f"tools.deny takes precedence; remove denied tools from allow: "
                f"{sorted(duplicate_tools)}"
            )

        for tool_name in manifest.tools.allow:
            if tool_name not in self.registered_tools and not self._is_package_local_tool(
                package_path,
                tool_name,
            ):
                raise ManifestError(f"tool is not registered or package-local: {tool_name}")

        for source in manifest.knowledge.sources:
            if source.backend not in {"weaviate", "postgres", "sqlite", "local"}:
                raise ManifestError(f"unsupported knowledge backend: {source.backend}")
            if source.type in {"vector_collection", "sql_table"} and not source.collection:
                raise ManifestError(
                    f"knowledge source requires collection for type {source.type}: {source.id}"
                )

        secret_errors = validate_secret_refs(manifest.model_dump())
        if secret_errors:
            raise ManifestError(secret_errors[0])

    def _validate_file_refs(self, package_path: Path, raw: dict[str, Any]) -> None:
        refs: list[str] = []
        refs.extend((raw.get("prompts") or {}).values())

        routing_rules = (raw.get("routing") or {}).get("rules")
        if routing_rules:
            refs.append(routing_rules)

        safety_policy = (raw.get("safety") or {}).get("policy")
        if safety_policy:
            refs.append(safety_policy)

        refs.extend((raw.get("evals") or {}).get("suites") or [])

        for ref in refs:
            ref_path = (package_path / ref).resolve()
            if not self._is_relative_to(ref_path, package_path.resolve()):
                raise ManifestError(f"manifest reference escapes package root: {ref}")
            if not ref_path.exists():
                raise ManifestError(f"manifest reference not found: {ref}")

    @staticmethod
    def _is_relative_to(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_package_local_tool(package_path: Path, tool_name: str) -> bool:
        local_name = tool_name.rsplit(".", maxsplit=1)[-1]
        candidates = [
            package_path / "tools" / f"{local_name}.py",
            package_path / "tools" / f"{tool_name.replace('.', '/')}.py",
        ]
        return any(path.exists() for path in candidates)

    @staticmethod
    def _runtime_compat_satisfied(runtime_compat: str) -> bool:
        if not _RUNTIME_COMPAT_PATTERN.fullmatch(runtime_compat):
            return False

        platform = ManifestLoader._parse_version(_PLATFORM_VERSION)
        for clause in runtime_compat.split():
            if clause.startswith(">="):
                if platform < ManifestLoader._parse_version(clause[2:]):
                    return False
            elif clause.startswith("<"):
                if platform >= ManifestLoader._parse_version(clause[1:]):
                    return False
            else:
                return False
        return True

    @staticmethod
    def _parse_version(version: str) -> tuple[int, int, int]:
        major, minor, patch = version.split(".")
        return int(major), int(minor), int(patch)
