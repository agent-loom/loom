"""产物签名验证器单元测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_platform.artifacts.signer import ArtifactSigner
from agent_platform.domain.models import AgentManifest

# ---------------------------------------------------------------------------
# 辅助工具
# ---------------------------------------------------------------------------

def _make_manifest(**overrides) -> AgentManifest:
    """创建一个最小的 AgentManifest 用于测试。"""
    defaults = {
        "api_version": "agent.platform/v1",
        "kind": "AgentPackage",
        "metadata": {"id": "test-agent", "name": "Test Agent"},
        "version": {"package_version": "1.0.0"},
        "output": {"protocol": "agent-chat/v1"},
    }
    defaults.update(overrides)
    return AgentManifest(**defaults)


def _make_package_dir(files: dict[str, str] | None = None) -> Path:
    """创建临时 package 目录并写入文件。"""
    tmpdir = Path(tempfile.mkdtemp())
    if files is None:
        files = {
            "manifest.yaml": "api_version: agent.platform/v1\n",
            "src/main.py": "print('hello')\n",
        }
    for name, content in files.items():
        p = tmpdir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmpdir


# ---------------------------------------------------------------------------
# Manifest 签名测试
# ---------------------------------------------------------------------------


class TestArtifactSignerManifest:
    """Manifest 签名和验证测试。"""

    def test_sign_manifest_returns_hex_string(self):
        """签名返回 64 字符的十六进制字符串（SHA-256）。"""
        signer = ArtifactSigner()
        manifest = _make_manifest()
        sig = signer.sign_manifest(manifest)
        assert isinstance(sig, str)
        assert len(sig) == 64
        # 确认是十六进制
        int(sig, 16)

    def test_sign_manifest_deterministic(self):
        """相同 manifest 的签名应该是确定性的。"""
        signer = ArtifactSigner()
        m1 = _make_manifest()
        m2 = _make_manifest()
        assert signer.sign_manifest(m1) == signer.sign_manifest(m2)

    def test_verify_manifest_success(self):
        """验证正确签名应返回 True。"""
        signer = ArtifactSigner()
        manifest = _make_manifest()
        sig = signer.sign_manifest(manifest)
        assert signer.verify_manifest(manifest, sig) is True

    def test_verify_manifest_failure_wrong_hash(self):
        """验证错误签名应返回 False。"""
        signer = ArtifactSigner()
        manifest = _make_manifest()
        assert signer.verify_manifest(manifest, "0" * 64) is False

    def test_modified_manifest_different_signature(self):
        """修改 manifest 后签名应不同。"""
        signer = ArtifactSigner()
        m1 = _make_manifest()
        m2 = _make_manifest(
            metadata={"id": "test-agent", "name": "Modified Agent"},
        )
        sig1 = signer.sign_manifest(m1)
        sig2 = signer.sign_manifest(m2)
        assert sig1 != sig2

    def test_verify_manifest_after_modification(self):
        """对修改后的 manifest 用原始签名验证应失败。"""
        signer = ArtifactSigner()
        original = _make_manifest()
        sig = signer.sign_manifest(original)
        modified = _make_manifest(
            metadata={"id": "test-agent", "name": "Changed"},
        )
        assert signer.verify_manifest(modified, sig) is False


# ---------------------------------------------------------------------------
# Package 签名测试
# ---------------------------------------------------------------------------


class TestArtifactSignerPackage:
    """Package 签名和验证测试。"""

    def test_sign_package_directory(self):
        """对目录签名应返回有效的哈希。"""
        signer = ArtifactSigner()
        pkg = _make_package_dir()
        sig = signer.sign_package(pkg)
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_sign_package_deterministic(self):
        """相同内容的目录签名应该一致。"""
        signer = ArtifactSigner()
        files = {"a.txt": "hello", "b.txt": "world"}
        pkg1 = _make_package_dir(files)
        pkg2 = _make_package_dir(files)
        assert signer.sign_package(pkg1) == signer.sign_package(pkg2)

    def test_verify_package_success(self):
        """验证正确的 package 签名应返回 True。"""
        signer = ArtifactSigner()
        pkg = _make_package_dir()
        sig = signer.sign_package(pkg)
        assert signer.verify_package(pkg, sig) is True

    def test_verify_package_failure_after_modification(self):
        """修改 package 内容后验证应失败。"""
        signer = ArtifactSigner()
        pkg = _make_package_dir({"file.txt": "original"})
        sig = signer.sign_package(pkg)
        # 修改文件内容
        (pkg / "file.txt").write_text("modified")
        assert signer.verify_package(pkg, sig) is False

    def test_sign_single_file(self):
        """对单个文件签名应返回有效哈希。"""
        signer = ArtifactSigner()
        tmpdir = Path(tempfile.mkdtemp())
        f = tmpdir / "test.tar.gz"
        f.write_bytes(b"fake archive content")
        sig = signer.sign_package(f)
        assert isinstance(sig, str)
        assert len(sig) == 64

    def test_sign_nonexistent_path_raises(self):
        """对不存在的路径签名应抛出 FileNotFoundError。"""
        signer = ArtifactSigner()
        with pytest.raises(FileNotFoundError):
            signer.sign_package(Path("/nonexistent/path"))
