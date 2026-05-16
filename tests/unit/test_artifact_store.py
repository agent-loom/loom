"""Tests for InMemoryArtifactStore and LocalArtifactStore."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agent_platform.registry.artifact import (
    ArtifactStore,
    ArtifactStoreProtocol,
    InMemoryArtifactStore,
    LocalArtifactStore,
)


@pytest.fixture()
def agent_dir(tmp_path: Path) -> Path:
    pkg = tmp_path / "my_agent"
    pkg.mkdir()
    (pkg / "main.py").write_text("print('hello')")
    (pkg / "manifest.yaml").write_text("name: my_agent\nversion: 1.0.0\n")
    sub = pkg / "utils"
    sub.mkdir()
    (sub / "helper.py").write_text("def helper(): pass")
    return pkg


@pytest.fixture()
def agent_dir_no_manifest(tmp_path: Path) -> Path:
    pkg = tmp_path / "bare_agent"
    pkg.mkdir()
    (pkg / "run.py").write_text("pass")
    return pkg


# ---------------------------------------------------------------------------
# InMemoryArtifactStore
# ---------------------------------------------------------------------------


class TestInMemoryArtifactStore:
    def test_alias_points_to_in_memory(self):
        assert ArtifactStore is InMemoryArtifactStore

    def test_protocol_conformance(self):
        assert isinstance(InMemoryArtifactStore(), ArtifactStoreProtocol)

    def test_create_and_get_metadata(self, agent_dir: Path):
        store = InMemoryArtifactStore()
        meta = store.create_artifact("agent-a", "1.0.0", agent_dir)

        assert meta.artifact_id == "agent-a@1.0.0"
        assert meta.agent_id == "agent-a"
        assert meta.version == "1.0.0"
        assert meta.size_bytes > 0
        assert len(meta.files) == 3
        assert meta.checksum_sha256

        fetched = store.get_metadata("agent-a@1.0.0")
        assert fetched is not None
        assert fetched.artifact_id == meta.artifact_id

    def test_get_data(self, agent_dir: Path):
        store = InMemoryArtifactStore()
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        data = store.get_data("agent-a@1.0.0")
        assert data is not None
        assert len(data) > 0

    def test_get_metadata_missing(self):
        store = InMemoryArtifactStore()
        assert store.get_metadata("nonexistent@0.0.0") is None

    def test_get_data_missing(self):
        store = InMemoryArtifactStore()
        assert store.get_data("nonexistent@0.0.0") is None

    def test_list_artifacts(self, agent_dir: Path):
        store = InMemoryArtifactStore()
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        store.create_artifact("agent-b", "0.1.0", agent_dir)

        all_artifacts = store.list_artifacts()
        assert len(all_artifacts) == 2

        filtered = store.list_artifacts(agent_id="agent-a")
        assert len(filtered) == 1
        assert filtered[0].agent_id == "agent-a"

    def test_verify_checksum(self, agent_dir: Path):
        store = InMemoryArtifactStore()
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        assert store.verify_checksum("agent-a@1.0.0") is True

    def test_verify_checksum_missing(self):
        store = InMemoryArtifactStore()
        assert store.verify_checksum("missing@0.0.0") is False

    def test_list_versions(self, agent_dir: Path):
        store = InMemoryArtifactStore()
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        store.create_artifact("agent-a", "2.0.0", agent_dir)
        store.create_artifact("agent-b", "0.1.0", agent_dir)

        versions = store.list_versions("agent-a")
        assert versions == ["1.0.0", "2.0.0"]

    def test_get_previous_version(self, agent_dir: Path):
        store = InMemoryArtifactStore()
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        store.create_artifact("agent-a", "2.0.0", agent_dir)

        assert store.get_previous_version("agent-a", "2.0.0") == "1.0.0"
        assert store.get_previous_version("agent-a", "1.0.0") is None
        assert store.get_previous_version("agent-a", "9.9.9") is None

    def test_manifest_sha256_present(self, agent_dir: Path):
        store = InMemoryArtifactStore()
        meta = store.create_artifact("agent-a", "1.0.0", agent_dir)
        expected = hashlib.sha256(
            (agent_dir / "manifest.yaml").read_bytes()
        ).hexdigest()
        assert meta.manifest_sha256 == expected

    def test_manifest_sha256_absent(self, agent_dir_no_manifest: Path):
        store = InMemoryArtifactStore()
        meta = store.create_artifact("bare", "1.0.0", agent_dir_no_manifest)
        assert meta.manifest_sha256 is None


# ---------------------------------------------------------------------------
# LocalArtifactStore
# ---------------------------------------------------------------------------


class TestLocalArtifactStore:
    def test_protocol_conformance(self, tmp_path: Path):
        assert isinstance(LocalArtifactStore(tmp_path), ArtifactStoreProtocol)

    def test_create_and_get_metadata(self, agent_dir: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        meta = store.create_artifact("agent-a", "1.0.0", agent_dir)

        assert meta.artifact_id == "agent-a@1.0.0"
        assert meta.size_bytes > 0
        assert len(meta.files) == 3

        fetched = store.get_metadata("agent-a@1.0.0")
        assert fetched is not None
        assert fetched.artifact_id == meta.artifact_id
        assert fetched.checksum_sha256 == meta.checksum_sha256

    def test_get_data(self, agent_dir: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        data = store.get_data("agent-a@1.0.0")
        assert data is not None
        assert len(data) > 0

    def test_get_metadata_missing(self, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        assert store.get_metadata("missing@0.0.0") is None

    def test_get_data_missing(self, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        assert store.get_data("missing@0.0.0") is None

    def test_files_on_disk(self, agent_dir: Path, tmp_path: Path):
        base = tmp_path / "store"
        store = LocalArtifactStore(base)
        store.create_artifact("agent-a", "1.0.0", agent_dir)

        assert (base / "agent-a" / "1.0.0" / "artifact.tar.gz").exists()
        assert (base / "agent-a" / "1.0.0" / "metadata.json").exists()

    def test_verify_checksum(self, agent_dir: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        assert store.verify_checksum("agent-a@1.0.0") is True

    def test_verify_checksum_missing(self, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        assert store.verify_checksum("missing@0.0.0") is False

    def test_list_artifacts(self, agent_dir: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        store.create_artifact("agent-b", "0.1.0", agent_dir)

        all_artifacts = store.list_artifacts()
        assert len(all_artifacts) == 2

        filtered = store.list_artifacts(agent_id="agent-a")
        assert len(filtered) == 1
        assert filtered[0].agent_id == "agent-a"

    def test_list_versions(self, agent_dir: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        store.create_artifact("agent-a", "2.0.0", agent_dir)

        versions = store.list_versions("agent-a")
        assert versions == ["1.0.0", "2.0.0"]

    def test_list_versions_empty(self, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        assert store.list_versions("nonexistent") == []

    def test_get_previous_version(self, agent_dir: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        store.create_artifact("agent-a", "1.0.0", agent_dir)
        store.create_artifact("agent-a", "2.0.0", agent_dir)

        assert store.get_previous_version("agent-a", "2.0.0") == "1.0.0"
        assert store.get_previous_version("agent-a", "1.0.0") is None
        assert store.get_previous_version("agent-a", "9.9.9") is None

    def test_manifest_sha256_present(self, agent_dir: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        meta = store.create_artifact("agent-a", "1.0.0", agent_dir)
        expected = hashlib.sha256(
            (agent_dir / "manifest.yaml").read_bytes()
        ).hexdigest()
        assert meta.manifest_sha256 == expected

        fetched = store.get_metadata("agent-a@1.0.0")
        assert fetched is not None
        assert fetched.manifest_sha256 == expected

    def test_manifest_sha256_absent(self, agent_dir_no_manifest: Path, tmp_path: Path):
        store = LocalArtifactStore(tmp_path / "store")
        meta = store.create_artifact("bare", "1.0.0", agent_dir_no_manifest)
        assert meta.manifest_sha256 is None

    def test_default_base_dir(self, agent_dir: Path):
        store = LocalArtifactStore()
        meta = store.create_artifact("agent-a", "1.0.0", agent_dir)
        assert meta.artifact_id == "agent-a@1.0.0"
        assert store.verify_checksum("agent-a@1.0.0") is True
