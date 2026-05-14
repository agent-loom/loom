import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from agent_platform.domain.models import AgentManifest, AgentSpec
from agent_platform.tools import create_default_tool_registry


class ManifestError(ValueError):
    pass


_AGENT_ID_PATTERN = re.compile(r"^[a-z0-9_-]+$")
_SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_RUNTIME_COMPAT_PATTERN = re.compile(r"^(>=\d+\.\d+\.\d+)( <\d+\.\d+\.\d+)?$")
_PLATFORM_VERSION = "0.1.0"


class ManifestLoader:
    def __init__(self, registered_tools: set[str] | None = None):
        if registered_tools is None:
            registered_tools = {tool.name for tool in create_default_tool_registry().list_tools()}
        self.registered_tools = registered_tools

    def load_file(self, path: Path) -> AgentSpec:
        manifest_path = path.resolve()
        if not manifest_path.exists():
            raise ManifestError(f"manifest not found: {path}")
        if manifest_path.name != "manifest.yaml":
            raise ManifestError(f"manifest file must be named manifest.yaml: {path}")

        raw = yaml.safe_load(manifest_path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ManifestError(f"manifest must be a mapping: {path}")

        try:
            manifest = AgentManifest.model_validate(raw)
        except ValidationError as exc:
            raise ManifestError(str(exc)) from exc

        package_path = manifest_path.parent
        self._validate_contract(manifest, package_path)
        self._validate_file_refs(package_path, raw)
        return AgentSpec(manifest=manifest, package_path=package_path)

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
