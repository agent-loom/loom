from __future__ import annotations

import hashlib
import logging
import tarfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ArtifactMetadata(BaseModel):
    artifact_id: str
    agent_id: str
    version: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    checksum_sha256: str
    size_bytes: int
    files: list[str] = Field(default_factory=list)


class ArtifactStore:
    """In-memory store for agent artifacts (tar.gz packages with metadata and checksums)."""

    def __init__(self) -> None:
        self._artifacts: dict[str, ArtifactMetadata] = {}
        self._artifact_data: dict[str, bytes] = {}

    def create_artifact(self, agent_id: str, version: str, package_path: Path) -> ArtifactMetadata:
        """Package an agent directory into a tar.gz artifact with SHA256 checksum."""
        artifact_id = f"{agent_id}@{version}"

        buf = BytesIO()
        files: list[str] = []

        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for file_path in sorted(package_path.rglob("*")):
                if file_path.is_file():
                    rel = str(file_path.relative_to(package_path))
                    files.append(rel)
                    tar.add(file_path, arcname=rel)

        data = buf.getvalue()
        checksum = hashlib.sha256(data).hexdigest()

        metadata = ArtifactMetadata(
            artifact_id=artifact_id,
            agent_id=agent_id,
            version=version,
            checksum_sha256=checksum,
            size_bytes=len(data),
            files=files,
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
        """List all artifact versions for an agent, useful for rollback."""
        versions = []
        for _aid, meta in self._artifacts.items():
            if meta.agent_id == agent_id:
                versions.append(meta.version)
        return sorted(versions)

    def get_previous_version(self, agent_id: str, current_version: str) -> str | None:
        """Get the version before current_version for rollback."""
        versions = self.list_versions(agent_id)
        try:
            idx = versions.index(current_version)
            if idx > 0:
                return versions[idx - 1]
        except ValueError:
            pass
        return None
