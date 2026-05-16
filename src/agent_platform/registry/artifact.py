"""Agent 制品存储：打包、校验和版本管理。"""

from __future__ import annotations

import hashlib
import logging
import tarfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ArtifactMetadata(BaseModel):
    """制品元数据，包含校验和与文件清单。"""
    artifact_id: str
    agent_id: str
    version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checksum_sha256: str
    size_bytes: int
    files: list[str] = Field(default_factory=list)
    manifest_sha256: str | None = None


@runtime_checkable
class ArtifactStoreProtocol(Protocol):
    def create_artifact(
        self, agent_id: str, version: str, package_path: Path
    ) -> ArtifactMetadata: ...

    def get_metadata(self, artifact_id: str) -> ArtifactMetadata | None: ...

    def get_data(self, artifact_id: str) -> bytes | None: ...

    def list_artifacts(self, agent_id: str | None = None) -> list[ArtifactMetadata]: ...

    def verify_checksum(self, artifact_id: str) -> bool: ...

    def list_versions(self, agent_id: str) -> list[str]: ...

    def get_previous_version(self, agent_id: str, current_version: str) -> str | None: ...


def _compute_manifest_sha256(package_path: Path) -> str | None:
    manifest_path = package_path / "manifest.yaml"
    if not manifest_path.exists():
        manifest_path = package_path / "manifest.yml"
    if not manifest_path.exists():
        return None
    return hashlib.sha256(manifest_path.read_bytes()).hexdigest()


def _package_directory(package_path: Path) -> tuple[bytes, list[str], str | None]:
    buf = BytesIO()
    files: list[str] = []

    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for file_path in sorted(package_path.rglob("*")):
            if file_path.is_file():
                rel = str(file_path.relative_to(package_path))
                files.append(rel)
                tar.add(file_path, arcname=rel)

    data = buf.getvalue()
    manifest_sha = _compute_manifest_sha256(package_path)
    return data, files, manifest_sha


class InMemoryArtifactStore:
    """In-memory store for agent artifacts (tar.gz packages with metadata and checksums)."""

    def __init__(self) -> None:
        self._artifacts: dict[str, ArtifactMetadata] = {}
        self._artifact_data: dict[str, bytes] = {}

    def create_artifact(self, agent_id: str, version: str, package_path: Path) -> ArtifactMetadata:
        artifact_id = f"{agent_id}@{version}"

        data, files, manifest_sha = _package_directory(package_path)
        checksum = hashlib.sha256(data).hexdigest()

        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            agent_id=agent_id,
            version=version,
            checksum_sha256=checksum,
            size_bytes=len(data),
            files=files,
            manifest_sha256=manifest_sha,
        )

        self._artifacts[artifact_id] = metadata
        self._artifact_data[artifact_id] = data

        logger.info(
            "created artifact %s (%d bytes, %d files, sha256=%s...)",
            artifact_id,
            len(data),
            len(files),
            checksum[:16],
        )
        return metadata

    def get_metadata(self, artifact_id: str) -> ArtifactMetadata | None:
        return self._artifacts.get(artifact_id)

    def get_data(self, artifact_id: str) -> bytes | None:
        return self._artifact_data.get(artifact_id)

    def list_artifacts(self, agent_id: str | None = None) -> list[ArtifactMetadata]:
        artifacts = list(self._artifacts.values())
        if agent_id:
            artifacts = [a for a in artifacts if a.agent_id == agent_id]
        return artifacts

    def verify_checksum(self, artifact_id: str) -> bool:
        metadata = self._artifacts.get(artifact_id)
        data = self._artifact_data.get(artifact_id)
        if not metadata or not data:
            return False
        return hashlib.sha256(data).hexdigest() == metadata.checksum_sha256

    def list_versions(self, agent_id: str) -> list[str]:
        versions = []
        for _aid, meta in self._artifacts.items():
            if meta.agent_id == agent_id:
                versions.append(meta.version)
        return sorted(versions)

    def get_previous_version(self, agent_id: str, current_version: str) -> str | None:
        versions = self.list_versions(agent_id)
        try:
            idx = versions.index(current_version)
            if idx > 0:
                return versions[idx - 1]
        except ValueError:
            pass
        return None


class LocalArtifactStore:
    """Filesystem-backed store for agent artifacts."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            import tempfile
            base_dir = Path(tempfile.mkdtemp(prefix="artifact_store_"))
        self._base_dir = base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _version_dir(self, agent_id: str, version: str) -> Path:
        return self._base_dir / agent_id / version

    def _artifact_path(self, agent_id: str, version: str) -> Path:
        return self._version_dir(agent_id, version) / "artifact.tar.gz"

    def _metadata_path(self, agent_id: str, version: str) -> Path:
        return self._version_dir(agent_id, version) / "metadata.json"

    def _parse_artifact_id(self, artifact_id: str) -> tuple[str, str]:
        agent_id, version = artifact_id.rsplit("@", 1)
        return agent_id, version

    def create_artifact(self, agent_id: str, version: str, package_path: Path) -> ArtifactMetadata:
        artifact_id = f"{agent_id}@{version}"
        vdir = self._version_dir(agent_id, version)
        vdir.mkdir(parents=True, exist_ok=True)

        data, files, manifest_sha = _package_directory(package_path)
        checksum = hashlib.sha256(data).hexdigest()

        self._artifact_path(agent_id, version).write_bytes(data)

        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            agent_id=agent_id,
            version=version,
            checksum_sha256=checksum,
            size_bytes=len(data),
            files=files,
            manifest_sha256=manifest_sha,
        )

        self._metadata_path(agent_id, version).write_text(
            metadata.model_dump_json(indent=2),
            encoding="utf-8",
        )

        logger.info(
            "created artifact %s (%d bytes, %d files, sha256=%s...)",
            artifact_id,
            len(data),
            len(files),
            checksum[:16],
        )
        return metadata

    def get_metadata(self, artifact_id: str) -> ArtifactMetadata | None:
        agent_id, version = self._parse_artifact_id(artifact_id)
        meta_path = self._metadata_path(agent_id, version)
        if not meta_path.exists():
            return None
        return ArtifactMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def get_data(self, artifact_id: str) -> bytes | None:
        agent_id, version = self._parse_artifact_id(artifact_id)
        art_path = self._artifact_path(agent_id, version)
        if not art_path.exists():
            return None
        return art_path.read_bytes()

    def list_artifacts(self, agent_id: str | None = None) -> list[ArtifactMetadata]:
        results: list[ArtifactMetadata] = []
        if agent_id:
            agent_dir = self._base_dir / agent_id
            if not agent_dir.exists():
                return []
            for version_dir in sorted(agent_dir.iterdir()):
                meta_path = version_dir / "metadata.json"
                if meta_path.exists():
                    results.append(
                        ArtifactMetadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
                    )
        else:
            if not self._base_dir.exists():
                return []
            for agent_dir in sorted(self._base_dir.iterdir()):
                if agent_dir.is_dir():
                    for version_dir in sorted(agent_dir.iterdir()):
                        meta_path = version_dir / "metadata.json"
                        if meta_path.exists():
                            results.append(
                                ArtifactMetadata.model_validate_json(
                                    meta_path.read_text(encoding="utf-8")
                                )
                            )
        return results

    def verify_checksum(self, artifact_id: str) -> bool:
        metadata = self.get_metadata(artifact_id)
        data = self.get_data(artifact_id)
        if not metadata or not data:
            return False
        return hashlib.sha256(data).hexdigest() == metadata.checksum_sha256

    def list_versions(self, agent_id: str) -> list[str]:
        agent_dir = self._base_dir / agent_id
        if not agent_dir.exists():
            return []
        versions = []
        for version_dir in sorted(agent_dir.iterdir()):
            meta_path = version_dir / "metadata.json"
            if meta_path.exists():
                versions.append(version_dir.name)
        return sorted(versions)

    def get_previous_version(self, agent_id: str, current_version: str) -> str | None:
        versions = self.list_versions(agent_id)
        try:
            idx = versions.index(current_version)
            if idx > 0:
                return versions[idx - 1]
        except ValueError:
            pass
        return None


ArtifactStore = InMemoryArtifactStore
